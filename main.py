import os
import sys
import json
import argparse
import asyncio
import logging
from dotenv import load_dotenv
from telethon import TelegramClient, events
from telethon.sessions import StringSession

from database import init_db, is_already_synced, record_synced
from parser import parse_overview_message, extract_subject_info
from fcis_api import FcisApiClient

logging.basicConfig(
    format="%(asctime)s - [%(levelname)s] - %(name)s: %(message)s",
    level=logging.INFO
)
logger = logging.getLogger("TelegramSync")

load_dotenv()

TG_API_ID = os.getenv("TG_API_ID")
TG_API_HASH = os.getenv("TG_API_HASH")
TG_PHONE = os.getenv("TG_PHONE")
TG_COMMUNITY = os.getenv("TG_COMMUNITY") or "FCISCommunity29"

FCIS_API_BASE = os.getenv("FCIS_API_BASE") or "http://fcishub.runasp.net/api"
FCIS_ADMIN_EMAIL = os.getenv("FCIS_ADMIN_EMAIL", "")
FCIS_ADMIN_PASSWORD = os.getenv("FCIS_ADMIN_PASSWORD", "")
ADMIN_PERSONAL_TARGET = os.getenv("ADMIN_PERSONAL_TARGET", "")

TEMP_DIR = os.path.join(os.path.dirname(__file__), "downloads_temp")
os.makedirs(TEMP_DIR, exist_ok=True)

async def send_admin_alert(client: TelegramClient, message_text: str):
    if not ADMIN_PERSONAL_TARGET:
        return
    try:
        target = ADMIN_PERSONAL_TARGET.strip()
        if target.lstrip("-").isdigit():
            target = int(target)
        await client.send_message(target, message_text)
    except Exception as e:
        logger.warning("Could not send alert to %s: %s", ADMIN_PERSONAL_TARGET, e)

async def scan_overview_items(client: TelegramClient, api_client: FcisApiClient, community_entity, limit: int = 150):
    discovered = []
    seen_msg_ids = set()
    candidate_messages = []

    # 1. Search for overview messages across group history (server-side search)
    for term in ["مجمعة", "Lectures"]:
        try:
            async for msg in client.iter_messages(community_entity, search=term, limit=50):
                if msg.id not in seen_msg_ids:
                    seen_msg_ids.add(msg.id)
                    candidate_messages.append(msg)
        except Exception as e:
            logger.warning("Search for '%s' failed: %s", term, e)

    # 2. Add pinned messages
    try:
        from telethon.tl import types
        async for msg in client.iter_messages(community_entity, filter=types.InputMessagesFilterPinned(), limit=30):
            if msg.id not in seen_msg_ids:
                seen_msg_ids.add(msg.id)
                candidate_messages.append(msg)
    except Exception as e:
        logger.debug("Pinned messages fetch: %s", e)

    # 3. Add recent messages
    try:
        async for msg in client.iter_messages(community_entity, limit=limit):
            if msg.id not in seen_msg_ids:
                seen_msg_ids.add(msg.id)
                candidate_messages.append(msg)
    except Exception as e:
        logger.warning("Recent messages fetch: %s", e)

    # Process candidate messages
    for msg in candidate_messages:
        text = msg.text or ""
        items = parse_overview_message(text, msg.entities)
        if not items and "مجمعة" not in text and "Lectures" not in text:
            continue
        if not items:
            continue

        subject_info = extract_subject_info(text)
        chat = await msg.get_chat()

        for item in items:
            topic_id = str(item["topic_id"])
            msg_id = item["message_id"]

            try:
                target_msg = await client.get_messages(chat, ids=msg_id)
                if not target_msg or not target_msg.media:
                    continue

                file_name = getattr(target_msg.file, 'name', None) or f"{item['title']}.pdf"
                file_size = getattr(target_msg.file, 'size', 0)
                subject_id = api_client.resolve_subject_id(topic_id, subject_info.get("name"))
                already_uploaded = api_client.is_material_already_on_platform(subject_id, item["title"], item["type"])

                discovered.append({
                    "messageId": msg_id,
                    "topicId": topic_id,
                    "title": item["title"],
                    "type": item["type"],
                    "subjectId": subject_id,
                    "subjectName": subject_info.get("name") or f"Topic {topic_id}",
                    "originalFileName": file_name,
                    "fileSize": file_size,
                    "alreadyUploaded": already_uploaded,
                    "selected": not already_uploaded
                })
            except Exception as e:
                logger.warning("Error fetching target message %s: %s", msg_id, e)

    return discovered

