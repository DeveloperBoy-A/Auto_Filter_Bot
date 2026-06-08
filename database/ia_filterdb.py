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

# Cover fetch karne ke liye (lazy import to avoid circular imports)
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

# ---------------- Watermark Config ----------------
WATERMARK_TEXT = "[@Tokyo_Updates]"

# Random color palettes — (text_color, box_color_RGBA)
WATERMARK_STYLES = [
    {"text": (255, 255, 255), "box": (101,  67,  33, 210)},   # Dark brown (original)
    {"text": (255, 255, 255), "box": ( 20,  20,  20, 200)},   # Near black
    {"text": (255, 255, 255), "box": (139,   0,   0, 210)},   # Dark red
    {"text": (255, 255, 255), "box": (  0,  70, 127, 210)},   # Dark blue
    {"text": (255, 255, 255), "box": ( 34, 100,  34, 210)},   # Dark green
    {"text": (255, 255, 255), "box": ( 80,   0, 120, 210)},   # Dark purple
    {"text": (255, 255, 255), "box": (180,  90,   0, 210)},   # Dark orange
    {"text": (  0,   0,   0), "box": (255, 215,   0, 210)},   # Gold box, black text
]

# Random corner positions
WATERMARK_POSITIONS = ["bottom_right", "bottom_left", "top_right", "top_left"]

# ---------------- Cover Image Fetcher ----------------
async def _fetch_cover_url(title: str) -> str | None:
    """
    File ke clean title se poster URL fetch karo.
    Pehle TMDB free public API try hogi, phir IMDB fallback.
    """
    import aiohttp

    details = None

    # --- Step 1: TMDB (free public worker endpoint, no API key needed) ---
    try:
        logger.info(f"[COVER] Trying TMDB for: '{title}'")
        base_url = "https://tmdb.blazeposters.workers.dev/api/movie-posters"
        params = {"query": title.strip()}
        if TMDB_API_KEY:
            params["api_key"] = TMDB_API_KEY

        async with aiohttp.ClientSession() as session:
            async with session.get(base_url, params=params, timeout=aiohttp.ClientTimeout(total=10)) as resp:
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
                        # ✅ Title match check — galat movie reject karo
                        result_title = str(data.get("title", "")).lower().strip()
                        search_words = title.lower().split()
                        # Search ke pehle 2 main words result mein hone chahiye
                        main_words = [w for w in search_words if len(w) > 2][:2]
                        if main_words and not any(w in result_title for w in main_words):
                            logger.warning(f"[COVER] TMDB title mismatch: searched='{title}' got='{result_title}' — skipping")
                        else:
                            if poster_url:
                                poster_url = poster_url.replace("/original/", "/w1280/")
                            if backdrop_url:
                                backdrop_url = backdrop_url.replace("/original/", "/w1280/")
                            details = {"poster_url": poster_url, "backdrop_url": backdrop_url}
                            logger.info(f"[COVER] TMDB success | poster={poster_url} | backdrop={backdrop_url}")
                else:
                    logger.warning(f"[COVER] TMDB status {resp.status} for '{title}'")
    except Exception as e:
        logger.warning(f"[COVER] TMDB error for '{title}': {e}")

    # --- Step 2: IMDB fallback ---
    if not details:
        try:
            logger.info(f"[COVER] Trying IMDB for: '{title}'")
            from plugins.Dreamxfutures.Imdbposter import get_movie_details
            imdb_data = await get_movie_details(title)
            if imdb_data and imdb_data.get("poster_url"):
                details = {
                    "poster_url": imdb_data.get("poster_url"),
                    "backdrop_url": None
                }
                logger.info(f"[COVER] IMDB success | poster={details['poster_url']}")
            else:
                logger.warning(f"[COVER] IMDB also returned nothing for '{title}'")
        except Exception as e:
            logger.warning(f"[COVER] IMDB error for '{title}': {e}")

    if not details:
        logger.warning(f"[COVER] No cover found from any source for '{title}'")
        return None

    poster = details.get("poster_url")
    backdrop = details.get("backdrop_url")

    # Hamesha landscape (backdrop) prefer karo — portrait convert se better quality
    if backdrop:
        return backdrop
    return poster


