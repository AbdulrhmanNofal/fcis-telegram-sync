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

async def inspect():
    if not SESSION_STRING:
        print("Error: TG_SESSION_STRING is missing.")
        return

    client = TelegramClient(StringSession(SESSION_STRING), API_ID, API_HASH)
    await client.connect()

    if not await client.is_user_authorized():
        print("Error: Client is not authorized.")
        await client.disconnect()
        return

    me = await client.get_me()
    print(f"Logged in as: {me.first_name} (@{me.username})")

    try:
        bot_entity = await client.get_entity(BOT_USERNAME)
        print(f"Found bot: {bot_entity.first_name} (@{bot_entity.username}) [ID: {bot_entity.id}]")
    except Exception as e:
        print(f"Error finding bot @{BOT_USERNAME}: {e}")
        await client.disconnect()
        return

    print("\nFetching last 5 messages from the chat with the bot...")
    messages = await client.get_messages(bot_entity, limit=5)
    
    if not messages:
        print("No prior messages found. Sending /start to trigger menu...")
        await client.send_message(bot_entity, "/start")
        await asyncio.sleep(2)
        messages = await client.get_messages(bot_entity, limit=3)

    for msg in messages:
        print("\n" + "=" * 50)
        print(f"Message ID: {msg.id} | Date: {msg.date}")
        if msg.text:
            print(f"Text:\n{msg.text.strip()}")
        if msg.media:
            print(f"Media detected: {type(msg.media).__name__}")
        
        # Check Reply Markup
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
    asyncio.run(inspect())
