import os


def _require_env(name: str) -> str:
    """Read a required secret from the environment.

    Locally: export it in your shell before running, e.g.
        export TELEGRAM_BOT_TOKEN=...
    On GitHub Actions: set it as a repository secret (Settings -> Secrets
    and variables -> Actions -> New repository secret) -- see
    .github/workflows/bot.yml, which passes these through as env vars.
    """
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(
            f"Missing required environment variable: {name}. "
            f"Set it locally (export {name}=...) or as a GitHub Actions secret "
            f"of the same name."
        )
    return value


# --- Telegram settings ---
TELEGRAM_BOT_TOKEN = _require_env("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = _require_env("TELEGRAM_CHAT_ID")

# --- Marketapp.org API settings ---
MARKETAPP_API_KEY = _require_env("MARKETAPP_API_KEY")
MARKETAPP_BASE_URL = "https://api.marketapp.org"

# --- What you're looking for ---
# Two kinds of watches:
#
# 1) "gift" watches use the fast server-side filters on /v1/gifts/onsale/
#    (collection_address, model, symbol, backdrop, min_price, max_price, etc.)
#    -- best for regular Gift collections.
#
# 2) "attribute" watches are for collections whose useful traits (like
#    Anonymous Numbers' "Has Digit 8" or "Unique Digits") aren't supported
#    as server-side filters. These fetch on-sale items for the collection
#    and filter by attribute values on our side.
#    - "require": trait_type -> value that MUST match (single condition set)
#    - "require_any_of": [ {..}, {..} ] -- like "require", but a list of
#      condition sets OR'd together: the item matches if it fully satisfies
#      AT LEAST ONE of them. Use this instead of "require" when a category
#      should match on "this OR that".
#    - "exclude": trait_type -> value that must NOT match -- always
#      enforced, on top of whichever require/require_any_of option matched.
#
# Every watch can also set its own "max_percent_above_floor" -- how far
# above the collection's current floor price a listing is still allowed to
# be for THIS category. A listing priced further above floor than that is
# simply skipped for that category (not notified, not remembered -- it can
# still match later if the price drops). If a watch doesn't set this, it
# falls back to the global MAX_PERCENT_ABOVE_FLOOR default below.
#
# All Anonymous Number traits computed here (Has Digit 0-9, Unique Digits,
# 6 or Fewer Unique Digits, Has Two 8s) are always computed on the number's
# BODY only -- the fixed "888" prefix is stripped first and never counts,
# in every category.
#
# Run list_collections.py to find collection addresses, and
# list_attributes.py (edit COLLECTION_ADDRESS in it) to see available
# trait_type/value names for a given collection.

WATCHES = [
    {
        "type": "attribute",
        "name": "Lucky number",
        "label": "Lucky number",
        "collection_address": "EQAOQdwdw8kGftJCSFgOErM1mBjYPe4DBPq8-AhF6vr9si5N",
        # Always at least one 8 in the body, and no 4. On top of that, EITHER
        # two 8's OR 6-or-fewer unique digits (the "at least one 8" part is
        # spelled out again in the second option since "Has Two 8s" already
        # implies it in the first, but the second doesn't on its own).
        "require_any_of": [
            {"Has Two 8s": "Yes"},
            {"Has Digit 8": "Yes", "6 or Fewer Unique Digits": "Yes"},
        ],
        "exclude": {"Has Digit 4": "Yes"},
        "max_percent_above_floor": 2,
    },
    {
    "type": "attribute",
    "name": "Test number",
    "label": "Test number",
    "collection_address": "EQAOQdwdw8kGftJCSFgOErM1mBjYPe4DBPq8-AhF6vr9si5N",
    "require": {"Has Digit 4": "Yes", "Has Digit 5": "Yes",  "Has Digit 6": "Yes", "Has Digit 8": "Yes"},
    "exclude": {},
    "max_percent_above_floor": 20,
},
    {
        "type": "attribute",
        "name": "Unique digits: 5",
        "label": "Unique digits: 5",
        "collection_address": "EQAOQdwdw8kGftJCSFgOErM1mBjYPe4DBPq8-AhF6vr9si5N",
        "require": {"Unique Digits": "5"},
        "exclude": {},
        "max_percent_above_floor": 2,
        # Kept as its own category -- note it still overlaps with Lucky
        # number's "6 or fewer unique digits" condition, so a 5-unique-
        # digit number that also has an 8 (and no 4) can get tagged with
        # both labels.
    },
]

# --- Polling ---
POLL_INTERVAL_SECONDS = 150  # how often to check, e.g. every 1 minute

# For "attribute" watches, how many pages (100 items each) to scan per
# check. The collection can have 100k+ items total, but only on-sale ones
# are returned, and we only need to catch NEW ones each cycle.
ATTRIBUTE_WATCH_MAX_PAGES = 3

# Default max-percent-above-floor for any watch that doesn't set its own
# "max_percent_above_floor". The color coding in notifier.py scales to
# whichever threshold actually applied to a given listing (green/yellow/
# orange/red at 20%/50%/80% of that listing's own threshold).
MAX_PERCENT_ABOVE_FLOOR = 20

