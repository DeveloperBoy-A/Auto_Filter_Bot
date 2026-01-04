import logging
import os
import re
import base64
from struct import pack
from pyrogram.file_id import FileId
from typing import Dict, List
from collections import defaultdict
from pymongo.errors import DuplicateKeyError
from umongo import Instance, Document, fields
from motor.motor_asyncio import AsyncIOMotorClient
from marshmallow import ValidationError
from info import *
from utils import get_settings, save_group_settings
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

# ---------------- Global DB cache ----------------
_db_stats_cache = {"timestamp": None, "primary_size": 0.0}

# ---------------- DB Setup ----------------
# Primary DB
client = AsyncIOMotorClient(DATABASE_URI)
db = client[DATABASE_NAME]
instance = Instance.from_db(db)

# Secondary DB
client2 = AsyncIOMotorClient(DATABASE_URI2)
db2 = client2[DATABASE_NAME]
instance2 = Instance.from_db(db2)

# ---------------- Media Models ----------------
@instance.register
class Media(Document):
    file_id = fields.StrField(attribute="_id")
    file_ref = fields.StrField(allow_none=True)
    file_name = fields.StrField(required=True)
    file_size = fields.IntField(required=True)
    file_type = fields.StrField(allow_none=True)
    mime_type = fields.StrField(allow_none=True)
    caption = fields.StrField(allow_none=True)

    class Meta:
        indexes = ("$file_name",)
        collection_name = COLLECTION_NAME


@instance2.register
class Media2(Document):
    file_id = fields.StrField(attribute="_id")
    file_ref = fields.StrField(allow_none=True)
    file_name = fields.StrField(required=True)
    file_size = fields.IntField(required=True)
    file_type = fields.StrField(allow_none=True)
    mime_type = fields.StrField(allow_none=True)
    caption = fields.StrField(allow_none=True)

    class Meta:
        indexes = ("$file_name",)
        collection_name = COLLECTION_NAME

# ---------------- DB Size Checker ----------------
async def check_db_size(db_instance):
    try:
        now = datetime.utcnow()
        cache_stale_by_time = _db_stats_cache["timestamp"] is None or (
            now - _db_stats_cache["timestamp"] > timedelta(minutes=10)
        )
        refresh_if_size_threshold = _db_stats_cache["primary_size"] >= 10.0
        if not cache_stale_by_time and not refresh_if_size_threshold:
            return _db_stats_cache["primary_size"]
        stats = await db_instance.command("dbstats")
        db_logical_size = stats["dataSize"]
        db_index_size = stats["indexSize"]
        db_logical_size_mb = db_logical_size / (1024 * 1024)
        db_index_size_mb = db_index_size / (1024 * 1024)
        db_size_mb = db_logical_size_mb + db_index_size_mb
        _db_stats_cache["primary_size"] = db_size_mb
        _db_stats_cache["timestamp"] = now
        return db_size_mb
    except Exception as e:
        logger.error(f"Error Checking Database Size: {e}")
        return 0

# ---------------- File ID Helper ----------------
def encode_file_id(s: bytes) -> str:
    r = b""
    n = 0
    for i in s + bytes([22]) + bytes([4]):
        if i == 0:
            n += 1
        else:
            if n:
                r += b"\x00" + bytes([n])
                n = 0
            r += bytes([i])
    return base64.urlsafe_b64encode(r).decode().rstrip("=")

def encode_file_ref(file_ref: bytes) -> str:
    return base64.urlsafe_b64encode(file_ref).decode().rstrip("=")

def unpack_new_file_id(new_file_id):
    try:
        decoded = FileId.decode(new_file_id)
        file_id = encode_file_id(
            pack(
                "<iiqq",
                int(decoded.file_type),
                decoded.dc_id,
                decoded.media_id,
                decoded.access_hash,
            )
        )
        file_ref = encode_file_ref(decoded.file_reference)
        return file_id, file_ref
    except Exception as e:
        logger.error(f"Failed to unpack file_id: {e}")
        return None, None

# ---------------- Language Aliases ----------------
LANGUAGE_ALIASES = {
    "Hindi": ["hindi", "hin"],
    "English": ["english", "eng"],
    "Tamil": ["tamil", "tam"],
    "Telugu": ["telugu", "tel"],
    "Malayalam": ["malayalam", "mal"],
    "Kannada": ["kannada", "kan"],
    "Punjabi": ["punjabi", "pan"],
    "Bengali": ["bengali", "ben"]
}

