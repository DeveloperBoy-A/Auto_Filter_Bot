import re
import logging
import asyncio
import math
from typing import Optional, Tuple, List
from collections import defaultdict
from difflib import SequenceMatcher

from database.ia_filterdb import Media, Media2, MEDIA_DBS
from info import MULTIPLE_DB, ADMINS

from pyrogram import Client, filters
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton

logger = logging.getLogger(__name__)
logger.setLevel(logging.WARNING)

QUALITY_CLEANUP_SEMAPHORE = asyncio.Semaphore(2)
QUALITY_TASK_LOCK = asyncio.Lock()

QUALITY_ACTIVE_TASK = None
QUALITY_TASKS = {}
CANCEL_Q_TASKS = {}
DRY_RUN_CACHE = {}

# =========================================================
# QUALITY HIERARCHY
# =========================================================
QUALITY_HIERARCHY = {
    "camrip": 1, "cam rip": 1, "hdcam": 1, "hd cam": 1,
    "hdtc": 2, "hd tc": 2, "hdts": 2, "hd ts": 2,
    "ts": 2, "tc": 2, "telesync": 2,
    "predvd": 3, "predvdrip": 3, "pre dvd": 3,
    "dvdscr": 3, "dvd scr": 3,
    "dvdrip": 4, "dvd rip": 4,
    "tvrip": 5, "tv rip": 5, "hdtv": 5, "hd tv": 5,
    "webrip": 6, "web rip": 6,
    "web-dl": 7, "web dl": 7, "webdl": 7,
    "hdrip": 8, "hd rip": 8,
    "bluray": 9, "blu ray": 9,
    "bdrip": 9, "bd rip": 9, "brrip": 9, "br rip": 9,
}

# Resolution is extracted/reporting only.
RESOLUTION_HIERARCHY = {
    "140p": 1, "240p": 1, "360p": 2, "480p": 3, "540p": 4,
    "720p": 5, "1080p": 6, "1440p": 7, "2160p": 8, "4k": 8,
}

LANGUAGES = {
    "hindi": [r"\bhindi\b", r"\bhin\b", r"\bhi\b"],
    "english": [r"\benglish\b", r"\beng\b", r"\ben\b"],
    "tamil": [r"\btamil\b", r"\btam\b", r"\bta\b"],
    "telugu": [r"\btelugu\b", r"\btel\b", r"\bte\b"],
    "malayalam": [r"\bmalayalam\b", r"\bmal\b", r"\bml\b"],
    "kannada": [r"\bkannada\b", r"\bkan\b", r"\bkn\b"],
    "punjabi": [r"\bpunjabi\b", r"\bpan\b", r"\bpbi\b", r"\bpa\b"],
    "bengali": [r"\bbengali\b", r"\bben\b", r"\bbn\b"],
    "marathi": [r"\bmarathi\b", r"\bmar\b", r"\bmr\b"],
    "gujarati": [r"\bgujarati\b", r"\bguj\b", r"\bgujrat\b", r"\bgu\b"],
    "urdu": [r"\burdu\b", r"\burd\b"],
    "korean": [r"\bkorean\b", r"\bkor\b"],
    "japanese": [r"\bjapanese\b", r"\bjpn\b"],
}

LOW_QUALITY_SOURCES = [
    "camrip", "cam rip", "hdcam", "hd cam", "hdtc", "hd tc",
    "hdts", "hd ts", "ts", "tc", "telesync", "predvd",
    "predvdrip", "pre dvd", "dvdscr", "dvd scr",
]

MEDIUM_QUALITY_SOURCES = [
    "dvdrip", "dvd rip", "tvrip", "tv rip", "hdtv", "hd tv",
]

HIGH_QUALITY_SOURCES = [
    "webrip", "web rip", "web-dl", "web dl", "webdl",
    "hdrip", "hd rip", "bluray", "blu ray", "bdrip", "bd rip",
    "brrip", "br rip",
]

TITLE_NOISE_WORDS = {
    "hevc", "x265", "x264", "h264", "avc", "av1", "aac", "flac",
    "dts", "ac3", "eac3", "ddp", "ddp5", "ddp51", "dd5", "dd51",
    "51", "71", "20", "dub", "dubbed", "sub", "subs", "esub",
    "esubs", "multi", "proper", "uncut", "repack", "extended",
    "complete", "season", "episode", "ep", "nf", "netflix",
    "amzn", "amazon", "prime", "primevideo", "sonyliv", "sony",
    "sliv", "hotstar", "jio", "jhs", "zee5", "aha", "hbo",
    "paramount", "apple", "hoichoi", "sunnxt", "viki",
    "movies4u", "tokyo", "updates", "telly", "www",
    "web", "dl", "rip", "ray", "blu", "bd", "br", "cam",
    "tc", "ts", "hd", "dvd", "scr", "tv", "pre",
}

QUALITY_ALIASES = sorted(QUALITY_HIERARCHY.keys(), key=len, reverse=True)
RESOLUTION_ALIASES = sorted(RESOLUTION_HIERARCHY.keys(), key=len, reverse=True)

# =========================================================
# LANGUAGE & QUALITY EXTRACTION
# =========================================================
def extract_language(text: str) -> List[str]:
    text = (text or "").lower()
    found = []
    for lang, patterns in LANGUAGES.items():
        for pattern in patterns:
            try:
                if re.search(pattern, text):
                    if lang not in found:
                        found.append(lang)
                    break
            except Exception:
                pass
    return found if found else ["unknown"]

