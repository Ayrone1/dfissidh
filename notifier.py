"""Sends notifications to Telegram."""
import time

import requests

from config import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, MAX_PERCENT_ABOVE_FLOOR

# Custom emoji from the @marketapp static pack (t.me/addemoji/marketappext),
# used inline via Telegram's <tg-emoji> HTML tag. Each needs a visible
# fallback character (shown to clients that can't render custom emoji) plus
# its custom_emoji_id.
CURRENCY_EMOJI = {
    "GRAM": {"fallback": "\U0001F48E", "id": "5280557149632632595"},  # 💎
    "TON": {"fallback": "\U0001F48E", "id": "5280557149632632595"},   # 💎 (same as GRAM)
    "USDT": {"fallback": "\U0001F4B2", "id": "5285027218450326247"},  # 💲
}

# On-chain decimal places per currency -- GRAM/TON use 9, USDT (as a TON
# jetton) uses 6. Using the wrong divisor is what made USDT prices look off.
CURRENCY_DECIMALS = {
    "GRAM": 9,
    "TON": 9,
    "USDT": 6,
}

# Telegram allows roughly 1 message/second to the same chat. Sending faster
# triggers 429 errors and those messages are lost. This is the minimum gap
# enforced between consecutive sends.
MIN_SECONDS_BETWEEN_MESSAGES = 1.2


def send_telegram_message(text: str, max_retries: int = 3) -> bool:
    """Send a plain-text message to the configured Telegram chat.

    Automatically waits and retries if Telegram responds with 429 (rate
    limited), honoring the `retry_after` value Telegram provides. Returns
    True if the message was eventually sent, False if it was dropped after
    max_retries.
    """
    if not TELEGRAM_CHAT_ID:
        print("[notifier] TELEGRAM_CHAT_ID is not set in config.py -- skipping send.")
        print(f"[notifier] Message was: {text}")
        return False

    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": False,
    }

    for attempt in range(max_retries + 1):
        try:
            response = requests.post(url, data=payload, timeout=10)
        except requests.RequestException as e:
            print(f"[notifier] Failed to send Telegram message: {e}")
            return False

        if response.status_code == 429:
            retry_after = 2
            try:
                retry_after = response.json().get("parameters", {}).get("retry_after", 2)
            except ValueError:
                pass
            print(f"[notifier] Rate limited, waiting {retry_after}s before retry ({attempt+1}/{max_retries})...")
            time.sleep(retry_after + 0.5)
            continue

        try:
            response.raise_for_status()
        except requests.RequestException as e:
            print(f"[notifier] Failed to send Telegram message: {e}")
            return False

        time.sleep(MIN_SECONDS_BETWEEN_MESSAGES)  # pace the next message
        return True

    print("[notifier] Giving up on message after repeated rate limiting.")
    return False


def _format_number(amount: float) -> str:
    """Format a number with thousands separators, dropping trailing .00
    for whole numbers (2,247 instead of 2,247.00; 0.44 keeps its decimals).
    """
    formatted = f"{amount:,.2f}"
    if formatted.endswith(".00"):
        formatted = formatted[:-3]
    return formatted


def _format_price(min_bid_nano, currency: str) -> tuple[str, float | None]:
    """Convert a raw min_bid amount to a human-readable price string,
    using the correct decimal precision for the given currency.

    Returns (formatted_string, raw_amount) -- raw_amount is None if the
    input couldn't be parsed, so callers can still use it for USD conversion.
    """
    if min_bid_nano is None:
        return "N/A", None

    decimals = CURRENCY_DECIMALS.get(currency, 9)
    try:
        amount = int(min_bid_nano) / (10 ** decimals)
    except (TypeError, ValueError):
        return str(min_bid_nano), None

    return _format_number(amount), amount


def _currency_emoji_html(currency: str) -> str:
    """Return the <tg-emoji> HTML snippet for a currency, or an empty
    string if we don't have a mapped emoji for it.
    """
    emoji = CURRENCY_EMOJI.get(currency)
    if not emoji:
        return ""
    return f'<tg-emoji emoji-id="{emoji["id"]}">{emoji["fallback"]}</tg-emoji>'