# ---------------- Resolutions ----------------
RESOLUTIONS = ["2160p","4k","1080p","720p","480p","360p","240p","144p"]

# ---------------- Sources ----------------
SOURCES = {
    "HDRIP": ["hdrip","hd rip"],
    "WEB-DL": ["web-dl","webdl"],
    "WEBRIP": ["webrip","web rip"],
    "BLURAY": ["bluray","blu-ray","bdrip"]
}

# ---------------- Extract info from caption ----------------
def extract_languages_quality(caption: str):
    caption = caption.lower()
    found_languages = []
    seen_langs = set()
    resolution = None
    source = None

    # Detect languages
    for lang, aliases in LANGUAGE_ALIASES.items():
        for alias in aliases:
            if re.search(rf"\b{re.escape(alias)}\b", caption):
                if lang not in seen_langs:
                    found_languages.append(lang)
                    seen_langs.add(lang)
                break

    # Detect resolution
    for r in RESOLUTIONS:
        if r in caption:
            resolution = r.upper()
            break

    # Detect source
    for src, aliases in SOURCES.items():
        for alias in aliases:
            if alias in caption:
                source = src
                break
        if source:
            break

    return found_languages, resolution, source

# ---------------- Save File ----------------
async def save_file(media):
    """Save file in database, append missing info from caption and clean file_name."""

    file_id, file_ref = unpack_new_file_id(media.file_id)

    # Original file name
    original_name = str(media.file_name or "Unnamed File").strip()
    base_name, ext = os.path.splitext(original_name)

    caption_text = getattr(media.caption, "text", "") or ""

    # Extract info from caption (imported)
    languages, resolution_caption, source_caption = extract_languages_quality(caption_text)

    resolution_in_name = next((r.upper() for r in RESOLUTIONS if r.lower() in base_name.lower()), None)
    source_in_name = next((src for src, aliases in SOURCES.items() if any(a in base_name.lower() for a in aliases)), None)

    # Build final file name
    parts = [base_name]
    for lang in languages:
        if lang.lower() not in base_name.lower():
            parts.append(lang)
    if not resolution_in_name and resolution_caption:
        parts.append(resolution_caption)
    if not source_in_name and source_caption:
        parts.append(source_caption)
    file_name = " ".join(parts) + ext
    file_name = re.sub(r"[_\-\,#+$%^&*()!~`,;:\"'?/<>\[\]{}=|\\]", " ", file_name)
    file_name = re.sub(r"\s+", " ", file_name).strip()

    saveMedia = Media
    target_db = "Primary"

    if MULTIPLE_DB:
        try:
            exists = await Media.count_documents({"file_id": file_id}, limit=1)
            if exists:
                logger.info(f"[SKIP] '{file_name}' already in Primary DB.")
                return False, 0

            # ---------------- FIXED DB SIZE ----------------
            primary_db_size = await check_db_size(db)
            if primary_db_size >= 407:
                saveMedia = Media2
                target_db = "Secondary"
                logger.warning("Switching to Secondary DB due to size threshold.")

        except Exception as e:
            logger.error(
                "Error during MULTIPLE_DB check; defaulting to primary DB.",
                exc_info=e
            )

    try:
        caption_html = getattr(media.caption, "html", None) if media.caption and INDEX_CAPTION else None
        record = saveMedia(
            file_id=file_id,
            file_ref=file_ref,
            file_name=file_name,
            file_size=media.file_size,
            file_type=media.file_type,
            mime_type=media.mime_type,
            caption=caption_html,
        )
        logger.debug(f"DEBUG → Saving file_name: {record.file_name}")
    except ValidationError as e:
        logger.exception(f"[VALIDATION ERROR] '{file_name}' → {e}")
        return False, 2

    try:
        await record.commit()
    except DuplicateKeyError:
        logger.info(f"[SKIP] DuplicateKey: '{file_name}' already exists in {target_db} DB.")
        return False, 0
    except Exception as e:
        logger.exception(f"[ERROR] Failed commit of '{file_name}' to {target_db} DB.", exc_info=e)
        return False, 3

    logger.info(f"[SUCCESS] '{file_name}' saved to {target_db} DB.")
    return True, 1