def extract_quality_info(filename: str, caption: str = "") -> dict:
    text = f"{filename or ''} {caption or ''}".lower()
    info = {
        "source": None, "resolution": None,
        "quality_score": 0, "source_score": 0, "resolution_score": 0,
    }

    matches = []
    for source, score in QUALITY_HIERARCHY.items():
        pattern = rf"(?<![a-z0-9]){re.escape(source)}(?![a-z0-9])"
        if re.search(pattern, text, re.I):
            matches.append((score, source))

    if matches:
        score, source = max(matches, key=lambda x: x[0])
        info["source"] = source
        info["source_score"] = score

    res_matches = []
    for res, score in RESOLUTION_HIERARCHY.items():
        pattern = rf"(?<![a-z0-9]){re.escape(res)}(?![a-z0-9])"
        if re.search(pattern, text, re.I):
            res_matches.append((score, res))

    if res_matches:
        score, res = max(res_matches, key=lambda x: x[0])
        info["resolution"] = res
        info["resolution_score"] = score

    info["quality_score"] = (info["source_score"] * 0.7 + info["resolution_score"] * 0.3)
    return info

def is_low_quality_print(quality_info: dict) -> bool:
    return (quality_info.get("source") or "").lower().strip() in LOW_QUALITY_SOURCES

def is_high_quality(quality_info: dict) -> bool:
    return (quality_info.get("source") or "").lower().strip() in HIGH_QUALITY_SOURCES

# =========================================================
# SAFE TITLE / MOVIE IDENTITY
# =========================================================
def get_base_title(filename: str) -> str:
    text = (filename or "").strip().lower()
    text = re.sub(r"\.(mkv|mp4|avi|mov|wmv|flv|webm|m4v|mpg|mpeg)$", "", text, flags=re.I)
    text = re.sub(r"https?://\S+|www\.\S+|@\S+", " ", text, flags=re.I)
    text = re.sub(r"\[(?:[^\]]*)\]|\((?!(?:19|20)\d{2}\s*$)[^\)]*\)|\{[^\}]*\}", " ", text, flags=re.I)
    text = re.sub(r"[._\-–—]+", " ", text)

    patterns = [
        r"\bs\d{1,2}\s*e\d{1,3}(?:\s*(?:to|-)\s*e?\d{1,3})?\b",
        r"\bs\d{1,2}\b", r"\bseason\s*\d{1,2}\b",
        r"\bepisode\s*\d{1,3}\b", r"\bep(?:isode)?\s*\d{1,3}(?:\s*(?:to|-)\s*(?:ep(?:isode)?)?\s*\d{1,3})?\b",
    ]
    for pattern in patterns:
        text = re.sub(pattern, " ", text, flags=re.I)

    for q in QUALITY_ALIASES + RESOLUTION_ALIASES:
        text = re.sub(rf"(?<![a-z0-9]){re.escape(q)}(?![a-z0-9])", " ", text, flags=re.I)

    noise_pattern = (
        r"\b(?:hevc|x265|x264|h264|avc|av1|aac|flac|dts|ac3|eac3|"
        r"ddp(?:5\.1)?|dd(?:5\.1)?|5\.1|7\.1|2\.0|"
        r"dub(?:bed)?|sub(?:s)?|esub(?:s)?|multi|proper|uncut|repack|"
        r"extended|complete|"
        r"hindi|english|tamil|telugu|malayalam|kannada|punjabi|"
        r"bengali|marathi|gujarati|urdu|korean|japanese|"
        r"hin|eng|tam|tel|mal|kan|pan|pbi|ben|mar|guj|urd|kor|jpn|"
        r"hi|en|ta|te|ml|kn|pa|bn|mr|gu|"
        r"nf|netflix|amzn|amazon|prime|primevideo|sonyliv|sony|sliv|"
        r"hotstar|jio|jhs|zee5|aha|hbo|max|paramount|apple|hoichoi|"
        r"sunnxt|viki|movies4u|telly|tokyo_updates|tokyoupdates)\b"
    )
    text = re.sub(noise_pattern, " ", text, flags=re.I)
    text = re.sub(r"\b(?:web|dl|rip|bluray|bdrip|brrip|hdrip|dvdrip|dvdscr)\b", " ", text, flags=re.I)
    text = re.sub(r"[^a-z0-9\u0900-\u097f]+", " ", text, flags=re.I)
    text = re.sub(r"\s+", " ", text).strip()

    tokens = text.split()
    while tokens and tokens[0] in {"movie", "film"}: tokens.pop(0)
    while tokens and tokens[-1] in {"movie", "film"}: tokens.pop()

    return " ".join(tokens)

def _title_tokens(title: str) -> List[str]:
    return [x for x in re.findall(r"[a-z0-9\u0900-\u097f]+", (title or "").lower()) if x]

def _extract_year(title: str) -> Optional[str]:
    match = re.search(r"\b((?:19|20)\d{2})\b", title or "")
    return match.group(1) if match else None

def same_movie_title(title_a: str, title_b: str) -> bool:
    # OPTIMIZED: Relaxed matching logic to get more results
    a = get_base_title(title_a)
    b = get_base_title(title_b)
    if not a or not b: return False

    year_a = _extract_year(title_a)
    year_b = _extract_year(title_b)
    if year_a and year_b and year_a != year_b: return False
    if a == b: return True

    ta = set(_title_tokens(a))
    tb = set(_title_tokens(b))
    if not ta or not tb: return False

    intersection = ta & tb
    union = ta | tb
    jaccard = len(intersection) / len(union) if union else 0
    ratio = SequenceMatcher(None, a, b).ratio()

    # Relaxed rules
    if jaccard >= 0.60 or ratio >= 0.80:
        return True
    return False

# =========================================================
# LANGUAGE MATCH & DELETE DECISION
# =========================================================
def languages_match(old_langs, new_langs) -> bool:
    old_set = set(old_langs or [])
    new_set = set(new_langs or [])
    if "unknown" in old_set or "unknown" in new_set: return False
    return old_set <= new_set