def _floor_pct_emoji(pct: float, threshold: float) -> str:
    """Colored circle indicating how close to the floor price a listing is.

    Telegram doesn't support literal text color in bot messages, so this
    is the standard workaround: a colored emoji standing in for a color.
    Green near the floor, shading through yellow/orange up to red as it
    approaches `threshold` -- the max-percent-above-floor cutoff for
    whichever category(ies) this listing matched (anything past its own
    threshold never reaches this function -- it's filtered out earlier).
    """
    if pct <= 0 or not threshold:
        return "\U0001F7E2"  # 🟢 green

    fraction = min(pct / threshold, 1.0)
    if fraction <= 0.2:
        return "\U0001F7E2"  # 🟢 green
    elif fraction <= 0.5:
        return "\U0001F7E1"  # 🟡 yellow
    elif fraction <= 0.8:
        return "\U0001F7E0"  # 🟠 orange
    else:
        return "\U0001F534"  # 🔴 red


def format_listing_message(
    listing: dict,
    label: str = "",
    gram_usd_rate: float = None,
    floor_price: float = None,
    threshold: float = None,
) -> str:
    """Turn a marketapp.org listing (NFTItem) into a Telegram message.

    `label` is a friendly name for which watch(es) matched (e.g. "Lucky
    number", or "Good Number, Lucky number" if it matched more than one).
    `gram_usd_rate` is USD per 1 GRAM/TON (see marketapp_client.get_gram_usd_rate).
    When provided and the listing is priced in GRAM/TON, an approximate USD
    value is appended after the price, matching the site's "~$3,016" style.
    `floor_price` is the collection's current floor price in GRAM (see
    marketapp_client.get_all_collection_floors). When provided, shows how
    far above the floor this listing's price is as a percentage next to
    the listing's own price, and also as its own "Floor price: ..." line
    (with a USD estimate, when gram_usd_rate is available) before the link.
    `threshold` is the max-percent-above-floor cutoff to scale the
    green/yellow/orange/red coloring against -- typically the specific
    watch's own `max_percent_above_floor` (or the loosest one, if the
    listing matched multiple categories with different thresholds).
    Falls back to the global MAX_PERCENT_ABOVE_FLOOR default if omitted.
    """
    if threshold is None:
        threshold = MAX_PERCENT_ABOVE_FLOOR

    name = listing.get("name", "Untitled listing")
    address = listing.get("address", "")
    currency = listing.get("currency", "GRAM")
    item_num = listing.get("item_num")

    price_str, raw_amount = _format_price(listing.get("min_bid"), currency)
    emoji_html = _currency_emoji_html(currency)

    usd_str = ""
    if gram_usd_rate is not None and raw_amount is not None and currency in ("GRAM", "TON"):
        usd_amount = raw_amount * gram_usd_rate
        usd_str = f" ~${_format_number(usd_amount)}"

    floor_str = ""
    if floor_price is not None and floor_price > 0 and raw_amount is not None:
        # Floor is always in GRAM -- convert the listing's price to a GRAM
        # equivalent first if it's priced in USDT, so the comparison is fair.
        price_in_gram = raw_amount
        if currency == "USDT" and gram_usd_rate:
            price_in_gram = raw_amount / gram_usd_rate
        elif currency == "USDT":
            price_in_gram = None  # can't convert without a rate

        if price_in_gram is not None:
            pct_above_floor = (price_in_gram - floor_price) / floor_price * 100
            color = _floor_pct_emoji(pct_above_floor, threshold)
            floor_str = f" ({color} {pct_above_floor:+.1f}% vs floor)"

    title = f"{name} #{item_num}" if item_num else name

    floor_line = ""
    if floor_price is not None and floor_price > 0 and gram_usd_rate is not None:
        floor_usd_str = _format_number(floor_price * gram_usd_rate)
        floor_line = f"\n\nFloor price: ~${floor_usd_str}"

    text = f"<b>{title}</b>\nPrice: <b>{price_str}</b>{emoji_html}{usd_str}{floor_str}"
    if label:
        text += f"\n\n{label}"
    text += floor_line
    if address:
        text += f"\n\nhttps://marketapp.org/nft/{address}/"

    return text