# ---------------- Fetch / Clean Media Functions ----------------
async def dreamxbotz_fetch_media(limit: int) -> list:
    try:
        if MULTIPLE_DB:
            db_size = await check_db_size(db)
            if db_size > 407:
                cursor = Media2.find().sort("$natural", -1).limit(limit)
                files = await cursor.to_list(length=limit)
            else:
                cursor = Media.find().sort("$natural", -1).limit(limit)
                files = await cursor.to_list(length=limit)
        else:
            cursor = Media.find().sort("$natural", -1).limit(limit)
            files = await cursor.to_list(length=limit)

        cleaned_files = []
        for file in files:
            is_series = bool(re.search(r"(S\d{1,2}|Season\s*\d+)", file.file_name, re.IGNORECASE))
            file.file_name = await dreamxbotz_clean_title(file.file_name, is_series=is_series)
            cleaned_files.append(file)
        return cleaned_files
    except Exception as e:
        logger.error(f"Error in dreamxbotz_fetch_media: {e}")
        return []

async def dreamxbotz_clean_title(filename: str, is_series: bool = False) -> str:
    try:
        parts = filename.rsplit(".", 1)
        name_part = parts[0]
        ext = parts[1] if len(parts) > 1 else ""
        name_part = re.sub(r"[._\-]+", " ", name_part)
        name_part = re.sub(r"\s+", " ", name_part).strip()
        filename_cleaned = name_part

        if is_series:
            season_match = re.search(
                r"(.*?)(?:S(\d{1,2})|Season\s*(\d+)|Season(\d+))(?:\s*Combined)?",
                filename_cleaned,
                re.IGNORECASE,
            )
            if season_match:
                title = season_match.group(1).strip()
                season = season_match.group(2) or season_match.group(3) or season_match.group(4)
                title = re.sub(r"(?:@[^ \n\r\t.,:;!?()\[\]{}<>\\\/\"'=_%]+|[._\-\[\]@()]+)",
                               " ", title).strip().title()
                return f"{title} S{int(season):02}.{ext}" if ext else f"{title} S{int(season):02}"

        # Default cleaning
        title = re.sub(r"(?:@[^ \n\r\t.,:;!?()\[\]{}<>\\\/\"'=_%]+|[._\-\[\]@()]+)",
                       " ", filename_cleaned).strip().title()
        return f"{title}.{ext}" if ext else title
    except Exception as e:
        logger.error(f"Error in dreamxbotz_clean_title: {e}")
        return filename

# ---------------- More helper fetch functions ----------------
async def dreamxbotz_get_movies(limit: int = 20) -> List[str]:
    try:
        cursor = await dreamxbotz_fetch_media(limit * 2)
        results = set()
        pattern = r"(?:s\d{1,2}|season\s*\d+|season\d+)(?:\s*combined)?(?:e\d{1,2}|episode\s*\d+)?\b"
        for file in cursor:
            file_name = getattr(file, "file_name", "")
            parts = file_name.rsplit(".", 1)
            name_part = re.sub(r"[._\-]+", " ", parts[0])
            name_part = re.sub(r"\s+", " ", name_part).strip()
            ext = parts[1] if len(parts) > 1 else ""
            if not re.search(pattern, name_part, re.IGNORECASE):
                title = await dreamxbotz_clean_title(name_part)
                if ext:
                    title = f"{title}.{ext}"
                results.add(title)
            if len(results) >= limit:
                break
        return sorted(list(results))[:limit]
    except Exception as e:
        logger.error(f"Error in dreamxbotz_get_movies: {e}")
        return []

async def dreamxbotz_get_series(limit: int = 30) -> Dict[str, List[int]]:
    try:
        cursor = await dreamxbotz_fetch_media(limit * 5)
        grouped = defaultdict(list)
        pattern = r"(.*?)(?:S(\d{1,2})|Season\s*(\d+)|Season(\d+))(?:\s*Combined)?(?:E(\d{1,2})|Episode\s*(\d+))?\b"
        for file in cursor:
            file_name = getattr(file, "file_name", "")
            parts = file_name.rsplit(".", 1)
            name_part = re.sub(r"[._\-]+", " ", parts[0])
            ext = parts[1] if len(parts) > 1 else ""
            match = re.search(pattern, name_part, re.IGNORECASE)
            if match:
                title_raw = match.group(1)
                title = await dreamxbotz_clean_title(title_raw, is_series=True)
                if ext:
                    title = f"{title}.{ext}"
                season = int(match.group(2) or match.group(3) or match.group(4))
                grouped[title].append(season)
        return {title: sorted(set(seasons))[:10] for title, seasons in grouped.items() if seasons}
    except Exception as e:
        logger.error(f"Error in dreamxbotz_get_series: {e}")
        return []