async def sync_approved_items(client: TelegramClient, api_client: FcisApiClient, community_entity, items_to_sync):
    uploaded = []
    failed = []
    skipped = []

    for item in items_to_sync:
        msg_id = item["messageId"]
        topic_id = str(item.get("topicId", "general"))
        title = item["title"]
        material_type = item["type"]
        subject_id = int(item["subjectId"])
        subject_name = item.get("subjectName", "")

        # Live Deduplication check: Do not re-download or re-upload if already on platform
        if api_client.is_material_already_on_platform(subject_id, title, material_type):
            logger.info("Skipping '%s' (%s) - already exists on platform.", title, material_type)
            record_synced(topic_id, msg_id, material_type, title, subject_id)
            skipped.append(title)
            continue

        try:
            target_msg = await client.get_messages(community_entity, ids=msg_id)
            if not target_msg or not target_msg.media:
                logger.warning("Message %s has no file attached.", msg_id)
                failed.append({"messageId": msg_id, "error": "No media found in message"})
                continue

            original_filename = getattr(target_msg.file, 'name', None) or f"{title}.pdf"
            ext = os.path.splitext(original_filename)[1].lower()
            if ext not in ['.pdf', '.docx', '.pptx', '.xlsx', '.png', '.jpg', '.jpeg', '.html', '.htm', '.zip']:
                logger.warning("Skipping '%s' (%s) - unsupported extension '%s'.", title, original_filename, ext)
                continue

            temp_file_path = os.path.join(TEMP_DIR, f"{msg_id}_{original_filename}")
            
            logger.info("Downloading file from Telegram (Msg: %s)...", msg_id)
            downloaded_path = await client.download_media(target_msg, file=temp_file_path)

            if not downloaded_path or not os.path.exists(downloaded_path):
                failed.append({"messageId": msg_id, "error": "Download failed"})
                continue

            logger.info("Uploading %s to FCIS Hub API (Subject: %s)...", title, subject_id)
            upload_result = api_client.upload_material(downloaded_path, title, material_type, subject_id)

            if upload_result:
                fcis_mat_id = upload_result.get("id")
                record_synced(topic_id, msg_id, material_type, title, subject_id, fcis_mat_id, original_filename)
                uploaded.append({"messageId": msg_id, "title": title, "fcisId": fcis_mat_id})

                alert_text = (
                    f"✅ **تم نشر ملف جديد بنجاح في FCIS Hub!**\n\n"
                    f"📌 **العنوان:** `{title}`\n"
                    f"📚 **المادة:** {subject_name or subject_id}\n"
                    f"🏷️ **النوع:** {material_type}\n"
                    f"📄 **الملف:** `{original_filename}`"
                )
                await send_admin_alert(client, alert_text)
            else:
                failed.append({"messageId": msg_id, "error": "API upload rejected"})

            if os.path.exists(downloaded_path):
                try: os.remove(downloaded_path)
                except Exception: pass

        except Exception as e:
            logger.error("Error processing item %s: %s", title, e, exc_info=True)
            failed.append({"messageId": msg_id, "error": str(e)})

    # Summary alert
    if uploaded or skipped or failed:
        summary_text = (
            f"📊 **تقرير المزامنة الذكية من تليجرام**\n\n"
            f"✅ **تم نشر (جديد):** {len(uploaded)} ملفات بنجاح في المنصة.\n"
            f"⏭️ **تم تخطي (مرفوع مسبقاً):** {len(skipped)} ملفات.\n"
            f"❌ **أخطاء:** {len(failed)}"
        )
        await send_admin_alert(client, summary_text)

    return {"uploaded": uploaded, "skipped": skipped, "failed": failed}

