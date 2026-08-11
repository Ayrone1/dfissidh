"""Lists gift collections from marketapp.org, so you can find the
collection_address to use in config.py's WATCHES.

Run with: python list_collections.py
"""
import requests

from config import MARKETAPP_API_KEY, MARKETAPP_BASE_URL


def main():
    url = f"{MARKETAPP_BASE_URL}/v1/collections/gifts/"
    headers = {"Accept": "application/json", "Authorization": MARKETAPP_API_KEY}

    response = requests.get(url, headers=headers, timeout=15)
    response.raise_for_status()
    collections = response.json()

    print(f"{'Name':<30} {'Address':<50} {'Floor':<10}")
    print("-" * 90)
    for c in collections:
        name = c.get("name", "?")
        address = c.get("address", "?")
        floor = (c.get("extra_data") or {}).get("floor", "?")
        print(f"{name:<30} {address:<50} {floor:<10}")


if __name__ == "__main__":
    main()
