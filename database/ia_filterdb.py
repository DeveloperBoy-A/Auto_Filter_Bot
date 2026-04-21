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
from utils import get_settings, save_group_settings, remove_prefix_garbage
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
    cover = fields.StrField(allow_none=True)

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
    cover = fields.StrField(allow_none=True)

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

#__________________________________
def normalize_season_episode(text):
    import re

    text = text.lower()

    text = re.sub(r'(\d+)[xX](\d+)',
                  lambda m: f"S{int(m.group(1)):02d} E{int(m.group(2)):02d}",
                  text)

    text = re.sub(r'season[\s\-]*(\d+)',
                  lambda m: f"S{int(m.group(1)):02d}", text)
    text = re.sub(r'\bs[\s\-]*(\d+)',
                  lambda m: f"S{int(m.group(1)):02d}", text)

    text = re.sub(r'episode[\s\-]*(\d+)',
                  lambda m: f"E{int(m.group(1)):02d}", text)
    text = re.sub(r'\bep[\s\-]*(\d+)',
                  lambda m: f"E{int(m.group(1)):02d}", text)
    text = re.sub(r'\be[\s\-]*(\d+)',
                  lambda m: f"E{int(m.group(1)):02d}", text)

    text = re.sub(r's(\d+)e(\d+)',
                  lambda m: f"S{int(m.group(1)):02d} E{int(m.group(2)):02d}",
                  text)

    text = re.sub(r'\s+', ' ', text).strip()

    text = text.title()

    return text

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
    seen = set()
    resolution = None
    source = None

    # Languages
    for lang, aliases in LANGUAGE_ALIASES.items():
        for a in aliases:
            if re.search(rf"\b{re.escape(a)}\b", caption):
                if lang not in seen:
                    found_languages.append(lang)
                    seen.add(lang)
                break

    # Resolution
    for r in RESOLUTIONS:
        if r in caption:
            resolution = r.upper()
            break

    # Source
    for src, aliases in SOURCES.items():
        for a in aliases:
            if a in caption:
                source = src
                break
        if source:
            break

    return found_languages, resolution, source


# ---------------- Save File ----------------
async def save_file(media):
    """
    Save file in database with FORCED language + quality from caption
    """

    try:
        file_id, file_ref = unpack_new_file_id(media.file_id)

        original_name = str(media.file_name or "Unnamed File").strip()
        base_name, ext = os.path.splitext(original_name)

        # ---------------- CLEAN BASE NAME ----------------
        base_name = re.sub(r"[._\-]+", " ", base_name)
        base_name = re.sub(r"\s+", " ", base_name).strip()

        # ✅ season/episode format fix
        base_name = normalize_season_episode(base_name)

        # prefix se pehle ka garbage remove
        base_name = remove_prefix_garbage(base_name)
        
        # ---------------- CAPTION READ (IMPORTANT) ----------------
        caption_text = media.caption or ""

        # ---------------- EXTRACT INFO ----------------
        languages, resolution_caption, source_caption = extract_languages_quality(
            caption_text
        )

        # ---------------- BUILD FINAL NAME (FORCED) ----------------
        parts = [base_name]

        # 🔹 language duplicate avoid (minimal & safe)
        base_lower = base_name.lower()
        added_langs = set()

        for lang in languages:
            l = lang.lower()
            if l in base_lower or l in added_langs:
                continue
            parts.append(lang)
            added_langs.add(l)

        # 🔹 Optional: resolution duplicate avoid
        added_res = set()
        if resolution_caption:
            r_upper = resolution_caption.upper()
            if r_upper not in added_res:
                parts.append(r_upper)
                added_res.add(r_upper)

        # 🔹 Optional: source duplicate avoid
        added_src = set()
        if source_caption:
            if source_caption not in added_src:
                parts.append(source_caption)
                added_src.add(source_caption)

        file_name = " ".join(parts) + ext

        # ---------------- FINAL CLEAN ----------------
        file_name = re.sub(r"[^\w\s().]", " ", file_name)
        file_name = re.sub(r"\s+", " ", file_name).strip()

        # ---------------- DB SELECTION ----------------
        saveMedia = Media
        target_db = "Primary"

        if MULTIPLE_DB:
            try:
                exists = await Media.count_documents({"file_id": file_id}, limit=1)
                if exists:
                    logger.info(f"[SKIP] '{file_name}' already exists.")
                    return False, 0

                db_size = await check_db_size(db)
                if db_size >= 407:
                    saveMedia = Media2
                    target_db = "Secondary"
                    logger.warning("Switching to Secondary DB")

            except Exception as e:
                logger.error("DB check failed, using Primary DB", exc_info=e)

        # ---------------- SAVE ----------------
        caption_html = (
            getattr(media.caption, "html", None)
            if media.caption and INDEX_CAPTION
            else None
        )

        record = saveMedia(
            file_id=file_id,
            file_ref=file_ref,
            file_name=file_name,
            file_size=media.file_size,
            file_type=media.file_type,
            mime_type=media.mime_type,
            caption=caption_html,
        )

        await record.commit()

        logger.info(f"[SUCCESS] Saved → {file_name} ({target_db})")
        return True, 1

    except DuplicateKeyError:
        logger.info(f"[SKIP] Duplicate → {file_name}")
        return False, 0

    except Exception as e:
        logger.exception(f"[ERROR] Save failed → {file_name}", exc_info=e)
        return False, 3
