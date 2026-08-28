import logging
import os
import re
import asyncio
import base64
import io
import random
import aiohttp
from struct import pack
from pyrogram.file_id import FileId
from typing import Dict, List
from collections import defaultdict
from pymongo.errors import DuplicateKeyError
from pymongo import UpdateOne
from umongo import Instance, Document, fields
from motor.motor_asyncio import AsyncIOMotorClient
from marshmallow import ValidationError
from datetime import datetime, timedelta
from PIL import Image, ImageDraw, ImageFont

from info import *
from utils import get_settings, save_group_settings, remove_prefix_garbage, temp

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

# ==========================================
# ⚙️ WATERMARK CONFIGURATION
# ==========================================
# Styling, placement, and color palettes for the Tokyo Updates watermark.

WATERMARK_TEXT = "[@Tokyo_Updates]"
WATERMARK_STYLES = [
    {"text": (255, 255, 255), "box": (101, 67, 33, 210)},  # Dark brown
    {"text": (255, 255, 255), "box": (20, 20, 20, 200)},   # Near black
    {"text": (255, 255, 255), "box": (139, 0, 0, 210)},    # Dark red
    {"text": (255, 255, 255), "box": (0, 70, 127, 210)},   # Dark blue
    {"text": (255, 255, 255), "box": (34, 100, 34, 210)},  # Dark green
    {"text": (255, 255, 255), "box": (80, 0, 120, 210)},   # Dark purple
    {"text": (255, 255, 255), "box": (180, 90, 0, 210)},   # Dark orange
    {"text": (0, 0, 0), "box": (255, 215, 0, 210)},        # Gold box, black text
]
WATERMARK_POSITIONS = ["bottom_right", "bottom_left", "top_right", "bottom_center"]

# ==========================================
# 🖼️ COVER IMAGE FETCHER (TMDB / IMDB)
# ==========================================
# Fetches movie/series posters from TMDB (official & proxy) with fallback to IMDB.
# Filters by year to prevent mismatched metadata.

def _year_matches(candidate_date: str | None, expected_year: str | None) -> bool:
    if not expected_year or not candidate_date:
        return True
    return str(candidate_date).strip()[:4] == str(expected_year).strip()

async def _fetch_cover_url_official_tmdb(title: str, year: str | None) -> dict | None:
    if not TMDB_API_KEY: 
        return None
    session = await _get_session()
    try:
        params = {"api_key": TMDB_API_KEY, "query": title.strip(), "include_adult": "false"}
        if year:
            params["year"] = year
        async with session.get(
            "https://api.themoviedb.org/3/search/movie", params=params, timeout=aiohttp.ClientTimeout(total=10)
        ) as resp:
            if resp.status != 200:
                return None
            data = await resp.json()
            results = data.get("results") or []
            if not results:
                return None

            chosen = None
            if year:
                for r in results:
                    if _year_matches(r.get("release_date"), year):
                        chosen = r
                        break
            if not chosen:
                chosen = results[0]
                
            if year and not _year_matches(chosen.get("release_date"), year):
                return None

            poster_path = chosen.get("poster_path")
            backdrop_path = chosen.get("backdrop_path")
            if not poster_path and not backdrop_path:
                return None
            return {
                "poster_url": f"https://image.tmdb.org/t/p/w1280{poster_path}" if poster_path else None,
                "backdrop_url": f"https://image.tmdb.org/t/p/w1280{backdrop_path}" if backdrop_path else None,
                "title": chosen.get("title"),
            }
    except Exception:
        return None

