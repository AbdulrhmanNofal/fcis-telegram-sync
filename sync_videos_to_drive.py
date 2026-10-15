import os
import json
import asyncio
import logging
import requests
from telethon import TelegramClient
from telethon.sessions import StringSession
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("DriveVideoSync")

# Telegram Credentials
TG_API_ID = int(os.getenv("TG_API_ID", "38638396").strip())
TG_API_HASH = os.getenv("TG_API_HASH", "6d24a31f0bcde46003175e912b000f09").strip()
TG_SESSION_STRING = os.getenv("TG_SESSION_STRING", "").strip()

# Google Drive Credentials
GDRIVE_CLIENT_ID = os.getenv("GDRIVE_CLIENT_ID", "").strip()
GDRIVE_CLIENT_SECRET = os.getenv("GDRIVE_CLIENT_SECRET", "").strip()
GDRIVE_REFRESH_TOKEN = os.getenv("GDRIVE_REFRESH_TOKEN", "").strip()
GDRIVE_FOLDER_ID = os.getenv("GDRIVE_FOLDER_ID", "1y3myf00rLLXJgq9jKCehlZWh3zkQSLaw").strip()

def get_drive_service():
    token_resp = requests.post(
        "https://oauth2.googleapis.com/token",
        data={
            "client_id": GDRIVE_CLIENT_ID,
            "client_secret": GDRIVE_CLIENT_SECRET,
            "refresh_token": GDRIVE_REFRESH_TOKEN,
            "grant_type": "refresh_token"
        },
        timeout=15
    )
    if token_resp.status_code != 200:
        logger.error("Failed to refresh token: %s", token_resp.text)
        raise RuntimeError(f"Google OAuth refresh failed: {token_resp.text}")

    access_token = token_resp.json()["access_token"]
    logger.info("Acquired fresh Google Drive access token successfully!")

    creds = Credentials(
        token=access_token,
        refresh_token=GDRIVE_REFRESH_TOKEN,
        token_uri="https://oauth2.googleapis.com/token",
        client_id=GDRIVE_CLIENT_ID,
        client_secret=GDRIVE_CLIENT_SECRET
    )
    return build("drive", "v3", credentials=creds)

def ensure_videos_folder(drive_service, parent_folder_id: str) -> str:
    """Finds or creates a 'Videos' subfolder inside target folder"""
    query = f"name = 'Videos' and '{parent_folder_id}' in parents and mimeType = 'application/vnd.google-apps.folder' and trashed = false"
    res = drive_service.files().list(q=query, fields="files(id, name)").execute()
    files = res.get("files", [])
    if files:
        return files[0]["id"]
    
    meta = {
        "name": "Videos",
        "mimeType": "application/vnd.google-apps.folder",
        "parents": [parent_folder_id]
    }
    created = drive_service.files().create(body=meta, fields="id").execute()
    logger.info("Created 'Videos' folder in Google Drive: %s", created["id"])
    return created["id"]

def upload_to_drive(drive_service, file_path: str, filename: str, folder_id: str) -> str:
    """Uploads video file using resumable chunked upload and makes it streamable"""
    logger.info("Uploading %s to Google Drive...", filename)
    file_metadata = {
        "name": filename,
        "parents": [folder_id]
    }
    media = MediaFileUpload(file_path, mimetype="video/mp4", resumable=True, chunksize=15*1024*1024)
    request = drive_service.files().create(body=file_metadata, media_body=media, fields="id")
    
    response = None
    last_reported = 0
    while response is None:
        status, response = request.next_chunk()
        if status:
            pct = int(status.progress() * 100)
            if pct - last_reported >= 15 or pct == 100:
                logger.info("Google Drive upload: %d%%", pct)
                last_reported = pct
            
    file_id = response.get("id")
    logger.info("Uploaded successfully to Drive! File ID: %s", file_id)
    
    try:
        drive_service.permissions().create(
            fileId=file_id,
            body={"role": "reader", "type": "anyone"},
            fields="id"
        ).execute()
        logger.info("Public streaming permissions granted for File ID: %s", file_id)
    except Exception as e:
        logger.warning("Could not set public permission: %s", e)
        
    return file_id

def iter_records(value, record_type):
    """Safely yields (key_name, dict_record) from list or dict structure"""
    if isinstance(value, list):
        for idx, item in enumerate(value):
            if isinstance(item, dict):
                yield (f"item_{idx}", item)
            else:
                logger.warning("Ignoring invalid %s at index %s", record_type, idx)
    elif isinstance(value, dict):
        for k, v in value.items():
            if isinstance(v, dict):
                yield (k, v)
            else:
                logger.warning("Ignoring invalid %s for key %s", record_type, k)

