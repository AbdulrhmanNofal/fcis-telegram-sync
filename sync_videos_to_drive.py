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
    # 1. Fetch fresh access token via OAuth2 token endpoint directly
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
    media = MediaFileUpload(file_path, mimetype="video/mp4", resumable=True, chunksize=10*1024*1024)
    request = drive_service.files().create(body=file_metadata, media_body=media, fields="id")
    
    response = None
    while response is None:
        status, response = request.next_chunk()
        if status:
            logger.info("Upload progress: %d%%", int(status.progress() * 100))
            
    file_id = response.get("id")
    logger.info("File uploaded successfully. Drive File ID: %s", file_id)
    
    # Set public read permissions so custom web player can stream it
    try:
        drive_service.permissions().create(
            fileId=file_id,
            body={"role": "reader", "type": "anyone"},
            fields="id"
        ).execute()
        logger.info("Public permissions granted for Drive File ID: %s", file_id)
    except Exception as e:
        logger.warning("Could not set public permission: %s", e)
        
    return file_id

async def main():
    if not TG_SESSION_STRING:
        logger.error("TG_SESSION_STRING environment variable is required!")
        return

    if not GDRIVE_REFRESH_TOKEN:
        logger.error("GDRIVE_REFRESH_TOKEN environment variable is required!")
        return

    # Load catalog
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

        lectures_dict = subject.get("lectures", {})
        if isinstance(lectures_dict, dict):
            for lec_key, lec in lectures_dict.items():
                if not isinstance(lec, dict):
                    logger.warning("Skipping malformed lecture entry '%s': expected object, got %s", lec_key, type(lec).__name__)
                    continue

                if lec.get("available") and lec.get("messageId") and not lec.get("driveFileId"):
                    msg_id = lec["messageId"]
                    logger.info("Downloading %s (Message ID: %s)...", lec_key, msg_id)
                    msg = await tg_client.get_messages("me", ids=msg_id)
                    if not msg or not msg.media:
                        msg = await tg_client.get_messages("@fcishubbot", ids=msg_id)
                    
                    if msg and msg.media:
                        fname = f"Sub{sub_id}_{lec_key.replace(' ', '_')}.mp4"
                        local_path = os.path.join(temp_dir, fname)
                        await tg_client.download_media(msg, file=local_path)
                        
                        drive_id = upload_to_drive(drive_service, local_path, fname, videos_folder_id)
                        lec["driveFileId"] = drive_id
                        lec["streamUrl"] = f"https://lh3.googleusercontent.com/d/{drive_id}"
                        updated_count += 1
                        
                        if os.path.exists(local_path):
                            os.remove(local_path)
                    else:
                        logger.warning("Message %s has no media!", msg_id)

        sections_dict = subject.get("sections", {})
        if isinstance(sections_dict, dict):
            for sec_key, sec in sections_dict.items():
                if not isinstance(sec, dict):
                    logger.warning("Skipping malformed section entry '%s': expected object, got %s", sec_key, type(sec).__name__)
                    continue

                if sec.get("available") and sec.get("messageId") and not sec.get("driveFileId"):
                    msg_id = sec["messageId"]
                    logger.info("Downloading %s (Message ID: %s)...", sec_key, msg_id)
                    msg = await tg_client.get_messages("me", ids=msg_id)
                    if not msg or not msg.media:
                        msg = await tg_client.get_messages("@fcishubbot", ids=msg_id)
                    
                    if msg and msg.media:
                        fname = f"Sub{sub_id}_{sec_key.replace(' ', '_')}.mp4"
                        local_path = os.path.join(temp_dir, fname)
                        await tg_client.download_media(msg, file=local_path)
                        
                        drive_id = upload_to_drive(drive_service, local_path, fname, videos_folder_id)
                        sec["driveFileId"] = drive_id
                        sec["streamUrl"] = f"https://lh3.googleusercontent.com/d/{drive_id}"
                        updated_count += 1
                        
                        if os.path.exists(local_path):
                            os.remove(local_path)

    # Save updated catalog
    with open(catalog_path, "w", encoding="utf-8") as f:
        json.dump(catalog, f, indent=2, ensure_ascii=False)

    logger.info("Finished sync! Updated %d videos in catalog.", updated_count)
    await tg_client.disconnect()

if __name__ == "__main__":
    asyncio.run(main())