async def _add_watermark(image_url: str) -> "io.BytesIO | None":
    """
    Landscape poster download karo aur watermark lagao.
    TMDB se hamesha backdrop (landscape) fetch hoga.
    """
    import io
    import random
    import aiohttp
    from PIL import Image, ImageDraw, ImageFont

    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(image_url, timeout=aiohttp.ClientTimeout(total=15)) as resp:
                if resp.status != 200:
                    return None
                data = await resp.read()

        original = Image.open(io.BytesIO(data)).convert("RGBA")

        # Resize to 1280x720 (16:9 landscape)
        TARGET_W, TARGET_H = 1280, 720
        img = original.resize((TARGET_W, TARGET_H), Image.LANCZOS)
        W, H = img.size

        # ── Watermark ────────────────────────────────────────────────────
        style      = random.choice(WATERMARK_STYLES)
        position   = random.choice(WATERMARK_POSITIONS)
        text_color = style["text"]
        box_color  = style["box"]

        font_size = max(22, int(W * 0.042))
        try:
            font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSerif-Bold.ttf", font_size)
        except Exception:
            font = ImageFont.load_default()

        dummy = ImageDraw.Draw(img)
        bbox  = dummy.textbbox((0, 0), WATERMARK_TEXT, font=font)
        tw    = bbox[2] - bbox[0]
        th    = bbox[3] - bbox[1]

        pad_x, pad_y = int(font_size * 0.6), int(font_size * 0.35)
        margin       = int(W * 0.025)
        box_w = tw + pad_x * 2
        box_h = th + pad_y * 2

        corners = {
            "bottom_right": (W - box_w - margin, H - box_h - margin),
            "bottom_left":  (margin,              H - box_h - margin),
            "top_right":    (W - box_w - margin,  margin),
            "top_left":     (margin,              margin),
        }
        x0, y0 = corners[position]
        x1, y1 = x0 + box_w, y0 + box_h

        overlay = Image.new("RGBA", img.size, (0, 0, 0, 0))
        draw    = ImageDraw.Draw(overlay)
        radius  = int(box_h * 0.35)
        draw.rounded_rectangle([x0, y0, x1, y1], radius=radius, fill=box_color)
        draw.text((x0 + pad_x, y0 + pad_y), WATERMARK_TEXT, font=font, fill=text_color)

        img = Image.alpha_composite(img.convert("RGBA"), overlay).convert("RGB")

        out = io.BytesIO()
        img.save(out, format="JPEG", quality=92)
        out.seek(0)
        out.name = "cover.jpg"
        return out

    except Exception as e:
        logger.warning(f"[WATERMARK] Failed: {e}")
        return None


async def _upload_cover(bot, image_url: str) -> str | None:
    """
    COVER_WATERMARK=True  → watermark lagao aur Telegram pe upload karo
    COVER_WATERMARK=False → plain URL hi save karo
    """
    try:
        if not COVER_WATERMARK:
            logger.info(f"[COVER] Watermark OFF — plain URL save ho raha hai")
            return image_url

        wm_image = await _add_watermark(image_url)
        if not wm_image:
            return image_url

        msg = await bot.send_photo(chat_id=BIN_CHANNEL, photo=wm_image)
        file_id = msg.photo.file_id
        logger.info(f"[COVER] Watermarked cover uploaded | file_id={file_id}")
        return file_id
    except Exception as e:
        logger.warning(f"[COVER] Upload failed: {e}")
        return image_url



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






# =========================================================
# 1. GLOBAL CONFIGURATIONS & CLEAN MAPS
# =========================================================
RELEASE_TAG = "~[Tokyo_Updates]"

LANGUAGE_ALIASES = {
    "Hindi": [r'\bhindi\b', r'\bhin\b'],
    "English": [r'\benglish\b', r'\beng\b'],
    "Tamil": [r'\btamil\b', r'\btam\b'],
    "Telugu": [r'\btelugu\b', r'\btel\b'],
    "Malayalam": [r'\bmalayalam\b', r'\bmal\b'],
    "Kannada": [r'\bkannada\b', r'\bkan\b'],
    "Punjabi": [r'\bpunjabi\b', r'\bpan\b'],
    "Bengali": [r'\bbengali\b', r'\bben\b'],
    "Gujarati": [r'\bgujarati\b', r'\bguj\b'],
    "Marathi": [r'\bmarathi\b'],
    "Korean": [r'\bkorean\b', r'\bkor\b', r'\bk-drama\b'],
    "Japanese": [r'\bjapanese\b', r'\bjap\b'],
    "Chinese": [r'\bchinese\b', r'\bmandarin\b', r'\bchi\b']
}

OTT_MAP = {
    "NF": ["netflix", "nf"],
    "AMZN": ["amazon", "amzn", "prime"],
    "DSNP": ["hotstar", "disney", "dsnp"],
    "JIO": ["jiocinema", "jio"],
    "ZEE5": ["zee5", "zee"],
    "LIV": ["sonyliv", "liv"],
    "HMAX": ["hbomax", "hbo", "hmax"],
    "APTV": ["apple", "aptv", "apple tv"]
}


