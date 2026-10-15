import asyncio
import os
import sys
from dotenv import load_dotenv
from telethon import TelegramClient
from telethon.sessions import StringSession

sys.stdout.reconfigure(encoding='utf-8')
load_dotenv()

API_ID = int(os.getenv("TG_API_ID", 0))
API_HASH = os.getenv("TG_API_HASH", "")
SESSION_STRING = os.getenv("TG_SESSION_STRING", "")

BOT_USERNAME = "fcishubbot"

async def explore_year():
    client = TelegramClient(StringSession(SESSION_STRING), API_ID, API_HASH)
    await client.connect()

    bot_entity = await client.get_entity(BOT_USERNAME)
    print("Sending '2nd Year' to @fcishubbot...")
    await client.send_message(bot_entity, "2nd Year")
    await asyncio.sleep(2.5)

    messages = await client.get_messages(bot_entity, limit=3)
    for msg in messages:
        print("\n" + "=" * 50)
        print(f"ID: {msg.id} | Date: {msg.date}")
        if msg.text:
            print(f"Text:\n{msg.text.strip()}")
        if msg.media:
            print(f"Media: {type(msg.media).__name__}")
        if msg.reply_markup:
            print(f"Reply Markup: {type(msg.reply_markup).__name__}")
            if hasattr(msg.reply_markup, 'rows'):
                for r_idx, row in enumerate(msg.reply_markup.rows):
                    row_buttons = []
                    for b in row.buttons:
                        b_text = getattr(b, 'text', str(b))
                        extra = ""
                        if hasattr(b, 'data') and b.data:
                            extra = f" [data: {b.data.decode('utf-8', errors='ignore')}]"
                        elif hasattr(b, 'url') and b.url:
                            extra = f" [url: {b.url}]"
                        row_buttons.append(f"({b_text}{extra})")
                    print(f"  Row {r_idx + 1}: {' | '.join(row_buttons)}")

    await client.disconnect()

if __name__ == "__main__":
    asyncio.run(explore_year())