async def _fetch_cover_url(title: str, year: str | None = None) -> str | None:
    details = None
    session = await _get_session()
    
    details = await _fetch_cover_url_official_tmdb(title, year)
    
    # TMDB Proxy Fallback
    if not details:
        try:
            search_title = f"{title} {year}" if year else title
            base_url = "https://tmdb.blazeposters.workers.dev/api/movie-posters"
            async with session.get(base_url, params={"query": search_title.strip()}, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    poster_url = data.get("poster_url")
                    backdrop_url = data.get("backdrop_url")
                    
                    if not poster_url:
                        posters = data.get("images", {}).get("posters", {})
                        for key in ("en", "xx"):
                            if posters.get(key):
                                poster_url = posters[key][0]
                                break
                    if not backdrop_url:
                        backdrops = data.get("images", {}).get("backdrops", {})
                        for key in ("en", "xx"):
                            if backdrops.get(key):
                                backdrop_url = backdrops[key][0]
                                break
                                
                    if poster_url or backdrop_url:
                        result_title = str(data.get("title", "")).lower().strip()
                        result_date = data.get("release_date") or data.get("first_air_date")
                        search_words = [w for w in title.lower().split() if not w.isdigit()]
                        main_words = [w for w in search_words if len(w) >= 2][:2]
                        
                        title_ok = not main_words or all(w in result_title for w in main_words)
                        year_ok = _year_matches(result_date, year)
                        
                        if title_ok and year_ok:
                            if poster_url:
                                poster_url = poster_url.replace("/original/", "/w1280/")
                            if backdrop_url:
                                backdrop_url = backdrop_url.replace("/original/", "/w1280/")
                            details = {"poster_url": poster_url, "backdrop_url": backdrop_url}
        except Exception:
            pass

    # IMDB Fallback
    if not details:
        try:
            from plugins.Dreamxfutures.Imdbposter import get_movie_details
            imdb_data = await get_movie_details(f"{title} {year}" if year else title)
            if imdb_data and imdb_data.get("poster_url"):
                details = {"poster_url": imdb_data.get("poster_url"), "backdrop_url": None}
        except Exception:
            pass

    if not details:
        return None
    return details.get("poster_url") or details.get("backdrop_url")

# ==========================================
# 🖌️ WATERMARK RENDERING (CPU-BOUND)
# ==========================================
# Uses Pillow to draw the dynamic watermark. Runs via asyncio.to_thread 
# to prevent blocking the event loop.

def _render_watermark_sync(data: bytes) -> io.BytesIO | None:
    original = Image.open(io.BytesIO(data)).convert("RGBA")
    TARGET_W, TARGET_H = 1280, 720
    img = original.resize((TARGET_W, TARGET_H), Image.LANCZOS)
    W, H = img.size
    
    style = random.choice(WATERMARK_STYLES)
    position = random.choice(WATERMARK_POSITIONS)
    text_color, box_color = style["text"], style["box"]
    font_size = max(22, int(W * 0.042))
    
    try:
        font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSerif-Bold.ttf", font_size)
    except Exception:
        font = ImageFont.load_default()
        
    dummy = ImageDraw.Draw(img)
    bbox = dummy.textbbox((0, 0), WATERMARK_TEXT, font=font)
    tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
    
    pad_x, pad_y = int(font_size * 0.6), int(font_size * 0.35)
    margin = int(W * 0.025)
    box_w, box_h = tw + pad_x * 2, th + pad_y * 2
    
    corners = {
        "bottom_right": (W - box_w - margin, H - box_h - margin),
        "bottom_left": (margin, H - box_h - margin),
        "top_right": (W - box_w - margin, margin),
        "top_left": (margin, margin),
        "bottom_center": ((W - box_w) // 2, H - box_h - margin),
    }
    
    x0, y0 = corners[position]
    x1, y1 = x0 + box_w, y0 + box_h
    
    overlay = Image.new("RGBA", img.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    draw.rounded_rectangle([x0, y0, x1, y1], radius=int(box_h * 0.35), fill=box_color)
    draw.text((x0 + pad_x, y0 + pad_y), WATERMARK_TEXT, font=font, fill=text_color)
    
    img = Image.alpha_composite(img.convert("RGBA"), overlay).convert("RGB")
    out = io.BytesIO()
    img.save(out, format="JPEG", quality=92)
    out.seek(0)
    out.name = "cover.jpg"
    return out

async def _add_watermark(image_url: str) -> io.BytesIO | None:
    try:
        session = await _get_session()
        async with session.get(image_url, timeout=aiohttp.ClientTimeout(total=15)) as resp:
            if resp.status != 200:
                return None
            data = await resp.read()
            return await asyncio.to_thread(_render_watermark_sync, data)
    except Exception:
        return None

async def _upload_cover(bot, image_url: str) -> str | None:
    try:
        if not COVER_WATERMARK:
            return image_url
        wm_image = await _add_watermark(image_url)
        if not wm_image:
            return image_url
        msg = await bot.send_photo(chat_id=BIN_CHANNEL, photo=wm_image)
        return msg.photo.file_id
    except Exception:
        return image_url

# ==========================================
# 🗄️ MULTI-DATABASE SETUP & ROUTING
# ==========================================
# Manages up to 5 DBs to bypass free tier storage limits (400MB threshold).

_db_stats_cache: Dict[int, dict] = {}
DB_SIZE_LIMIT_MB = 400

client = AsyncIOMotorClient(DATABASE_URI)
db = client[DATABASE_NAME]
instance = Instance.from_db(db)

client2 = AsyncIOMotorClient(DATABASE_URI2)
db2 = client2[DATABASE_NAME]
instance2 = Instance.from_db(db2)

client3 = AsyncIOMotorClient(DATABASE_URI3)
db3 = client3[DATABASE_NAME]
instance3 = Instance.from_db(db3)

client4 = AsyncIOMotorClient(DATABASE_URI4)
db4 = client4[DATABASE_NAME]
instance4 = Instance.from_db(db4)

client5 = AsyncIOMotorClient(DATABASE_URI5)
db5 = client5[DATABASE_NAME]
instance5 = Instance.from_db(db5)

def create_media_model(instance_obj):
    @instance_obj.register
    class DynamicMedia(Document):
        file_id = fields.StrField(attribute="_id")
        file_ref = fields.StrField(allow_none=True)
        file_name = fields.StrField(required=True)
        file_size = fields.IntField(required=True)
        file_type = fields.StrField(allow_none=True)
        mime_type = fields.StrField(allow_none=True)
        caption = fields.StrField(allow_none=True)
        cover = fields.StrField(allow_none=True)
        media_type = fields.StrField(allow_none=True)
        file_date = fields.DateTimeField(allow_none=True)
        title = fields.StrField(allow_none=True)
        year = fields.StrField(allow_none=True)

        class Meta:
            indexes = ("$file_name", "media_type", "-file_date", "title", "year")
            collection_name = COLLECTION_NAME
    return DynamicMedia

Media = create_media_model(instance)
Media2 = create_media_model(instance2)
Media3 = create_media_model(instance3)
Media4 = create_media_model(instance4)
Media5 = create_media_model(instance5)

_ALL_MEDIA_CLASSES = [Media, Media2, Media3, Media4, Media5]
_ALL_DB_CLIENTS = [client, client2, client3, client4, client5]

MEDIA_DBS = _ALL_MEDIA_CLASSES[:TOTAL_DATABASES]
DB_CLIENTS = _ALL_DB_CLIENTS[:TOTAL_DATABASES]
_DB_LABELS = ["Primary DB", "Secondary DB", "Tertiary DB", "Quaternary DB", "Quinary DB"]

async def check_db_size(db_instance):
    """Calculates combined logical and index size of the database."""
    try:
        key = id(db_instance)
        now = datetime.utcnow()
        cached = _db_stats_cache.get(key)

        if cached:
            cache_stale_by_time = (now - cached["timestamp"]) > timedelta(minutes=10)
            near_limit = cached["size_mb"] >= DB_SIZE_LIMIT_MB - 10
            if not cache_stale_by_time and not near_limit:
                return cached["size_mb"]

        stats = await db_instance.command("dbstats")
        db_size_mb = (stats["dataSize"] + stats["indexSize"]) / (1024 * 1024)
        _db_stats_cache[key] = {"timestamp": now, "size_mb": db_size_mb}
        return db_size_mb
    except Exception:
        return 0

async def get_active_media_db():
    """Routes to the first configured database with available space."""
    for media_cls in MEDIA_DBS:
        size_mb = await check_db_size(media_cls.collection.database)
        if size_mb < DB_SIZE_LIMIT_MB:
            return media_cls
    return MEDIA_DBS[-1]

async def delete_file_by_id(file_id: str) -> int:
    for media_cls in MEDIA_DBS:
        result = await media_cls.collection.delete_one({"_id": file_id})
        if result.deleted_count:
            return result.deleted_count
    return 0

async def delete_files_by_query(query: dict) -> int:
    total = 0
    for media_cls in MEDIA_DBS:
        result = await media_cls.collection.delete_many(query)
        total += result.deleted_count
    return total

# ==========================================
# 🔑 FILE ID HELPERS
# ==========================================
# Safely packs and unpacks Pyrogram's Base64 File IDs.

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
            pack("<iiqq", int(decoded.file_type), decoded.dc_id, decoded.media_id, decoded.access_hash)
        )
        file_ref = encode_file_ref(decoded.file_reference)
        return file_id, file_ref
    except Exception:
        return None, None

# ==========================================
# 🧠 SMART DYNAMIC EXTRACTOR CONFIGS
# ==========================================
# Pre-compiled Regex patterns and dictionaries for highly optimized parsing.

RELEASE_TAG = "~[Tokyo_Updates]"
LANGUAGE_ALIASES = {
    "Hindi": [r'\bhindi\b', r'\bhin\b'],
    "English": [r'\benglish\b', r'\beng\b'],
    "Tamil": [r'\btamil\b', r'\btam\b'],
    "Telugu": [r'\btelugu\b', r'\btel\b'],
    "Malayalam": [r'\bmalayalam\b', r'\bmal\b'],
    "Kannada": [r'\bkannada\b', r'\bkan\b'],
    "Punjabi": [r'\bpunjabi\b', r'\bpan\b', r'\bpbi\b'],
    "Bengali": [r'\bbengali\b', r'\bben\b'],
    "Gujarati": [r'\bgujarati\b', r'\bguj\b', r'\bgujrat\b', r'\bgujrati\b'],
    "Marathi": [r'\bmarathi\b', r'\bmar\b'],
    "Korean": [r'\bkorean\b', r'\bkor\b', r'\bk-drama\b', r'\bkdrama\b'],
    "Japanese": [r'\bjapanese\b', r'\bjap\b'],
    "Chinese": [r'\bchinese\b', r'\bmandarin\b', r'\bchi\b'],
    "Spanish": [r'\bspanish\b', r'\besp\b', r'\bspa\b'],
    "Russian": [r'\brussian\b', r'\brus\b'],
    "French": [r'\bfrench\b', r'\bfre\b', r'\bfra\b'],
    "Urdu": [r'\burdu\b'],
    "Bhojpuri": [r'\bbhojpuri\b', r'\bbho\b']
}

OTT_MAP = {
    "NF": ["netflix", "nf"], "AMZN": ["amazon", "amzn", "prime"],
    "DSNP": ["hotstar", "disney", "dsnp"], "JIO": ["jiocinema", "jio", "jc"],
    "ZEE5": ["zee5", "zee"], "LIV": ["sonyliv", "liv"],
    "HMAX": ["hbomax", "hbo", "hmax", "max"], "APTV": ["apple", "aptv", "apple tv"],
    "HULU": ["hulu"], "PMNT": ["paramount", "pmnt", "paramount+"],
    "PEACOCK": ["peacock", "pcok"], "AHA": ["aha", "aha video"],
    "SUNNXT": ["sunnxt", "sun nxt"], "MX": ["mx player", "mxplayer", "mx"],
    "ALTB": ["altbalaji", "alt"], "VOOT": ["voot"],
    "LIONSGATE": ["lionsgate", "lions gate", "lionsgateplay"]
}

URL_CLEAN_RE = re.compile(r'(?:https?://)?(?:www\.)?[-a-zA-Z0-9@:%.\+~#=]{1,256}\.[a-zA-Z0-9()]{1,6}\b(?:[-a-zA-Z0-9()@:%_\+.~#?&//=]*)', re.IGNORECASE)
TME_CLEAN_RE = re.compile(r't\.me/[a-zA-Z0-9]+', re.IGNORECASE)
_MOVIE_VIDEO_RE = re.compile(r'\b(full|hindi|tamil|english|telugu|malayalam|kannada|bengali|new|latest|hd|mp4)\s+(movie|video)\b', re.IGNORECASE)
_SERIES_WORD_RE = re.compile(r'\b(web[\s\-]?series|tv[\s\-]?series)\b', re.IGNORECASE)
_UPLOADER_TAGS = [r'(?:join\s+)?us\s*bobfiles']
_UPLOADER_CLEANUP_RE = re.compile(r'^(?:(?:' + '|'.join(_UPLOADER_TAGS) + r')[\s]*)+', re.IGNORECASE)

_PREFIX_TAGS = [
    r's\d{1,2}(?:-\d{1,2})?', r'e\d{1,4}(?:-\d{1,4})?', r'\d{3,4}[pi]', r'4k', r'8k',
    r'(?:19|20)\d{2}', r'combined', r'complete', r'dual[\s\-]?audio', r'multi[\s\-]?audio',
    r'hindi', r'english', r'tamil', r'telugu', r'malayalam', r'kannada', r'bengali', r'marathi', r'korean', r'japanese', r'chinese', r'spanish', r'russian', r'french',
    r'web[\-\s]?dl', r'web[\-\s]?rip', r'hdrip', r'bluray', r'brrip', r'dvdrip', r'camrip', r'hdts', r'hdcam',
    r'av1', r'x264', r'x265', r'hevc', r'10bit', r'aac', r'eac3', r'ac3', r'ddp[\s\-]?7\.1', r'ddp[\s\-]?5\.1', r'dd[\s\-]?5\.1', r'dd[\s\-]?2\.0', r'ddp', r'5\.1', r'7\.1', r'2\.0', r'2ch', r'stereo',
    r'download', r'watch', r'full[\s\-]?movie', r'web[\s\-]?series', r'new', r'latest',
    r'netflix', r'amazon', r'prime', r'hotstar', r'zee5', r'sonyliv', r'jio', r'jiocinema', r'voot', r'altbalaji'
]

_LANG_PREFIX_WORDS = {
    'hindi', 'english', 'tamil', 'telugu', 'malayalam', 'kannada',
    'bengali', 'marathi', 'korean', 'japanese', 'chinese', 'spanish',
    'russian', 'french'
}
_PREFIX_TOKEN_RE = re.compile('(?:' + '|'.join(_PREFIX_TAGS) + ')', re.IGNORECASE)
SEP_RE = re.compile(r'[\s_\-]*')

STOP_ANCHORS = [
    r'\bseason[\s\-_]*\d{1,2}\b', r'\be\d{1,4}[\s\-_]*[tT][\s\-_]*e?\d{1,4}\b',
    r'\bepisode[\s\-_]*\d{1,4}\b', r'\bep[\s\-_]*\d{1,4}\b', r'\b\d{1,2}x\d{1,4}\b',
    r'\bs\d{1,2}\s?e\d{1,4}\b', r'\bs\d{1,2}\b', r'\be\d{1,4}\b',
    r'\b(?:vol|volume|chapter|part|pt)[\s\.\-_]*(?:\d{1,4}|[ivx]+)\b',
    r'\b(19|20)\d{2}\b', r'\b\d{3,4}[pi]\b', r'\b4k\b', r'\b8k\b',
    r'\bcombined\b', r'\bcomplete\b', r'\bdual[\s\-]?audio\b', r'\bmulti[\s\-]?audio\b',
    r'\bweb[\s\-]?dl\b', r'\bwebrip\b', r'\bbluray\b', r'\bbdrip\b', r'\bbrrip\b', r'\bbdremux\b', r'\bremux\b',
    r'\bhdrip\b', r'\bdvdrip\b', r'\bdvdscr\b', r'\bhdtc\b', r'\bhdts\b', r'\bhdcam\b', r'\bcamrip\b', r'\bpredvd\b',
    r'\bx264\b', r'\bx265\b', r'\bh264\b', r'\bh265\b', r'\bhevc\b', r'\bavc\b', r'\bav1\b',
    r'\b10bit\b', r'\b12bit\b',
    r'\bnetflix\b', r'\bamazon\b', r'\bprime\b', r'\bhotstar\b', r'\bdisney\b',
    r'\bzee5\b', r'\bsonyliv\b', r'\bjiocinema\b', r'\bjio\b', r'\bvoot\b', r'\baltbalaji\b',
    r'\bhbomax\b', r'\bapple[\s\-]?tv\b', r'\bparamount\b', r'\bpeacock\b',
    r'\bsunnxt\b', r'\bmx[\s\-]?player\b', r'\blionsgate\b',
]
_STOP_ANCHOR_RES = [re.compile(a) for a in STOP_ANCHORS]
_LANG_STRIP_RES = [
    (lang, re.compile(rf'\b{re.escape(alias)}\b$', re.IGNORECASE))
    for lang, aliases in LANGUAGE_ALIASES.items()
    for alias in aliases
]

# ==========================================
# ✂️ CORE TITLE & DATA EXTRACTOR
# ==========================================

def extract_pure_title(original_name):
    """Strips tags, URLs, and noise to extract just the clean title."""
    clean_name = re.sub(r'^\(.*?\)', '', original_name).strip()
    clean_name = re.sub(r'^@\w+[\s_\-–]*', '', clean_name).strip()
    clean_name = re.sub(r'[@\[\]_]+', ' ', clean_name)
    clean_name = re.sub(r"[._\-]+", " ", clean_name)
    clean_name = URL_CLEAN_RE.sub('', clean_name)
    clean_name = TME_CLEAN_RE.sub('', clean_name)
    clean_name = _MOVIE_VIDEO_RE.sub('', clean_name).strip()
    clean_name = _SERIES_WORD_RE.sub('', clean_name).strip()
    clean_name = _UPLOADER_CLEANUP_RE.sub('', clean_name).strip()

    _prefix_token_re = _PREFIX_TOKEN_RE
    _sep_re = SEP_RE

    def _splits_a_word(text, end_pos):
        return end_pos < len(text) and text[end_pos].isalnum()

    pos = 0
    while True:
        sep_m = _sep_re.match(clean_name, pos)
        p = sep_m.end() if sep_m else pos
        tok_m = _prefix_token_re.match(clean_name, p)
        if not tok_m: break
        if _splits_a_word(clean_name, tok_m.end()):
            break
        is_lang = tok_m.group(0).lower() in _LANG_PREFIX_WORDS
        if is_lang:
            sep_m2 = _sep_re.match(clean_name, tok_m.end())
            p2 = sep_m2.end() if sep_m2 else tok_m.end()
            if not _prefix_token_re.match(clean_name, p2):
                break
        pos = tok_m.end()

    clean_name = clean_name[pos:].strip()
    lower_name = clean_name.lower()
    first_match_index = len(clean_name)
    
    for anchor_re in _STOP_ANCHOR_RES:
        match = anchor_re.search(lower_name)
        if match and match.start() < first_match_index:
            if match.start() > 2:
                first_match_index = match.start()

    if first_match_index < len(clean_name):
        pure_title = clean_name[:first_match_index].strip()
    else:
        pure_title = clean_name.strip()

    for lang, lang_re in _LANG_STRIP_RES:
        pure_title = lang_re.sub('', pure_title).strip()
        
    return re.sub(r'\s+', ' ', pure_title).strip()

def normalize_season_episode(text):
    """Converts complex S/E ranges (S01E01-E05, etc.) to standard formats."""
    text = text.lower()
    text = re.sub(r'\bs(\d{1,2})\.e(\d{1,4})\b', r's\1 e\2', text)
    text = re.sub(r'\bs(\d{1,2})[\s\-_]*e(\d{1,4})[\s\-_~]+(?:to|and|&)?[\s\-_~]*s\d{1,2}[\s\-_]*e(\d{1,4})\b', lambda m: f"s{int(m.group(1)):02d} e{int(m.group(2)):02d}-{int(m.group(3)):02d}", text)
    text = re.sub(r'\bs(\d{1,2})[\s\-_~]*e(\d{1,4})[\s\-_~]*e(\d{1,4})\b', lambda m: f"s{int(m.group(1)):02d} e{int(m.group(2)):02d}-{int(m.group(3)):02d}", text)
    text = re.sub(r'\bs(\d{1,2})[\s\-_]*e(\d{1,4})[\s\-_]*(?:to|&|and)[\s\-_]*e?(\d{1,4})\b', lambda m: f"s{int(m.group(1)):02d} e{int(m.group(2)):02d}-{int(m.group(3)):02d}", text)
    text = re.sub(r'\bs(\d{1,2})[\s\-_]+e(\d{1,4})[\s\-_]+e?(\d{1,4})\b', lambda m: f"s{int(m.group(1)):02d} e{int(m.group(2)):02d}-{int(m.group(3)):02d}", text)
    text = re.sub(r'\b(\d{1,2})[xX](\d{1,4})[\s\-_]+(?:\d{1,2}[xX])?(\d{1,4})\b', lambda m: f"s{int(m.group(1)):02d} e{int(m.group(2)):02d}-{int(m.group(3)):02d}", text)
    text = re.sub(r'\bseason[\s\-_]*(\d{1,2})[\s\-_~]+(?:to|and|&)?[\s\-_~]*(?:season[\s\-_]*)?(\d{1,2})\b', lambda m: f"s{int(m.group(1)):02d}-{int(m.group(2)):02d}", text)
    text = re.sub(r'\bs(\d{1,2})[\s\-_~]+(?:to|and|&)?[\s\-_~]*s?(\d{1,2})\b', lambda m: f"s{int(m.group(1)):02d}-{int(m.group(2)):02d}", text)
    text = re.sub(r'\be(\d{1,4})[tT]e?(\d{1,4})\b', lambda m: f"e{int(m.group(1)):02d}-{int(m.group(2)):02d}", text)
    text = re.sub(r'\be(\d{1,4})[\s\-_]e(\d{1,4})\b', lambda m: f"e{int(m.group(1)):02d}-{int(m.group(2)):02d}", text)
    text = re.sub(r'\bep(?:isode)?[\s\-_]*(\d{1,4})[\s\-_]+(?:to|and|&)?[\s\-_]*ep(?:isode)?[\s\-_]*(\d{1,4})\b', lambda m: f"e{int(m.group(1)):02d}-{int(m.group(2)):02d}", text)
    text = re.sub(r'\bep(?:isode)?[\s\-_]*\[?(\d{1,4})(?:[\s\-–~]+(?:to|and|&)?[\s\-–~]*(\d{1,4}))?\]?\b', lambda m: f"e{int(m.group(1)):02d}-{int(m.group(2)):02d}" if m.group(2) else f"e{int(m.group(1)):02d}", text)
    text = re.sub(r'\be(\d{1,4})(?:[\s\-–~]+(?:to|and|&)?[\s\-–~]*e?(\d{1,4}))?\b', lambda m: f"e{int(m.group(1)):02d}-{int(m.group(2)):02d}" if m.group(2) else f"e{int(m.group(1)):02d}", text)
    text = re.sub(r'\b(\d{2})[\s\-–]+(\d{2})\b', lambda m: f"e{int(m.group(1)):02d}-{int(m.group(2)):02d}" if int(m.group(1)) < 60 and int(m.group(2)) < 60 else m.group(0), text)
    text = re.sub(r'\b(\d{1,2})[xX](\d{1,4})\b', lambda m: f"s{int(m.group(1)):02d} e{int(m.group(2)):02d}", text)
    text = re.sub(r'\b(?:season)[\s\-_]*(\d{1,2})\b', lambda m: f"s{int(m.group(1)):02d}", text)
    text = re.sub(r'\bs[\s\-_]*(\d{1,2})\b', lambda m: f"s{int(m.group(1)):02d}", text)
    text = re.sub(r'\b(?:episode)[\s\-_]*(\d{1,4})\b', lambda m: f"e{int(m.group(1)):02d}", text)
    text = re.sub(r'\bep(?:isode)?[\s\-_]*(\d{1,4})\b', lambda m: f"e{int(m.group(1)):02d}", text)
    text = re.sub(r'\be[\s\-_]*(\d{1,4})\b', lambda m: f"e{int(m.group(1)):02d}", text)
    text = re.sub(r'\bs(\d{2})e(\d{2,4})\b', r's\1 e\2', text)
    return re.sub(r'\s+', ' ', text).strip().upper()

def apply_dual_multi_audio_tag(languages, scan_lower):
    """Adds Dual/Multi Audio tags safely without overwriting."""
    if "Dual Audio" in languages or "Multi Audio" in languages:
        return languages
    has_dual_word = bool(re.search(r'\bdual\b', scan_lower) or re.search(r'\bdual[\s\.\-_]?audio\b', scan_lower))
    has_multi_word = bool(re.search(r'\bmulti\b', scan_lower) or re.search(r'\bmulti[\s\.\-_]?audio\b', scan_lower))
    
    if has_multi_word:
        languages.append("Multi Audio")
    elif has_dual_word:
        languages.append("Dual Audio")
    elif len(languages) > 2:
        languages.append("Multi Audio")
    elif len(languages) == 2:
        languages.append("Dual Audio")
    return languages

def extract_episode_title(text):
    text = re.sub(r'\.[a-z0-9]{2,4}$', '', text, flags=re.IGNORECASE)
    stop_keywords = [
        r'19\d{2}', r'20\d{2}', r'2160p', r'1080p', r'720p', r'480p',
        r'web[- ]?dl', r'webrip', r'bluray', r'hdrip', r'x264', r'x265', r'hevc',
        r'avc', r'aac', r'ac3', r'ddp', r'hindi', r'english', r'tamil', r'telugu',
        r'dual', r'multi', r'combined', r'complete', r'jhs', r'netflix', r'amazon', r'🗃️'
    ]
    stop_pattern = '|'.join(stop_keywords)
    lookahead = rf'(?=\b(?:{stop_pattern})\b|$|\[|\(|@|🗃️)'
    patterns = [
        r'(?:S\d{1,2}[\s._\-]*E\d{1,4}[-\d]*)[\s._\-]+(.*?)' + lookahead,
        r'(?:Episode|Ep)[\s._\-]*\d+[\s._\-]+(.*?)' + lookahead
    ]
    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            title = match.group(1)
            title = re.sub(r'[\s._\-()\[\]]+', ' ', title).strip()
            if len(title) > 2 and not re.fullmatch(r'[\d\s\-🗃️]+', title):
                if re.fullmatch(r'\s*\d{1,4}\s*(?:to|and|&|-)?\s*\d{0,4}\s*', title, flags=re.IGNORECASE):
                    continue
                return title.title()
    return None

_SOURCES = {
    "WEB-DL": ["web-dl", "webdl", "web dl"], "WEBRip": ["webrip", "web rip"],
    "HDRip": ["hdrip"], "BluRay": ["bluray", "bdrip", "brrip", "bdremux"],
    "DVDRip": ["dvdrip"], "DVDScr": ["dvdscr", "scr", "dvd-scr"],
    "REMUX": ["remux"], "Digital": ["digital"],
    "HDTC": ["hdtc", "hd-tc", "telecine"], "HDTS": ["hdts", "hd-ts", "ts", "telesync"],
    "HDCAM": ["hdcam", "hd-cam", "hd cam"], "CAMRip": ["cam", "camrip", "cinema"],
    "PreDVD": ["predvd", "pre dvd"]
}

_OTT_RES = {
    platform: [(re.compile(r'\b' + re.escape(a) + r'\b'), a) for a in aliases]
    for platform, aliases in OTT_MAP.items()
}

_TAGS_MAP = {
    "AV1": ["av1"], "HEVC X265": ["x265", "hevc", "h265"], "AVC X264": ["x264", "avc", "h264"],
    "10Bit": ["10bit"], "12Bit": ["12bit"], "SDR": ["sdr"], "HDR": ["hdr", "hdr10", "hdr10+"],
    "Dolby Vision": ["dolby vision", "dv", "dovi"], "IMAX": ["imax"], "60FPS": ["60fps"],
    "Dolby Atmos": ["atmos", "dolby atmos"], "Dolby TrueHD": ["truehd", "dolby truehd"],
    "DDP 7.1": ["ddp7.1", "ddp 7.1", "eac3 7.1", "dd+ 7.1"], "DDP 5.1": ["ddp5.1", "ddp 5.1", "eac3", "dd+", "ddp"],
    "DD 5.1": ["dd5.1", "dd 5.1", "ac3 5.1", "ac3", "5.1", "6ch"], "DD 2.0": ["dd2.0", "dd 2.0", "ac3 2.0", "2.0", "2ch", "stereo"],
    "DTS-X": ["dts-x", "dtsx"], "DTS-HD": ["dts-hd", "dtshd", "dts-hd ma"], "DTS 5.1": ["dts 5.1", "dts5.1", "dts"],
    "AAC 5.1": ["aac 5.1", "aac5.1"], "AAC": ["aac", "aac 2.0"],
    "ESubs": ["esub", "esubs"], "HardSubs": ["hsub", "hsubs", "hc", "hcsub"], "MSubs": ["msub", "msubs"]
}

_TARGET_KEYWORDS = [
    r'\bunrated\b', r'\bopen[\s-]?matte\b', r'\bultimate[\s-]?edition\b', r'\bchronological\b', r'\bredux\b',
    r'\bleak\b', r'\bstudio\b', r'\bdub\b', r'\bdubbed\b',
    r'\bunofficial\b', r'\bre[\s-]?dub(?:bed)?\b', r'\bfan[\s-]?dub(?:bed)?\b',
    r'\bhq[\s-]?dub(?:bed)?\b', r'\bstudio[\s-]?dub(?:bed)?\b', r'\bclean[\s-]?audio\b',
    r'\boriginal[\s-]?audio\b', r'\bline[\s-]?audios?\b', r'\bline\b', r'\bmultiplex\b',
    r'\bextended\b', r'\bextendded\b', r'\buncut\b', r"\bdirector's[\s-]?cut\b",
    r'\bdc\b', r'\bremastered\b', r'\bremaster\b', r'\bproper\b',
    r'\bpre[\s-]?release\b', r'\bprerelease\b', r'\bworkprint\b', r'\bwp\b',
    r'\bspecial[\s-]?edition\b', r'\btheatrical\b', r'\banniversary\b',
    r'\bhq\b', r'\bhybrid\b', r'\bpatched\b', r'\bcorrected\b', r'\bsoftsub\b',
    r'\bv[1-4]\b', r'\borgs?\b', r'\bds4k\b', r'\bmulti\b'
]
_CUSTOM_QUALIFIER_RE = re.compile('|'.join(_TARGET_KEYWORDS), re.IGNORECASE)

def extract_languages_quality(text_to_scan):
    """Scans full string to detect embedded Tags, Year, Res, Languages, and Qualifiers."""
    scan_text = re.sub(r'[._]+', ' ', text_to_scan)
    scan_lower = scan_text.lower()
    
    year_match = re.search(r'\b(19\d{2}|20[0-2]\d)\b', text_to_scan)
    year = year_match.group(1) if year_match else None
    
    normalized_se = normalize_season_episode(scan_text)
    season_episode = None
    episode_title = None
    
    full_match = re.search(r'\b(S\d{2})[\s\-]*?(E\d{2,4}(?:[\s\-]*E?[-\s]*\d{2,4})?)\b', normalized_se)
    if full_match:
        season_episode = full_match.group(0)
        if not re.search(r'E\d{1,4}\s*-\s*\d{1,4}', season_episode, flags=re.IGNORECASE):
            episode_title = extract_episode_title(text_to_scan)
    else:
        s_match = re.search(r'\b(S\d{2}(?:-\d{2})?)\b', normalized_se)
        e_match = re.search(r'\b(E\d{2,4}(?:-\d{2,4})?)\b', normalized_se)
        if s_match and e_match:
            season_episode = f"{s_match.group(1)} {e_match.group(1)}"
        elif e_match:
            season_episode = e_match.group(1)
            if not re.search(r'E\d{1,4}\s*-\s*\d{1,4}', season_episode, flags=re.IGNORECASE):
                episode_title = extract_episode_title(text_to_scan)
        elif s_match:
            season_episode = s_match.group(1)

    series_status = None
    status_match = re.search(r'\b(combined|complete)\b', scan_lower)
    if status_match:
        series_status = "COMBINED" if status_match.group(1) == "combined" else "COMPLETE"
        
    resolution = None
    res = re.search(r'(4320[pi]|2160[pi]|1440[pi]|1080[pi]|720[pi]|480[pi]|360[pi]|240[pi]|4k|8k)', scan_lower)
    if res:
        resolution = "2160P" if res.group(1) == "4k" else res.group(1).upper()
        
    source = None
    for src, aliases in _SOURCES.items():
        if any(a in scan_lower for a in aliases):
            source = src
            break
            
    ott_tag = None
    for platform, ott_res in _OTT_RES.items():
        for ott_re, a in ott_res:
            if ott_re.search(scan_lower) or a in scan_lower:
                ott_tag = platform
                break
        if ott_tag: break
        
    extra_tags = []
    for tag, aliases in _TAGS_MAP.items():
        if any(a in scan_lower for a in aliases):
            extra_tags.append(tag)
            
    custom_qualifiers = []
    found_matches = []
    for match in _CUSTOM_QUALIFIER_RE.finditer(scan_lower):
        start_pos = match.start()
        end_pos = match.end()
        original_string = text_to_scan[start_pos:end_pos].strip()
        words = re.split(r'[@\[\]_\.\-\s]+', original_string)
        current_offset = 0
        for word in words:
            word_clean = word.strip()
            if word_clean:
                exact_word_pos = original_string.find(word_clean, current_offset)
                actual_index = start_pos + exact_word_pos
                found_matches.append((actual_index, word_clean))
                current_offset = exact_word_pos + len(word_clean)
                
    found_matches.sort(key=lambda x: x[0])
    seen_lower = set()
    for position, word in found_matches:
        if word.lower() not in seen_lower:
            custom_qualifiers.append(word)
            seen_lower.add(word.lower())

    languages = []
    for lang, aliases in LANGUAGE_ALIASES.items():
        for a in aliases:
            if re.search(a, scan_lower):
                languages.append(lang)
                break
    languages = apply_dual_multi_audio_tag(languages, scan_lower)
    
    kbps_tag = None
    kbps = re.search(r'(\d{2,4}\s?kbps)', scan_lower)
    if kbps:
        kbps_tag = kbps.group(1).upper().replace(" ", "")

    title_part = None
    tp_match = re.search(r'\b(vol|volume|chapter|part|pt)[\s\.\-_]*(\d{1,2}|[IVX]+)\b(?!\d)', scan_lower)
    if tp_match:
        tag_name = tp_match.group(1).capitalize()
        if tag_name == "Pt": tag_name = "Part"
        if tag_name == "Volume": tag_name = "Vol"
        title_part = f"{tag_name} {tp_match.group(2).upper()}"

    split_part = None
    sp_match = re.search(r'\b(?:part|pt)[\s\.\-_]*(\d{3,4})\b', scan_lower)
    if sp_match:
        split_part = f"Part {sp_match.group(1)}"

    return {
        "year": year, "season_episode": season_episode, "episode_title": episode_title,
        "languages": languages, "resolution": resolution, "source": source,
        "ott": ott_tag, "extra_tags": extra_tags, "kbps": kbps_tag,
        "custom_qualifiers": custom_qualifiers, "series_status": series_status,
        "title_part": title_part, "split_part": split_part
    }

# ==========================================
# 💾 MAIN ASYNC SAVE PIPELINE
# ==========================================
# Orchestrates metadata extraction, name assembling, cover fetching, and saving to DB.

async def _get_session() -> aiohttp.ClientSession:
    if temp.AIOHTTP_SESSION is None or temp.AIOHTTP_SESSION.closed:
        temp.AIOHTTP_SESSION = aiohttp.ClientSession()
    return temp.AIOHTTP_SESSION

_COVER_LOCKS = {}
_COVER_CACHE = {}

async def _fetch_and_save_cover(file_id: str, final_title: str, year: str | None, bot=None):
    """Background task for Cover generation using lock to prevent duplicate API hits."""
    lock_key = f"{final_title.lower().strip()}::{(year or '').strip()}"
    if lock_key not in _COVER_LOCKS:
        _COVER_LOCKS[lock_key] = asyncio.Lock()
        
    async with _COVER_LOCKS[lock_key]:
        try:
            if lock_key in _COVER_CACHE:
                cover_url = _COVER_CACHE[lock_key]
            else:
                query = {"title": {"$regex": rf"^{re.escape(final_title)}$", "$options": "i"}, "cover": {"$ne": None}}
                if year:
                    query["year"] = year
                    
                existing = None
                for media_cls in MEDIA_DBS:
                    existing = await media_cls.find_one(query)
                    if existing: break
                    
                if existing and existing.cover:
                    cover_url = existing.cover
                    _COVER_CACHE[lock_key] = cover_url
                else:
                    raw_url = await _fetch_cover_url(final_title, year)
                    if not raw_url: return
                    cover_url = await _upload_cover(bot, raw_url) if bot else raw_url
                    if cover_url:
                        _COVER_CACHE[lock_key] = cover_url
                    else:
                        return
                        
            for media_cls in MEDIA_DBS:
                await media_cls.collection.update_one({"_id": file_id}, {"$set": {"cover": cover_url}})
        except Exception as e:
            logger.warning(f"[COVER] Background task error for '{final_title}': {e}")

async def save_file(media, bot=None, extracted_info=None):
    """The heart of the indexer: formats names and stores media properly."""
    try:
        file_id, file_ref = unpack_new_file_id(media.file_id)
        original_name = str(media.file_name or "Unnamed File")
        base_name, ext = os.path.splitext(original_name)
        text_to_scan = f"{original_name} {getattr(media, 'caption', '') or ''}"

        extracted = await asyncio.to_thread(extract_languages_quality, text_to_scan)
        if extracted_info and extracted_info.get("language") and extracted_info.get("language") != "N/A":
            extracted["languages"] = [lang.strip() for lang in extracted_info["language"].split(",")]
            
        extracted["languages"] = apply_dual_multi_audio_tag(extracted["languages"], text_to_scan.lower())
        
        if not extracted.get("resolution"): extracted["resolution"] = "720P"
        if not extracted.get("source"): extracted["source"] = "WEB-DL"
        
        audio_codecs = ["Dolby TrueHD", "Dolby Atmos", "DTS-X", "DTS-HD", "DDP 7.1", "DDP 5.1", "DD 5.1", "DD 2.0", "DTS 5.1", "AAC 5.1", "AAC"]
        audio_tags = extracted.get("extra_tags", [])
        if not any(codec in audio_tags for codec in audio_codecs):
            if "AAC" not in audio_tags:
                audio_tags.append("AAC")
        extracted["extra_tags"] = audio_tags

        cleaned_title = await asyncio.to_thread(extract_pure_title, base_name)
        formatted_words = [word[0].upper() + word[1:] if len(word) > 1 else word.upper() for word in cleaned_title.split()]
        final_title = " ".join(formatted_words)
        
        # Assemble string strictly based on defined standards
        parts = []
        def add_unique(value):
            if value and str(value).lower() not in " ".join(map(str, parts)).lower():
                parts.append(value)

        if final_title: add_unique(final_title)
        if extracted.get("title_part"): add_unique(extracted["title_part"])
        if extracted.get("season_episode"): add_unique(extracted["season_episode"])
        if extracted.get("season_episode") and extracted.get("episode_title"): add_unique(extracted["episode_title"])
        if extracted.get("series_status"): add_unique(extracted["series_status"])
        if extracted.get("year"): add_unique(extracted["year"])
        if extracted.get("resolution"): add_unique(extracted["resolution"])
        for lang in extracted.get("languages", []): add_unique(lang)
        for qual in extracted.get("custom_qualifiers", []): add_unique(qual)
        
        for tag in ["10Bit", "12Bit", "SDR", "HDR", "Dolby Vision", "IMAX", "60FPS"]:
            if tag in extracted.get("extra_tags", []): add_unique(tag)
            
        if extracted.get("ott") and extracted["ott"] not in parts:
            parts.append(extracted["ott"])
            
        if extracted.get("source"): add_unique(extracted["source"])
        
        for vcodec in ["AV1", "HEVC X265", "AVC X264"]:
            if vcodec in extracted.get("extra_tags", []): add_unique(vcodec)
            
        audio_tags = extracted.get("extra_tags", [])
        if "DDP 5.1" in audio_tags and "DD 5.1" in audio_tags: audio_tags.remove("DD 5.1")
        if "DDP 7.1" in audio_tags and "DD 5.1" in audio_tags: audio_tags.remove("DD 5.1")
        if "AAC 5.1" in audio_tags and "AAC" in audio_tags: audio_tags.remove("AAC")
        
        for acodec in audio_codecs:
            if acodec in audio_tags: add_unique(acodec)
            
        for sub in ["ESubs", "HardSubs", "MSubs"]:
            if sub in extracted.get("extra_tags", []): add_unique(sub)
            
        if extracted.get("kbps"): add_unique(extracted["kbps"])
        if extracted.get("split_part"): add_unique(extracted["split_part"])
        
        parts = [p for p in parts if p and "Tokyo_Updates" not in str(p)]
        parts.append(RELEASE_TAG)
        
        file_name = " ".join(map(str, parts)).strip()
        file_name = re.sub(r'\s+', ' ', file_name) + ext.lower()
        file_name = re.sub(r'\s+\.', '.', file_name)

        if getattr(temp, "COVERX", True):
            asyncio.create_task(_fetch_and_save_cover(
                file_id=file_id, final_title=final_title, year=extracted.get("year"), bot=bot
            ))

        for db_index, media_cls in enumerate(MEDIA_DBS):
            existing_file = await media_cls.find_one({"_id": file_id})
            if existing_file:
                return False, 0, None

        target_media = await get_active_media_db()
        record = target_media(
            file_id=file_id, file_ref=file_ref, file_name=file_name, file_size=media.file_size,
            file_type=media.file_type, mime_type=media.mime_type,
            caption=getattr(media.caption, "html", None) if media.caption else None,
            cover=None, media_type=("series" if is_series_file(file_name) else "movie"),
            file_date=datetime.utcnow(), title=final_title, year=extracted.get("year")
        )
        await record.commit()
        clear_search_cache()
        return True, 1, file_name
    except DuplicateKeyError:
        return False, 0, None
    except Exception as e:
        logger.error(f"Error saving file: {e}", exc_info=True)
        return False, 0, None

# ==========================================
# 🔍 ADVANCED SEARCH & SORTING ENGINE
# ==========================================
# Caches results, performs cross-DB querying, exact matching, and recency sorting.

SOURCE_ORDER = {
    "bluray": 15, "blu-ray": 15, "bdrip": 14, "brrip": 14, "bdremux": 14, "remux": 14,
    "web-dl": 13, "webdl": 13, "web dl": 13, "webrip": 12, "web rip": 12, "digital": 11, "web": 11,
    "hdtv": 10, "hdrip": 9, "dvdrip": 8, "dvd": 7,
    "predvd": 6, "pre-dvd": 6, "pre dvd": 6, "pre": 5, "dvdscr": 2, "dvd-scr": 2, "scr": 2,
    "hdts": 4, "hd-ts": 4, "ts": 4, "telesync": 4, "hdtc": 3, "hd-tc": 3, "tc": 3, "telecine": 3,
    "hdcam": 2, "hd-cam": 2, "hd cam": 2, "camrip": 2, "cam": 1, "cinema": 1
}

QUALITY_ORDER = {
    "4320p": 8, "8k": 8, "2160p": 7, "4k": 7, "1440p": 6, "1080p": 5, "1080i": 5, "720p": 4, "720i": 4, "480p": 3, "360p": 2, "240p": 1, "144p": 0
}

def extract_quality(name):
    name = name.lower()
    for q, weight in QUALITY_ORDER.items():
        if q in name:
            return weight
    return -1

def extract_source(name):
    name = name.lower()
    for s, weight in SOURCE_ORDER.items():
        if s in name:
            return weight
    return -1

def extract_season_episode(name):
    name = name.lower()
    combined = re.search(r"\bs(?:eason)?[\s._-]*(\d{1,2})[\s._-]*e(?:p(?:isode)?)?[\s._-]*(\d{1,4})", name)
    if combined:
        return int(combined.group(1)), int(combined.group(2))
    s = re.search(r"\bs(?:eason)?[\s._-]*(\d{1,2})", name)
    e = re.search(r"\be(?:pisode|p)?[\s._-]*(\d{1,4})", name)
    season = int(s.group(1)) if s else 0
    episode = int(e.group(1)) if e else 0
    return season, episode

SERIES_PATTERNS = [
    r"\bs\d{1,2}[\s.-]e\d{1,4}\b", r"\bs\d{1,2}\s-\s*s?\d{1,2}\b",
    r"\be(?:p(?:isode)?)?[\s.-]\d{1,4}\s-\s*(?:e(?:p(?:isode)?)?[\s.-]*)?\d{1,4}\b",
    r"\b\d{1,2}x\d{1,3}\b", r"\bseason[\s.-]\d{1,2}\b", r"\bweb[\s.-]?series\b",
    r"\bseries\b", r"\bs\d{1,2}\b", r"\bepisode[\s.-]\d{1,4}\b", r"\bep[\s._-]\d{1,4}\b",
    r"\ball\sepisodes?\b", r"\bcomplete\b.{0,40}\b(?:season|series)\b", r"\b(?:season|series)\b.{0,40}\bcomplete\b",
]

SERIES_REGEX = re.compile("|".join(f"(?:{p})" for p in SERIES_PATTERNS), re.IGNORECASE)

def is_series_file(name) -> bool:
    return bool(SERIES_REGEX.search(str(name).lower()))

async def backfill_media_type(batch_size: int = 500, media_dbs=None, progress_cb=None, sleep_between_batches: float = 0.25) -> dict:
    """Utility to pre-classify old media as 'movie' or 'series' for faster sorting."""
    dbs = media_dbs if media_dbs is not None else MEDIA_DBS
    report = {}
    for media_cls in dbs:
        coll = media_cls.collection
        updated = 0
        ops = []
        pending_total = await coll.count_documents({"media_type": None})
        if pending_total == 0:
            report[media_cls.__name__] = 0
            continue
            
        cursor = coll.find({"media_type": None}, {"_id": 1, "file_name": 1}).batch_size(batch_size)
        try:
            async for doc in cursor:
                mtype = "series" if is_series_file(doc.get("file_name", "")) else "movie"
                ops.append(UpdateOne({"_id": doc["_id"]}, {"$set": {"media_type": mtype}}))
                if len(ops) >= batch_size:
                    res = await coll.bulk_write(ops, ordered=False)
                    updated += res.modified_count
                    ops = []
                    if progress_cb:
                        try: await progress_cb(media_cls.__name__, updated, pending_total)
                        except Exception: pass
                    if sleep_between_batches:
                        await asyncio.sleep(sleep_between_batches)
                        
            if ops:
                res = await coll.bulk_write(ops, ordered=False)
                updated += res.modified_count
                if progress_cb:
                    try: await progress_cb(media_cls.__name__, updated, pending_total)
                    except Exception: pass
        finally:
            await cursor.close()
        report[media_cls.__name__] = updated
    return report

def normalize_for_search(text):
    text = text.lower()
    text = re.sub(r'(\d+)xX', lambda m: f"s{int(m.group(1)):02d} e{int(m.group(2)):02d}", text)
    text = re.sub(r'\bseason[\s-](\d+)', lambda m: f"s{int(m.group(1)):02d}", text)
    text = re.sub(r"(?<!['’])\bs(\d+)\b", lambda m: f"s{int(m.group(1)):02d}", text)
    text = re.sub(r'\b(?:episode|ep)[\s-](\d+)', lambda m: f"e{int(m.group(1)):02d}", text)
    text = re.sub(r"(?<!['’])\be(\d+)\b", lambda m: f"e{int(m.group(1)):02d}", text)
    text = re.sub(r'\bs(\d+)e(\d+)', lambda m: f"s{int(m.group(1)):02d} e{int(m.group(2)):02d}", text)
    return re.sub(r"\s+", " ", text).strip()

def expand_query(query):
    query = query.lower()
    patterns = [query]
    title = re.sub(r'\b(s\d+|e\d+|season[\s-]*\d+|episode[\s-]*\d+|ep[\s-]*\d+)\b', '', query)
    title = re.sub(r'[\s._-]+', ' ', title).strip()
    
    s_match = re.search(r"\bs(\d{1,2})|season[\s-]*(\d{1,2})", query)
    e_match = re.search(r"\be(\d{1,4})|episode[\s-]*(\d{1,4})|ep[\s-]*(\d{1,4})", query)
    s_num = int(s_match.group(1) or s_match.group(2)) if s_match else None
    e_num = int(e_match.group(1) or e_match.group(2) or e_match.group(3)) if e_match else None
    
    if s_num and e_num:
        for v in [f"s{s_num:02d}e{e_num:02d}", f"s{s_num:02d} e{e_num:02d}", f"s{s_num}e{e_num}"]:
            patterns.append(f"{title} {v}".strip())
    elif s_num:
        for v in [f"s{s_num:02d}", f"s{s_num}", f"season {s_num}"]:
            patterns.append(f"{title} {v}".strip())
    elif e_num:
        for v in [f"e{e_num:02d}", f"e{e_num}", f"episode {e_num}"]:
            if title: patterns.append(f"{title} {v}".strip())
            else: patterns.append(v)
    return list(set(patterns))

_SEARCH_CACHE: dict = {}
_SEARCH_CACHE_TTL = 90
_SEARCH_CACHE_MAX_ENTRIES = 300

def _search_cache_key(chat_id, query, file_type, max_results, offset, filter, media_type):
    q = tuple(query) if isinstance(query, list) else query
    return (chat_id, q, file_type, max_results, offset, filter, media_type)

def _search_cache_get(key):
    entry = _SEARCH_CACHE.get(key)
    if not entry: return None
    cached_at, value = entry
    if (datetime.utcnow() - cached_at).total_seconds() > _SEARCH_CACHE_TTL:
        _SEARCH_CACHE.pop(key, None)
        return None
    return value

def _search_cache_set(key, value):
    if len(_SEARCH_CACHE) >= _SEARCH_CACHE_MAX_ENTRIES:
        oldest_key = min(_SEARCH_CACHE, key=lambda k: _SEARCH_CACHE[k][0])
        _SEARCH_CACHE.pop(oldest_key, None)
    _SEARCH_CACHE[key] = (datetime.utcnow(), value)

def clear_search_cache():
    _SEARCH_CACHE.clear()

def _word_to_regex(word):
    if "'" in word or "\u2019" in word:
        parts = [p for p in re.split(r"['\u2019]", word) if p]
        if len(parts) > 1: return "['\u2019]?".join(re.escape(p) for p in parts)
        return re.escape(word)
    if len(word) > 2 and word.endswith("s"):
        return re.escape(word[:-1]) + "['\u2019]?s"
    return re.escape(word)

async def get_search_results(chat_id, query, file_type=None, max_results=10, offset=0, filter=False, media_type=None):
    cache_key = _search_cache_key(chat_id, query, file_type, max_results, offset, filter, media_type)
    cached = _search_cache_get(cache_key)
    if cached is not None:
        return cached

    if chat_id:
        settings = await get_settings(int(chat_id))
        max_results = 10 if settings.get("max_btn") else int(getattr(temp, 'MAX_B_TN', 10))
        
    original_query = str(query).lower().strip()
    if not isinstance(query, list):
        query = normalize_for_search(query)
        query = expand_query(query)[:5]
        
    regex_list = []
    for q in query:
        q = q.strip()
        if not q: continue
        words = q.split()
        pattern = r'.*'.join(_word_to_regex(w) for w in words)
        regex_list.append(pattern)

    combined_regex = None
    if regex_list:
        try: combined_regex = re.compile("(?:" + "|".join(regex_list) + ")", re.IGNORECASE)
        except re.error: combined_regex = None
        
    conditions = []
    if combined_regex is not None:
        conditions.append({"file_name": combined_regex})
        if getattr(temp, 'USE_CAPTION_FILTER', False):
            conditions.append({"caption": combined_regex})
            
    filter_mongo = {"$or": conditions}
    if file_type: filter_mongo["file_type"] = file_type
    
    if media_type == "movie":
        filter_mongo = {"$and": [filter_mongo, {"$or": [{"media_type": "movie"}, {"media_type": None, "file_name": {"$not": SERIES_REGEX}}]}]}
    elif media_type == "series":
        filter_mongo = {"$and": [filter_mongo, {"$or": [{"media_type": "series"}, {"media_type": None, "file_name": SERIES_REGEX}]}]}
        
    fetch_limit = max(100, offset + max_results * 2)

    count_tasks = [m.count_documents(filter_mongo) for m in MEDIA_DBS]
    find_tasks = [
        m.find(filter_mongo).sort([("file_date", -1), ("_id", -1)]).limit(fetch_limit).to_list(length=fetch_limit)
        for m in MEDIA_DBS
    ]
    counts, per_db_files = await asyncio.gather(asyncio.gather(*count_tasks), asyncio.gather(*find_tasks))
    
    total_results = sum(counts)
    files = [f for db_files in per_db_files for f in db_files]
    
    def _recency_key(x):
        fd = getattr(x, "file_date", None)
        return fd if fd is not None else datetime.min
        
    files.sort(key=_recency_key, reverse=True)
    files = files[:fetch_limit]
    
    is_series = any(re.search(r"s\d{1,2}.*e\d{1,4}", str(file.file_name).lower()) for file in files)
    first_word = original_query.split()[0] if original_query.split() else original_query
    orig_re = re.compile(rf"^[\s._\-\[\(]*{re.escape(original_query)}")
    first_re = re.compile(rf"^[\s._\-\[\(]*{re.escape(first_word)}")
    
    def _normalize_exact(text):
        text = str(text).lower()
        text = re.sub(r"\.\w{2,4}$", "", text)
        return re.sub(r"[\s._\-\[\]]+", " ", text).strip()
        
    exact_query_norm = _normalize_exact(original_query)
    def _is_exact(x): return _normalize_exact(x.file_name) == exact_query_norm

    indexed_files = list(enumerate(files))
    group_min_idx = {}
    for idx, x in indexed_files:
        grp = (x.title or x.file_name).strip().lower()
        if grp not in group_min_idx or idx < group_min_idx[grp]:
            group_min_idx[grp] = idx
            
    def _unified_key(item):
        idx, x = item
        name_lower = x.file_name.lower()
        grp = (x.title or x.file_name).strip().lower()
        file_is_series = is_series_file(x.file_name)
        season, episode = extract_season_episode(x.file_name) if file_is_series else (0, 0)
        return (
            not _is_exact(x),
            not orig_re.match(name_lower),
            not first_re.match(name_lower),
            group_min_idx[grp],
            -season,
            -episode,
            idx,
            -extract_quality(x.file_name),
            -extract_source(x.file_name),
        )
        
    indexed_files = sorted(indexed_files, key=_unified_key)
    sorted_files = [x for idx, x in indexed_files]
    paginated_files = sorted_files[offset:offset + max_results]
    
    next_offset = offset + max_results
    if next_offset >= total_results or len(paginated_files) < max_results:
        next_offset = ""
        
    result = (paginated_files, next_offset, total_results)
    _search_cache_set(cache_key, result)
    return result

async def get_bad_files(query, file_type=None):
    query = query.strip()
    if not query:
        raw_pattern = '.'
    elif ' ' not in query:
        raw_pattern = r"(\b|[.+-])" + query + r"(\b|[.+-])"
    else:
        raw_pattern = query.replace(" ", r".*[\s.+-_()]")
        
    try: regex = re.compile(raw_pattern, flags=re.IGNORECASE)
    except: return []
    
    filter = {'$or': [{'file_name': regex}, {'caption': regex}]} if getattr(temp, 'USE_CAPTION_FILTER', False) else {'file_name': regex}
    if file_type: filter['file_type'] = file_type
    
    files = []
    for media_cls in MEDIA_DBS:
        cursor = media_cls.find(filter).sort('$natural', -1)
        files.extend(await cursor.to_list(length=(await media_cls.count_documents(filter))))
    return files, len(files)

async def update_cover_url(file_id: str, cover_url: str) -> bool:
    try:
        for media_cls in MEDIA_DBS:
            result = await media_cls.collection.update_one({"_id": file_id}, {"$set": {"cover": cover_url}})
            if result.modified_count: return True
        return False
    except Exception as e:
        logger.error(f"[COVER] update_cover_url error: {e}")
        return False

async def get_cover_url(file_id: str) -> str | None:
    try:
        details = await get_file_details(file_id)
        if details:
            return getattr(details[0], 'cover', None) or details[0].get('cover', None)
        return None
    except Exception as e:
        logger.error(f"[COVER] get_cover_url error: {e}")
        return None

async def get_file_details(query):
    filter = {"file_id": query}
    filedetails = []
    for media_cls in MEDIA_DBS:
        cursor = media_cls.find(filter)
        filedetails = await cursor.to_list(length=1)
        if filedetails: break
    return filedetails

# ==========================================
# 📊 UTILITIES FOR FETCHING & FORMATTING 
# ==========================================

async def dreamxbotz_fetch_media(limit: int) -> list:
    try:
        target_media = MEDIA_DBS[0] if len(MEDIA_DBS) == 1 else await get_active_media_db()
        cursor = target_media.find().sort([("file_date", -1), ("_id", -1)]).limit(limit)
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
        filename_cleaned = re.sub(r"\s+", " ", name_part).strip()
        
        year_match = re.search(r"^(.*?(\d{4}|\[\d{4}\]))", filename_cleaned, re.IGNORECASE)
        if year_match:
            title = year_match.group(1).replace("(", "").replace(")", "")
            title = re.sub(r"(?:@[^ \n\r\t.,:;!?()\[\]{}<>\\\/\"'=_%]+|[._\-\[\]@()]+)", " ", title).strip().title()
            return f"{title}.{ext}" if ext else title
            
        if is_series:
            season_match = re.search(r"(.*?)(?:S(\d{1,2})|Season\s*(\d+)|Season(\d+))(?:\s*Combined)?", filename_cleaned, re.IGNORECASE)
            if season_match:
                title = season_match.group(1).strip()
                season = season_match.group(2) or season_match.group(3) or season_match.group(4)
                title = re.sub(r"(?:@[^ \n\r\t.,:;!?()\[\]{}<>\\\/\"'=_%]+|[._\-\[\]@()]+)", " ", title).strip().title()
                return f"{title} S{int(season):02}.{ext}" if ext else f"{title} S{int(season):02}"
                
        title = re.sub(r"(?:@[^ \n\r\t.,:;!?()\[\]{}<>\\\/\"'=_%]+|[._\-\[\]@()]+)", " ", filename_cleaned).strip().title()
        return f"{title}.{ext}" if ext else title
    except Exception as e:
        logger.error(f"Error in dreamxbotz_clean_title: {e}")
        return filename

async def dreamxbotz_get_movies(limit: int = 20) -> List[str]:
    try:
        cursor = await dreamxbotz_fetch_media(limit * 2)
        results = set()
        pattern = r"(?:s\d{1,2}|season\s*\d+|season\d+)(?:\scombined)?(?:e\d{1,4}|episode\s\d+)?\b"

        for file in cursor:
            file_name = getattr(file, "file_name", "")
            parts = file_name.rsplit(".", 1)
            name_part = re.sub(r"[._\-]+", " ", parts[0])
            file_name_cleaned = re.sub(r"\s+", " ", name_part).strip()
            ext = parts[1] if len(parts) > 1 else ""
            
            if not re.search(pattern, file_name_cleaned, re.IGNORECASE):
                title = await dreamxbotz_clean_title(file_name_cleaned)
                if ext: title = f"{title}.{ext}"
                results.add(title)
                if len(results) >= limit: break
        return sorted(list(results))[:limit]
    except Exception as e:
        logger.error(f"Error in dreamxbotz_get_movies: {e}")
        return []

async def dreamxbotz_get_series(limit: int = 30) -> Dict[str, List[int]]:
    try:
        cursor = await dreamxbotz_fetch_media(limit * 5)
        grouped = defaultdict(list)
        pattern = r"(.*?)(?:S(\d{1,2})|Season\s(\d+)|Season(\d+))(?:\sCombined)?(?:E(\d{1,4})|Episode\s(\d+))?\b"

        for file in cursor:
            file_name = getattr(file, "file_name", "")
            parts = file_name.rsplit(".", 1)
            name_part = re.sub(r"[._\-]+", " ", parts[0])
            file_name_cleaned = re.sub(r"\s+", " ", name_part).strip()
            ext = parts[1] if len(parts) > 1 else ""
            
            match = re.search(pattern, file_name_cleaned, re.IGNORECASE)
            if match:
                title = await dreamxbotz_clean_title(match.group(1), is_series=True)
                if ext: title = f"{title}.{ext}"
                season = int(match.group(2) or match.group(3) or match.group(4))
                grouped[title].append(season)
                
        return {title: sorted(set(seasons))[:10] for title, seasons in grouped.items() if seasons}
    except Exception as e:
        logger.error(f"Error in dreamxbotz_get_series: {e}")
        return []