def _quality_score(source: str) -> int:
    return QUALITY_HIERARCHY.get((source or "").lower().strip(), 0)

def should_delete_existing(existing_quality: dict, new_quality: dict, existing_langs: List[str], new_langs: List[str]) -> bool:
    try:
        old_source = (existing_quality.get("source") or "").lower().strip()
        new_source = (new_quality.get("source") or "").lower().strip()
        if not old_source or not new_source: return False
        if old_source in HIGH_QUALITY_SOURCES: return False
        if old_source not in LOW_QUALITY_SOURCES and old_source not in MEDIUM_QUALITY_SOURCES: return False
        if new_source not in QUALITY_HIERARCHY: return False

        old_q = _quality_score(old_source)
        new_q = _quality_score(new_source)
        if old_q <= 0 or new_q <= 0 or new_q <= old_q: return False
        if not languages_match(existing_langs, new_langs): return False
        return True
    except Exception as e:
        logger.error("[QUALITY] should_delete_existing error: %s", e)
        return False

def should_delete_file_against_files(current: dict, all_files: List[dict]) -> bool:
    source = (current.get("quality") or "").lower().strip()
    if source in HIGH_QUALITY_SOURCES: return False
    if source not in LOW_QUALITY_SOURCES and source not in MEDIUM_QUALITY_SOURCES: return False
    
    current_q = _quality_score(source)
    if current_q <= 0: return False

    for other in all_files:
        if other is current: continue
        other_source = (other.get("quality") or "").lower().strip()
        if not other_source or other_source not in QUALITY_HIERARCHY: continue
        if not languages_match(current.get("languages", []), other.get("languages", [])): continue

        other_q = _quality_score(other_source)
        if other_q > current_q: return True
    return False

# =========================================================
# AUTOMATIC BACKGROUND QUALITY CLEANUP
# =========================================================
async def quality_yield(counter: int, every: int = 100):
    if counter % every == 0:
        await asyncio.sleep(0.01)

async def find_and_delete_lower_quality(db_collection, new_filename: str, new_caption: str = "", file_id: Optional[str] = None) -> Tuple[bool, str]:
    try:
        new_quality = extract_quality_info(new_filename, new_caption or "")
        new_source = (new_quality.get("source") or "").lower().strip()
        
        if new_source not in HIGH_QUALITY_SOURCES and new_source not in MEDIUM_QUALITY_SOURCES:
            return True, "New file is low quality"

        base_title = get_base_title(new_filename)
        if not base_title: return True, "Could not extract title"
        
        new_langs = extract_language(f"{new_filename} {new_caption or ''}")
        words = [w for w in _title_tokens(base_title) if len(w) > 2]
        if not words: return True, "No significant words"

        pattern = ".*".join(re.escape(w) for w in words[:5])
        search_query = {"file_name": {"$regex": pattern, "$options": "i"}}
        if file_id: search_query["_id"] = {"$ne": file_id}

        cursor = db_collection.find(search_query, projection={"_id": 1, "file_name": 1, "caption": 1}, batch_size=100).limit(500)
        
        processed, deleted_count = 0, 0
        try:
            async for file_in_db in cursor:
                processed += 1
                existing_filename = file_in_db.get("file_name", "")
                existing_caption = file_in_db.get("caption", "") or ""

                if not same_movie_title(new_filename, existing_filename):
                    await quality_yield(processed, 50); continue

                existing_quality = extract_quality_info(existing_filename, existing_caption)
                existing_source = (existing_quality.get("source") or "").lower().strip()
                if not existing_source or existing_source in HIGH_QUALITY_SOURCES:
                    await quality_yield(processed, 50); continue

                existing_langs = extract_language(f"{existing_filename} {existing_caption}")
                if not languages_match(existing_langs, new_langs):
                    await quality_yield(processed, 50); continue

                if not should_delete_existing(existing_quality, new_quality, existing_langs, new_langs):
                    await quality_yield(processed, 50); continue

                try:
                    result = await db_collection.delete_one({"_id": file_in_db["_id"]})
                    if result.deleted_count:
                        deleted_count += 1
                        logger.warning("[QUALITY] Deleted %s: %s -> %s", existing_source.upper(), existing_filename[:100], new_source.upper())
                except Exception as e: logger.error("[QUALITY] Delete error: %s", e)
                await quality_yield(processed, 50)
        finally:
            try: await cursor.close()
            except Exception: pass

        if deleted_count: return True, f"Deleted {deleted_count} lower-quality files"
        return True, "No lower quality files"
    except Exception as e: return False, str(e)

async def run_quality_cleanup_background(media_dbs, file_name: str, caption: str):
    async with QUALITY_CLEANUP_SEMAPHORE:
        try:
            for idx, media_cls in enumerate(media_dbs, start=1):
                success, msg = await find_and_delete_lower_quality(db_collection=media_cls.collection, new_filename=file_name, new_caption=caption)
                if success and "Deleted" in msg:
                    logger.warning("[QUALITY DB%d] %s -> %s", idx, file_name[:60], msg)
        except Exception as e:
            logger.error("[QUALITY] Background cleanup failed: %s", e)

# =========================================================
# STREAM MONGODB COLLECTION (OPTIMIZED)
# =========================================================
async def stream_collection_files(collection, task_id, projection=None, batch_size=1000):
    # OPTIMIZED: MongoDB pre-filtering to prevent full DB scans
    quality_keywords = (
        "camrip|hdcam|hdtc|hdts|telesync|predvd|dvdscr|dvdrip|"
        "tvrip|hdtv|webrip|web-dl|webdl|hdrip|bluray|bdrip|brrip"
    )
    query = {"file_name": {"$regex": quality_keywords, "$options": "i"}}

    cursor = collection.find(query, projection=projection, batch_size=batch_size)
    counter = 0
    try:
        async for document in cursor:
            if CANCEL_Q_TASKS.get(task_id): break
            counter += 1
            yield document
            
            # Explicitly yield event loop
            if counter % 100 == 0:
                await asyncio.sleep(0.01)
    finally:
        try: await cursor.close()
        except Exception: pass

