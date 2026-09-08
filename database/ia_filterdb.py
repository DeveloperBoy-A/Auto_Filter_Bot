import logging
import os
import re
import io
import asyncio
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
from utils import get_settings, save_group_settings, remove_prefix_garbage, temp
from datetime import datetime, timedelta

# Lazy import to avoid circular imports
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


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
# 🖌️ CLOUDINARY WATERMARK RENDERING (0% RAM/CPU)
# ==========================================
def get_cloud_watermark_url(original_tmdb_url: str) -> str:
    """बिना इमेज डाउनलोड किए डायनामिक रूप से Cloudinary के ज़रिए रैंडम वॉटरमार्क लगाता है।"""
    import random
    if not original_tmdb_url:
        return None
        
    # 👇 यहाँ "your_cloud_name" को हटाकर अपना असली Cloud Name डालें
    cloud_name = "ci2woc0d"  
    
    watermark_text = "%5B%40Tokyo_Updates%5D" 
    
    positions = {
        "bottom_right": "g_south_east,x_30,y_50",
        "bottom_left": "g_south_west,x_30,y_50",
        "top_right": "g_north_east,x_30,y_30",
        "bottom_center": "g_south,y_50"
    }
    
    styles = [
        ("FFFFFF", "654321D0"), ("FFFFFF", "141414D0"), 
        ("FFFFFF", "8B0000D0"), ("FFFFFF", "00467FD0"), 
        ("FFFFFF", "226422D0"), ("FFFFFF", "500078D0"), 
        ("FFFFFF", "B45A00D0"), ("000000", "FFD700D0"),
    ]
    
    pos_key = random.choice(list(positions.keys()))
    gravity = positions[pos_key]
    text_color, box_color = random.choice(styles)
    
    # 🖼️ लैंडस्केप साइज़ और वॉटरमार्क लेयर
    resize_layer = "w_1280,h_720,c_scale"
    wm_layer = f"l_text:Arial_40_bold:{watermark_text},co_rgb:{text_color},b_rgb:{box_color},{gravity}"
    
    return f"https://res.cloudinary.com/{cloud_name}/image/fetch/{resize_layer}/{wm_layer}/{original_tmdb_url}"


# =========================================================
# 🗄️ MULTI-DATABASE SETUP & ROUTING
# =========================================================
# Global DB cache (per-database, keyed by db object id)
_db_stats_cache: Dict[int, dict] = {}

# Safe threshold (MB) before hitting the free-tier ~512MB cap. 
# Once a database crosses this, new files route to the next configured database.
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


# =========================================================
# MEDIA MODELS
# =========================================================
def create_media_model(instance_obj):
    """Helper to dynamically generate Document schemas for multiple databases."""
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
        file_date = fields.DateTimeField(allow_none=True) # Upload timestamp for sorting
        title = fields.StrField(allow_none=True)          # Extracted title for cover reuse
        year = fields.StrField(allow_none=True)           # Release year for cover reuse

        class Meta:
            indexes = ("$file_name", "media_type", "-file_date", "title", "year")
            collection_name = COLLECTION_NAME

    return DynamicMedia

Media = create_media_model(instance)
Media2 = create_media_model(instance2)
Media3 = create_media_model(instance3)
Media4 = create_media_model(instance4)
Media5 = create_media_model(instance5)

# Slice lists down to the actual number of configured databases (TOTAL_DATABASES)
_ALL_MEDIA_CLASSES = [Media, Media2, Media3, Media4, Media5]
_ALL_DB_CLIENTS = [client, client2, client3, client4, client5]

MEDIA_DBS = _ALL_MEDIA_CLASSES[:TOTAL_DATABASES]
DB_CLIENTS = _ALL_DB_CLIENTS[:TOTAL_DATABASES]
_DB_LABELS = ["Primary DB", "Secondary DB", "Tertiary DB", "Quaternary DB", "Quinary DB"]


async def check_db_size(db_instance):
    """Returns the current logical + index size (MB) for the given motor Database object."""
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
        db_logical_size_mb = stats["dataSize"] / (1024 * 1024)
        db_index_size_mb = stats["indexSize"] / (1024 * 1024)
        db_size_mb = db_logical_size_mb + db_index_size_mb

        _db_stats_cache[key] = {"timestamp": now, "size_mb": db_size_mb}
        return db_size_mb
    except Exception as e:
        logger.error(f"Error Checking Database Size: {e}")
        return 0


async def get_active_media_db():
    """Returns the first configured database that has space under DB_SIZE_LIMIT_MB."""
    for media_cls in MEDIA_DBS:
        size_mb = await check_db_size(media_cls.collection.database)
        if size_mb < DB_SIZE_LIMIT_MB:
            return media_cls
    return MEDIA_DBS[-1]


async def delete_file_by_id(file_id: str) -> int:
    """Deletes a file by its ID from the configured DBs."""
    for media_cls in MEDIA_DBS:
        result = await media_cls.collection.delete_one({"_id": file_id})
        if result.deleted_count:
            return result.deleted_count
    return 0


async def delete_files_by_query(query: dict) -> int:
    """Deletes files matching the query across all configured DBs."""
    total = 0
    for media_cls in MEDIA_DBS:
        result = await media_cls.collection.delete_many(query)
        total += result.deleted_count
    return total

# =========================================================
# FILE ID HELPERS
# =========================================================
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
# GLOBAL CONSTANTS & MAPPINGS
# =========================================================
RELEASE_TAG = "~[Tokyo_Updates]"

LANGUAGE_ALIASES = {
    "Hindi": [r'\bhindi\b', r'\bhin\b', r'\bbhin\b'],
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
    "NF": ["netflix", "nf"],
    "AMZN": ["amazon", "amzn", "prime"],
    "DSNP": ["hotstar", "disney", "dsnp"],
    "JIO": ["jiocinema", "jio", "jc"],
    "ZEE5": ["zee5", "zee"],
    "LIV": ["sonyliv", "liv"],
    "HMAX": ["hbomax", "hbo", "hmax", "max"],
    "APTV": ["apple", "aptv", "apple tv"],
    "HULU": ["hulu"],
    "PMNT": ["paramount", "pmnt", "paramount+"],
    "PEACOCK": ["peacock", "pcok"],
    "AHA": ["aha", "aha video"],
    "SUNNXT": ["sunnxt", "sun nxt"],
    "MX": ["mx player", "mxplayer", "mx"],
    "ALTB": ["altbalaji", "alt"],
    "VOOT": ["voot"],
    "LIONSGATE": ["lionsgate", "lions gate", "lionsgateplay"]
}


