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

async def get_last_buttons(client, bot):
    await asyncio.sleep(1.2)
    msgs = await client.get_messages(bot, limit=1)
    if not msgs or not msgs[0].reply_markup or not hasattr(msgs[0].reply_markup, 'rows'):
        return []
    buttons = []
    for r in msgs[0].reply_markup.rows:
        for b in r.buttons:
            if hasattr(b, 'text'):
                buttons.append(b.text)
    return buttons

async def main():
    client = TelegramClient(StringSession(SESSION_STRING), API_ID, API_HASH)
    await client.connect()
    bot = await client.get_entity(BOT_USERNAME)

    print("Resetting to /start...", flush=True)
    await client.send_message(bot, "/start")
    await asyncio.sleep(1.5)

    print("Clicking '2nd Year'...", flush=True)
    await client.send_message(bot, "2nd Year")
    terms = await get_last_buttons(client, bot)
    print("Terms buttons:", terms, flush=True)

    print("Clicking '1st Term'...", flush=True)
    await client.send_message(bot, "1st Term")
    subjects = await get_last_buttons(client, bot)
    print("Subjects buttons:", subjects, flush=True)

    results = {}

    for subj_btn in subjects:
        if "رجوع" in subj_btn:
            continue
        print(f"\n--- Exploring Subject: {repr(subj_btn)} ---", flush=True)
        results[subj_btn] = {"lectures": [], "sections": []}

        # 1. Check lectures
        await client.send_message(bot, "/start")
        await asyncio.sleep(1)
        await client.send_message(bot, "2nd Year")
        await asyncio.sleep(1)
        await client.send_message(bot, "1st Term")
        await asyncio.sleep(1)
        await client.send_message(bot, subj_btn)
        await asyncio.sleep(1)
        await client.send_message(bot, "Videos")
        await asyncio.sleep(1)
        v_types = await get_last_buttons(client, bot)
        print("  Video types:", v_types, flush=True)

        lec_btn = next((b for b in v_types if "Lec" in b), None)
        if lec_btn:
            await client.send_message(bot, lec_btn)
            lec_buttons = await get_last_buttons(client, bot)
            filtered_lecs = [b for b in lec_buttons if "رجوع" not in b and "Send" not in b]
            results[subj_btn]["lectures"] = filtered_lecs
            print("    Lec buttons:", filtered_lecs, flush=True)

        # 2. Check sections
        await client.send_message(bot, "/start")
        await asyncio.sleep(1)
        await client.send_message(bot, "2nd Year")
        await asyncio.sleep(1)
        await client.send_message(bot, "1st Term")
        await asyncio.sleep(1)
        await client.send_message(bot, subj_btn)
        await asyncio.sleep(1)
        await client.send_message(bot, "Videos")
        await asyncio.sleep(1)
        v_types = await get_last_buttons(client, bot)

        sec_btn = next((b for b in v_types if "Sec" in b), None)
        if sec_btn:
            await client.send_message(bot, sec_btn)
            sec_buttons = await get_last_buttons(client, bot)
            filtered_secs = [b for b in sec_buttons if "رجوع" not in b and "Send" not in b]
            results[subj_btn]["sections"] = filtered_secs
            print("    Sec buttons:", filtered_secs, flush=True)

    with open("TOOLS/telegram_sync/bot_videos_tree.json", "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)

    print("\nSaved bot_videos_tree.json successfully!", flush=True)
    await client.disconnect()

if __name__ == '__main__':
    asyncio.run(main())
