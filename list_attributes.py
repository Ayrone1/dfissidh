"""Lists trait attributes (models, symbols, backdrops) for one gift
collection, so you can pick exact variants to watch for.

Run with: python list_attributes.py
"""
import requests

from config import MARKETAPP_API_KEY, MARKETAPP_BASE_URL

# The collection you're watching
COLLECTION_ADDRESS = "EQAOQdwdw8kGftJCSFgOErM1mBjYPe4DBPq8-AhF6vr9si5N"


def main():
    url = f"{MARKETAPP_BASE_URL}/v1/collections/{COLLECTION_ADDRESS}/attributes/"
    headers = {"Accept": "application/json", "Authorization": MARKETAPP_API_KEY}

    response = requests.get(url, headers=headers, timeout=15)
    response.raise_for_status()
    data = response.json()

    for attr in data.get("attributes", []):
        trait_type = attr.get("trait_type", "?")
        print(f"\n=== {trait_type} ===")
        for v in attr.get("values", []):
            value = v.get("value", "?")
            count = v.get("count", 0)
            floor = v.get("floor", "?")
            print(f"  {value:<25} count={count:<6} floor={floor}")


if __name__ == "__main__":
    main()