# =========================================================
# SMART DYNAMIC TITLE EXTRACTOR
# =========================================================
def extract_pure_title(original_name):
    """Cleans a raw file string to extract just the pure movie or series title."""
    clean_name = re.sub(r'^\[.*?\]', '', original_name).strip() 
    clean_name = re.sub(r'^@\w+[\s_\-–]*', '', clean_name).strip()
    
    # 🚀 SYMBOL SANITIZER
    clean_name = re.sub(r'[!@#$%^&*()_+={}\[\]:;<>,.?/\\|~`–—―•✓™®©🎬🗃️✨🔥]+', ' ', clean_name)
    clean_name = re.sub(r'[@\[\]\(\)_]+', ' ', clean_name)
    clean_name = re.sub(r"[._\-]+", " ", clean_name)

    # Remove URLs and Telegram links
    clean_name = re.sub(r'(?:https?://)?(?:www\.)?[-a-zA-Z0-9@:%._\+~#=]{1,256}\.[a-zA-Z0-9()]{1,6}\b(?:[-a-zA-Z0-9()@:%_\+.~#?&//=]*)', '', clean_name, flags=re.IGNORECASE)
    clean_name = re.sub(r't\.me/[a-zA-Z0-9_]+', '', clean_name, flags=re.IGNORECASE)

    clean_name = re.sub(r'\b(full|hindi|tamil|english|telugu|malayalam|kannada|bengali|new|latest|hd|mp4)\s+(movie|video)\b', '', clean_name, flags=re.IGNORECASE).strip()
    clean_name = re.sub(r'\b(web[\s\-]?series|tv[\s\-]?series)\b', '', clean_name, flags=re.IGNORECASE).strip()

    uploader_tags = [r'(?:join\s+)?us\s*bobfiles']
    uploader_cleanup = r'^(?:(?:' + '|'.join(uploader_tags) + r')[\s]*)+'
    clean_name = re.sub(uploader_cleanup, '', clean_name, flags=re.IGNORECASE).strip()

    prefix_tags = [
        r's\d{1,2}(?:-\d{1,2})?', r'e\d{1,4}(?:-\d{1,4})?', 
        r'\d{3,4}[pi]', r'4k', r'8k',                                 
        r'(?:19|20)\d{2}',                                  
        r'combined', r'complete',                           
        r'dual[\s\-]?audio', r'multi[\s\-]?audio',          
        r'hindi', r'english', r'tamil', r'telugu', r'malayalam', r'kannada', r'bengali', r'marathi', r'korean', r'japanese', r'chinese', r'spanish', r'russian', r'french',
        r'web[\-\s]?dl', r'web[\-\s]?rip', r'hd[\-\s]?rip', r'bluray', r'brrip', r'dvd[\-\s]?rip', r'cam[\-\s]?rip', r'ts[\-\s]?rip', r'hdts', r'hdcam', 
        r'av1', r'x264', r'x265', r'hevc', r'10bit', r'aac', r'eac3', r'ac3', r'ddp[\s\-]?7\.1', r'ddp[\s\-]?5\.1', r'dd[\s\-]?5\.1', r'dd[\s\-]?2\.0', r'ddp', r'5\.1', r'7\.1', r'2\.0', r'2ch', r'stereo',
        r'download', r'watch', r'full[\s\-]?movie', r'web[\s\-]?series', r'new', r'latest', 
        r'netflix', r'amazon', r'prime', r'hotstar', r'zee5', r'sonyliv', r'jio', r'jiocinema', r'voot', r'altbalaji' 
    ]

    LANG_PREFIX_WORDS = {
        'hindi', 'english', 'tamil', 'telugu', 'malayalam', 'kannada',
        'bengali', 'marathi', 'korean', 'japanese', 'chinese', 'spanish',
        'russian', 'french', 'bhin'
    }
    _prefix_token_re = re.compile('(?:' + '|'.join(prefix_tags) + ')', re.IGNORECASE)
    _sep_re = re.compile(r'[\s_\-]*')

    def _splits_a_word(text, end_pos):
        return end_pos < len(text) and text[end_pos].isalnum()

    pos = 0
    while True:
        sep_m = _sep_re.match(clean_name, pos)
        p = sep_m.end() if sep_m else pos
        tok_m = _prefix_token_re.match(clean_name, p)
        if not tok_m:
            break
        if _splits_a_word(clean_name, tok_m.end()):
            break
        is_lang = tok_m.group(0).lower() in LANG_PREFIX_WORDS
        if is_lang:
            sep_m2 = _sep_re.match(clean_name, tok_m.end())
            p2 = sep_m2.end() if sep_m2 else tok_m.end()
            if not _prefix_token_re.match(clean_name, p2):
                break
        pos = tok_m.end()

    clean_name = clean_name[pos:].strip()

    stop_anchors = [
        r'\bseason[\s\-_]*\d{1,2}\b',
        r'\be\d{1,4}[\s\-_]*[tT][\s\-_]*e?\d{1,4}\b',
        r'\bepisode[\s\-_]*\d{1,4}\b',
        r'\bep[\s\-_]*\d{1,4}\b',
        r'\b\d{1,2}x\d{1,4}\b',
        r'\bs\d{1,2}\s?e\d{1,4}\b', 
        r'\bs\d{1,2}\b', 
        r'\be\d{1,4}\b', 
        r'\b(?:vol|volume|chapter|part|pt)[\s\.\-_]*(?:\d{1,4}|[ivx]+)\b',
        r'\b(19|20)\d{2}\b',                                      
        r'\b\d{3,4}[pi]\b', 
        r'\b4k\b', r'\b8k\b',
        r'\bcombined\b', r'\bcomplete\b',
        r'\bdual[\s\-]?audio\b', r'\bmulti[\s\-]?audio\b',
        r'\bweb[\s\-]?dl\b', 
        r'\bwebrip\b', 
        r'\bbluray\b', r'\bbdrip\b', r'\bbrrip\b', r'\bbdremux\b', r'\bremux\b',
        r'\bhd[\-\s]?rip\b', r'\bdvd[\-\s]?rip\b', r'\bdvdscr\b',
        r'\bhdtc\b', r'\bhdts\b', r'\bhdcam\b', r'\bcam[\-\s]?rip\b', r'\bts[\-\s]?rip\b', r'\bpredvd\b',
        r'\bx264\b', r'\bx265\b', r'\bh264\b', r'\bh265\b', r'\bhevc\b', r'\bavc\b', r'\bav1\b',
        r'\b10bit\b', r'\b12bit\b',
        r'\bnetflix\b', r'\bamazon\b', r'\bprime\b', r'\bhotstar\b', r'\bdisney\b',
        r'\bzee5\b', r'\bsonyliv\b', r'\bjiocinema\b', r'\bjio\b', r'\bvoot\b', r'\baltbalaji\b',
        r'\bhbomax\b', r'\bapple[\s\-]?tv\b', r'\bparamount\b', r'\bpeacock\b',
        r'\bsunnxt\b', r'\bmx[\s\-]?player\b', r'\blionsgate\b',
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
# SEASON & EPISODE NORMALIZER
# =========================================================
def normalize_season_episode(text):
    text = text.lower()

    # --- Season + Episode Ranges ---
    text = re.sub(r'\bs(\d{1,2})[\s._\-]*e(\d{1,4})\b', r's\1 e\2', text)
    # S02.E01 -> S02 E01
    text = re.sub(r'\bs(\d{1,2})\.e(\d{1,4})\b', r's\1 e\2', text)

    # S01E01 to S01E10
    text = re.sub(r'\bs(\d{1,2})[\s\-_]*e(\d{1,4})[\s\-_~]+(?:to|and|&)?[\s\-_~]*s\d{1,2}[\s\-_]*e(\d{1,4})\b',
                  lambda m: f"s{int(m.group(1)):02d} e{int(m.group(2)):02d}-{int(m.group(3)):02d}", text)

    # S02E01E04 or S02-E01-E04
    text = re.sub(r'\bs(\d{1,2})[\s\-_~]*e(\d{1,4})[\s\-_~]*e(\d{1,4})\b', 
                  lambda m: f"s{int(m.group(1)):02d} e{int(m.group(2)):02d}-{int(m.group(3)):02d}", text)

    # S02 E01 to E04
    text = re.sub(r'\bs(\d{1,2})[\s\-_]*e(\d{1,4})[\s\-_]*(?:to|&|and)[\s\-_]*e?(\d{1,4})\b', 
                  lambda m: f"s{int(m.group(1)):02d} e{int(m.group(2)):02d}-{int(m.group(3)):02d}", text)

    # S02-E01-E04 (multiple separators)
    text = re.sub(r'\bs(\d{1,2})[\s\-_]+e(\d{1,4})[\s\-_]+e?(\d{1,4})\b', 
                  lambda m: f"s{int(m.group(1)):02d} e{int(m.group(2)):02d}-{int(m.group(3)):02d}", text)

    # 2x01-04
    text = re.sub(r'\b(\d{1,2})[xX](\d{1,4})[\s\-_]+(?:\d{1,2}[xX])?(\d{1,4})\b', 
                  lambda m: f"s{int(m.group(1)):02d} e{int(m.group(2)):02d}-{int(m.group(3)):02d}", text)

    # Season 1 to Season 3
    text = re.sub(r'\bseason[\s\-_]*(\d{1,2})[\s\-_~]+(?:to|and|&)?[\s\-_~]*(?:season[\s\-_]*)?(\d{1,2})\b',
                  lambda m: f"s{int(m.group(1)):02d}-{int(m.group(2)):02d}", text)

    # S02-S04
    text = re.sub(r'\bs(\d{1,2})[\s\-_~]+(?:to|and|&)?[\s\-_~]*s?(\d{1,2})\b', 
                  lambda m: f"s{int(m.group(1)):02d}-{int(m.group(2)):02d}", text)


    # --- Episode Ranges ---

    # E01TE04 (T for "to")
    text = re.sub(r'\be(\d{1,4})[tT]e?(\d{1,4})\b', 
                  lambda m: f"e{int(m.group(1)):02d}-{int(m.group(2)):02d}", text)

    # E01_E04
    text = re.sub(r'\be(\d{1,4})[\s\-_]e(\d{1,4})\b', 
                  lambda m: f"e{int(m.group(1)):02d}-{int(m.group(2)):02d}", text)

    # EP01 to EP10
    text = re.sub(r'\bep(?:isode)?[\s\-_]*(\d{1,4})[\s\-_]+(?:to|and|&)?[\s\-_]*ep(?:isode)?[\s\-_]*(\d{1,4})\b',
                  lambda m: f"e{int(m.group(1)):02d}-{int(m.group(2)):02d}", text)

    # ep(01-04) or ep 01
    text = re.sub(r'\bep(?:isode)?[\s\-_]*\(?(\d{1,4})(?:[\s\-–~]+(?:to|and|&)?[\s\-–~]*(\d{1,4}))?\)?\b', 
                  lambda m: f"e{int(m.group(1)):02d}-{int(m.group(2)):02d}" if m.group(2) else f"e{int(m.group(1)):02d}", text)

    # e01-e04 or e01
    text = re.sub(r'\be(\d{1,4})(?:[\s\-–~]+(?:to|and|&)?[\s\-–~]*e?(\d{1,4}))?\b', 
                  lambda m: f"e{int(m.group(1)):02d}-{int(m.group(2)):02d}" if m.group(2) else f"e{int(m.group(1)):02d}", text)

    # 01-04 (fallback if both numbers < 60)
    text = re.sub(r'\b(\d{2})[\s\-–]+(\d{2})\b', 
                  lambda m: f"e{int(m.group(1)):02d}-{int(m.group(2)):02d}" if int(m.group(1)) < 60 and int(m.group(2)) < 60 else m.group(0), text)

    # 2x01
    text = re.sub(r'\b(\d{1,2})[xX](\d{1,4})\b', 
                  lambda m: f"s{int(m.group(1)):02d} e{int(m.group(2)):02d}", text)


    # --- Standalone Patterns ---

    # Season keywords
    text = re.sub(r'\b(?:season)[\s\-_]*(\d{1,2})\b', lambda m: f"s{int(m.group(1)):02d}", text)
    text = re.sub(r'\bs[\s\-_]*(\d{1,2})\b', lambda m: f"s{int(m.group(1)):02d}", text)

    # Episode keywords
    text = re.sub(r'\b(?:episode)[\s\-_]*(\d{1,4})\b', lambda m: f"e{int(m.group(1)):02d}", text)
    text = re.sub(r'\bep(?:isode)?[\s\-_]*(\d{1,4})\b', lambda m: f"e{int(m.group(1)):02d}", text)
    text = re.sub(r'\be[\s\-_]*(\d{1,4})\b', lambda m: f"e{int(m.group(1)):02d}", text)
    
    text = re.sub(r'\s+', ' ', text).strip()

    return text.upper()


# =========================================================
# DUAL AUDIO / MULTI AUDIO TAG HELPER
# =========================================================
def apply_dual_multi_audio_tag(languages, scan_lower):
    """
    Ensures 'Dual/Multi Audio' tags are preserved or dynamically 
    added based on explicit keywords or language count.
    """
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
    """Robust extraction for episode titles like 'Day 01 Housemates Bane'."""
    text = re.sub(r'\.[a-z0-9]{2,4}$', '', text, flags=re.IGNORECASE)

    # Stop keywords to prevent capturing quality tags as episode titles
    stop_keywords = [
        r'19\d{2}', r'20\d{2}', r'2160p', r'1080p', r'720p', r'480p', r'360p',
        r'web[- ]?dl', r'webrip', r'bluray', r'hdrip', r'x264', r'x265', r'hevc', r'avc',
        r'aac', r'ac3', r'ddp', r'hindi', r'english', r'tamil', r'telugu',
        r'dual', r'multi', r'combined', r'complete', r'jhs', r'netflix', r'amazon', r'mp4', r'mkv', r'🗃️'
    ]
    stop_pattern = '|'.join(stop_keywords)
    lookahead = rf'(?=\b(?:{stop_pattern})\b|$|\[|\(|@|🗃️)'

    patterns = [
        r'(?:S\d{1,2}[\s._\-]*E\d{1,4}[-\d]*)[\s._\-]+(.*?)(?=' + stop_pattern + r'|$)',
        r'(?:Episode|Ep)[\s._\-]*\d+[\s._\-]+(.*?)(?=' + stop_pattern + r'|$)',
        r'S\d{1,2}\s*E\d{1,4}\s+[A-Za-z0-9\s\-]+(?:\d{4})?\s+(.*?)(?=' + stop_pattern + r'|$)'
    ]

    specific_match = re.search(r'S\d{1,2}\s*E\d{1,4}\s+(.*?)(?=\s+(?:\d{3,4}[pi]|\d{4}|WEB|BluRay|Mp4|Mkv))\b', text, re.IGNORECASE)
    if specific_match:
        title = specific_match.group(1)
        title = re.sub(r'[!@#$%^&*()_+={}\[\]:;<>,.?/\\|~`–—―•]+', ' ', title)
        title = re.sub(r'\s+', ' ', title).strip()
        if len(title) > 1 and not re.fullmatch(r'[\d\s\-]+', title):
            return title.title()

    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            title = match.group(1)
            title = re.sub(r'[!@#$%^&*()_+={}\[\]:;<>,.?/\\|~`–—―•]+', ' ', title)
            title = re.sub(r'\s+', ' ', title).strip()

            # Ignore pure numeric/symbol sequences 
            if len(title) > 2 and not re.fullmatch(r'[\d\s\-🗃️]+', title):
                # Reject range artifacts like "21 To 25"
                if re.fullmatch(r'\s*\d{1,4}\s*(?:to|and|&|-)?\s*\d{0,4}\s*', title, flags=re.IGNORECASE):
                    continue
                return title.title()

    return None


# =========================================================
# DATA EXTRACTOR
# =========================================================
def extract_languages_quality(text_to_scan):
    scan_text = re.sub(r'[._]+', ' ', text_to_scan)
    scan_lower = scan_text.lower()

    year_match = re.search(r'\b(19\d{2}|20[0-2]\d)\b', text_to_scan)
    year = year_match.group(1) if year_match else None

    normalized_se = normalize_season_episode(scan_text)
    season_episode = None
    episode_title = None

    full_match = re.search(r'\b(S\d{2})[\s\[\]\-_]*?(E\d{2,4}(?:-\d{2,4})?)\b', normalized_se)
    if full_match:
        season_episode = f"{full_match.group(1)} {full_match.group(2)}"
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
    SOURCES = {
        "WEB-DL": ["web-dl", "webdl", "web dl"],
        "WEBRip": ["webrip", "web rip"],
        "HDRip": ["hdrip", "hd rip", "hd-rip"],
        "BluRay": ["bluray", "bdrip", "brrip", "bdremux"],
        "DVDRip": ["dvdrip", "dvd rip"],
        "DVDScr": ["dvdscr", "scr", "dvd-scr"],
        "REMUX": ["remux"],
        "Digital": ["digital"],
        "HDTC": ["hdtc", "hd-tc", "telecine", "tcrip", "tc rip"],       
        "HDTS": ["hdts", "hd-ts", "tsrip", "ts rip", "telesync", "ts"], 
        "HDCAM": ["hdcam", "hd-cam", "hd cam"],
        "CAMRip": ["camrip", "cam rip", "cinema", "cam"],
        "PreDVD": ["predvd", "pre dvd"]
    }
    for src, aliases in SOURCES.items():
        for a in aliases:
            # 🚀 FIX: Ensures alias strictly matches the word (stops "ts" from matching inside "sports" or "tsrip")
            if re.search(rf'(?<![a-z0-9]){re.escape(a)}(?![a-z0-9])', scan_lower):
                source = src  
                break
        if source:
            break

    ott_tag = None
    for platform, aliases in OTT_MAP.items():
        for a in aliases:
            # 🚀 FIX: Strict word boundary for OTT tags
            if re.search(rf'(?<![a-z0-9]){re.escape(a)}(?![a-z0-9])', scan_lower):
                ott_tag = platform
                break
        if ott_tag:
            break

    extra_tags = []
    TAGS_MAP = {
        "AV1": ["av1"], 
        "HEVC X265": ["x265", "hevc", "h265"], 
        "AVC X264": ["x264", "avc", "h264"], 
        "10Bit": ["10bit"], 
        "12Bit": ["12bit"],
        "SDR": ["sdr"],
        "HDR": ["hdr", "hdr10", "hdr10+"],
        "Dolby Vision": ["dolby vision", "dv", "dovi"],
        "IMAX": ["imax"],
        "60FPS": ["60fps"],
        "Dolby Atmos": ["atmos", "dolby atmos"],
        "Dolby TrueHD": ["truehd", "dolby truehd"],
        "DDP 7.1": ["ddp7.1", "ddp 7.1", "eac3 7.1", "dd+ 7.1"],
        "DDP 5.1": ["ddp5.1", "ddp 5.1", "eac3", "dd+", "ddp"],
        "DD 5.1": ["dd5.1", "dd 5.1", "ac3 5.1", "ac3", "5.1", "6ch"],
        "DD 2.0": ["dd2.0", "dd 2.0", "ac3 2.0", "2.0", "2ch", "stereo"],
        "DTS-X": ["dts-x", "dtsx"],
        "DTS-HD": ["dts-hd", "dtshd", "dts-hd ma"],
        "DTS 5.1": ["dts 5.1", "dts5.1", "dts"],
        "AAC 5.1": ["aac 5.1", "aac5.1"],
        "AAC": ["aac", "aac 2.0"],
        "ESubs": ["esub", "esubs"], 
        "HardSubs": ["hsub", "hsubs", "hc", "hcsub"],
        "MSubs": ["msub", "msubs"]
    }
    for tag, aliases in TAGS_MAP.items():
        for a in aliases:
            # 🚀 FIX: Prevents "dv" from falsely matching inside "dvdrip"
            if re.search(rf'(?<![a-z0-9]){re.escape(a)}(?![a-z0-9])', scan_lower):
                extra_tags.append(tag)
                break

    custom_qualifiers = []
    target_keywords = [
        r'\bunrated\b', r'\bopen[\s\-]?matte\b', r'\bultimate[\s\-]?edition\b', r'\bchronological\b', r'\bredux\b',
        r'\bleak\b', r'\bstudio\b', r'\bdub\b', r'\bdubbed\b',
        r'\bunofficial\b', r'\bre[\s\-]?dub(?:bed)?\b', r'\bfan[\s\-]?dub(?:bed)?\b', 
        r'\bhq[\s\-]?dub(?:bed)?\b', r'\bstudio[\s\-]?dub(?:bed)?\b', r'\bclean[\s\-]?audio\b',
        r'\boriginal[\s\-]?audio\b', r'\bline[\s\-]?audios?\b', r'\bline\b', r'\bmultiplex\b',
        r'\bextended\b', r'\bextendded\b', r'\buncut\b', r'\bdirector\'s[\s\-]?cut\b', 
        r'\bdc\b', r'\bremastered\b', r'\bremaster\b', r'\bproper\b', 
        r'\bpre[\s\-]?release\b', r'\bprerelease\b', r'\bworkprint\b', r'\bwp\b', 
        r'\bspecial[\s\-]?edition\b', r'\btheatrical\b', r'\banniversary\b',
        r'\bhq\b', r'\bhybrid\b', r'\bpatched\b', r'\bcorrected\b', r'\bsoftsub\b',
        r'\bv[1-4]\b',
        r'\borgs?\b', r'\bds4k\b', r'\bmulti\b'
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

    # Language Scan
    languages = []
    for lang, aliases in LANGUAGE_ALIASES.items():
        for a in aliases:
            if re.search(a, scan_lower):
                languages.append(lang)
                break

    if "Dual Audio" not in languages and "Multi Audio" not in languages:
        languages = apply_dual_multi_audio_tag(languages, scan_lower)

    kbps_tag = None
    kbps = re.search(r'(\d{2,4}\s?kbps)', scan_lower)
    if kbps:
        kbps_tag = kbps.group(1).upper().replace(" ", "")

    # Title Part / Volume / Chapter
    title_part = None
    tp_match = re.search(r'\b(vol|volume|chapter|part|pt)[\s\.\-_]*(\d{1,2}|[IVX]+)\b(?!\d)', scan_lower)
    if tp_match:
        tag_name = tp_match.group(1).capitalize()
        if tag_name == "Pt": tag_name = "Part"
        if tag_name == "Volume": tag_name = "Vol"
        title_part = f"{tag_name} {tp_match.group(2).upper()}"

    # File Split Part
    split_part = None
    sp_match = re.search(r'\b(?:part|pt)[\s\.\-_]*(\d{3,4})\b', scan_lower)
    if sp_match:
        split_part = f"Part {sp_match.group(1)}"

    return {
        "year": year, 
        "season_episode": season_episode,
        "episode_title": episode_title, 
        "languages": languages,
        "resolution": resolution, 
        "source": source, 
        "ott": ott_tag, 
        "extra_tags": extra_tags, 
        "kbps": kbps_tag, 
        "custom_qualifiers": custom_qualifiers,
        "series_status": series_status,
        "title_part": title_part,
        "split_part": split_part
    }

# =========================================================
# MAIN ASYNC SAVE PIPELINE
# =========================================================
async def _get_session() -> "aiohttp.ClientSession":
    import aiohttp
    if temp.AIOHTTP_SESSION is None or temp.AIOHTTP_SESSION.closed:
        temp.AIOHTTP_SESSION = aiohttp.ClientSession()
    return temp.AIOHTTP_SESSION


_COVER_LOCKS = {}
_COVER_CACHE = {}
_COVER_CACHE_MAX_ENTRIES = 1000
_COVER_LOCKS_MAX_ENTRIES = 1000   # bounded so this dict can't grow forever during big indexing runs
_COVER_SEMAPHORE = asyncio.Semaphore(3)


def _cover_cache_set(key, value):
    if not value:
        return
    if len(_COVER_CACHE) >= _COVER_CACHE_MAX_ENTRIES and key not in _COVER_CACHE:
        _COVER_CACHE.pop(next(iter(_COVER_CACHE)), None)
    _COVER_CACHE[key] = value

async def _fetch_and_save_cover(file_id: str, final_title: str, year: str | None, bot=None):
    """Background cover resolver with bounded concurrency and cache."""
    
    # 1. STRICT INFO.PY CONTROL: Handles both boolean False and string "False"
    if str(COVERX).strip().lower() in ['false', '0', 'no']:
        return

    lock_key = f"{final_title.lower().strip()}::{(year or '').strip()}"
    if lock_key not in _COVER_LOCKS:
        # Evict the oldest lock if we're at capacity, so this dict (and the Lock
        # objects it holds) can't grow unbounded across a long indexing run.
        if len(_COVER_LOCKS) >= _COVER_LOCKS_MAX_ENTRIES:
            oldest_key = next(iter(_COVER_LOCKS))
            if not _COVER_LOCKS[oldest_key].locked():
                _COVER_LOCKS.pop(oldest_key, None)
        _COVER_LOCKS[lock_key] = asyncio.Lock()

    async with _COVER_SEMAPHORE:
        async with _COVER_LOCKS[lock_key]:
            try:
                # 1. Check fast memory cache
                if lock_key in _COVER_CACHE:
                    cover_url = _COVER_CACHE[lock_key]
                    logger.debug(f"[COVER] Reused cover from memory cache for '{final_title}' ({year})")
                else:
                    # 2. STRICT DATABASE MATCH: Must match BOTH Title and Year exactly.
                    query = {
                        "title": {"$regex": rf"^{re.escape(final_title)}$", "$options": "i"},
                        "cover": {"$ne": None},
                        "year": year # This guarantees year must match (if year is None, it only matches DB entries with no year)
                    }

                    existing = None
                    for media_cls in MEDIA_DBS:
                        existing = await media_cls.find_one(query)
                        if existing:
                            break

                    if existing and existing.cover:
                        cover_url = existing.cover
                        _cover_cache_set(lock_key, cover_url)
                        logger.debug(f"[COVER] Reused existing cover from DB for '{final_title}'")
                    else:
                        # 3. Fetch from API (If no exact match found in DB)
                        raw_url = await _fetch_cover_url(final_title, year)

                        if not raw_url:
                            logger.debug(f"[COVER] No cover found for '{final_title}' - Skipping cover.")
                            return

                        # 🚀 CLOUDINARY WATERMARK URL GEN (0% CPU LOAD)
                        cover_url = get_cloud_watermark_url(raw_url)
                        
                        if cover_url:
                            _cover_cache_set(lock_key, cover_url)
                            logger.debug(f"[COVER] Fetched new cover for '{final_title}'")
                        else:
                            return

                # Update the specific file_id with the resolved cover
                for media_cls in MEDIA_DBS:
                    await media_cls.collection.update_one({"_id": file_id}, {"$set": {"cover": cover_url}})
                logger.debug(f"[COVER] DB updated | file_id={file_id}")
            except Exception as e:
                logger.warning(f"[COVER] Background task error for '{final_title}': {e}")

async def save_file(media, bot=None, extracted_info=None):
    """
    Save media file to the database with extracted details and proper routing.
    
    Args:
        media: Media object with file details
        bot: Pyrogram bot instance
        extracted_info: Pre-extracted media info containing language (optional)
    """
    try:
        file_id, file_ref = unpack_new_file_id(media.file_id)
        original_name = str(media.file_name or "Unnamed File")
        base_name, ext = os.path.splitext(original_name)

        text_to_scan = f"{original_name} {getattr(media, 'caption', '') or ''}"

        # Offload heavy regex parsing to a worker thread to prevent blocking the event loop
        extracted = await asyncio.to_thread(extract_languages_quality, text_to_scan)

        # Merge pre-extracted language information from caption if available
        if extracted_info and extracted_info.get("language") and extracted_info.get("language") != "N/A":
            extracted["languages"] = [lang.strip() for lang in extracted_info["language"].split(",")]
            logger.debug(f"[LANGUAGE] Using pre-extracted languages: {extracted['languages']}")

            # Re-apply dual/multi audio rules since the override clears previous determinations
            extracted["languages"] = apply_dual_multi_audio_tag(
                extracted["languages"], text_to_scan.lower()
            )

        # Smart defaults
        if not extracted.get("resolution"):
            extracted["resolution"] = "720P"

        if not extracted.get("source"):
            extracted["source"] = "WEB-DL"

        # Apply AAC codec as a default if no other audio tags exist
        audio_codecs = [
            "Dolby TrueHD", "Dolby Atmos", "DTS-X", "DTS-HD", 
            "DDP 7.1", "DDP 5.1", "DD 5.1", "DD 2.0", 
            "DTS 5.1", "AAC 5.1", "AAC"
        ]
        audio_tags = extracted.get("extra_tags", [])

        has_audio = any(codec in audio_tags for codec in audio_codecs)
        if not has_audio:
            if "AAC" not in audio_tags:
                audio_tags.append("AAC")
            extracted["extra_tags"] = audio_tags

        # Offload title extraction
        cleaned_title = await asyncio.to_thread(extract_pure_title, base_name)

        # FALLBACK: many channel files are named like "S01E05.1080p.WEB-DL.x264.mkv"
        # with no real title in the filename at all — the title only exists in the
        # caption (e.g. "🎬 Movie Name (2023)"). If the filename gave us nothing
        # usable, try pulling the title out of the caption instead.
        if not cleaned_title.strip():
            raw_caption = getattr(media, "caption", None)
            caption_text = getattr(raw_caption, "html", None) or str(raw_caption or "")
            if caption_text:
                caption_plain = re.sub(r'<[^>]+>', ' ', caption_text)   # strip html tags
                caption_plain = re.sub(r'[\U0001F000-\U0001FFFF\u2600-\u27BF]+', ' ', caption_plain)  # strip emoji
                caption_plain = caption_plain.splitlines()[0] if caption_plain.strip() else caption_plain
                cleaned_title = await asyncio.to_thread(extract_pure_title, caption_plain)

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
        if final_title: add_unique(final_title)

        # [1.5] Title Part / Volume / Chapter
        if extracted.get("title_part"): add_unique(extracted["title_part"])

        # [2] Season & Episode
        if extracted.get("season_episode"): add_unique(extracted["season_episode"])

        # [2.1] Episode Title
        if extracted.get("season_episode") and extracted.get("episode_title"):
            add_unique(extracted["episode_title"])

        # [2.5] Series Status
        if extracted.get("series_status"): add_unique(extracted["series_status"])

        # [3] Release Year
        if extracted.get("year"): add_unique(extracted["year"])

        # [4] Video Resolution
        if extracted.get("resolution"): add_unique(extracted["resolution"])

        # [5] Audio Languages
        for lang in extracted.get("languages", []): add_unique(lang)

        # [6] Custom Qualifiers
        for qual in extracted.get("custom_qualifiers", []): add_unique(qual)

        # [7] Color Depth / HDR
        for tag in ["10Bit", "12Bit", "SDR", "HDR", "Dolby Vision", "IMAX", "60FPS"]:
            if tag in extracted.get("extra_tags", []): add_unique(tag)

        # [8] OTT Platform Tag
        if extracted.get("ott") and extracted["ott"] not in parts:
            parts.append(extracted["ott"])

        # [9] Source Type
        if extracted.get("source"): add_unique(extracted["source"])

        # [10] Video Codec
        for vcodec in ["AV1", "HEVC X265", "AVC X264"]:
            if vcodec in extracted.get("extra_tags", []): add_unique(vcodec)

        # [11] Audio Codec & Channels (Smart Overlap Handler)
        audio_tags = extracted.get("extra_tags", [])
        if "DDP 5.1" in audio_tags and "DD 5.1" in audio_tags: audio_tags.remove("DD 5.1")
        if "DDP 7.1" in audio_tags and "DD 5.1" in audio_tags: audio_tags.remove("DD 5.1")
        if "AAC 5.1" in audio_tags and "AAC" in audio_tags: audio_tags.remove("AAC")

        for acodec in ["Dolby TrueHD", "Dolby Atmos", "DTS-X", "DTS-HD", "DDP 7.1", "DDP 5.1", "DD 5.1", "DD 2.0", "DTS 5.1", "AAC 5.1", "AAC"]:
            if acodec in audio_tags: 
                add_unique(acodec)

        # [12] Subtitles
        for sub in ["ESubs", "HardSubs", "MSubs"]:
            if sub in extracted.get("extra_tags", []): add_unique(sub)

        # [13] Audio Bitrate
        if extracted.get("kbps"): add_unique(extracted["kbps"])

        # [13.5] File Split Part (e.g. part001)
        if extracted.get("split_part"): add_unique(extracted["split_part"])

        # [14] Branding Signature
        parts = [p for p in parts if p and "Tokyo_Updates" not in str(p)]
        parts.append(RELEASE_TAG)

        # Final String Assembly
        file_name = " ".join(map(str, parts)).strip()
        file_name = re.sub(r'\s+', ' ', file_name)
        file_name = file_name + ext.lower()
        file_name = re.sub(r'\s+\.', '.', file_name)

        # Run cover fetch in the background
        if COVERX:
            asyncio.ensure_future(_fetch_and_save_cover(
                file_id=file_id,
                final_title=final_title,
                year=extracted.get("year"),
                bot=bot
            ))

        # Check for duplicates across all active databases
        for db_index, media_cls in enumerate(MEDIA_DBS):
            existing_file = await media_cls.find_one({
                "$or": [
                    {"_id": file_id},
                    {"file_name": file_name, "file_size": media.file_size}
                ]
            })

            if existing_file:
                db_label = _DB_LABELS[db_index] if db_index < len(_DB_LABELS) else f"DB {db_index + 1}"
                existing_name = existing_file.file_name or "Unknown"

                logger.warning(
                    f"⚠️ [DUPLICATE SKIPPED] "
                    f"📄 {original_name} | "
                    f"🗄️ {db_label} | "
                    f"♻️ Existing: {existing_name}"
                )

                return False, 0, None

        # Determine target database based on storage capacity
        target_media = await get_active_media_db()

        record = target_media(
            file_id=file_id,
            file_ref=file_ref,
            file_name=file_name,
            file_size=media.file_size,
            file_type=media.file_type,
            mime_type=media.mime_type,
            caption=getattr(media.caption, "html", None) if media.caption else None,
            cover=None,
            media_type=("series" if is_series_file(file_name) else "movie"),
            file_date=datetime.utcnow(), 
            title=final_title,
            year=extracted.get("year")
        )
        await record.commit()

        db_index = MEDIA_DBS.index(target_media)
        db_label = _DB_LABELS[db_index] if db_index < len(_DB_LABELS) else f"DB {db_index + 1}"

        logger.info(
            f"✅ [FILE SAVED] "
            f"📄 {file_name} | "
            f"🗄️ {db_label}"
        )

        clear_search_cache() 
        return True, 1, file_name

    except DuplicateKeyError:
        return False, 0, None
    except Exception as e:
        logger.error(f"Error saving file: {e}", exc_info=True)
        return False, 0, None



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
    "4320p": 8, "8k": 8, "2160p": 7, "4k": 7, "1440p": 6, "1080p": 5, "1080i": 5, "720p": 4, "720i": 4, "480p": 3, "360p": 2, "240p": 1, "144p": 0
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

    # Combined pattern first: catches attached "S01E01" style (no separator between
    # the season digits and 'e', where a bare \be...\b episode regex can't match
    # because there's no word boundary between the '1' and the 'e').
    combined = re.search(r"\bs(?:eason)?[\s._-]*(\d{1,2})[\s._-]*e(?:p(?:isode)?)?[\s._-]*(\d{1,4})", name)
    if combined:
        return int(combined.group(1)), int(combined.group(2))

    s = re.search(r"\bs(?:eason)?[\s._-]*(\d{1,2})", name)
    e = re.search(r"\be(?:pisode|p)?[\s._-]*(\d{1,4})", name)

    season = int(s.group(1)) if s else 0
    episode = int(e.group(1)) if e else 0
    return season, episode

SERIES_PATTERNS = [
    r"\bs\d{1,2}[\s._-]*e\d{1,4}\b",                                              # S01E01, S1E1
    r"\bs\d{1,2}\s*-\s*s?\d{1,2}\b",                                              # S01-S05, S01-05 (season range)
    r"\be(?:p(?:isode)?)?[\s._-]*\d{1,4}\s*-\s*(?:e(?:p(?:isode)?)?[\s._-]*)?\d{1,4}\b",  # E01-E10, EP01-10, Episode 1-24
    r"\b\d{1,2}x\d{1,3}\b",                                                       # 1x05, 01x01
    r"\bseason[\s._-]*\d{1,2}\b",                                                 # Season 1
    r"\bweb[\s._-]?series\b",                                                     # Web Series
    r"\bseries\b",                                                                # Series
    r"\bs\d{1,2}\b",                                                              # lone S01 (season pack)
    r"\bepisode[\s._-]*\d{1,4}\b",                                                # Episode 5
    r"\bep[\s._-]*\d{1,4}\b",                                                     # EP05
    r"\ball\s*episodes?\b",                                                       # All Episodes
    r"\bcomplete\b.{0,40}\b(?:season|series)\b",                                  # Complete ... Season/Series (bounded gap)
    r"\b(?:season|series)\b.{0,40}\bcomplete\b",                                  # Season/Series ... Complete (bounded gap)
]

# Ek hi combined regex — DB query ($regex) aur Python dono jagah reuse hota hai,
# taaki filtering hamesha poore collection ke against DB level pe ho, post-fetch
# limited-batch pe nahi (isse pagination/total count bhi sahi aata hai).
SERIES_REGEX = re.compile("|".join(f"(?:{p})" for p in SERIES_PATTERNS), re.IGNORECASE)

def is_series_file(name) -> bool:
    """
    File name ke andar Season/Episode jaisa pattern hai ya nahi, ye check karta hai.
    True  -> Series/Web-Series ka file lagta hai
             (S01E01, Season 2, 1x05, EP03, S01-S05, E01-E10, Complete Series, etc.)
    False -> Movie ka file lagta hai
    """
    return bool(SERIES_REGEX.search(str(name).lower()))

# 🚀 SPEED FIX: Movie/Series button (mtype#..) aur uske Next/Back pagination

async def backfill_media_type(
    batch_size: int = 500,
    media_dbs=None,
    progress_cb=None,
    sleep_between_batches: float = 0.25,
) -> dict:
    """
    One-time (safe to re-run) migration: existing files jinka `media_type`
    abhi bhi None hai, unko is_series_file() se classify karke
    "movie"/"series" set kar deta hai.

    12 lakh+ files jaise bade collections ke liye safe rehne ke liye:
    - Chhote batches (default 500) me hi bulk_write hota hai — poora
      collection ek saath RAM me kabhi load nahi hota.
    - Har batch ke baad `sleep_between_batches` sec ka chhota sa pause hota
      hai, taaki DB aur bot event-loop dono par ek saath extra load na pade
      aur bot baaki users ke normal search/messages handle karta rahe.
    - `progress_cb(collection_name, done, total)` — agar diya jaaye to har
      batch ke baad call hota hai, isse caller "live status" dikha sakta hai
      (kitni files ho gayi, kitni baaki hai).

    Returns: {"<CollectionName>": <docs_updated>, ...}
    """
    from pymongo import UpdateOne

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

        cursor = coll.find(
            {"media_type": None},
            {"_id": 1, "file_name": 1},
        ).batch_size(batch_size)

        try:
            async for doc in cursor:
                mtype = "series" if is_series_file(doc.get("file_name", "")) else "movie"
                ops.append(UpdateOne({"_id": doc["_id"]}, {"$set": {"media_type": mtype}}))
                if len(ops) >= batch_size:
                    res = await coll.bulk_write(ops, ordered=False)
                    updated += res.modified_count
                    ops = []
                    if progress_cb:
                        try:
                            await progress_cb(media_cls.__name__, updated, pending_total)
                        except Exception:
                            pass
                    if sleep_between_batches:
                        await asyncio.sleep(sleep_between_batches)
            if ops:
                res = await coll.bulk_write(ops, ordered=False)
                updated += res.modified_count
                if progress_cb:
                    try:
                        await progress_cb(media_cls.__name__, updated, pending_total)
                    except Exception:
                        pass
        finally:
            await cursor.close()

        report[media_cls.__name__] = updated

    return report

# ----------------- 3. क्वेरी नॉर्मलाइजेशन और स्मार्ट एक्सपेंशन -----------------

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


# ----------------- 4. मुख्य सर्च और सॉर्टिंग फंक्शन -----------------

# 🚀 IN-MEMORY SEARCH CACHE
# Movie / Series filter button (mtype#movie / mtype#series) re-runs the full

_SEARCH_CACHE: dict = {}
_SEARCH_CACHE_TTL = 90          # seconds a cached search result stays valid
_SEARCH_CACHE_MAX_ENTRIES = 300  # hard cap so memory can't grow unbounded
_SEARCH_POOL_CACHE: dict = {}
_SEARCH_POOL_TTL = 90
_SEARCH_POOL_MAX_ENTRIES = 150


def _search_cache_key(chat_id, query, file_type, max_results, offset, filter, media_type):
    q = tuple(query) if isinstance(query, list) else query
    return (chat_id, q, file_type, max_results, offset, filter, media_type)


def _search_cache_get(key):
    entry = _SEARCH_CACHE.get(key)
    if not entry:
        return None
    cached_at, value = entry
    if (datetime.utcnow() - cached_at).total_seconds() > _SEARCH_CACHE_TTL:
        _SEARCH_CACHE.pop(key, None)
        return None
    return value


def _search_cache_set(key, value):
    if len(_SEARCH_CACHE) >= _SEARCH_CACHE_MAX_ENTRIES:
        # drop the oldest entry to keep the cache bounded
        oldest_key = min(_SEARCH_CACHE, key=lambda k: _SEARCH_CACHE[k][0])
        _SEARCH_CACHE.pop(oldest_key, None)
    _SEARCH_CACHE[key] = (datetime.utcnow(), value)


def clear_search_cache():
    """Invalidate every cached search result (called whenever a new file is indexed)."""
    _SEARCH_CACHE.clear()
    _SEARCH_POOL_CACHE.clear()
def _word_to_regex(word):
    """Build a per-word regex fragment that treats an apostrophe as optional.
    Files for the same title sometimes get indexed as both "Newton's ..." and
    "Newtons ..." (uploaders strip punctuation inconsistently). Without this,
    a search for one spelling would never match a file saved with the other.
    """
    if "'" in word or "\u2019" in word:
        parts = [p for p in re.split(r"['\u2019]", word) if p]
        if len(parts) > 1:
            return "['\u2019]?".join(re.escape(p) for p in parts)
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
        max_results = 10 if settings.get("max_btn") else int(MAX_B_TN)

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
        try:
            combined_regex = re.compile("(?:" + "|".join(regex_list) + ")", re.IGNORECASE)
        except re.error:
            combined_regex = None

    conditions = []
    if combined_regex is not None:
        conditions.append({"file_name": combined_regex})
        if USE_CAPTION_FILTER:
            conditions.append({"caption": combined_regex})

    # Never send an empty $or to Mongo; empty/emoji-only queries should simply return no results.
    if not conditions:
        result = ([], "", 0)
        _search_cache_set(cache_key, result)
        return result

    filter_mongo = {"$or": conditions}
    if file_type:
        filter_mongo["file_type"] = file_type

    if media_type == "movie":
        filter_mongo = {
            "$and": [
                filter_mongo,
                {
                    "$or": [
                        {"media_type": "movie"},
                        {"media_type": None, "file_name": {"$not": SERIES_REGEX}},
                    ]
                },
            ]
        }
    elif media_type == "series":
        filter_mongo = {
            "$and": [
                filter_mongo,
                {
                    "$or": [
                        {"media_type": "series"},
                        {"media_type": None, "file_name": SERIES_REGEX},
                    ]
                },
            ]
        }

    # 🚀 SEARCH POOL CACHE: after the first MongoDB search, keep the sorted
    # result pool in memory so pagination/repeated clicks do not rescan MongoDB.
    pool_q = tuple(query) if isinstance(query, list) else query
    pool_key = (chat_id, pool_q, file_type, filter, media_type)
    pool_entry = _SEARCH_POOL_CACHE.get(pool_key)
    if pool_entry:
        cached_at, cached_files, cached_total = pool_entry
        if (datetime.utcnow() - cached_at).total_seconds() <= _SEARCH_POOL_TTL:
            paginated_files = cached_files[offset:offset + max_results]
            if paginated_files or offset == 0:
                next_offset = offset + max_results
                if next_offset >= cached_total or len(paginated_files) < max_results:
                    next_offset = ""
                result = (paginated_files, next_offset, cached_total)
                _search_cache_set(cache_key, result)
                return result
        _SEARCH_POOL_CACHE.pop(pool_key, None)

    fetch_limit = max(100, offset + max_results * 2)

    find_tasks = [
        m.find(filter_mongo).sort([("file_date", -1), ("_id", -1)]).limit(fetch_limit).to_list(length=fetch_limit)
        for m in MEDIA_DBS
    ]
    per_db_files = await asyncio.gather(*find_tasks)

    count_tasks = []
    count_task_indexes = []
    counts = [len(db_files) for db_files in per_db_files]
    for i, (media_cls, db_files) in enumerate(zip(MEDIA_DBS, per_db_files)):
        if len(db_files) >= fetch_limit:
            count_tasks.append(media_cls.count_documents(filter_mongo))
            count_task_indexes.append(i)
    if count_tasks:
        exact_counts = await asyncio.gather(*count_tasks)
        for i, c in zip(count_task_indexes, exact_counts):
            counts[i] = c

    total_results = sum(counts)

    files = [f for db_files in per_db_files for f in db_files]

    def _recency_key(x):
        fd = getattr(x, "file_date", None)
        # None (legacy pre-migration records) sorts as oldest
        return fd if fd is not None else datetime.min

    files.sort(key=_recency_key, reverse=True)
    files = files[:fetch_limit]

    is_series = any(re.search(r"s\d{1,2}.*e\d{1,4}", str(file.file_name).lower()) for file in files)

    first_word = original_query.split()[0] if original_query.split() else original_query
    orig_re = re.compile(rf"^[\s._\-\[\(]*{re.escape(original_query)}")
    first_re = re.compile(rf"^[\s._\-\[\(]*{re.escape(first_word)}")

    def _normalize_exact(text):
        # strip extension, collapse every separator (., _, -, [](), spaces) to
        # a single space, so "Money.Heist.S01E01.1080p" and
        # "money heist s01e01 1080p" compare equal.
        text = str(text).lower()
        text = re.sub(r"\.\w{2,4}$", "", text)
        text = re.sub(r"[\s._\-\[\]\(\)]+", " ", text)
        return text.strip()

    exact_query_norm = _normalize_exact(original_query)

    # 🎯 EXACT MATCH FIX: a file whose whole name (ignoring dots/underscores/
    # brackets/extension) equals the search query exactly now ranks above
    # everything else — even above files that merely *start with* the query.
    def _is_exact(x):
        return _normalize_exact(x.file_name) == exact_query_norm

    # 🚀 RECENT FILE FIX: enumerate() का इस्तेमाल ताकि DB का newest-first order सुरक्षित रहे (idx = 0 मतलब सबसे नई फाइल)
    # `files` is now already true-recency sorted across ALL databases combined, so idx 0 really is the newest file.
    indexed_files = list(enumerate(files))

    group_min_idx = {}
    for idx, x in indexed_files:
        grp = (x.title or x.file_name).strip().lower()
        if grp not in group_min_idx or idx < group_min_idx[grp]:
            group_min_idx[grp] = idx

    def _unified_key(item):
        idx, x = item  # idx 0, 1, 2... (0 is most recent)
        name_lower = x.file_name.lower()
        grp = (x.title or x.file_name).strip().lower()
        file_is_series = is_series_file(x.file_name)
        season, episode = extract_season_episode(x.file_name) if file_is_series else (0, 0)
        return (
            not _is_exact(x),              # 🎯 exact match always first
            not orig_re.match(name_lower),
            not first_re.match(name_lower),
            group_min_idx[grp],            # 🚀 नए title/series का group पहले आएगा
            -season,                       # उसी title के अंदर: बड़ा season/episode पहले
            -episode,
            idx,                           # आख़िरी tie-break, और non-series files के लिए recency
            -extract_quality(x.file_name),
            -extract_source(x.file_name),
        )

    indexed_files = sorted(indexed_files, key=_unified_key)

    # वापस ओरिजिनल फाइल ऑब्जेक्ट्स निकालें
    sorted_files = [x for idx, x in indexed_files]

    # Keep the already-ranked result pool for pagination and repeated filters.
    if len(_SEARCH_POOL_CACHE) >= _SEARCH_POOL_MAX_ENTRIES:
        oldest_key = min(_SEARCH_POOL_CACHE, key=lambda k: _SEARCH_POOL_CACHE[k][0])
        _SEARCH_POOL_CACHE.pop(oldest_key, None)
    _SEARCH_POOL_CACHE[pool_key] = (datetime.utcnow(), sorted_files, total_results)

    paginated_files = sorted_files[offset:offset + max_results]

    next_offset = offset + max_results
    if next_offset >= total_results or len(paginated_files) < max_results:
        next_offset = ""

    result = (paginated_files, next_offset, total_results)
    _search_cache_set(cache_key, result)
    return result



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
    files = []
    for media_cls in MEDIA_DBS:
        cursor = media_cls.find(filter).sort('$natural', -1)
        files.extend(await cursor.to_list(length=(await media_cls.count_documents(filter))))
    total_results = len(files)
    return files, total_results

async def update_cover_url(file_id: str, cover_url: str) -> bool:
    try:
        for media_cls in MEDIA_DBS:
            result = await media_cls.collection.update_one(
                {"_id": file_id},
                {"$set": {"cover": cover_url}}
            )
            if result.modified_count:
                return True
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
        if filedetails:
            break
    return filedetails


async def dreamxbotz_fetch_media(limit: int) -> list:
    try:
        target_media = MEDIA_DBS[0] if len(MEDIA_DBS) == 1 else await get_active_media_db()
        # 🚀 sort by real upload timestamp (file_date) instead of $natural,
        # which is not a reliable "most recently inserted" order once a
        # collection has had updates/compaction.
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
        name_part = re.sub(r"\s+", " ", name_part).strip()

        filename_cleaned = name_part

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
        pattern = r"(?:s\d{1,2}|season\s*\d+|season\d+)(?:\s*combined)?(?:e\d{1,4}|episode\s*\d+)?\b"

        for file in cursor:
            file_name = getattr(file, "file_name", "")

            parts = file_name.rsplit(".", 1)
            name_part = parts[0]
            ext = parts[1] if len(parts) > 1 else ""

            name_part = re.sub(r"[._\-]+", " ", name_part)
            name_part = re.sub(r"\s+", " ", name_part).strip()

            file_name_cleaned = name_part

            if not re.search(pattern, file_name_cleaned, re.IGNORECASE):
                title = await dreamxbotz_clean_title(file_name_cleaned)
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
        pattern = r"(.*?)(?:S(\d{1,2})|Season\s*(\d+)|Season(\d+))(?:\s*Combined)?(?:E(\d{1,4})|Episode\s*(\d+))?\b"

        for file in cursor:
            file_name = getattr(file, "file_name", "")

            parts = file_name.rsplit(".", 1)
            name_part = parts[0]
            ext = parts[1] if len(parts) > 1 else ""

            name_part = re.sub(r"[._\-]+", " ", name_part)
            name_part = re.sub(r"\s+", " ", name_part).strip()

            file_name_cleaned = name_part

            match = re.search(pattern, file_name_cleaned, re.IGNORECASE)
            if match:
                title_raw = match.group(1)
                title = await dreamxbotz_clean_title(title_raw, is_series=True)
                if ext:
                    title = f"{title}.{ext}"

                season = int(match.group(2) or match.group(3) or match.group(4))
                grouped[title].append(season)

        return {
            title: sorted(set(seasons))[:10]
            for title, seasons in grouped.items()
            if seasons
        }
    except Exception as e:
        logger.error(f"Error in dreamxbotz_get_series: {e}")
        return []