#___________________________________

def normalize_for_search(text):
    text = text.lower()

    # 1x02 → s01 e02
    text = re.sub(r'(\d+)[xX](\d+)',
                  lambda m: f"s{int(m.group(1)):02d} e{int(m.group(2)):02d}",
                  text)

    # season → s01
    text = re.sub(r'season[\s\-]*(\d+)',
                  lambda m: f"s{int(m.group(1)):02d}", text)

    # s1 → s01
    text = re.sub(r'\bs[\s\-]*(\d+)',
                  lambda m: f"s{int(m.group(1)):02d}", text)

    # episode → e01
    text = re.sub(r'episode[\s\-]*(\d+)',
                  lambda m: f"e{int(m.group(1)):02d}", text)

    # ep → e01
    text = re.sub(r'\bep[\s\-]*(\d+)',
                  lambda m: f"e{int(m.group(1)):02d}", text)

    # e1 → e01
    text = re.sub(r'\be[\s\-]*(\d+)',
                  lambda m: f"e{int(m.group(1)):02d}", text)

    # s1e2 → s01 e02
    text = re.sub(r's(\d+)e(\d+)',
                  lambda m: f"s{int(m.group(1)):02d} e{int(m.group(2)):02d}",
                  text)

    text = re.sub(r"\s+", " ", text).strip()
    return text


def expand_query(query):
    query = query.lower()
    patterns = []
    
    # Title nikalne ke liye season/episode part ko hatayein
    # Jaise "money heist s01" se "money heist" alag karein
    title = re.sub(r'(s\d+|e\d+|season\s*\d+|episode\s*\d+|ep\s*\d+)', '', query).strip()
    
    s_match = re.search(r"s(\d{1,2})|season\s*(\d{1,2})", query)
    e_match = re.search(r"e(\d{1,2})|episode\s*(\d{1,2})|ep\s*(\d{1,2})", query)

    s_num = None
    if s_match:
        s_num = int(s_match.group(1) or s_match.group(2))
        
    e_num = None
    if e_match:
        e_num = int(e_match.group(1) or e_match.group(2) or e_match.group(3))

    # 1. Agar sirf Title ho (Koi S ya E na ho)
    if not s_num and not e_num:
        return [query]

    # 2. Agar Season aur Episode dono hon
    if s_num and e_num:
        variations = [
            f"s{s_num}e{e_num}", f"s{s_num:02d}e{e_num:02d}",
            f"s{s_num} e{e_num}", f"s{s_num:02d} e{e_num:02d}",
            f"season {s_num} episode {e_num}",
            f"{s_num}x{e_num:02d}"
        ]
        for v in variations:
            patterns.append(f"{title} {v}".strip())

    # 3. Agar sirf Season ho
    elif s_num:
        variations = [f"s{s_num}", f"s{s_num:02d}", f"season {s_num}", f"season{s_num}"]
        for v in variations:
            patterns.append(f"{title} {v}".strip())

    # 4. Agar sirf Episode ho
    elif e_num:
        variations = [f"e{e_num}", f"e{e_num:02d}", f"ep{e_num}", f"episode {e_num}"]
        for v in variations:
            patterns.append(f"{title} {v}".strip())

    # Original query ko bhi add karein
    patterns.append(query)
    
    return list(set(patterns))