# =========================================================
# BUILD MOVIE GROUPS (OPTIMIZED)
# =========================================================
async def build_movie_groups(collection, task_id, p_state, total_docs, msg=None, cancel_markup=None, dry_run=False):
    movies = defaultdict(list)
    projection = {"_id": 1, "file_name": 1, "caption": 1}

    async for file in stream_collection_files(collection, task_id, projection=projection, batch_size=500):
        if CANCEL_Q_TASKS.get(task_id): return None

        p_state["count"] += 1
        count = p_state["count"]
        file_name = file.get("file_name", "")
        caption = file.get("caption", "") or ""

        if not file_name: continue
        base_title = get_base_title(file_name)
        if base_title:
            quality = extract_quality_info(file_name, caption)
            languages = extract_language(f"{file_name} {caption}")
            movies[base_title].append({
                "file_id": file.get("_id"),
                "name": file_name,
                "quality": quality.get("source"),
                "resolution": quality.get("resolution"),
                "languages": languages,
                "score": quality.get("quality_score", 0),
            })

        if count % 100 == 0: await asyncio.sleep(0.01)
        
        # Edit less frequently to prevent FloodWait
        if msg and count % 2500 == 0:
            percent = (count / total_docs * 100) if total_docs else 0
            try:
                mode = "DRY RUN" if dry_run else "DELETE"
                delete_text = "🗑️ Delete: DISABLED" if dry_run else "🗑️ Delete: ACTIVE"
                await msg.edit_text(
                    f"🔍 **QUALITY SCAN - {mode}**\n\n"
                    f"📁 Scanned: **{count:,} / {total_docs:,}**\n"
                    f"⏳ Progress: **{percent:.1f}%**\n\n"
                    "🧠 Memory: Streaming Mode\n🤖 Bot: Online\n"
                    f"{delete_text}",
                    reply_markup=cancel_markup,
                )
            except Exception: pass

    return movies

async def analyze_movie_groups(movies, task_id):
    total_delete, duplicate_movies = 0, []
    if not movies: return 0, []
    checked = 0
    for base_title, files in movies.items():
        if CANCEL_Q_TASKS.get(task_id): return None, None
        if len(files) <= 1: continue

        movie_delete = 0
        for file in files:
            if CANCEL_Q_TASKS.get(task_id): return None, None
            if should_delete_file_against_files(file, files): movie_delete += 1
            checked += 1
            if checked % 100 == 0: await asyncio.sleep(0.01)

        if movie_delete:
            total_delete += movie_delete
            duplicate_movies.append({"title": base_title, "count": len(files), "to_delete": movie_delete})
    return total_delete, duplicate_movies

# =========================================================
# SINGLE MOVIE FINDER (OPTIMIZED)
# =========================================================
async def find_single_movie_files(collection, movie_name, task_id, max_files=1000):
    base_title = get_base_title(movie_name)
    if not base_title: return []

    words = [w for w in _title_tokens(base_title) if len(w) > 1]
    if not words: return []

    # OPTIMIZED: Removed strict \b boundaries
    pattern = ".*".join(re.escape(w) for w in words[:3])
    cursor = collection.find(
        {"file_name": {"$regex": pattern, "$options": "i"}},
        projection={"_id": 1, "file_name": 1, "caption": 1},
        batch_size=200,
    )

    results, processed = [], 0
    try:
        async for file in cursor:
            if CANCEL_Q_TASKS.get(task_id): break
            processed += 1
            
            if same_movie_title(movie_name, file.get("file_name", "")):
                results.append(file)
            if len(results) >= max_files: break
            if processed % 50 == 0: await asyncio.sleep(0.01)
    finally:
        try: await cursor.close()
        except Exception: pass
    return results

# =========================================================
# BACKGROUND TASK RUNNER & CANCEL BUTTON
# =========================================================
async def run_quality_task(task_id, worker):
    global QUALITY_ACTIVE_TASK
    async with QUALITY_TASK_LOCK:
        QUALITY_ACTIVE_TASK = task_id
        QUALITY_TASKS[task_id] = asyncio.current_task()
        try: await worker()
        except asyncio.CancelledError: logger.warning("[QUALITY] Task cancelled: %s", task_id)
        except Exception as e: logger.error("[QUALITY] Background task error: %s", e)
        finally:
            QUALITY_TASKS.pop(task_id, None)
            CANCEL_Q_TASKS.pop(task_id, None)
            QUALITY_ACTIVE_TASK = None

@Client.on_callback_query(filters.regex(r"^cancel_q_task_(.*)"))
async def cancel_q_task(client, query):
    task_id = query.data.split("cancel_q_task_", 1)[-1]
    if task_id in CANCEL_Q_TASKS:
        CANCEL_Q_TASKS[task_id] = True
        await query.answer("🛑 Cancellation requested...", show_alert=True)
    else: await query.answer("⚠️ Task already finished.", show_alert=True)

async def auto_delete_msg(msg, command_msg, task_id, delay=300):
    try:
        await asyncio.sleep(delay)
        DRY_RUN_CACHE.pop(task_id, None)
        await msg.delete()
        await command_msg.delete()
    except Exception: pass

