#Thanks [@Tokyo_Updates] for helping in this journey
from pyrogram import Client
from typing import Any, Optional
from pyrogram.types import Message
from pyrogram.file_id import FileId
from pyrogram.raw.types.messages import Messages
from dreamxbotz.server.exceptions import FIleNotFound


async def parse_file_id(message: "Message") -> Optional[FileId]:
    media = get_media_from_message(message)
    if media:
        return FileId.decode(media.file_id)

async def parse_file_unique_id(message: "Messages") -> Optional[str]:
    media = get_media_from_message(message)
    if media:
        return media.file_unique_id

async def get_file_ids(client: Client, chat_id: int, id: int, prefer_db_name: bool = False) -> Optional[FileId]:
    message = await client.get_messages(chat_id, id)
    if message.empty:
        raise FIleNotFound
    media = get_media_from_message(message)
    file_unique_id = await parse_file_unique_id(message)
    file_id = await parse_file_id(message)
    setattr(file_id, "file_size", getattr(media, "file_size", 0))
    setattr(file_id, "mime_type", getattr(media, "mime_type", ""))
    # Telegram keeps the original uploaded media filename.  The bot's database
    # may contain a cleaned/custom filename; when requested, prefer that name
    # without changing the underlying Telegram media or adding DB fields.
    telegram_name = getattr(media, "file_name", "")
    setattr(file_id, "file_name", telegram_name)
    if prefer_db_name:
        try:
            from database.ia_filterdb import get_file_details, unpack_new_file_id
            db_file_id, _ = unpack_new_file_id(media.file_id)
            if db_file_id:
                details = await get_file_details(db_file_id)
                if details and getattr(details[0], "file_name", None):
                    setattr(file_id, "file_name", details[0].file_name)
        except Exception:
            # Never break streaming just because the optional DB-name lookup failed.
            pass
    setattr(file_id, "unique_id", file_unique_id)
    return file_id

def get_media_from_message(message: "Message") -> Any:
    media_types = (
        "audio",
        "document",
        "photo",
        "sticker",
        "animation",
        "video",
        "voice",
        "video_note",
    )
    for attr in media_types:
        media = getattr(message, attr, None)
        if media:
            return media


def get_hash(media_msg: Message) -> str:
    media = get_media_from_message(media_msg)
    return getattr(media, "file_unique_id", "")[:6]

def get_name(media_msg: Message) -> str:
    media = get_media_from_message(media_msg)
    return getattr(media, 'file_name', "")

def get_media_file_size(m):
    media = get_media_from_message(m)
    return getattr(media, "file_size", 0)