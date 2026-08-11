"""Fetches the custom_emoji_id for each emoji in the @marketapp static
("marketappext") emoji pack, so we can use them in bot notifications.

Run with: python get_emoji_set.py
"""
import requests

from config import TELEGRAM_BOT_TOKEN

EMOJI_SET_NAME = "marketappext"


def main():
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/getStickerSet"
    response = requests.get(url, params={"name": EMOJI_SET_NAME}, timeout=15)
    data = response.json()

    if not data.get("ok"):
        print("Failed to fetch emoji set:", data)
        return

    stickers = data["result"]["stickers"]
    print(f"Found {len(stickers)} emoji in set '{EMOJI_SET_NAME}':\n")
    print(f"{'#':<4}{'Fallback':<10}{'custom_emoji_id'}")
    print("-" * 50)
    for i, sticker in enumerate(stickers, 1):
        fallback = sticker.get("emoji", "?")
        custom_emoji_id = sticker.get("custom_emoji_id", "?")
        print(f"{i:<4}{fallback:<10}{custom_emoji_id}")


if __name__ == "__main__":
    main()