# =========================================================
# PAGINATION
# =========================================================
async def send_dry_page(msg, task_id, page):
    data = DRY_RUN_CACHE.get(task_id)
    if not data:
        if hasattr(msg, "edit_text"): return await msg.edit_text("❌ Data expired or auto-deleted.\nPlease run command again.")
        return await msg.message.edit_text("❌ Data expired or auto-deleted.\nPlease run command again.")

    ITEMS_PER_PAGE = 15
    total_files = len(data["files"])
    total_pages = math.ceil(total_files / ITEMS_PER_PAGE) if total_files else 1
    page = max(0, min(page, total_pages - 1))
    chunk = data["files"][page * ITEMS_PER_PAGE: (page + 1) * ITEMS_PER_PAGE]

    report = f"📊 **DRY RUN - SINGLE MOVIE**\n━━━━━━━━━━━━━━━━━━━━\n🎬 Movie: **{data['movie_name']}**\n📁 Found: **{total_files}** files\n📋 **Page {page + 1}/{total_pages}**\n━━━━━━━━━━━━━━━━━━━━\n"
    for f_text in chunk: report += f_text
    report += f"\n━━━━━━━━━━━━━━━━━━━━\n⚠️ **PREVIEW SUMMARY**\n✅ Will KEEP: **{data['keep']}**\n❌ Will DELETE: **{data['delete']}**\n\n"
    
    if data["delete"] > 0: report += f"👉 **Confirm & Delete:**\n`/cleanup_confirm_single {data['movie_name']}`\n\n"
    else: report += "ℹ️ No files to delete.\n\n"
    report += "⏱️ Auto-delete in 5 minutes."

    buttons = []
    if page > 0: buttons.append(InlineKeyboardButton("⬅️ Previous", callback_data=f"dry_page_{task_id}_{page - 1}"))
    if page < total_pages - 1: buttons.append(InlineKeyboardButton("Next ➡️", callback_data=f"dry_page_{task_id}_{page + 1}"))
    reply_markup = InlineKeyboardMarkup([buttons]) if buttons else None

    try:
        if hasattr(msg, "edit_text"): await msg.edit_text(report, reply_markup=reply_markup)
        else: await msg.message.edit_text(report, reply_markup=reply_markup)
    except Exception as e: logger.error("[QUALITY] Pagination error: %s", e)

@Client.on_callback_query(filters.regex(r"^dry_page_"))
async def dry_page_callback(client, query):
    try:
        parts = query.data.split("_")
        await send_dry_page(query, parts[2], int(parts[3]))
        await query.answer()
    except Exception: await query.answer("❌ Page expired.", show_alert=True)

# =========================================================
# /QUALITY_REPORT
# =========================================================
@Client.on_message(filters.command("quality_report") & filters.user(ADMINS))
async def quality_report_cmd(bot, message):
    global QUALITY_ACTIVE_TASK
    if QUALITY_ACTIVE_TASK: return await message.reply_text("⏳ **Task Already Running**")

    task_id = str(message.id)
    CANCEL_Q_TASKS[task_id] = False
    cancel_markup = InlineKeyboardMarkup([[InlineKeyboardButton("🛑 CANCEL", callback_data=f"cancel_q_task_{task_id}")]])
    msg = await message.reply_text("📊 **QUALITY REPORT STARTED**\n⏳ Scanning database...", reply_markup=cancel_markup)

    async def worker():
        try:
            total_docs = sum([await media_cls.collection.estimated_document_count() for media_cls in MEDIA_DBS])
            if total_docs == 0: return await msg.edit_text("❌ **DATABASE EMPTY**")

            processed, quality_dist, resolution_dist = 0, defaultdict(int), defaultdict(int)
            for media_cls in MEDIA_DBS:
                cursor = media_cls.collection.find({}, projection={"file_name": 1, "caption": 1}, batch_size=500)
                try:
                    async for file in cursor:
                        if CANCEL_Q_TASKS.get(task_id): return await msg.edit_text("🛑 **CANCELLED**")
                        processed += 1
                        filename = file.get("file_name", "")
                        caption = file.get("caption", "") or ""
                        qi = extract_quality_info(filename, caption)
                        quality_dist[qi.get("source") or "unknown"] += 1
                        resolution_dist[qi.get("resolution") or "unknown"] += 1
                        
                        if processed % 100 == 0: await asyncio.sleep(0.01)
                        if processed % 5000 == 0:
                            try: await msg.edit_text(f"📊 **REPORTING**\n📁 Scanned: **{processed:,}/{total_docs:,}**", reply_markup=cancel_markup)
                            except Exception: pass
                finally:
                    try: await cursor.close()
                    except Exception: pass

            report = f"📊 **QUALITY REPORT**\n📁 **Total:** {total_docs:,}\n\n🎬 **SOURCE QUALITY**\n"
            for q in ["camrip", "hdcam", "hdts", "predvd", "dvdscr", "dvdrip", "tvrip", "webrip", "web-dl", "hdrip", "bluray", "bdrip", "brrip", "unknown"]:
                if q in quality_dist: report += f"{q.upper()}: {quality_dist[q]:,} ({(quality_dist[q]/total_docs*100):.1f}%)\n"
            await msg.edit_text(report, reply_markup=None)
        except Exception as e: await msg.edit_text(f"❌ **ERROR**\n`{str(e)[:1000]}`")

    asyncio.create_task(run_quality_task(task_id, worker))