# =========================================================
# 2. SMART DYNAMIC TITLE EXTRACTOR (WITH AUTO-JUNK CLEANER)
# =========================================================
def extract_pure_title(original_name):
    clean_name = re.sub(r'^\[.*?\]', '', original_name).strip() 
    clean_name = re.sub(r'^@\w+[\s_\-–]*', '', clean_name).strip()
    clean_name = re.sub(r'[@\[\]\(\)_]+', ' ', clean_name)
    clean_name = re.sub(r"[._\-]+", " ", clean_name)
    # 🔥 NEW FIX: "Us BoBFiles" ya aise aur uploader tags ko shuru se remove karna
    uploader_tags = [r'(?:join\s+)?us\s*bobfiles'] # Aap yahan aur bhi naam add kar sakte hain comma lagakar
    uploader_cleanup = r'^(?:(?:' + '|'.join(uploader_tags) + r')[\s]*)+'
    clean_name = re.sub(uploader_cleanup, '', clean_name, flags=re.IGNORECASE).strip()

    # 🔥 SUPER FIX: Shuru me aane wale har tarah ke faltu tags ko filter karna
    prefix_tags = [
        r's\d{1,2}(?:-\d{1,2})?', r'e\d{1,2}(?:-\d{1,2})?', # Season & Episode
        r'\d{3,4}p', r'4k',                                 # Resolution
        r'(?:19|20)\d{2}',                                  # Year (Agar saal shuru me ho)
        r'combined', r'complete',                           # Status
        r'dual[\s\-]?audio', r'multi[\s\-]?audio',          # Audio Types
        r'hindi', r'english', r'tamil', r'telugu', r'malayalam', r'kannada', r'bengali', r'marathi', r'korean', r'japanese', r'chinese', # All Languages
        r'web[\-\s]?dl', r'web[\-\s]?rip', r'hdrip', r'bluray', r'brrip', r'dvdrip', r'camrip', r'hdts', r'hdcam', # Sources
        r'x264', r'x265', r'hevc', r'10bit', r'aac', r'eac3', r'5\.1', r'7\.1', # Codec & Sound
        r'download', r'watch', r'full[\s\-]?movie', r'web[\s\-]?series', r'new', r'latest', # Fluff words
        r'netflix', r'amazon', r'prime', r'hotstar', r'zee5', r'sonyliv', r'jio', r'voot', r'altbalaji' # OTT platforms
    ]
    
    prefix_cleanup = r'^(?:(?:' + '|'.join(prefix_tags) + r')[\s_\-]*)+'
    clean_name = re.sub(prefix_cleanup, '', clean_name, flags=re.IGNORECASE).strip()

    stop_anchors = [
        r'\bs\d{1,2}\s?e\d{1,2}\b', 
        r'\bs\d{1,2}\b', 
        r'\be\d{1,2}\b', 
        r'\b(19|20)\d{2}\b',                                      
        r'\b\d{3,4}p\b', 
        r'\b4k\b',
        r'\bweb[\s\-]?dl\b', 
        r'\bwebrip\b', 
        r'\bbluray\b', 
        r'\bhdrip\b'
    ]

    lower_name = clean_name.lower()
    first_match_index = len(clean_name)

    for anchor in stop_anchors:
        match = re.search(anchor, lower_name)
        if match and match.start() < first_match_index:
            if match.start() > 2: 
                first_match_index = match.start()

    if first_match_index < len(clean_name):
        pure_title = clean_name[:first_match_index].strip()
    else:
        pure_title = clean_name.strip()

    for lang, aliases in LANGUAGE_ALIASES.items():
        for alias in aliases:
            pure_title = re.sub(rf'\b{re.escape(alias)}\b$', '', pure_title, flags=re.IGNORECASE).strip()

    return re.sub(r'\s+', ' ', pure_title).strip()

