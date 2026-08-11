"""Marketapp.org listing watcher -> Telegram notifier.

Run with: python bot.py
Stop with: Ctrl+C

Notification behavior:
- Nothing is persisted to disk -- every time the bot starts, it's a
  "virgin" run: the very first check notifies about every current match,
  same as if it had never seen any of them before.
- After that first check, within the same run, a listing that's already
  been notified about is NOT notified again on later cycles -- UNLESS its
  price has changed since the last notification, in which case it's sent
  again (so you catch price drops/rises on things you're already tracking).
- Restarting the bot forgets all of this and starts fresh.
- Each watch/category has its own "how far above floor is still OK"
  threshold (see `max_percent_above_floor` in config.py). A listing that's
  priced too far above floor for a given category simply doesn't count as
  a match for that category (it's not remembered, not notified, and can
  still match later if the price comes back down) -- but if it matches
  more than one category and clears at least one of their thresholds, it's
  still notified, tagged with every category label it actually qualifies
  for.
- Floor prices are cached across polling cycles. If a single cycle's
  floor-price fetch fails (timeout, rate limit, etc.), the bot reuses the
  last known-good floor prices for that cycle instead of treating every
  listing as unfiltered -- see get_collection_floors() in
  marketapp_client.py and _within_floor_threshold() below.
"""
import signal
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime

from config import WATCHES, POLL_INTERVAL_SECONDS, ATTRIBUTE_WATCH_MAX_PAGES, MAX_PERCENT_ABOVE_FLOOR
from marketapp_client import (
    search_gift_listings,
    get_collection_onsale_items,
    matches_conditions,
    extract_id,
    get_gram_usd_rate,
    get_collection_floors,
    price_in_gram,
)
from notifier import send_telegram_message, format_listing_message

DEBUG = True  # set to False once things are working, to quiet the logs


def fetch_all_collection_items(collection_addresses: set[str]) -> dict[str, list[dict]]:
    """Fetch on-sale items for each unique collection concurrently.

    Pages within one collection are still sequential (cursor pagination
    requires it), but different collections are fetched in parallel threads,
    and each collection is only fetched once even if multiple watches use it.
    """
    results: dict[str, list[dict]] = {}

    with ThreadPoolExecutor(max_workers=max(1, len(collection_addresses))) as executor:
        future_to_address = {
            executor.submit(get_collection_onsale_items, address, ATTRIBUTE_WATCH_MAX_PAGES, DEBUG): address
            for address in collection_addresses
        }
        for future in as_completed(future_to_address):
            address = future_to_address[future]
            try:
                results[address] = future.result()
            except Exception as e:
                print(f"[bot] Failed fetching collection {address}: {e}")
                results[address] = []

    return results


def _require_options(watch: dict) -> list[dict]:
    """Normalize a watch's requirement config into a list of require-dicts
    that are OR'd together -- an item matches if it fully satisfies AT
    LEAST ONE of them.

    `require_any_of: [ {...}, {...} ]` is used as-is. A plain `require:
    {...}` is treated as a single-option list (backward compatible with
    simpler watches that only need one set of conditions). If neither is
    present, there's no requirement to satisfy (only `exclude` applies).
    """
    if "require_any_of" in watch:
        return watch["require_any_of"] or [{}]
    if "require" in watch:
        return [watch["require"]]
    return [{}]


def _within_floor_threshold(
    listing: dict, watch: dict, gram_usd_rate: float | None, collection_floors: dict[str, float]
) -> bool:
    """Whether a listing is priced within *this watch's own* max-percent-
    above-floor threshold. Falls back to the global MAX_PERCENT_ABOVE_FLOOR
    default if the watch doesn't set `max_percent_above_floor` itself.

    If the floor or price can't be determined, the listing does NOT count
    as a match -- an unknown floor must never be treated as "assume it's
    fine," since that's what silently disables this filter (e.g. during a
    transient floor-price API failure). `collection_floors` is expected to
    be the cached, last-known-good floors (see main()/check_watches()), so
    "unknown" here means genuinely never fetched successfully, not just
    "this cycle's fetch had a hiccup."
    """
    threshold = watch.get("max_percent_above_floor", MAX_PERCENT_ABOVE_FLOOR)
    floor_price = collection_floors.get(listing.get("collection_address"))
    price = price_in_gram(listing, gram_usd_rate)

    if not floor_price or floor_price <= 0 or price is None:
        return False

    pct_above_floor = (price - floor_price) / floor_price * 100
    return pct_above_floor <= threshold


