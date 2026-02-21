#!/usr/bin/env python3
"""Generate a Telethon StringSession for the Telegram channel listener.

Run this script once interactively to authenticate with your Telegram account.
It will print a session string that you paste into your .env file as
TELEGRAM_SESSION_STRING.

Usage::

    python scripts/generate_telegram_session.py

You will need:
    - TELEGRAM_API_ID and TELEGRAM_API_HASH from https://my.telegram.org/apps
    - Your phone number and the verification code Telegram sends you
"""

from __future__ import annotations

import asyncio
import sys


async def main() -> None:
    try:
        from telethon import TelegramClient
        from telethon.sessions import StringSession
    except ImportError:
        print("Error: telethon is not installed. Run: pip install telethon")
        sys.exit(1)

    print("=== Telegram StringSession Generator ===\n")

    api_id_str = input("Enter your TELEGRAM_API_ID: ").strip()
    try:
        api_id = int(api_id_str)
    except ValueError:
        print("Error: API ID must be a number")
        sys.exit(1)

    api_hash = input("Enter your TELEGRAM_API_HASH: ").strip()
    if not api_hash:
        print("Error: API hash cannot be empty")
        sys.exit(1)

    client = TelegramClient(StringSession(), api_id, api_hash)
    await client.connect()

    if not await client.is_user_authorized():
        phone = input("Enter your phone number (with country code, e.g. +79001234567): ").strip()
        await client.send_code_request(phone)

        code = input("Enter the verification code from Telegram: ").strip()

        try:
            await client.sign_in(phone, code)
        except Exception:
            # May need 2FA password.
            password = input("Enter your 2FA password (if enabled): ").strip()
            await client.sign_in(password=password)

    session_string = client.session.save()
    await client.disconnect()

    print("\n=== SUCCESS ===")
    print("Add this to your .env file:\n")
    print(f"TELEGRAM_SESSION_STRING={session_string}")
    print("\nKeep this value secret — it grants full access to your Telegram account!")


if __name__ == "__main__":
    asyncio.run(main())
