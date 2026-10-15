import os
import sys
import json
import argparse
import asyncio
import logging
from dotenv import load_dotenv

# Ensure UTF-8 output across all environments on Windows / Linux
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')
if hasattr(sys.stderr, 'reconfigure'):
    sys.stderr.reconfigure(encoding='utf-8')

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

    # 3. Add overview messages directly from each configured topic (guaranteed discovery)
    topic_ids = [int(tid) for tid in api_client.config.get("topics", {}).keys() if str(tid).isdigit()]
    for tid in topic_ids:
        try:
            async for msg in client.iter_messages(community_entity, reply_to=tid, limit=12):
                if msg.id not in seen_msg_ids and msg.text and ("╭━━" in msg.text or "مجمعة" in msg.text or "Lectures" in msg.text):
                    seen_msg_ids.add(msg.id)
                    candidate_messages.append(msg)
        except Exception as e:
            logger.debug("Topic %s check: %s", tid, e)

    # 4. Add recent messages
    try:
        async for msg in client.iter_messages(community_entity, limit=limit):
            if msg.id not in seen_msg_ids:
                seen_msg_ids.add(msg.id)
                candidate_messages.append(msg)
    except Exception as e:
        logger.warning("Recent messages fetch: %s", e)

    # Collect parsed items from candidate overview messages
    parsed_items = []
    for msg in candidate_messages:
        text = msg.text or ""
        items = parse_overview_message(text, getattr(msg, 'entities', None))
        if not items:
            continue
        subject_info = extract_subject_info(text)
        for item in items:
            item["_subject_info"] = subject_info
            item["_chat"] = community_entity
            parsed_items.append(item)

    # BATCH FETCH target messages in chunks of 50 to avoid flood wait
    all_target_ids = list({it["message_id"] for it in parsed_items if it.get("message_id")})
    msg_cache = {}
    for i in range(0, len(all_target_ids), 50):
        chunk = all_target_ids[i:i+50]
        try:
            chunk_msgs = await client.get_messages(community_entity, ids=chunk)
            for m in chunk_msgs:
                if m:
                    msg_cache[m.id] = m
        except Exception as be:
            logger.warning("Batch fetch error for chunk %s: %s", chunk, be)

    # Process items with fetched messages
    for item in parsed_items:
        topic_id = str(item["topic_id"])
        msg_id = item["message_id"]
        target_msg = msg_cache.get(msg_id)
        if not target_msg or not target_msg.media:
            continue

        msgs_to_inspect = [target_msg]
        if target_msg.grouped_id:
            try:
                surrounding = await client.get_messages(community_entity, ids=list(range(max(1, target_msg.id - 15), target_msg.id + 16)))
                album = [m for m in surrounding if m and m.media and m.grouped_id == target_msg.grouped_id]
                if len(album) > 1:
                    album.sort(key=lambda m: m.id)
                    msgs_to_inspect = album
            except Exception as ge:
                logger.debug("Album fetch error: %s", ge)

        for single_msg in msgs_to_inspect:
            file_name = getattr(single_msg.file, 'name', None) or f"{item['title']}.pdf"
            file_size = getattr(single_msg.file, 'size', 0)
            subject_info = item.get("_subject_info", {})
            subject_id = api_client.resolve_subject_id(topic_id, subject_info.get("name"))

            item_title = item["title"]
            if len(msgs_to_inspect) > 1:
                import re
                m_num = re.search(r'(?:lec|sec|ch|محاضرة|سكشن)\s*0*(\d+)', file_name, re.IGNORECASE)
                if m_num:
                    prefix = "Sec" if item["type"] == "Section" else "Lec"
                    num_str = f"{int(m_num.group(1)):02d}"
                    tag = ""
                    tag_m = re.search(r'\(طالب \d+\)', item["title"])
                    if tag_m:
                        tag = f" {tag_m.group(0)}"
                    item_title = f"{prefix} {num_str}{tag}"

            already_uploaded = api_client.is_material_already_on_platform(subject_id, item_title, item["type"])

            discovered.append({
                "messageId": single_msg.id,
                "topicId": topic_id,
                "title": item_title,
                "type": item["type"],
                "subjectId": subject_id,
                "subjectName": subject_info.get("name") or f"Topic {topic_id}",
                "originalFileName": file_name,
                "fileSize": file_size,
                "alreadyUploaded": already_uploaded,
                "selected": not already_uploaded
            })

    # 5. Direct topic media scanner: Deep scan across all configured topics
    for tid in topic_ids:
        subject_id = api_client.resolve_subject_id(str(tid))
        sub_info = api_client.config.get("topics", {}).get(str(tid), {})
        subject_name = sub_info.get("englishName") or sub_info.get("subjectName") or f"Topic {tid}"

        try:
            # Increased limit from 20 to 250 to ensure full topic history is scanned
            async for msg in client.iter_messages(community_entity, reply_to=tid, limit=250):
                if not msg.media or not getattr(msg, 'file', None):
                    continue
                file_name = getattr(msg.file, 'name', '') or ''
                file_ext = os.path.splitext(file_name)[1].lower()
                if file_ext not in ['.pdf', '.docx', '.pptx', '.xlsx', '.png', '.jpg', '.jpeg', '.zip']:
                    continue

                # Check if this msg.id was already discovered
                if any(it["messageId"] == msg.id for it in discovered):
                    continue

                combined_name = f"{file_name} {msg.text or ''}"
                import re

                # STRICT EXCLUSION: Skip assignments, homework, and tasks as requested
                if re.search(r'(?:assignment|task|homework|واجب|تكليف|شيت|sheet)\b', combined_name, re.IGNORECASE):
                    continue

                # STRICT INCLUSION: Lectures & Sections only
                m_num = re.search(r'(?:lec(?:ture)?|ch(?:apter)?|محاضر[ةه]|شابتر|فصل)\s*[-_.]?\s*0*(\d+)', combined_name, re.IGNORECASE)
                s_num = re.search(r'(?:sec(?:tion)?|lab|lap|سكشن|سيكشن|لاب|معمل)\s*[-_.]?\s*0*(\d+)', combined_name, re.IGNORECASE)

                inferred_type = None
                inferred_title = None
                if m_num:
                    inferred_type = "Lecture"
                    inferred_title = f"Lec {int(m_num.group(1)):02d}"
                elif s_num:
                    inferred_type = "Section"
                    inferred_title = f"Sec {int(s_num.group(1)):02d}"

                if inferred_type and inferred_title:
                    stu_m = re.search(r'(?:student|طالب)\s*0*(\d+)', combined_name, re.IGNORECASE)
                    if stu_m:
                        inferred_title += f" (طالب {stu_m.group(1)})"

                    already_up = api_client.is_material_already_on_platform(subject_id, inferred_title, inferred_type)
                    discovered.append({
                        "messageId": msg.id,
                        "topicId": str(tid),
                        "title": inferred_title,
                        "type": inferred_type,
                        "subjectId": subject_id,
                        "subjectName": subject_name,
                        "originalFileName": file_name,
                        "fileSize": getattr(msg.file, 'size', 0),
                        "alreadyUploaded": already_up,
                        "selected": not already_up
                    })
        except Exception as te:
            logger.debug("Direct topic scan for %s: %s", tid, te)

    # Deduplicate by messageId
    unique_discovered = []
    seen_ids = set()
    for it in discovered:
        if it["messageId"] not in seen_ids:
            seen_ids.add(it["messageId"])
            unique_discovered.append(it)
    discovered = unique_discovered

    # Cache discovered catalog locally
    try:
        catalog_path = os.path.join(os.path.dirname(__file__), "discovered_catalog.json")
        with open(catalog_path, "w", encoding="utf-8") as f:
            json.dump(discovered, f, ensure_ascii=False, indent=2)

        # Also update full_catalog.json
        full_path = os.path.join(os.path.dirname(__file__), "full_catalog.json")
        with open(full_path, "w", encoding="utf-8") as f:
            json.dump(discovered, f, ensure_ascii=False, indent=2)

        # Also sync to backend's telegram_catalog.json if present
        backend_catalog = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "BACK", "FcisHub.Api", "telegram_catalog.json"))
        if os.path.exists(os.path.dirname(backend_catalog)):
            with open(backend_catalog, "w", encoding="utf-8") as f:
                json.dump(discovered, f, ensure_ascii=False, indent=2)
    except Exception as se:
        logger.debug("Catalog persistence: %s", se)

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
            if ext not in ['.pdf', '.docx', '.pptx', '.xlsx', '.png', '.jpg', '.jpeg', '.zip']:
                logger.warning("Skipping '%s' (%s) - unsupported extension '%s'.", title, original_filename, ext)
                failed.append({"messageId": msg_id, "error": f"Unsupported file type '{ext}'"})
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
        await client.connect()
        if not await client.is_user_authorized():
            logger.warning("Userbot session unauthorized, starting auth...")
            await client.start(phone=TG_PHONE)  # type: ignore
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