def collect_matches(
    collection_items: dict[str, list[dict]],
    gram_usd_rate: float | None,
    collection_floors: dict[str, float],
) -> dict[str, dict]:
    """Run every watch and group results by listing address, so a listing
    matching multiple watches ends up with ALL its matching labels attached
    instead of only being reported under whichever watch runs first.

    Each watch's own floor-price threshold is applied here, per watch,
    before a label is attached -- so a listing only picks up a category's
    label if it clears that category's threshold, even if it clears a
    different category's (looser) threshold too.

    Returns {address: {"listing": listing_dict, "labels": [label, ...]}}
    """
    attribute_watches = [w for w in WATCHES if w.get("type", "gift") == "attribute"]
    gift_watches = [w for w in WATCHES if w.get("type", "gift") != "attribute"]

    matches: dict[str, dict] = {}
    skipped_by_watch: dict[str, int] = {}

    def add_match(listing: dict, label: str) -> None:
        address = extract_id(listing)
        entry = matches.setdefault(address, {"listing": listing, "labels": []})
        if label not in entry["labels"]:
            entry["labels"].append(label)

    # Attribute watches: filter from the already-fetched shared collection data
    for watch in attribute_watches:
        all_items = collection_items.get(watch["collection_address"], [])
        require_options = _require_options(watch)
        exclude = watch.get("exclude", {})
        label = watch.get("label", watch["name"])
        for item in all_items:
            if not matches_conditions(item, require_options, exclude):
                continue
            if not _within_floor_threshold(item, watch, gram_usd_rate, collection_floors):
                skipped_by_watch[label] = skipped_by_watch.get(label, 0) + 1
                continue
            add_match(item, label)

    # Gift watches: each has its own server-side filtered query, fetch concurrently
    if gift_watches:
        with ThreadPoolExecutor(max_workers=max(1, len(gift_watches))) as executor:
            future_to_watch = {
                executor.submit(search_gift_listings, w.get("params", {})): w
                for w in gift_watches
            }
            for future in as_completed(future_to_watch):
                watch = future_to_watch[future]
                try:
                    listings = future.result()
                except Exception as e:
                    print(f"[bot] Failed fetching gift watch '{watch['name']}': {e}")
                    listings = []
                label = watch.get("label", watch["name"])
                for listing in listings:
                    if listing.get("is_restricted"):
                        continue
                    if not _within_floor_threshold(listing, watch, gram_usd_rate, collection_floors):
                        skipped_by_watch[label] = skipped_by_watch.get(label, 0) + 1
                        continue
                    add_match(listing, label)

    if DEBUG:
        for label, count in skipped_by_watch.items():
            print(f"    -> '{label}': skipped {count} listing(s) priced above its floor threshold")

    return matches


def _price_signature(listing: dict) -> tuple:
    """Raw (currency, min_bid) pair identifying a listing's current price.

    Used (not the GRAM-converted value) so a genuine price change is always
    detected exactly, without any rounding/rate-conversion noise.
    """
    return (listing.get("currency"), listing.get("min_bid"))


