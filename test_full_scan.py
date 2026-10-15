import os, asyncio, json
from dotenv import load_dotenv

load_dotenv('.env')
from telethon import TelegramClient
from telethon.sessions import StringSession
from fcis_api import FcisApiClient
from main import scan_overview_items

async def main():
    api = FcisApiClient(os.getenv('FCIS_API_BASE'), os.getenv('FCIS_ADMIN_EMAIL'), os.getenv('FCIS_ADMIN_PASSWORD'))
    api.login()
    client = TelegramClient(StringSession(os.getenv('TG_SESSION_STRING')), int(os.getenv('TG_API_ID')), os.getenv('TG_API_HASH'))
    await client.connect()
    ent = await client.get_entity(os.getenv('TG_COMMUNITY'))

    items = await scan_overview_items(client, api, ent, limit=150)
    with open('discovered_catalog.json', 'w', encoding='utf-8') as f:
        json.dump(items, f, ensure_ascii=False, indent=2)

    print("TOTAL_DISCOVERED:", len(items))
    for it in items:
        print(it['messageId'], it['title'], it['type'], it['subjectId'], it['originalFileName'])

    await client.disconnect()

if __name__ == '__main__':
    asyncio.run(main())