async def get_search_results(chat_id, query, file_type=None, max_results=10, offset=0, filter=False):
    # Settings handle karna
    if chat_id:
        settings = await get_settings(int(chat_id))
        max_results = 10 if settings.get("max_btn") else int(MAX_B_TN)

    # Agar query list nahi hai toh normalize aur expand karein
    if not isinstance(query, list):
        query = normalize_for_search(query)
        query = expand_query(query)

    # Regex list taiyar karna (List of compiled patterns)
    regex_list = []
    for q in query:
        q = q.strip()
        if not q: continue
        
        # Regex pattern jo spaces ko wildcards se badal de
        pattern = re.escape(q).replace(r"\ ", r".*[\s\.\+\-_()\[\]]")
        regex_list.append(re.compile(pattern, re.IGNORECASE))

    # MongoDB Filter taiyar karna
    conditions = []
    for r in regex_list:
        conditions.append({"file_name": r})
        if USE_CAPTION_FILTER:
            conditions.append({"caption": r})
    
    filter_mongo = {"$or": conditions}
    
    if file_type:
        filter_mongo["file_type"] = file_type

    # Database query
    total_results = await Media.count_documents(filter_mongo)
    if MULTIPLE_DB:
        total_results += await Media2.count_documents(filter_mongo)

    files = await Media.find(filter_mongo).sort("$natural", -1).skip(offset).limit(max_results).to_list(length=max_results)

    if MULTIPLE_DB and len(files) < max_results:
        remaining = max_results - len(files)
        files2 = await Media2.find(filter_mongo).sort("$natural", -1).skip(offset).limit(remaining).to_list(length=remaining)
        files.extend(files2)

    next_offset = offset + len(files)
    if next_offset >= total_results:
        next_offset = ""

    return files, next_offset, total_results


async def get_bad_files(query, file_type=None):
    query = query.strip()
    if not query:
        raw_pattern = '.'
    elif ' ' not in query:
        raw_pattern = r"(\b|[\.\+\-_])" + query + r"(\b|[\.\+\-_])"
    else:
        raw_pattern = query.replace(" ", r".*[\s\.\+\-_()]")
    try:
        regex = re.compile(raw_pattern, flags=re.IGNORECASE)
    except:
        return []
    if USE_CAPTION_FILTER:
        filter = {'$or': [{'file_name': regex}, {'caption': regex}]}
    else:
        filter = {'file_name': regex}
    if file_type:
        filter['file_type'] = file_type
    cursor1 = Media.find(filter).sort('$natural', -1)
    files1 = await cursor1.to_list(length=(await Media.count_documents(filter)))
    if MULTIPLE_DB:
        cursor2 = Media2.find(filter).sort('$natural', -1)
        files2 = await cursor2.to_list(length=(await Media2.count_documents(filter)))
        files = files1 + files2
    else:
        files = files1
    total_results = len(files)
    return files, total_results

async def get_file_details(query):
    filter = {"file_id": query}
    cursor = Media.find(filter)
    filedetails = await cursor.to_list(length=1)
    if not filedetails:
        cursor2 = Media2.find(filter)
        filedetails = await cursor2.to_list(length=1)
    return filedetails


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


async def dreamxbotz_fetch_media(limit: int) -> list:
    """
    Fetch media from primary/secondary DB, clean file names.
    Returns list of Media objects with cleaned file_name.
    """
    try:
        # Decide which DB to use
        if MULTIPLE_DB:
            db_size = await check_db_size(Media)
            if db_size > 407:
                cursor = Media2.find().sort("$natural", -1).limit(limit)
                files = await cursor.to_list(length=limit)
            else:
                cursor = Media.find().sort("$natural", -1).limit(limit)
                files = await cursor.to_list(length=limit)
        else:
            cursor = Media.find().sort("$natural", -1).limit(limit)
            files = await cursor.to_list(length=limit)

        # Clean file names
        cleaned_files = []
        for file in files:
            # Determine if it's series (Sxx/Eyy in name)
            is_series = bool(re.search(r"(S\d{1,2}|Season\s*\d+)", file.file_name, re.IGNORECASE))
            # Clean title
            file.file_name = await dreamxbotz_clean_title(file.file_name, is_series=is_series)
            cleaned_files.append(file)

        return cleaned_files

    except Exception as e:
        logger.error(f"Error in dreamxbotz_fetch_media: {e}")
        return []