async def main():
    parser = argparse.ArgumentParser(description="FCIS Hub Telegram Sync Tool")
    parser.add_argument("--scan-only", action="store_true", help="Scan and return discovered items as JSON to stdout")
    parser.add_argument("--sync-items", type=str, help="JSON string or file path of approved items to upload")
    parser.add_argument("--watch", action="store_true", help="Keep running 24/7 in real-time listener mode")
    parser.add_argument("--limit", type=int, default=100, help="Number of recent messages to scan")
    args = parser.parse_args()

    if not TG_API_ID or not TG_API_HASH:
        logger.error("Please set TG_API_ID and TG_API_HASH in .env file.")
        print(json.dumps({"error": "Missing TG_API_ID or TG_API_HASH"}))
        sys.exit(1)

    init_db()

    if not FCIS_ADMIN_EMAIL or not FCIS_ADMIN_PASSWORD:
        logger.critical("FCIS_ADMIN_EMAIL and FCIS_ADMIN_PASSWORD must be set in environment.")
        print(json.dumps({"error": "Missing FCIS admin credentials"}))
        sys.exit(1)

    api_client = FcisApiClient(FCIS_API_BASE, FCIS_ADMIN_EMAIL, FCIS_ADMIN_PASSWORD)
    if not api_client.login():
        logger.critical("Authentication with FCIS Hub API failed. Aborting.")
        print(json.dumps({"error": "FCIS API authentication failed"}))
        sys.exit(1)

    TG_SESSION_STRING = os.getenv("TG_SESSION_STRING")
    if TG_SESSION_STRING:
        client = TelegramClient(StringSession(TG_SESSION_STRING), int(TG_API_ID), TG_API_HASH)
        await client.connect()
        if not await client.is_user_authorized():
            logger.error("Provided TG_SESSION_STRING is unauthorized or expired.")
            sys.exit(1)
        logger.info("Telegram Userbot authenticated via TG_SESSION_STRING!")
    else:
        client = TelegramClient("userbot_session", int(TG_API_ID), TG_API_HASH)
        await client.start(phone=TG_PHONE)
        logger.info("Telegram Userbot logged in successfully!")

    community_entity = await client.get_entity(TG_COMMUNITY)

    # MODE 1: Scan Only (Returns JSON to caller)
    if args.scan_only:
        items = await scan_overview_items(client, api_client, community_entity, limit=args.limit)
        # Output clean JSON token for backend parser
        print("___TELEGRAM_SCAN_RESULT___")
        print(json.dumps({"success": True, "items": items}, ensure_ascii=False))
        await client.disconnect()
        return

    # MODE 2: Sync approved items from JSON
    if args.sync_items:
        raw_input = args.sync_items
        if os.path.exists(raw_input):
            with open(raw_input, "r", encoding="utf-8") as f:
                items_to_sync = json.load(f)
        else:
            items_to_sync = json.loads(raw_input)

        result = await sync_approved_items(client, api_client, community_entity, items_to_sync)
        print("___TELEGRAM_SYNC_RESULT___")
        print(json.dumps({"success": True, "result": result}, ensure_ascii=False))
        await client.disconnect()
        return

    # MODE 3: Default On-Demand All
    logger.info("Scanning for new items to sync...")
    items = await scan_overview_items(client, api_client, community_entity, limit=args.limit)
    if items:
        logger.info("Found %d new items. Syncing...", len(items))
        await sync_approved_items(client, api_client, community_entity, items)
    else:
        logger.info("All items are up to date! Nothing to sync.")

    await client.disconnect()

if __name__ == "__main__":
    asyncio.run(main())
