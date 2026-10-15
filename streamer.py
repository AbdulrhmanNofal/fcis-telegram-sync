import os
import re
import math
from dotenv import load_dotenv
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import StreamingResponse, Response
from telethon import TelegramClient
from telethon.sessions import StringSession
from telethon.tl.types import MessageMediaDocument

load_dotenv()

API_ID = int(os.getenv("TG_API_ID", 0))
API_HASH = os.getenv("TG_API_HASH", "")
SESSION_STRING = os.getenv("TG_SESSION_STRING", "")
BOT_USERNAME = "fcishubbot"

CHUNK_SIZE = 1024 * 1024  # 1 MB chunk per range request

app = FastAPI(title="FCIS Hub Video Streamer")

client: TelegramClient = None

@app.on_event("startup")
async def startup_event():
    global client
    client = TelegramClient(StringSession(SESSION_STRING), API_ID, API_HASH)
    await client.connect()
    print("Telegram Streamer Client Connected Successfully.")

@app.on_event("shutdown")
async def shutdown_event():
    global client
    if client:
        await client.disconnect()

@app.get("/health")
async def health():
    return {"status": "healthy", "service": "fcis-hub-streamer"}

@app.get("/stream/{message_id}")
async def stream_video(message_id: int, request: Request):
    global client
    if not client or not client.is_connected():
        raise HTTPException(status_code=503, detail="Streamer service not ready")

    try:
        bot_entity = await client.get_entity(BOT_USERNAME)
        msg = await client.get_messages(bot_entity, ids=message_id)
        if not msg or not msg.media or not hasattr(msg.media, 'document'):
            raise HTTPException(status_code=404, detail="Video not found")

        doc = msg.media.document
        file_size = doc.size
        mime_type = doc.mime_type or "video/mp4"

        range_header = request.headers.get("Range")
        if range_header:
            range_match = re.match(r"bytes=(\d+)-(\d*)", range_header)
            if range_match:
                start = int(range_match.group(1))
                end = int(range_match.group(2)) if range_match.group(2) else file_size - 1
            else:
                start = 0
                end = file_size - 1
        else:
            start = 0
            end = file_size - 1

        end = min(end, file_size - 1)
        length = end - start + 1

        async def file_generator(offset: int, size: int):
            bytes_left = size
            curr_offset = offset
            while bytes_left > 0:
                step = min(CHUNK_SIZE, bytes_left)
                async for chunk in client.iter_download(doc, offset=curr_offset, limit=step):
                    yield chunk
                    curr_offset += len(chunk)
                    bytes_left -= len(chunk)

        headers = {
            "Content-Range": f"bytes {start}-{end}/{file_size}",
            "Accept-Ranges": "bytes",
            "Content-Length": str(length),
            "Content-Type": mime_type,
            "Access-Control-Allow-Origin": "*",
            "Cache-Control": "public, max-age=3600"
        }

        return StreamingResponse(
            file_generator(start, length),
            status_code=206 if range_header else 200,
            headers=headers
        )

    except Exception as e:
        print(f"Streaming error for msg {message_id}: {e}")
        raise HTTPException(status_code=500, detail="Internal streaming error")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("streamer:app", host="0.0.0.0", port=int(os.getenv("PORT", 8080)), reload=False)