def check_watches(notified_prices: dict[str, tuple], floor_cache: dict[str, float]) -> None:
    """Fetch everything matching the configured watches and notify about
    matches that are either new (not in `notified_prices` yet) or whose
    price has changed since the last time we notified about them.

    `notified_prices` is mutated in place: {address: (currency, min_bid)}
    for everything we've ever sent a notification for during this run.

    `floor_cache` is mutated in place: {collection_address: floor_price},
    the last known-good floor price per collection. It persists across
    calls (owned by main()'s loop) so that a single cycle's failed
    floor-price fetch doesn't wipe out floor data the bot already knew --
    see get_collection_floors() and _within_floor_threshold().
    """
    attribute_watches = [w for w in WATCHES if w.get("type", "gift") == "attribute"]
    watched_collections = {w["collection_address"] for w in attribute_watches}

    # Fetch the GRAM->USD rate and floor price, but only for the
    # collection(s) we're actually watching (e.g. just Anonymous Numbers),
    # not every collection on the site.
    gram_usd_rate = get_gram_usd_rate()

    fetched_floors = get_collection_floors(watched_collections, debug=DEBUG)
    if fetched_floors is None:
        # This cycle's floor-price fetch failed outright (network error,
        # timeout, bad response, etc). Reuse whatever floors we already
        # have cached instead of treating floor prices as unknown -- an
        # unknown floor makes _within_floor_threshold reject the listing,
        # so falling back to a stale-but-real floor for one cycle is far
        # safer than either bypassing the filter or blacking out every
        # watch until the next successful fetch.
        if DEBUG:
            print("    -> Floor price fetch failed this cycle, reusing last known floor prices")
    else:
        floor_cache.update(fetched_floors)
    collection_floors = floor_cache

    if DEBUG:
        print(f"    -> GRAM/USD rate: {gram_usd_rate}")

    # Fetch each unique collection only once, concurrently
    collection_items = fetch_all_collection_items(watched_collections) if watched_collections else {}

    if DEBUG and collection_items:
        for address, items in collection_items.items():
            print(f"    -> collection {address}: {len(items)} on-sale item(s) fetched")

    # Never notify about restricted numbers (Anonymous Numbers collection) --
    # these can't be freely used/resold the same way, so exclude them upfront.
    for address, items in collection_items.items():
        before = len(items)
        filtered = [item for item in items if not item.get("is_restricted")]
        if DEBUG and before != len(filtered):
            print(f"    -> collection {address}: filtered out {before - len(filtered)} restricted item(s)")
        collection_items[address] = filtered

    all_matches = collect_matches(collection_items, gram_usd_rate, collection_floors)
    addresses = list(all_matches.keys())

    if not addresses:
        print(f"[{datetime.now():%H:%M:%S}] no matches within threshold across {len(WATCHES)} watch(es)")
        return

    # Only notify about listings that are new this run, or whose price
    # changed since we last notified about them.
    to_notify = []
    unchanged_count = 0
    for address in addresses:
        listing = all_matches[address]["listing"]
        signature = _price_signature(listing)
        if notified_prices.get(address) == signature:
            unchanged_count += 1
            continue
        to_notify.append(address)

    if unchanged_count and DEBUG:
        print(f"    -> skipped {unchanged_count} listing(s) already notified with no price change")

    if not to_notify:
        print(f"[{datetime.now():%H:%M:%S}] no new or price-changed matches")
        return

    print(f"[{datetime.now():%H:%M:%S}] {len(to_notify)} match(es) to notify (combined across watches)")

    # Map each label to the threshold its watch was configured with, so a
    # notification's color-coding scales to the right cutoff even when a
    # listing matched multiple categories.
    label_thresholds = {
        w.get("label", w["name"]): w.get("max_percent_above_floor", MAX_PERCENT_ABOVE_FLOOR) for w in WATCHES
    }

    for address in to_notify:
        entry = all_matches[address]
        listing = entry["listing"]
        combined_label = ", ".join(entry["labels"])
        if DEBUG and len(entry["labels"]) > 1:
            print(f"    -> {address} matched multiple watches: {combined_label}")
        if DEBUG and address in notified_prices:
            print(f"    -> {address} price changed since last notification, re-notifying")

        # Use the loosest threshold among the categories that matched, since
        # the listing already cleared that category's own cutoff.
        threshold = max(
            (label_thresholds.get(l, MAX_PERCENT_ABOVE_FLOOR) for l in entry["labels"]),
            default=MAX_PERCENT_ABOVE_FLOOR,
        )

        floor_price = collection_floors.get(listing.get("collection_address"))
        message = format_listing_message(
            listing,
            label=combined_label,
            gram_usd_rate=gram_usd_rate,
            floor_price=floor_price,
            threshold=threshold,
        )
        sent = send_telegram_message(message)
        if sent:
            notified_prices[address] = _price_signature(listing)
        # if sending failed even after retries, leave its old signature (or
        # absence) in place so it's retried again next cycle instead of
        # silently being treated as already notified


def main():
    print("Starting marketapp watcher...")
    print(f"Watching {len(WATCHES)} search(es), checking every {POLL_INTERVAL_SECONDS}s.")
    print("Fresh start: this first check notifies about every current match. After that, a listing")
    print("is only re-notified if its price changes. Restarting the bot resets this tracking.\n")

    # GitHub Actions (and most process managers) stop a job with SIGTERM,
    # not Ctrl+C/SIGINT -- catch it the same way so a job that gets
    # cancelled/times out still shuts down cleanly instead of a raw traceback.
    def _handle_sigterm(signum, frame):
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, _handle_sigterm)

    notified_prices: dict[str, tuple] = {}
    floor_cache: dict[str, float] = {}  # persists across polls -- see check_watches()

    try:
        while True:
            check_watches(notified_prices, floor_cache)
            time.sleep(POLL_INTERVAL_SECONDS)
    except KeyboardInterrupt:
        print("\nStopped. Bye!")


if __name__ == "__main__":
    main()
