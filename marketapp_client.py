"""Talks to the marketapp.org API.

Docs: https://api.marketapp.org/docs/
"""
import requests

from config import MARKETAPP_API_KEY, MARKETAPP_BASE_URL

HEADERS = {
    "Accept": "application/json",
    "Authorization": MARKETAPP_API_KEY,  # raw token, no "Bearer " prefix
}


def search_gift_listings(params: dict) -> list[dict]:
    """Query /v1/gifts/onsale/ -- fast server-side filtering for Gift collections."""
    url = f"{MARKETAPP_BASE_URL}/v1/gifts/onsale/"
    clean_params = {k: v for k, v in params.items() if v is not None}

    try:
        response = requests.get(url, headers=HEADERS, params=clean_params, timeout=15)
        response.raise_for_status()
        data = response.json()
    except requests.RequestException as e:
        print(f"[marketapp_client] Gift request failed: {e}")
        return []
    except ValueError:
        print("[marketapp_client] Gift response was not valid JSON.")
        return []

    return data.get("items", [])


def get_collection_onsale_items(collection_address: str, max_pages: int = 20, debug: bool = False) -> list[dict]:
    """Fetch on-sale items for any collection (gifts, usernames, numbers)
    via the generic /v1/nfts/collections/{address}/ endpoint, paginating
    with cursor up to max_pages (100 items per page).
    """
    url = f"{MARKETAPP_BASE_URL}/v1/nfts/collections/{collection_address}/"
    items = []
    cursor = None

    for page_num in range(max_pages):
        params = {"filter_by": "onsale", "limit": 100}
        if cursor:
            params["cursor"] = cursor

        try:
            response = requests.get(url, headers=HEADERS, params=params, timeout=15)
            if debug:
                print(f"[marketapp_client] page {page_num+1}: HTTP {response.status_code}")
            response.raise_for_status()
            data = response.json()
        except requests.RequestException as e:
            print(f"[marketapp_client] Collection request failed: {e}")
            if hasattr(e, "response") and e.response is not None:
                print(f"[marketapp_client] Response body: {e.response.text[:500]}")
            break
        except ValueError:
            print("[marketapp_client] Collection response was not valid JSON.")
            break

        page_items = data.get("items", [])
        items.extend(page_items)

        cursor = data.get("cursor")
        if not cursor or not page_items:
            break

    return items


def _extract_digits(name: str) -> str:
    """Pull just the digit characters out of an item's name (e.g. a phone number)."""
    return "".join(ch for ch in (name or "") if ch.isdigit())


# Every item in the Anonymous Telegram Numbers collection is formatted as
# "+888 XXXX XXXX" -- the "888" is a fixed country/service code, not part
# of the actual number. Traits like "Has Digit 8" and "Unique Digits" (both
# on the site and here) are computed on the number BODY only, excluding
# this prefix -- otherwise "Has Digit 8" would trivially be true for almost
# every item just because of the "888" prefix.
NUMBER_PREFIX = "888"


def _extract_body_digits(name: str) -> str:
    """Digits of the number body only, with the fixed prefix stripped."""
    all_digits = _extract_digits(name)
    if all_digits.startswith(NUMBER_PREFIX):
        return all_digits[len(NUMBER_PREFIX):]
    return all_digits


def compute_number_traits(name: str) -> dict:
    """Derive Anonymous Number traits from the number itself, since the API
    doesn't return these as stored per-item attributes.

    Covers the traits needed for digit-presence and uniqueness watches:
    "Has Digit 0".."Has Digit 9", "Unique Digits", "Has Two 8s" (at least
    two 8's in the body), "6 or Fewer Unique Digits", and "Second Digit Is
    8" (the body's 2nd character, e.g. body "08123456" -> Yes). Computed
    on the number body only (prefix stripped -- see NUMBER_PREFIX above --
    so the prefix never counts toward any of these traits, in any watch).
    (Other traits shown by list_attributes.py, like Mask Left/Right or
    Arithmetic Progression, aren't computed here -- ask if you need those too.)
    """
    digits = _extract_body_digits(name)
    traits = {f"Has Digit {d}": ("Yes" if d in digits else "No") for d in "0123456789"}
    unique_count = len(set(digits)) if digits else 0
    traits["Unique Digits"] = str(unique_count)
    traits["6 or Fewer Unique Digits"] = "Yes" if unique_count <= 6 else "No"
    traits["Has Two 8s"] = "Yes" if digits.count("8") >= 2 else "No"
    traits["Second Digit Is 8"] = "Yes" if len(digits) > 1 and digits[1] == "8" else "No"
    return traits


def attributes_as_dict(listing: dict) -> dict:
    """Get a {trait_type: value} dict for a listing.

    Prefers real attributes returned by the API; if that list is empty
    (as it is for Anonymous Numbers via this endpoint), falls back to
    computing traits from the item's name/number.
    """
    api_attrs = {a["trait_type"]: a["value"] for a in listing.get("attributes", [])}
    if api_attrs:
        return api_attrs
    return compute_number_traits(listing.get("name", ""))


def _norm(s) -> str:
    """Normalize a value for loose comparison (case/whitespace-insensitive)."""
    return str(s).strip().lower()


