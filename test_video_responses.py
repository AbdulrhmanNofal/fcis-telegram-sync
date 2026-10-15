import asyncio
import os
import sys
import json
from dotenv import load_dotenv
from telethon import TelegramClient
from telethon.sessions import StringSession

sys.stdout.reconfigure(encoding='utf-8')
load_dotenv('TOOLS/telegram_sync/.env')

API_ID = int(os.getenv('TG_API_ID', 0))
API_HASH = os.getenv('TG_API_HASH', '')
SESSION_STRING = os.getenv('TG_SESSION_STRING', '')
BOT_USERNAME = 'fcishubbot'

async def check_button(client, bot, btn_text):
    print(f"Testing button: {btn_text}...", flush=True)
    before_msgs = await client.get_messages(bot, limit=1)
    last_id = before_msgs[0].id if before_msgs else 0

    await client.send_message(bot, btn_text)
    await asyncio.sleep(2.5)

    new_msgs = await client.get_messages(bot, min_id=last_id, limit=5)
    for m in new_msgs:
        media_type = type(m.media).__name__ if m.media else "None"
        doc_mime = getattr(getattr(m.media, 'document', None), 'mime_type', None) if m.media else None
        print(f"  Response msg id={m.id}, text={repr(m.text[:60] if m.text else '')}, media={media_type}, mime={doc_mime}", flush=True)

async def main():
    client = TelegramClient(StringSession(SESSION_STRING), API_ID, API_HASH)
    await client.connect()
    bot = await client.get_entity(BOT_USERNAME)

    # Let's navigate to DSA -> Videos -> Academic Videos ( Lec )
    await client.send_message(bot, "/start")
    await asyncio.sleep(1)
    await client.send_message(bot, "2nd Year")
    await asyncio.sleep(1)
    await client.send_message(bot, "1st Term")
    await asyncio.sleep(1)
    await client.send_message(bot, "Data-Structures & Algorithms (DSA)\u200e")
    await asyncio.sleep(1)
    await client.send_message(bot, "Videos")
    await asyncio.sleep(1)
    await client.send_message(bot, "Academic Videos ( Lec )")
    await asyncio.sleep(1.5)

    # Test Lec 01 (which we know has a video)
    await check_button(client, bot, "Lec 01")

    # Test Lec 10 (let's see if Lec 10 has a video or if it's missing!)
    await check_button(client, bot, "Lec 10")

    await client.disconnect()

if __name__ == '__main__':
    asyncio.run(main())
