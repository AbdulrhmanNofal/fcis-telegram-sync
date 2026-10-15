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

SUBJECTS_CONFIG = [
    {
        "id": 1,
        "name": "Data Structures & Algorithms",
        "bot_button": "Data-Structures & Algorithms (DSA)\u200e",
        "lec_btn": "Academic Videos ( Lec )",
        "sec_btn": "Academic videos ( Sec )",
        "total_lecs": 10,
        "total_secs": 10
    },
    {
        "id": 2,
        "name": "Computer Organization & Architecture",
        "bot_button": "Computer Organization & Architect",
        "lec_btn": "Academic Videos ( Lec )",
        "sec_btn": "Academic videos ( Sec )",
        "total_lecs": 10,
        "total_secs": 10
    },
    {
        "id": 3,
        "name": "Operating Systems",
        "bot_button": "Operating Systems",
        "lec_btn": "Academic Videos ( Lec )",
        "sec_btn": "Academic videos ( Sec )",
        "total_lecs": 10,
        "total_secs": 10
    },
    {
        "id": 4,
        "name": "Web Development",
        "bot_button": "Web Development",
        "lec_btn": "Academic Videos ( Lec )",
        "sec_btn": "Academic videos ( Sec )",
        "total_lecs": 10,
        "total_secs": 10
    },
    {
        "id": 5,
        "name": "Linear Algebra",
        "bot_button": "Linear Algebra",
        "lec_btn": "Academic videos ( lec )",
        "sec_btn": "Academic videos ( Sec )",
        "total_lecs": 10,
        "total_secs": 10
    }
]

async def check_button_for_video(client, bot, btn_text):
    before_msgs = await client.get_messages(bot, limit=1)
    last_id = before_msgs[0].id if before_msgs else 0

    await client.send_message(bot, btn_text)
    await asyncio.sleep(2.2)

    new_msgs = await client.get_messages(bot, min_id=last_id, limit=5)
    for m in new_msgs:
        if m.media and hasattr(m.media, 'document') and m.media.document:
            doc = m.media.document
            if getattr(doc, 'mime_type', '').startswith('video/'):
                duration = 0
                for attr in doc.attributes:
                    if hasattr(attr, 'duration'):
                        duration = attr.duration
                        break
                return {
                    "available": True,
                    "messageId": m.id,
                    "caption": m.text or btn_text,
                    "fileSize": doc.size,
                    "durationSeconds": duration,
                    "mimeType": doc.mime_type
                }
    return {
        "available": False
    }

async def scan_subject_videos(client, bot, subj):
    print(f"\n==========================================", flush=True)
    print(f"Scanning: {subj['name']} (ID: {subj['id']})", flush=True)
    print(f"==========================================", flush=True)

    result = {
        "subjectId": subj["id"],
        "subjectName": subj["name"],
        "lectures": {},
        "sections": {}
    }

    # 1. Lectures
    print(f"--- Navigating to Lectures ---", flush=True)
    await client.send_message(bot, "/start")
    await asyncio.sleep(1)
    await client.send_message(bot, "2nd Year")
    await asyncio.sleep(1)
    await client.send_message(bot, "1st Term")
    await asyncio.sleep(1)
    await client.send_message(bot, subj["bot_button"])
    await asyncio.sleep(1)
    await client.send_message(bot, "Videos")
    await asyncio.sleep(1)
    await client.send_message(bot, subj["lec_btn"])
    await asyncio.sleep(1.2)

    # Get active buttons in lec menu
    msgs = await client.get_messages(bot, limit=1)
    buttons = []
    if msgs and msgs[0].reply_markup:
        buttons = [b.text for r in msgs[0].reply_markup.rows for b in r.buttons]
    lec_buttons = [b for b in buttons if b.startswith("Lec") or "Lec" in b]

    for btn in lec_buttons:
        print(f"  Checking {btn}...", end="", flush=True)
        info = await check_button_for_video(client, bot, btn)
        if info["available"]:
            print(f" FOUND! (Size: {info['fileSize'] / 1048576:.1f}MB, Duration: {info['durationSeconds']}s)", flush=True)
        else:
            print(f" (Not Available)", flush=True)
        result["lectures"][btn] = info

    # 2. Sections
    print(f"--- Navigating to Sections ---", flush=True)
    await client.send_message(bot, "/start")
    await asyncio.sleep(1)
    await client.send_message(bot, "2nd Year")
    await asyncio.sleep(1)
    await client.send_message(bot, "1st Term")
    await asyncio.sleep(1)
    await client.send_message(bot, subj["bot_button"])
    await asyncio.sleep(1)
    await client.send_message(bot, "Videos")
    await asyncio.sleep(1)
    await client.send_message(bot, subj["sec_btn"])
    await asyncio.sleep(1.2)

    msgs = await client.get_messages(bot, limit=1)
    buttons = []
    if msgs and msgs[0].reply_markup:
        buttons = [b.text for r in msgs[0].reply_markup.rows for b in r.buttons]
    sec_buttons = [b for b in buttons if b.startswith("Sec") or "Sec" in b]

    for btn in sec_buttons:
        print(f"  Checking {btn}...", end="", flush=True)
        info = await check_button_for_video(client, bot, btn)
        if info["available"]:
            print(f" FOUND! (Size: {info['fileSize'] / 1048576:.1f}MB, Duration: {info['durationSeconds']}s)", flush=True)
        else:
            print(f" (Not Available)", flush=True)
        result["sections"][btn] = info

    return result

async def main():
    client = TelegramClient(StringSession(SESSION_STRING), API_ID, API_HASH)
    await client.connect()
    bot = await client.get_entity(BOT_USERNAME)

    all_data = []
    for subj in SUBJECTS_CONFIG:
        subj_data = await scan_subject_videos(client, bot, subj)
        all_data.append(subj_data)
        # Save intermediate results
        with open("TOOLS/telegram_sync/indexed_videos.json", "w", encoding="utf-8") as f:
            json.dump(all_data, f, ensure_ascii=False, indent=2)

    print("\n\nAll subjects scanned! Output saved in TOOLS/telegram_sync/indexed_videos.json", flush=True)
    await client.disconnect()

if __name__ == '__main__':
    asyncio.run(main())