# =========================================================
# 3. CUSTOM SEASON & EPISODE NORMALIZER (ULTRA PRO MAX)
# =========================================================
def normalize_season_episode(text):
    text = text.lower()

    # 1. Dot Separation Fix (s01.e01 -> s01 e01)
    text = re.sub(r'\bs(\d{1,2})\.e(\d{1,2})\b', r's\1 e\2', text)
    
    # 2. [NEW] Chipke hue episodes (S01E01E02 -> S01 E01-02)
    text = re.sub(r'\bs(\d{1,2})e(\d{1,2})e(\d{1,2})\b', lambda m: f"s{int(m.group(1)):02d} e{int(m.group(2)):02d}-{int(m.group(3)):02d}", text)

    # 3. [NEW] Text based separators (S01 E01 & E02, S01 E01 to 05, S01 E01 and E02)
    text = re.sub(r'\bs(\d{1,2})[\s\-]*e(\d{1,2})[\s\-_]*(?:&|and|to)[\s\-_]*e?(\d{1,2})\b', lambda m: f"s{int(m.group(1)):02d} e{int(m.group(2)):02d}-{int(m.group(3)):02d}", text)

    # 4. Standard Multi-Episode Range (S01E01-02, S01 E01 E02, S01E01-E02)
    text = re.sub(r'\bs(\d{1,2})[\s\-]*e(\d{1,2})[\s\-_]+e?(\d{1,2})\b', lambda m: f"s{int(m.group(1)):02d} e{int(m.group(2)):02d}-{int(m.group(3)):02d}", text)

    # 5. [NEW] Multi-Episode in 'X' Format (1x01-02, 1x01-1x02)
    text = re.sub(r'\b(\d{1,2})[xX](\d{1,2})[\s\-_]+(?:\d{1,2}[xX])?(\d{1,2})\b', lambda m: f"s{int(m.group(1)):02d} e{int(m.group(2)):02d}-{int(m.group(3)):02d}", text)

    # 6. Bulk Season Range (S01-S02 -> S01-02)
    text = re.sub(r'\bs(\d{1,2})[\s\-]+s?(\d{1,2})\b', lambda m: f"s{int(m.group(1)):02d}-{int(m.group(2)):02d}", text)

    # 7. Ep Brackets Range (Ep [01-02], Ep 01-02)
    text = re.sub(r'\bep[\s\-_]*\(?(\d+)[\s\-–]+(\d+)\)?', lambda m: f"e{int(m.group(1)):02d}-{int(m.group(2)):02d}", text)
    
    # 8. [NEW] Pure E01-E02 Range Fix (e01-e02 -> e01-02)
    text = re.sub(r'\be(\d{1,2})[\s\-–]+e(\d{1,2})\b', lambda m: f"e{int(m.group(1)):02d}-{int(m.group(2)):02d}", text)

    # 9. Pure Number Episode Range (01-02) if both under 60
    text = re.sub(r'\b(\d{2})[\s\-–]+(\d{2})\b', lambda m: f"e{int(m.group(1)):02d}-{int(m.group(2)):02d}" if int(m.group(1)) < 60 and int(m.group(2)) < 60 else m.group(0), text)

    # 10. Standard 1x05 Formats
    text = re.sub(r'\b(\d{1,2})[xX](\d{1,2})\b', lambda m: f"s{int(m.group(1)):02d} e{int(m.group(2)):02d}", text)

    # 11. Standard Season Keywords
    text = re.sub(r'\b(?:season|s)[\s\-_]*(\d{1,2})\b', lambda m: f"s{int(m.group(1)):02d}", text)

    # 12. Standard Episode Keywords
    text = re.sub(r'\b(?:episode|ep|e)[\s\-_]*(\d{1,2})\b', lambda m: f"e{int(m.group(1)):02d}", text)

    # 13. Direct S01E01 Merge Fix
    text = re.sub(r'\bs(\d{2})e(\d{2})\b', r's\1 e\2', text)

    text = re.sub(r'\s+', ' ', text).strip()
    return text.upper()