# =========================================================
# SINGLE CLEANUP COMMANDS
# =========================================================
@Client.on_message(filters.command("cleanup_dry_single") & filters.user(ADMINS))
async def cleanup_dry_single_cmd(bot, message):
    global QUALITY_ACTIVE_TASK
    if len(message.command) < 2: return await message.reply_text("❌ **Usage:** `/cleanup_dry_single Movie Name`")
    if QUALITY_ACTIVE_TASK: return await message.reply_text("⏳ **Task Already Running**")

    movie_name = " ".join(message.command[1:])
    task_id = str(message.id)
    CANCEL_Q_TASKS[task_id] = False
    cancel_markup = InlineKeyboardMarkup([[InlineKeyboardButton("🛑 CANCEL", callback_data=f"cancel_q_task_{task_id}")]])
    msg = await message.reply_text(f"🔍 **DRY RUN**\n🎬 **{movie_name}**\n⏳ Scanning...", reply_markup=cancel_markup)

    async def worker():
        try:
            all_files = []
            for media_cls in MEDIA_DBS:
                all_files.extend(await find_single_movie_files(media_cls.collection, movie_name, task_id))
            
            if CANCEL_Q_TASKS.get(task_id): return await msg.edit_text("🛑 **CANCELLED**")
            if not all_files: return await msg.edit_text(f"❌ No files found for **{movie_name}**")

            files_info = []
            for file in all_files:
                fname, caption = file.get("file_name", "Unknown"), file.get("caption", "") or ""
                qi = extract_quality_info(fname, caption)
                files_info.append({"name": fname, "quality": qi.get("source"), "resolution": qi.get("resolution"), "languages": extract_language(f"{fname} {caption}")})
                if len(files_info) % 50 == 0: await asyncio.sleep(0.01)

            to_delete = sum(1 for f in files_info if should_delete_file_against_files(f, files_info))
            formatted_files = []
            for idx, file in enumerate(files_info, 1):
                status = "❌ DELETE" if should_delete_file_against_files(file, files_info) else "✅ KEEP"
                formatted_files.append(f"\n**{idx}. {status}**\n📄 {file['name'][:70]}\n🎞️ Quality: {(file['quality'] or 'N/A').upper()}\n")

            DRY_RUN_CACHE[task_id] = {"movie_name": movie_name, "files": formatted_files, "keep": len(files_info) - to_delete, "delete": to_delete}
            await send_dry_page(msg, task_id, 0)
            asyncio.create_task(auto_delete_msg(msg, message, task_id, 300))
        except Exception as e: await msg.edit_text(f"❌ **ERROR**\n`{str(e)[:1000]}`")

    asyncio.create_task(run_quality_task(task_id, worker))

@Client.on_message(filters.command("cleanup_confirm_single") & filters.user(ADMINS))
async def cleanup_confirm_single_cmd(bot, message):
    global QUALITY_ACTIVE_TASK
    if len(message.command) < 2: return await message.reply_text("❌ **Usage:** `/cleanup_confirm_single Movie Name`")
    if QUALITY_ACTIVE_TASK: return await message.reply_text("⏳ **Task Already Running**")

    movie_name = " ".join(message.command[1:])
    task_id = str(message.id)
    CANCEL_Q_TASKS[task_id] = False
    cancel_markup = InlineKeyboardMarkup([[InlineKeyboardButton("🛑 CANCEL DELETE", callback_data=f"cancel_q_task_{task_id}")]])
    msg = await message.reply_text(f"⚠️ **DELETE STARTED**\n🎬 **{movie_name}**\n⏳ Processing...", reply_markup=cancel_markup)

    async def worker():
        try:
            total_deleted, deleted_files = 0, []
            for media_cls in MEDIA_DBS:
                collection = media_cls.collection
                files = await find_single_movie_files(collection, movie_name, task_id)
                if not files: continue

                files_info = []
                for file in files:
                    fname, caption = file.get("file_name", "Unknown"), file.get("caption", "") or ""
                    qi = extract_quality_info(fname, caption)
                    files_info.append({"file_id": file.get("_id"), "name": fname, "quality": qi.get("source"), "languages": extract_language(f"{fname} {caption}")})
                    if len(files_info) % 50 == 0: await asyncio.sleep(0.01)

                delete_candidates = [f for f in files_info if should_delete_file_against_files(f, files_info)]
                for file in delete_candidates:
                    if CANCEL_Q_TASKS.get(task_id): return await msg.edit_text(f"🛑 **CANCELLED**\nDeleted: **{total_deleted}**")
                    try:
                        res = await collection.delete_one({"_id": file["file_id"]})
                        if res.deleted_count:
                            total_deleted += 1
                            deleted_files.append(file["name"])
                    except Exception: pass
                    if total_deleted and total_deleted % 25 == 0: await asyncio.sleep(0.01)

            if total_deleted:
                preview = "".join(f"{i}. {fname[:65]}\n" for i, fname in enumerate(deleted_files[:10], 1))
                await msg.edit_text(f"✅ **CLEANUP DONE**\n🎬 **{movie_name}**\n🗑️ Deleted: **{total_deleted}**\n📋 Sample:\n{preview}")
            else: await msg.edit_text(f"ℹ️ **NOTHING DELETED**\n🎬 **{movie_name}**\nNo eligible lower-quality versions found.")
        except Exception as e: await msg.edit_text(f"❌ **ERROR**\n`{str(e)[:1000]}`")

    asyncio.create_task(run_quality_task(task_id, worker))