async def dreamxbotz_clean_title(filename: str, is_series: bool = False) -> str:
    try:
        # ----------------------
        # Split extension
        parts = filename.rsplit(".", 1)
        name_part = parts[0]
        ext = parts[1] if len(parts) > 1 else ""

        # Replace dots, underscores, hyphens with space
        name_part = re.sub(r"[._\-]+", " ", name_part)
        name_part = re.sub(r"\s+", " ", name_part).strip()

        filename_cleaned = name_part
        # ----------------------

        # Check for year in title
        year_match = re.search(r"^(.*?(\d{4}|\(\d{4}\)))", filename_cleaned, re.IGNORECASE)
        if year_match:
            title = year_match.group(1).replace("(", "").replace(")", "")
            title = (
                re.sub(
                    r"(?:@[^ \n\r\t.,:;!?()\[\]{}<>\\\/\"'=_%]+|[._\-\[\]@()]+)",
                    " ",
                    title,
                )
                .strip()
                .title()
            )
            return f"{title}.{ext}" if ext else title

        # Series handling
        if is_series:
            season_match = re.search(
                r"(.*?)(?:S(\d{1,2})|Season\s*(\d+)|Season(\d+))(?:\s*Combined)?",
                filename_cleaned,
                re.IGNORECASE,
            )
            if season_match:
                title = season_match.group(1).strip()
                season = season_match.group(2) or season_match.group(3) or season_match.group(4)
                title = (
                    re.sub(
                        r"(?:@[^ \n\r\t.,:;!?()\[\]{}<>\\\/\"'=_%]+|[._\-\[\]@()]+)",
                        " ",
                        title,
                    )
                    .strip()
                    .title()
                )
                return f"{title} S{int(season):02}.{ext}" if ext else f"{title} S{int(season):02}"

        # Default cleaning
        title = filename_cleaned
        title = (
            re.sub(
                r"(?:@[^ \n\r\t.,:;!?()\[\]{}<>\\\/\"'=_%]+|[._\-\[\]@()]+)", " ", title
            )
            .strip()
            .title()
        )
        return f"{title}.{ext}" if ext else title

    except Exception as e:
        logger.error(f"Error in dreamxbotz_clean_title: {e}")
        return filename


async def dreamxbotz_get_movies(limit: int = 20) -> List[str]:
    try:
        cursor = await dreamxbotz_fetch_media(limit * 2)
        results = set()
        # Regex to ignore series files
        pattern = r"(?:s\d{1,2}|season\s*\d+|season\d+)(?:\s*combined)?(?:e\d{1,2}|episode\s*\d+)?\b"

        for file in cursor:
            file_name = getattr(file, "file_name", "")

            # ----------------------
            # Clean file name start
            parts = file_name.rsplit(".", 1)  # Split extension
            name_part = parts[0]
            ext = parts[1] if len(parts) > 1 else ""

            # Replace dots/underscores/hyphens with space
            name_part = re.sub(r"[._\-]+", " ", name_part)
            name_part = re.sub(r"\s+", " ", name_part).strip()

            file_name_cleaned = name_part
            # ----------------------

            # Skip series files
            if not re.search(pattern, file_name_cleaned, re.IGNORECASE):
                # Clean title using your existing clean_title function
                title = await dreamxbotz_clean_title(file_name_cleaned)
                # Add extension back
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
        # Regex for series with season/episode
        pattern = r"(.*?)(?:S(\d{1,2})|Season\s*(\d+)|Season(\d+))(?:\s*Combined)?(?:E(\d{1,2})|Episode\s*(\d+))?\b"

        for file in cursor:
            file_name = getattr(file, "file_name", "")

            # ----------------------
            # Clean file name start
            parts = file_name.rsplit(".", 1)  # Split extension
            name_part = parts[0]
            ext = parts[1] if len(parts) > 1 else ""

            # Replace dots/underscores/hyphens with space
            name_part = re.sub(r"[._\-]+", " ", name_part)
            name_part = re.sub(r"\s+", " ", name_part).strip()

            file_name_cleaned = name_part
            # ----------------------

            match = re.search(pattern, file_name_cleaned, re.IGNORECASE)
            if match:
                title_raw = match.group(1)
                # Clean title using your existing clean_title function
                title = await dreamxbotz_clean_title(title_raw, is_series=True)
                # Add extension if exists
                if ext:
                    title = f"{title}.{ext}"

                season = int(match.group(2) or match.group(3) or match.group(4))
                grouped[title].append(season)

        # Limit seasons to 10 per series
        return {
            title: sorted(set(seasons))[:10]
            for title, seasons in grouped.items()
            if seasons
        }
    except Exception as e:
        logger.error(f"Error in dreamxbotz_get_series: {e}")
        return []