# =========================================================
# 4. UPDATED DATA EXTRACTOR
# =========================================================
def extract_languages_quality(text_to_scan):
    scan_lower = text_to_scan.lower()

    year_match = re.search(r'\b(19\d{2}|20[0-2]\d)\b', text_to_scan)
    year = year_match.group(1) if year_match else None

    # Pure Episode aur Season Pack Extraction Logic
    normalized_se = normalize_season_episode(text_to_scan) # <-- यह लाइन मिसिंग थी, इसे वापस जोड़ दिया है
    season_episode = None

    full_match = re.search(r'\b(S\d{2})\s(E\d{2}(?:-\d{2})?)\b', normalized_se)
    if full_match:
        season_episode = full_match.group(0)
    else:
        s_match = re.search(r'\b(S\d{2}(?:-\d{2})?)\b', normalized_se)
        e_match = re.search(r'\b(E\d{2}(?:-\d{2})?)\b', normalized_se)

        if s_match and e_match:
            season_episode = f"{s_match.group(1)} {e_match.group(1)}"
        elif e_match:
            season_episode = e_match.group(1)
        elif s_match:
            season_episode = s_match.group(1)
    # Complete / Combined Check
    series_status = None
    status_match = re.search(r'\b(combined|complete)\b', scan_lower)
    if status_match:
        series_status = "COMBINED" if status_match.group(1) == "combined" else "COMPLETE"

    resolution = None
    res = re.search(r'(4320p|2160p|1440p|1080p|720p|480p|360p|240p|4k)', scan_lower)
    if res:
        resolution = "2160P" if res.group(1) == "4k" else res.group(1).upper()

    source = None
    SOURCES = {
        "WEB-DL": ["web-dl", "webdl", "web dl"],
        "WEBRip": ["webrip", "web rip"],
        "HDRip": ["hdrip"],
        "BluRay": ["bluray"],
        "BRRip": ["brrip", "bdrip"],
        "DVDRip": ["dvdrip"],
        "DVDScr": ["dvdscr", "scr", "dvd-scr"],
        "REMUX": ["remux"],
        "Digital": ["digital"],
        "HDTC": ["hdtc", "hd-tc", "telecine"],       
        "HDTS": ["hdts", "hd-ts", "ts", "telesync"], 
        "HDCAM": ["hdcam", "hd-cam", "hd cam"],
        "CAMRip": ["cam", "camrip", "cinema"],
        "PreDVD": ["predvd", "pre dvd"]
    }
    for src, aliases in SOURCES.items():
        for a in aliases:
            if a in scan_lower:
                source = src  
                break
        if source:
            break

    ott_tag = None
    for platform, aliases in OTT_MAP.items():
        for a in aliases:
            if re.search(r'\b' + re.escape(a) + r'\b', scan_lower) or a in scan_lower:
                ott_tag = platform
                break
        if ott_tag:
            break

    extra_tags = []
    TAGS_MAP = {
        "HEVC X265": ["x265", "hevc"], 
        "AVC X264": ["x264", "avc"], 
        "10BIT": ["10bit"], 
        "AAC": ["aac"],
        "Dolby 5.1": ["5.1", "6ch", "dd+", "eac3", "dts"], 
        "Atmos 7.1": ["atmos", "truehd", "7.1", "8ch"],
        "ESubs": ["esub", "esubs"], 
        "HardSubs": ["hsub", "hsubs"],
        "MSubs": ["msub", "msubs"]
    }
    for tag, aliases in TAGS_MAP.items():
        for a in aliases:
            if a in scan_lower:
                extra_tags.append(tag)
                break

    # 🔥 NAYE TAGS YAHAN ADD HUE HAIN (ORGs, HC, DS4K, Multi)
    custom_qualifiers = []
    target_keywords = [
        r'\bleak\b', r'\bstudio\b', r'\bdub\b', r'\bdubbed\b',
        r'\bunofficial\b', r'\bre[\s\-]?dub(?:bed)?\b', r'\bfan[\s\-]?dub(?:bed)?\b', 
        r'\bhq[\s\-]?dub(?:bed)?\b', r'\bstudio[\s\-]?dub(?:bed)?\b', r'\bclean[\s\-]?audio\b',
        r'\boriginal[\s\-]?audio\b', r'\bline[\s\-]?audios?\b', r'\bline\b', r'\bmultiplex\b',
        r'\bextended\b', r'\bextendded\b', r'\buncut\b', r'\bdirector\'s[\s\-]?cut\b', 
        r'\bdc\b', r'\bimax\b', r'\bremastered\b', r'\bremaster\b', r'\bproper\b', 
        r'\bpre[\s\-]?release\b', r'\bprerelease\b', r'\bworkprint\b', r'\bwp\b', 
        r'\bspecial[\s\-]?edition\b', r'\btheatrical\b', r'\banniversary\b',
        r'\bhq\b', r'\bhdr\b', r'\bdolby[\s\-]?vision\b', r'\bdv\b', r'\bsdr\b', 
        r'\bhybrid\b', r'\bpatched\b', r'\bcorrected\b', r'\bsoftsub\b',
        r'\bv[1-4]\b',
        r'\borgs?\b', r'\bhc\b', r'\bds4k\b', r'\bmulti\b'
    ]

    combined_pattern = re.compile('|'.join(target_keywords), re.IGNORECASE)
    found_matches = []

    for match in combined_pattern.finditer(scan_lower):
        start_pos = match.start()
        end_pos = match.end()
        original_string = text_to_scan[start_pos:end_pos].strip()
        words = re.split(r'[@\[\]\(\)_\.\-\s]+', original_string)

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

    # === Language Scan ===
    languages = []
    for lang, aliases in LANGUAGE_ALIASES.items():
        for a in aliases:
            if re.search(a, scan_lower):
                languages.append(lang)
                break

    if "Dual Audio" not in languages and "Multi Audio" not in languages:
        if len(languages) == 2 or r'\bdual\b' in scan_lower or r'\bdual\s?audio\b' in scan_lower:
            languages.append("Dual Audio")
        elif len(languages) > 2 or r'\bmulti\b' in scan_lower or r'\bmulti\s?audio\b' in scan_lower:
            languages.append("Multi Audio")

    kbps_tag = None
    kbps = re.search(r'(\d{2,4}\s?kbps)', scan_lower)
    if kbps:
        kbps_tag = kbps.group(1).upper().replace(" ", "")

    # 🔥 NEW: Extracting Part at the end (Ignores Season/Episode)
    file_part = None
    part_match = re.search(r'\b(?:part|pt)[\.\-_]*\s?(\d{1,4})\b', scan_lower)
    if part_match:
        file_part = f"Part {part_match.group(1).zfill(2)}"

    return {
        "year": year, "season_episode": season_episode, "languages": languages,
        "resolution": resolution, "source": source, "ott": ott_tag, 
        "extra_tags": extra_tags, "kbps": kbps_tag, 
        "custom_qualifiers": custom_qualifiers,
        "series_status": series_status,
        "file_part": file_part
    }