# =========================================================
# BATCH CLEANUP COMMANDS
# =========================================================
@Client.on_message(filters.command("cleanup_dry_batch") & filters.user(ADMINS))
async def cleanup_dry_batch_cmd(bot, message):
    global QUALITY_ACTIVE_TASK
    if QUALITY_ACTIVE_TASK: return await message.reply_text("⏳ **Task Already Running**")

    task_id = str(message.id)
    CANCEL_Q_TASKS[task_id] = False
    cancel_markup = InlineKeyboardMarkup([[InlineKeyboardButton("🛑 CANCEL", callback_data=f"cancel_q_task_{task_id}")]])
    msg = await message.reply_text("🔍 **DRY RUN STARTED**\n⏳ Scanning filtered database...", reply_markup=cancel_markup)

    async def worker():
        try:
            total_docs = sum([await media_cls.collection.estimated_document_count() for media_cls in MEDIA_DBS])
            if total_docs == 0: return await msg.edit_text("❌ **DATABASE EMPTY**")

            p_state, total_movies, total_delete, all_duplicates = {"count": 0}, 0, 0, []
            for media_cls in MEDIA_DBS:
                if CANCEL_Q_TASKS.get(task_id): return await msg.edit_text("🛑 **CANCELLED**")
                movies = await build_movie_groups(media_cls.collection, task_id, p_state, total_docs, msg, cancel_markup, dry_run=True)
                if movies is None: return await msg.edit_text("🛑 **CANCELLED**")
                
                delete_count, duplicates = await analyze_movie_groups(movies, task_id)
                if delete_count is None: return await msg.edit_text("🛑 **CANCELLED**")
                
                total_movies += len(movies)
                total_delete += delete_count
                all_duplicates.extend(duplicates)

            all_duplicates.sort(key=lambda x: x["to_delete"], reverse=True)
            report = f"📊 **DRY RUN BATCH**\n📁 Scanned: **{p_state['count']:,}**\n🎬 Movies: **{total_movies:,}**\n⚠️ **Would Delete: {total_delete:,}**\n\n"
            if all_duplicates:
                report += "📋 **TOP DUPLICATES**\n"
                for i, movie in enumerate(all_duplicates[:10], 1): report += f"**{i}. {movie['title'][:45]}** (Del: {movie['to_delete']})\n"
                report += "\n👉 `/cleanup_confirm_batch`"
            else: report += "✅ **NO CLEANUP REQUIRED**"
            await msg.edit_text(report, reply_markup=None)
        except Exception as e: await msg.edit_text(f"❌ **ERROR**\n`{str(e)[:1000]}`")

    asyncio.create_task(run_quality_task(task_id, worker))

@Client.on_message(filters.command("cleanup_confirm_batch") & filters.user(ADMINS))
async def cleanup_confirm_batch_cmd(bot, message):
    global QUALITY_ACTIVE_TASK
    if QUALITY_ACTIVE_TASK: return await message.reply_text("⏳ **Task Already Running**")

    task_id = str(message.id)
    CANCEL_Q_TASKS[task_id] = False
    cancel_markup = InlineKeyboardMarkup([[InlineKeyboardButton("🛑 CANCEL DELETE", callback_data=f"cancel_q_task_{task_id}")]])
    msg = await message.reply_text("⚠️ **BATCH DELETE STARTED**\n⏳ Processing filtered database...", reply_markup=cancel_markup)

    async def worker():
        try:
            total_docs = sum([await media_cls.collection.estimated_document_count() for media_cls in MEDIA_DBS])
            if total_docs == 0: return await msg.edit_text("❌ **DATABASE EMPTY**")

            p_state, total_deleted, movies_cleaned_set = {"count": 0}, 0, set()
            for media_cls in MEDIA_DBS:
                if CANCEL_Q_TASKS.get(task_id): return await msg.edit_text(f"🛑 **CANCELLED**\nDeleted: **{total_deleted}**")
                collection = media_cls.collection
                movies = await build_movie_groups(collection, task_id, p_state, total_docs, msg, cancel_markup, dry_run=False)
                if movies is None: return await msg.edit_text(f"🛑 **CANCELLED**\nDeleted: **{total_deleted}**")

                for base_title, files in movies.items():
                    if CANCEL_Q_TASKS.get(task_id): return await msg.edit_text(f"🛑 **CANCELLED**\nDeleted: **{total_deleted}**")
                    if len(files) <= 1: continue
                    delete_candidates = [f for f in files if should_delete_file_against_files(f, files)]
                    for file in delete_candidates:
                        try:
                            res = await collection.delete_one({"_id": file["file_id"]})
                            if res.deleted_count:
                                total_deleted += 1
                                movies_cleaned_set.add(base_title)
                        except Exception: pass
                        if total_deleted and total_deleted % 25 == 0: await asyncio.sleep(0.01)

            if total_deleted: await msg.edit_text(f"✅ **BATCH DONE**\n🗑️ Total Deleted: **{total_deleted:,}**\n🎬 Movies Cleaned: **{len(movies_cleaned_set):,}**")
            else: await msg.edit_text("ℹ️ **NOTHING DELETED**\nNo eligible duplicates found.")
        except Exception as e: await msg.edit_text(f"❌ **ERROR**\n`{str(e)[:1000]}`")

    asyncio.create_task(run_quality_task(task_id, worker))