def download_progress_callback(current, total):
    if total:
        pct = int(current * 100 / total)
        if pct % 20 == 0:
            logger.info("Telegram download progress: %d%% (%d / %d MB)", pct, current // (1024*1024), total // (1024*1024))

async def main():
    if not TG_SESSION_STRING:
        logger.error("TG_SESSION_STRING environment variable is required!")
        return

    if not GDRIVE_REFRESH_TOKEN:
        logger.error("GDRIVE_REFRESH_TOKEN environment variable is required!")
        return

    catalog_path = os.path.join(os.path.dirname(__file__), "indexed_videos.json")
    if not os.path.exists(catalog_path):
        logger.error("indexed_videos.json not found!")
        return
        
    with open(catalog_path, "r", encoding="utf-8") as f:
        catalog = json.load(f)

    drive_service = get_drive_service()
    videos_folder_id = ensure_videos_folder(drive_service, GDRIVE_FOLDER_ID)

    tg_client = TelegramClient(StringSession(TG_SESSION_STRING), TG_API_ID, TG_API_HASH)
    await tg_client.connect()
    
    if not await tg_client.is_user_authorized():
        logger.error("Telegram session is not authorized!")
        await tg_client.disconnect()
        return

    logger.info("Connected to Telegram & Google Drive successfully!")

    temp_dir = os.path.join(os.path.dirname(__file__), "downloads_temp")
    os.makedirs(temp_dir, exist_ok=True)

    updated_count = 0

    for subject in catalog:
        sub_id = subject["subjectId"]
        sub_name = subject["subjectName"]
        logger.info("Processing subject: %s (ID: %s)", sub_name, sub_id)

        # Process lectures safely
        for lec_key, lec in iter_records(subject.get("lectures"), "lecture"):
            if lec.get("available") and lec.get("messageId") and not lec.get("driveFileId"):
                msg_id = lec["messageId"]
                logger.info("Downloading %s (Message ID: %s)...", lec_key, msg_id)
                msg = await tg_client.get_messages("me", ids=msg_id)
                if not msg or not msg.media:
                    msg = await tg_client.get_messages("@fcishubbot", ids=msg_id)
                
                if msg and msg.media:
                    fname = f"Sub{sub_id}_{lec_key.replace(' ', '_')}.mp4"
                    local_path = os.path.join(temp_dir, fname)
                    
                    last_pct = [0]
                    def prog(cur, tot):
                        if tot:
                            p = int(cur * 100 / tot)
                            if p - last_pct[0] >= 20 or p == 100:
                                logger.info("Telegram download: %d%% (%d / %d MB)", p, cur // (1024*1024), tot // (1024*1024))
                                last_pct[0] = p

                    await tg_client.download_media(msg, file=local_path, progress_callback=prog)
                    
                    drive_id = upload_to_drive(drive_service, local_path, fname, videos_folder_id)
                    lec["driveFileId"] = drive_id
                    lec["streamUrl"] = f"https://lh3.googleusercontent.com/d/{drive_id}"
                    updated_count += 1
                    
                    # Save catalog immediately after each video so progress is never lost
                    with open(catalog_path, "w", encoding="utf-8") as f:
                        json.dump(catalog, f, indent=2, ensure_ascii=False)
                    
                    if os.path.exists(local_path):
                        os.remove(local_path)
                else:
                    logger.warning("Message %s has no media!", msg_id)

        # Process sections safely
        for sec_key, sec in iter_records(subject.get("sections"), "section"):
            if sec.get("available") and sec.get("messageId") and not sec.get("driveFileId"):
                msg_id = sec["messageId"]
                logger.info("Downloading %s (Message ID: %s)...", sec_key, msg_id)
                msg = await tg_client.get_messages("me", ids=msg_id)
                if not msg or not msg.media:
                    msg = await tg_client.get_messages("@fcishubbot", ids=msg_id)
                
                if msg and msg.media:
                    fname = f"Sub{sub_id}_{sec_key.replace(' ', '_')}.mp4"
                    local_path = os.path.join(temp_dir, fname)
                    
                    last_pct = [0]
                    def prog_sec(cur, tot):
                        if tot:
                            p = int(cur * 100 / tot)
                            if p - last_pct[0] >= 20 or p == 100:
                                logger.info("Telegram download: %d%% (%d / %d MB)", p, cur // (1024*1024), tot // (1024*1024))
                                last_pct[0] = p

                    await tg_client.download_media(msg, file=local_path, progress_callback=prog_sec)
                    
                    drive_id = upload_to_drive(drive_service, local_path, fname, videos_folder_id)
                    sec["driveFileId"] = drive_id
                    sec["streamUrl"] = f"https://lh3.googleusercontent.com/d/{drive_id}"
                    updated_count += 1
                    
                    with open(catalog_path, "w", encoding="utf-8") as f:
                        json.dump(catalog, f, indent=2, ensure_ascii=False)
                        
                    if os.path.exists(local_path):
                        os.remove(local_path)

    logger.info("Finished sync! Updated %d videos in catalog.", updated_count)
    await tg_client.disconnect()

if __name__ == "__main__":
    asyncio.run(main())