# =========================================================
# 5. MAIN ASYNC SAVE PIPELINE
# =========================================================
async def save_file(media, bot=None):
    try:
        file_id, file_ref = unpack_new_file_id(media.file_id)
        original_name = str(media.file_name or "Unnamed File")
        base_name, ext = os.path.splitext(original_name)

        text_to_scan = f"{original_name} {getattr(media, 'caption', '') or ''}"

        extracted = extract_languages_quality(text_to_scan)
        cleaned_title = extract_pure_title(base_name)

        formatted_words = []
        for word in cleaned_title.split():
            if len(word) > 1:
                formatted_words.append(word[0].upper() + word[1:])
            else:
                formatted_words.append(word.upper())
        final_title = " ".join(formatted_words)

        parts = []
        def add_unique(value):
            if value and str(value).lower() not in " ".join(map(str, parts)).lower():
                parts.append(value)

        # === STRICT SEQUENCE ASSEMBLER ===
        # [1] Title
        if final_title:
            add_unique(final_title)

        # [2] Season & Episode
        if extracted["season_episode"]:
            add_unique(extracted["season_episode"])

        # [2.5] Series Status
        if extracted["series_status"]:
            add_unique(extracted["series_status"])

        # [3] Release Year
        if extracted["year"]:
            add_unique(extracted["year"])

        # [4] Video Resolution
        if extracted["resolution"]:
            add_unique(extracted["resolution"])

        # [5] Audio Languages
        for lang in extracted["languages"]:
            add_unique(lang)

        # [6] Custom Qualifiers
        for qual in extracted["custom_qualifiers"]:
            add_unique(qual)

        # [7] Color Depth
        if "10BIT" in extracted["extra_tags"]:
            add_unique("10BIT")

        # [8] OTT Platform Tag
        if extracted["ott"]:
            if extracted["ott"] not in parts:
                parts.append(extracted["ott"])

        # [9] Source Type
        if extracted["source"]:
            add_unique(extracted["source"])

        # [10] Video Codec
        for vcodec in ["HEVC X265", "AVC X264"]:
            if vcodec in extracted["extra_tags"]:
                add_unique(vcodec)

        # [11] Audio Codec & Channels
        for acodec in ["Atmos 7.1", "Dolby 5.1", "AAC"]:
            if acodec in extracted["extra_tags"]:
                add_unique(acodec)

        # [12] Subtitles
        for sub in ["ESubs", "HardSubs", "MSubs"]:
            if sub in extracted["extra_tags"]:
                add_unique(sub)

        # [13] Audio Bitrate
        if extracted["kbps"]:
            add_unique(extracted["kbps"])

        # 🔥 [13.5] File Part (Chunks aakhir mein judenge)
        if extracted.get("file_part"):
            add_unique(extracted["file_part"])

        # [14] Branding Signature
        parts = [p for p in parts if p and "Tokyo_Updates" not in str(p)]
        parts.append(RELEASE_TAG)

        # Assembly
        file_name = " ".join(map(str, parts)).strip()
        file_name = re.sub(r'\s+', ' ', file_name)
        file_name = file_name + ext.lower()
        file_name = re.sub(r'\s+\.', '.', file_name)

        # ============================================================
        # 🖼️ COVER IMAGE — fetch + watermark + upload
        # ============================================================
        cover_url = None
        if COVERX:
            try:
                # Same title ki existing cover check karo (reuse)
                existing = await Media.find_one({"file_name": {"$regex": re.escape(final_title), "$options": "i"}, "cover": {"$ne": None}})
                if not existing:
                    existing = await Media2.find_one({"file_name": {"$regex": re.escape(final_title), "$options": "i"}, "cover": {"$ne": None}})

                if existing and existing.cover:
                    cover_url = existing.cover
                    logger.info(f"[COVER] Reused existing cover for '{final_title}'")
                else:
                    # Year bhi saath pass karo accurate match ke liye
                    search_query = f"{final_title} {extracted['year']}" if extracted.get("year") else final_title
                    raw_url = await _fetch_cover_url(search_query)
                    if raw_url:
                        if bot:
                            # Watermark lagao aur Telegram pe upload karo
                            cover_url = await _upload_cover(bot, raw_url)
                        else:
                            # bot nahi mila toh plain URL hi save karo
                            cover_url = raw_url
                        logger.info(f"[COVER] Fetched new cover for '{final_title}'")
                    else:
                        logger.info(f"[COVER] No cover found for '{final_title}'")
            except Exception as e:
                logger.warning(f"[COVER] Error during cover lookup: {e}")

        # DB Commits
        record = Media(
            file_id=file_id,
            file_ref=file_ref,
            file_name=file_name,
            file_size=media.file_size,
            file_type=media.file_type,
            mime_type=media.mime_type,
            caption=getattr(media.caption, "html", None) if media.caption else None,
            cover=cover_url  # 🖼️ Cover URL save ho raha hai
        )
        await record.commit()
        logger.info(f"[SAVED] {file_name} | cover={'yes' if cover_url else 'no'}")
        return True, 1

    except DuplicateKeyError:
        return False, 0
    except Exception as e:
        logger.exception(f"[ERROR] {e}")
        return False, 3