# =========================================================
# NEW: YEAR-WISE CLEANUP COMMANDS
# =========================================================
@Client.on_message(filters.command("cleanup_dry_year") & filters.user(ADMINS))
async def cleanup_dry_year_cmd(bot, message):
    global QUALITY_ACTIVE_TASK
    if len(message.command) < 2: return await message.reply_text("❌ **Usage:** `/cleanup_dry_year 2024`")
    year = message.command[1]
    if not year.isdigit() or len(year) != 4: return await message.reply_text("❌ Please enter a valid 4-digit year.")
    if QUALITY_ACTIVE_TASK: return await message.reply_text("⏳ **Task Already Running.**")

    task_id = str(message.id)
    CANCEL_Q_TASKS[task_id] = False
    cancel_markup = InlineKeyboardMarkup([[InlineKeyboardButton("🛑 CANCEL", callback_data=f"cancel_q_task_{task_id}")]])
    msg = await message.reply_text(f"🔍 **DRY RUN FOR: {year}**\n⏳ Scanning...", reply_markup=cancel_markup)

    async def worker():
        try:
            total_movies, total_delete, all_duplicates = 0, 0, []
            for media_cls in MEDIA_DBS:
                if CANCEL_Q_TASKS.get(task_id): break
                collection = media_cls.collection
                cursor = collection.find({"file_name": {"$regex": rf"\b{year}\b"}}, projection={"_id": 1, "file_name": 1, "caption": 1}, batch_size=500)
                
                movies, processed = defaultdict(list), 0
                async for file in cursor:
                    if CANCEL_Q_TASKS.get(task_id): break
                    processed += 1
                    file_name, caption = file.get("file_name", ""), file.get("caption", "") or ""
                    base_title = get_base_title(file_name)
                    
                    if base_title:
                        quality = extract_quality_info(file_name, caption)
                        movies[base_title].append({"file_id": file.get("_id"), "name": file_name, "quality": quality.get("source"), "languages": extract_language(f"{file_name} {caption}")})
                    
                    if processed % 100 == 0: await asyncio.sleep(0.01)
                    if processed % 1000 == 0:
                        try: await msg.edit_text(f"🔍 **DRY RUN: {year}**\n📁 Scanned: **{processed:,}**", reply_markup=cancel_markup)
                        except Exception: pass

                for base_title, files in movies.items():
                    if len(files) > 1:
                        movie_delete = sum(1 for f in files if should_delete_file_against_files(f, files))
                        if movie_delete > 0:
                            total_delete += movie_delete
                            all_duplicates.append({"title": base_title, "count": len(files), "to_delete": movie_delete})
                total_movies += len(movies)

            all_duplicates.sort(key=lambda x: x["to_delete"], reverse=True)
            report = f"📊 **YEAR {year} DRY RUN**\n🎬 Movies: **{total_movies:,}**\n⚠️ **Would Delete: {total_delete:,}**\n\n"
            if all_duplicates:
                report += "📋 **TOP DUPLICATES**\n"
                for i, movie in enumerate(all_duplicates[:10], 1): report += f"**{i}. {movie['title'][:45]}** (Del: {movie['to_delete']})\n"
                report += f"\n👉 `/cleanup_confirm_year {year}`"
            await msg.edit_text(report, reply_markup=None)
        except Exception as e: await msg.edit_text(f"❌ **ERROR**\n`{str(e)[:500]}`")

    asyncio.create_task(run_quality_task(task_id, worker))

@Client.on_message(filters.command("cleanup_confirm_year") & filters.user(ADMINS))
async def cleanup_confirm_year_cmd(bot, message):
    global QUALITY_ACTIVE_TASK
    if len(message.command) < 2: return await message.reply_text("❌ **Usage:** `/cleanup_confirm_year 2024`")
    year = message.command[1]
    if not year.isdigit() or len(year) != 4: return await message.reply_text("❌ Please enter a valid 4-digit year.")
    if QUALITY_ACTIVE_TASK: return await message.reply_text("⏳ **Task Already Running.**")

    task_id = str(message.id)
    CANCEL_Q_TASKS[task_id] = False
    cancel_markup = InlineKeyboardMarkup([[InlineKeyboardButton("🛑 CANCEL DELETE", callback_data=f"cancel_q_task_{task_id}")]])
    msg = await message.reply_text(f"⚠️ **DELETE STARTED: {year}**\n⏳ Processing...", reply_markup=cancel_markup)

    async def worker():
        try:
            total_deleted = 0
            for media_cls in MEDIA_DBS:
                if CANCEL_Q_TASKS.get(task_id): break
                collection = media_cls.collection
                cursor = collection.find({"file_name": {"$regex": rf"\b{year}\b"}}, projection={"_id": 1, "file_name": 1, "caption": 1}, batch_size=500)
                
                movies, processed = defaultdict(list), 0
                async for file in cursor:
                    if CANCEL_Q_TASKS.get(task_id): break
                    processed += 1
                    file_name, caption = file.get("file_name", ""), file.get("caption", "") or ""
                    base_title = get_base_title(file_name)
                    
                    if base_title:
                        quality = extract_quality_info(file_name, caption)
                        movies[base_title].append({"file_id": file.get("_id"), "name": file_name, "quality": quality.get("source"), "languages": extract_language(f"{file_name} {caption}")})
                    if processed % 100 == 0: await asyncio.sleep(0.01)

                for base_title, files in movies.items():
                    if CANCEL_Q_TASKS.get(task_id): break
                    if len(files) > 1:
                        delete_candidates = [f for f in files if should_delete_file_against_files(f, files)]
                        for file in delete_candidates:
                            try:
                                res = await collection.delete_one({"_id": file["file_id"]})
                                if res.deleted_count: total_deleted += 1
                            except Exception: pass
                            await asyncio.sleep(0.01)

            await msg.edit_text(f"✅ **YEAR {year} DONE**\n🗑️ Total Deleted: **{total_deleted:,}** files.", reply_markup=None)
        except Exception as e: await msg.edit_text(f"❌ **ERROR**\n`{str(e)[:500]}`")

    asyncio.create_task(run_quality_task(task_id, worker))

# =========================================================
# HELP MENU
# =========================================================
@Client.on_message(filters.command("quality_help") & filters.user(ADMINS))
async def quality_help_cmd(bot, message):
    help_text = """
🛠️ **QUALITY MANAGER**
━━━━━━━━━━━━━━━━━━━━
📊 **1. REPORT**
`/quality_report`
➡️ Database ki quality/resolution report.

🔍 **2. SINGLE MOVIE**
`/cleanup_dry_single Movie Name`
`/cleanup_confirm_single Movie Name`

📅 **3. YEAR WISE BATCH (Recommended)**
`/cleanup_dry_year 2024`
`/cleanup_confirm_year 2024`
➡️ Fast aur safe, VPS hang nahi hoga.

🗑️ **4. FULL DATABASE BATCH**
`/cleanup_dry_batch`
`/cleanup_confirm_batch`
➡️ Poore database par scan. (1M+ files me slow ho sakta hai).
━━━━━━━━━━━━━━━━━━━━
🛡️ Same movie title + compatible language check active.
"""
    await message.reply_text(help_text)