def matches_conditions(listing: dict, require_options: list[dict], exclude: dict) -> bool:
    """Check whether a listing's attributes satisfy a watch's conditions.

    require_options: a list of require-dicts (trait_type -> value that MUST
    equal), OR'd together -- the listing matches if it fully satisfies AT
    LEAST ONE of them. An empty list (or a list containing only an empty
    dict) means "no requirement", i.e. everything passes this part.
    exclude: trait_type -> value that must NOT equal, applied on top of the
    above (always enforced, regardless of which require_option matched).
    Comparison is case- and whitespace-insensitive.
    """
    attrs = attributes_as_dict(listing)
    attrs_normalized = {_norm(k): _norm(v) for k, v in attrs.items()}

    for trait_type, forbidden_value in (exclude or {}).items():
        if attrs_normalized.get(_norm(trait_type)) == _norm(forbidden_value):
            return False

    if not require_options:
        return True

    for require in require_options:
        if all(
            attrs_normalized.get(_norm(trait_type)) == _norm(wanted_value)
            for trait_type, wanted_value in (require or {}).items()
        ):
            return True

    return False


def extract_id(listing: dict) -> str:
    """Unique identifier for a listing: its NFT address."""
    return listing["address"]


# On-chain decimal places per currency -- GRAM/TON use 9, USDT (as a TON
# jetton) uses 6.
CURRENCY_DECIMALS = {
    "GRAM": 9,
    "TON": 9,
    "USDT": 6,
}


def price_in_gram(listing: dict, gram_usd_rate: float | None) -> float | None:
    """Convert a listing's price to a GRAM-equivalent amount, so it can be
    compared against a collection's floor (which is always in GRAM).

    Returns None if the price can't be determined (e.g. a USDT listing
    when no exchange rate is available).
    """
    currency = listing.get("currency", "GRAM")
    min_bid = listing.get("min_bid")
    if min_bid is None:
        return None

    decimals = CURRENCY_DECIMALS.get(currency, 9)
    try:
        amount = int(min_bid) / (10 ** decimals)
    except (TypeError, ValueError):
        return None

    if currency == "USDT":
        if not gram_usd_rate:
            return None
        return amount / gram_usd_rate

    return amount


def get_collection_floors(collection_addresses: set[str], debug: bool = False) -> dict[str, float] | None:
    """Fetch the current floor price, but only for the given collection
    address(es) -- e.g. just the Anonymous Numbers collection we're
    actually watching, instead of parsing/logging every collection on the
    site. Floor is in GRAM (site's default currency).

    There's no per-collection lookup endpoint, so this still has to fetch
    the full /v1/collections/ list under the hood, but only returns (and
    only debug-prints) the address(es) you asked for.

    Returns None (not {}) if the request itself failed -- this is a
    distinct outcome from "the request succeeded but no matching
    collections were found," which legitimately returns {}. Callers rely
    on this distinction: {} would previously get treated by
    _within_floor_threshold as "floor unknown, let it through", so any
    transient network failure here silently disabled the price-threshold
    filter entirely. Returning None instead makes call sites handle a
    failed fetch explicitly (e.g. reuse a cached floor) instead of
    accidentally waving every listing through.
    """
    url = f"{MARKETAPP_BASE_URL}/v1/collections/"
    try:
        response = requests.get(url, headers=HEADERS, timeout=15)
        response.raise_for_status()
        collections = response.json()
    except requests.RequestException as e:
        print(f"[marketapp_client] Failed to fetch collection floors: {e}")
        return None
    except ValueError:
        print("[marketapp_client] Collection floors response was not valid JSON.")
        return None

    floors = {}
    for c in collections:
        address = c.get("address")
        if address not in collection_addresses:
            continue

        floor_raw = (c.get("extra_data") or {}).get("floor")
        if floor_raw is None:
            continue
        try:
            floor_value = float(floor_raw) / 1_000_000_000
        except (TypeError, ValueError):
            continue

        floors[address] = floor_value
        if debug:
            print(f"[marketapp_client] floor for {address}: raw={floor_raw} -> {floor_value}")

    return floors


def get_gram_usd_rate() -> float | None:
    """Fetch the current GRAM->USD exchange rate.

    Uses the documented /v1/fragment/stars/price/ endpoint, which returns
    the GRAM and USD cost of the same quantity of Stars -- the ratio
    usd/gram gives a live conversion rate without hardcoding or relying on
    a third-party price feed.
    """
    url = f"{MARKETAPP_BASE_URL}/v1/fragment/stars/price/"
    try:
        response = requests.post(url, headers=HEADERS, json={"quantity": 50}, timeout=15)
        response.raise_for_status()
        data = response.json()
    except requests.RequestException as e:
        print(f"[marketapp_client] Failed to fetch GRAM/USD rate: {e}")
        return None
    except ValueError:
        print("[marketapp_client] GRAM/USD rate response was not valid JSON.")
        return None

    gram = data.get("gram")
    usd = data.get("usd")
    if not gram or usd is None:
        return None

    try:
        return float(usd) / float(gram)
    except (TypeError, ValueError, ZeroDivisionError):
        return None