#__________________________________
# FOR GET SEARCH RESULT THIS CODE UPDATE BY 🅰️NKIT MEENA 
#__________________________________

# ----------------- 1. कॉन्फ़िगरेशन और रैंकिंग डिक्शनरी -----------------

SOURCE_ORDER = {
    "bluray": 15, "blu-ray": 15, "bdrip": 14, "brrip": 14, "bdremux": 14, "remux": 14,
    "web-dl": 13, "webdl": 13, "web dl": 13, "webrip": 12, "web rip": 12, "digital": 11, "web": 11,
    "hdtv": 10, "hdrip": 9, "dvdrip": 8, "dvd": 7,
    "predvd": 6, "pre-dvd": 6, "pre dvd": 6, "pre": 5, "dvdscr": 2, "dvd-scr": 2, "scr": 2,          
    "hdts": 4, "hd-ts": 4, "ts": 4, "telesync": 4, "hdtc": 3, "hd-tc": 3, "tc": 3, "telecine": 3,     
    "hdcam": 2, "hd-cam": 2, "hd cam": 2, "camrip": 2, "cam": 1, "cinema": 1        
}

QUALITY_ORDER = {
    "4320p": 8, "8k": 8, "2160p": 7, "4k": 7, "1440p": 6, "1080p": 5, "720p": 4, "480p": 3, "360p": 2, "240p": 1, "144p": 0
}

# ----------------- 2. एक्सट्रैक्शन फंक्शंस -----------------

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
    s = re.search(r"\bs(?:eason)?[\s._-]*(\d{1,2})", name)
    e = re.search(r"\be(?:pisode|p)?[\s._-]*(\d{1,2})", name)
    
    season = int(s.group(1)) if s else 0
    episode = int(e.group(1)) if e else 0
    return season, episode

# ----------------- 3. क्वेरी नॉर्मलाइजेशन और स्मार्ट एक्सपेंशन -----------------

def normalize_for_search(text):
    text = text.lower()
    text = re.sub(r'(\d+)[xX](\d+)', lambda m: f"s{int(m.group(1)):02d} e{int(m.group(2)):02d}", text)
    text = re.sub(r'\b(?:season|s)[\s\-]*(\d+)', lambda m: f"s{int(m.group(1)):02d}", text)
    text = re.sub(r'\b(?:episode|ep|e)[\s\-]*(\d+)', lambda m: f"e{int(m.group(1)):02d}", text)
    text = re.sub(r'\bs(\d+)e(\d+)', lambda m: f"s{int(m.group(1)):02d} e{int(m.group(2)):02d}", text)
    return re.sub(r"\s+", " ", text).strip()

def expand_query(query):
    query = query.lower()
    patterns = [query]

    # सीज़न/एपिसोड टैग्स को हटाकर साफ़ टाइटल निकालना
    title = re.sub(r'\b(s\d+|e\d+|season[\s-]*\d+|episode[\s-]*\d+|ep[\s-]*\d+)\b', '', query)
    title = re.sub(r'[\s._-]+', ' ', title).strip()

    s_match = re.search(r"\bs(\d{1,2})|season[\s-]*(\d{1,2})", query)
    e_match = re.search(r"\be(\d{1,2})|episode[\s-]*(\d{1,2})|ep[\s-]*(\d{1,2})", query)

    s_num = int(s_match.group(1) or s_match.group(2)) if s_match else None
    e_num = int(e_match.group(1) or e_match.group(2) or e_match.group(3)) if e_match else None

    if s_num and e_num:
        variations = [
            f"s{s_num:02d}e{e_num:02d}", f"s{s_num:02d} e{e_num:02d}", 
            f"s{s_num}e{e_num}", f"s{s_num:02d}", f"e{e_num:02d}"
        ]
        for v in variations:
            patterns.append(f"{title} {v}".strip())
    elif s_num:
        variations = [f"s{s_num:02d}", f"s{s_num}", f"season {s_num}"]
        for v in variations:
            patterns.append(f"{title} {v}".strip())
    elif e_num:
        variations = [f"e{e_num:02d}", f"e{e_num}", f"episode {e_num}"]
        for v in variations:
            patterns.append(f"{title} {v}".strip())

    return list(set(patterns))

# ----------------- 4. मुख्य सर्च और सॉर्टिंग फंक्शन -----------------

async def get_search_results(chat_id, query, file_type=None, max_results=10, offset=0, filter=False):
    if chat_id:
        settings = await get_settings(int(chat_id))
        max_results = 10 if settings.get("max_btn") else int(MAX_B_TN)

    # यूज़र की मूल सर्च क्वेरी को संभाल कर रखना
    original_query = str(query).lower().strip()

    if not isinstance(query, list):
        query = normalize_for_search(query)
        query = expand_query(query)[:5]

    regex_list = []
    for q in query:
        q = q.strip()
        if not q: continue
        
        # 🔥 यहाँ सुधार है: शब्दों को अलग करके उनके बीच वाइल्डकार्ड (.*) लगा रहे हैं।
        # इससे "jana", "nayagan" और "hindi" के बीच में कुछ भी लिखा हो (जैसे साल या क्वालिटी), मोंगोडीबी उसे ढूँढ लेगा।
        words = q.split()
        pattern = r'.*'.join(re.escape(w) for w in words)
        
        try:
            regex_list.append(re.compile(pattern, re.IGNORECASE))
        except re.error:
            continue

    conditions = []
    for r in regex_list:
        conditions.append({"file_name": r})
        if USE_CAPTION_FILTER:
            conditions.append({"caption": r})

    filter_mongo = {"$or": conditions}
    if file_type:
        filter_mongo["file_type"] = file_type

    total_results = await Media.count_documents(filter_mongo)
    if MULTIPLE_DB:
        total_results += await Media2.count_documents(filter_mongo)

    fetch_limit = max(100, offset + max_results) 
    
    files = await Media.find(filter_mongo).sort("$natural", -1).limit(fetch_limit).to_list(length=fetch_limit)
    if MULTIPLE_DB and len(files) < fetch_limit:
        remaining = fetch_limit - len(files)
        files2 = await Media2.find(filter_mongo).sort("$natural", -1).limit(remaining).to_list(length=remaining)
        files.extend(files2)

    is_series = any(re.search(r"s\d{1,2}.*e\d{1,2}", str(file.file_name).lower()) for file in files)

    # 🎯 स्मार्ट सॉर्टिंग लॉजिक (फर्स्ट वर्ड/क्वेरी मैचिंग को सबसे ऊपर रखने के लिए)
    # हम सिर्फ पहले शब्द को भी चेक करेंगे ताकि "jana nayagan hindi" सर्च करने पर "jana" से शुरू होने वाली ही पहले आए।
    first_word = original_query.split()[0] if original_query.split() else original_query

    if is_series:
        files = sorted(
            files,
            key=lambda x: (
                # 1. क्या फ़ाइल का नाम यूज़र की पूरी क्वेरी से शुरू होता है?
                not (re.match(rf"^[\s._\-\[\(]*{re.escape(original_query)}", x.file_name.lower())),
                
                # 1b. बैकअप: क्या फ़ाइल का नाम कम से कम पहले शब्द से शुरू होता है?
                not (re.match(rf"^[\s._\-\[\(]*{re.escape(first_word)}", x.file_name.lower())),

                # 2. न्यूमेरिकल रेटिंग (लेटेस्ट सीज़न/एपिसोड)
                -extract_season_episode(x.file_name)[0],      
                -extract_season_episode(x.file_name)[1],      

                # 3. टेक्स्ट पैटर्न मैचिंग 
                not (re.search(r'\bs\d{1,2}e\d{1,2}\b', x.file_name.lower())), 
                not (re.search(r'\bs\d{1,2}\s*e\d{1,2}\b', x.file_name.lower())), 

                # 4. क्वालिटी और सोर्स
                -extract_quality(x.file_name),                
                -extract_source(x.file_name),                 
                x.file_id
            )
        )
    else:
        files = sorted(
            files,
            key=lambda x: (
                # 1. क्या फ़ाइल का नाम यूज़र की पूरी क्वेरी से शुरू होता है?
                not (re.match(rf"^[\s._\-\[\(]*{re.escape(original_query)}", x.file_name.lower())),
                
                # 1b. बैकअप: क्या फ़ाइल का नाम कम से कम पहले शब्द से शुरू होता है?
                not (re.match(rf"^[\s._\-\[\(]*{re.escape(first_word)}", x.file_name.lower())),

                # बाकी पुराने रूल्स
                -extract_quality(x.file_name),
                -extract_source(x.file_name),
                x.file_id
            )
        )

    paginated_files = files[offset:offset + max_results]

    next_offset = offset + len(paginated_files)
    if next_offset >= total_results or len(paginated_files) == 0:
        next_offset = ""

    return paginated_files, next_offset, total_results


#_________________________________

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

async def update_cover_url(file_id: str, cover_url: str) -> bool:
    """
    Kisi existing file ka cover URL manually update karo.
    Admin command ya bulk update ke liye useful hai.
    """
    try:
        result = await Media.collection.update_one(
            {"_id": file_id},
            {"$set": {"cover": cover_url}}
        )
        if result.modified_count:
            return True
        result2 = await Media2.collection.update_one(
            {"_id": file_id},
            {"$set": {"cover": cover_url}}
        )
        return bool(result2.modified_count)
    except Exception as e:
        logger.error(f"[COVER] update_cover_url error: {e}")
        return False


async def get_cover_url(file_id: str) -> str | None:
    """
    File ID se cover URL fetch karo DB mein se.
    File send karte waqt use ho sakta hai.
    """
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
