import re
import logging
import asyncio
import math
from typing import Optional, Tuple, List
from collections import defaultdict

from database.ia_filterdb import Media, Media2, MEDIA_DBS
from info import MULTIPLE_DB, ADMINS

from pyrogram import Client, filters
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton


logger = logging.getLogger(__name__)

# =========================================================
# LOGGING
# =========================================================

# Quality manager ke normal INFO/DEBUG logs hide.
# Sirf WARNING / ERROR important logs console me aayenge.
logger.setLevel(logging.WARNING)


# =========================================================
# BACKGROUND CLEANUP LIMIT
# =========================================================

# Upload ke time automatic cleanup ke liye maximum
# 2 concurrent cleanup scans.
QUALITY_CLEANUP_SEMAPHORE = asyncio.Semaphore(2)


# Admin heavy commands ke liye sirf 1 command ek time par.
QUALITY_TASK_LOCK = asyncio.Lock()

QUALITY_ACTIVE_TASK = None
QUALITY_TASKS = {}

CANCEL_Q_TASKS = {}
DRY_RUN_CACHE = {}


# =========================================================
# QUALITY HIERARCHY
# =========================================================

QUALITY_HIERARCHY = {
    "camrip": 1,
    "cam rip": 1,
    "hdcam": 1,
    "hd cam": 1,

    "hdtc": 2,
    "hd tc": 2,
    "hdts": 2,
    "hd ts": 2,
    "ts": 2,
    "tc": 2,
    "telesync": 2,

    "predvd": 3,
    "predvdrip": 3,
    "pre dvd": 3,
    "dvdscr": 3,
    "dvd scr": 3,

    "dvdrip": 4,
    "dvd rip": 4,

    "tvrip": 5,
    "tv rip": 5,
    "hdtv": 5,
    "hd tv": 5,

    "webrip": 6,
    "web rip": 6,

    "web-dl": 7,
    "web dl": 7,
    "webdl": 7,

    "hdrip": 8,
    "hd rip": 8,

    "bluray": 9,
    "blu ray": 9,
    "bdrip": 9,
    "bd rip": 9,
    "brrip": 9,
    "br rip": 9,
}


# =========================================================
# RESOLUTION HIERARCHY
# =========================================================

RESOLUTION_HIERARCHY = {
    "240p": 1,
    "140p": 1,
    "360p": 2,
    "480p": 3,
    "540p": 4,
    "720p": 5,
    "1080p": 6,
    "1440p": 7,
    "2160p": 8,
    "4k": 8,
}


# =========================================================
# LANGUAGES
# =========================================================

LANGUAGES = {
    "hindi": [
        r"\bhindi\b",
        r"\bhin\b",
        r"\bhi\b",
    ],

    "english": [
        r"\benglish\b",
        r"\beng\b",
        r"\ben\b",
    ],

    "tamil": [
        r"\btamil\b",
        r"\btam\b",
        r"\bta\b",
    ],

    "telugu": [
        r"\btelugu\b",
        r"\btel\b",
        r"\bte\b",
    ],

    "malayalam": [
        r"\bmalayalam\b",
        r"\bmal\b",
        r"\bml\b",
    ],

    "kannada": [
        r"\bkannada\b",
        r"\bkan\b",
        r"\bkn\b",
    ],

    "punjabi": [
        r"\bpunjabi\b",
        r"\bpan\b",
        r"\bpbi\b",
        r"\bpa\b",
    ],

    "bengali": [
        r"\bbengali\b",
        r"\bben\b",
        r"\bbn\b",
    ],

    "marathi": [
        r"\bmarathi\b",
        r"\bmar\b",
        r"\bmr\b",
    ],

    "gujarati": [
        r"\bgujarati\b",
        r"\bguj\b",
        r"\bgujrat\b",
        r"\bgu\b",
    ],
}


# =========================================================
# QUALITY GROUPS
# =========================================================

LOW_QUALITY_SOURCES = [
    "camrip",
    "cam rip",
    "hdcam",
    "hd cam",
    "hdtc",
    "hd tc",
    "hdts",
    "hd ts",
    "ts",
    "tc",
    "telesync",
    "predvd",
    "predvdrip",
    "pre dvd",
    "dvdscr",
    "dvd scr",
]


MEDIUM_QUALITY_SOURCES = [
    "dvdrip",
    "dvd rip",
    "tvrip",
    "tv rip",
    "hdtv",
    "hd tv",
]


HIGH_QUALITY_SOURCES = [
    "webrip",
    "web rip",
    "web-dl",
    "web dl",
    "webdl",
    "hdrip",
    "hd rip",
    "bluray",
    "blu ray",
    "bdrip",
    "bd rip",
    "brrip",
    "br rip",
]


# =========================================================
# LANGUAGE EXTRACTION
# =========================================================

def extract_language(text: str) -> List[str]:
    """
    Text me se ALL languages detect karta hai.
    """

    text = (text or "").lower()

    found_languages = []

    for lang, patterns in LANGUAGES.items():

        for pattern in patterns:

            try:
                if re.search(pattern, text):
                    if lang not in found_languages:
                        found_languages.append(lang)

                    break

            except Exception:
                continue

    return found_languages if found_languages else ["unknown"]


# =========================================================
# QUALITY EXTRACTION
# =========================================================

def extract_quality_info(
    filename: str,
    caption: str = ""
) -> dict:

    text = f"{filename or ''} {caption or ''}".lower()

    quality_info = {
        "source": None,
        "resolution": None,
        "quality_score": 0,
        "source_score": 0,
        "resolution_score": 0,
    }

    # -----------------------------------------------------
    # Source
    # -----------------------------------------------------

    for source, score in QUALITY_HIERARCHY.items():

        pattern = (
            rf"[._\-\s]{re.escape(source)}[._\-\s]"
            rf"|^{re.escape(source)}[._\-\s]"
            rf"|[._\-\s]{re.escape(source)}$"
        )

        try:

            if re.search(pattern, text):

                quality_info["source"] = source
                quality_info["source_score"] = score

                break

        except Exception:
            continue

    # -----------------------------------------------------
    # Resolution
    # -----------------------------------------------------

    for res, score in RESOLUTION_HIERARCHY.items():

        pattern = (
            rf"[._\-\s]{re.escape(res)}[._\-\s]"
            rf"|^{re.escape(res)}[._\-\s]"
            rf"|[._\-\s]{re.escape(res)}$"
        )

        try:

            if re.search(pattern, text):

                quality_info["resolution"] = res
                quality_info["resolution_score"] = score

                break

        except Exception:
            continue

    quality_info["quality_score"] = (
        quality_info["source_score"] * 0.7
    ) + (
        quality_info["resolution_score"] * 0.3
    )

    return quality_info


def is_low_quality_print(
    quality_info: dict
) -> bool:

    return (
        quality_info.get("source")
        in LOW_QUALITY_SOURCES
    )


def is_high_quality(
    quality_info: dict
) -> bool:

    return (
        quality_info.get("source")
        in HIGH_QUALITY_SOURCES
    )


# =========================================================
# QUALITY DELETE DECISION
# =========================================================

def should_delete_existing(
    existing_quality: dict,
    new_quality: dict,
    existing_langs: List[str],
    new_langs: List[str]
) -> bool:

    try:

        existing_source = (
            existing_quality.get("source")
            or ""
        ).lower().strip()

        new_source = (
            new_quality.get("source")
            or ""
        ).lower().strip()

        if not existing_source or not new_source:
            return False

        # -------------------------------------------------
        # HIGH QUALITY NEVER DELETE
        # -------------------------------------------------

        if existing_source in HIGH_QUALITY_SOURCES:
            return False

        # -------------------------------------------------
        # LANGUAGE MATCH
        # -------------------------------------------------

        existing_set = set(
            existing_langs or []
        )

        new_set = set(
            new_langs or []
        )

        if "unknown" not in existing_set:

            if not existing_set <= new_set:
                return False

        # -------------------------------------------------
        # LOW -> HIGH
        # -------------------------------------------------

        if existing_source in LOW_QUALITY_SOURCES:

            if new_source in HIGH_QUALITY_SOURCES:
                return True

            if new_source in MEDIUM_QUALITY_SOURCES:
                return True

        # -------------------------------------------------
        # MEDIUM -> HIGH
        # -------------------------------------------------

        if existing_source in MEDIUM_QUALITY_SOURCES:

            if new_source in HIGH_QUALITY_SOURCES:
                return True

        return False

    except Exception as e:

        logger.error(
            f"[QUALITY] should_delete_existing error: {e}",
            exc_info=True
        )

        return False


# =========================================================
# BASE TITLE
# =========================================================

def get_base_title(
    filename: str
) -> str:

    text = (filename or "").lower()

    # Extension remove
    text = re.sub(
        r"\.(mkv|mp4|avi|mov|wmv|flv|webm)$",
        "",
        text,
        flags=re.I
    )

    # Separators
    text = re.sub(
        r"[._-]+",
        " ",
        text
    )

    # Season
    text = re.sub(
        r"\bs\d{1,2}\b|\bseason\s*\d{1,2}\b",
        "",
        text,
        flags=re.I
    )

    # Episode
    text = re.sub(
        r"\be\d{1,2}\b|\bepisode\s*\d{1,2}\b",
        "",
        text,
        flags=re.I
    )

    # Quality / resolution
    for quality in (
        list(QUALITY_HIERARCHY.keys())
        + list(RESOLUTION_HIERARCHY.keys())
    ):

        text = re.sub(
            rf"\b{re.escape(quality)}\b",
            "",
            text,
            flags=re.I
        )

    # Codec / language / misc
    text = re.sub(
        r"\b("
        r"hevc|x265|x264|h264|avc|av1|aac|flac|dts|ac3|"
        r"eac3|ddp|ddp5\.1|dd5\.1|5\.1|7\.1|2\.0|"
        r"dub|sub|esub|esubs|multi|proper|uncut|"
        r"hindi|english|tamil|telugu|malayalam|kannada|"
        r"punjabi|bengali|marathi|gujarati|"
        r"movies4u|tokyo_updates|telly|amzn|nf|dsnp|"
        r"hin|eng|tam|tel|mal|kan|pan|ben|mar|guj|"
        r"hi|en|ta|te|ml|kn|pa|bn|mr|gu"
        r")\b",
        "",
        text,
        flags=re.I
    )

    # Brackets
    text = re.sub(
        r"[\[\(\{].*?[\]\)\}]",
        "",
        text
    )

    # Spaces
    text = re.sub(
        r"\s+",
        " ",
        text
    ).strip()

    return text


# =========================================================
# LANGUAGE MATCH
# =========================================================

def languages_match(
    old_langs,
    new_langs
) -> bool:

    old_set = set(
        old_langs or []
    )

    new_set = set(
        new_langs or []
    )

    # Unknown language ko safely keep karenge.
    if "unknown" in old_set:
        return True

    return old_set <= new_set


# =========================================================
# GENERIC QUALITY DELETE DECISION
# =========================================================

def can_delete_quality_file(
    file_quality,
    file_langs,
    all_files
) -> bool:

    source = (
        file_quality or ""
    ).lower().strip()

    # =====================================================
    # HIGH = NEVER DELETE
    # =====================================================

    if source in HIGH_QUALITY_SOURCES:
        return False

    # =====================================================
    # MEDIUM -> HIGH
    # =====================================================

    if source in MEDIUM_QUALITY_SOURCES:

        for better in all_files:

            better_source = (
                better.get("quality")
                or ""
            ).lower().strip()

            if (
                better_source
                not in HIGH_QUALITY_SOURCES
            ):
                continue

            if languages_match(
                file_langs,
                better.get("languages", [])
            ):
                return True

        return False

    # =====================================================
    # LOW -> HIGH / MEDIUM
    # =====================================================

    if source in LOW_QUALITY_SOURCES:

        for better in all_files:

            better_source = (
                better.get("quality")
                or ""
            ).lower().strip()

            if (
                better_source not in HIGH_QUALITY_SOURCES
                and
                better_source not in MEDIUM_QUALITY_SOURCES
            ):
                continue

            if languages_match(
                file_langs,
                better.get("languages", [])
            ):
                return True

        return False

    return False


# =========================================================
# EVENT LOOP YIELD
# =========================================================

async def quality_yield(
    counter: int,
    every: int = 100
):

    if counter % every == 0:
        await asyncio.sleep(0)


# =========================================================
# AUTOMATIC QUALITY CLEANUP
# =========================================================

async def find_and_delete_lower_quality(
    db_collection,
    new_filename: str,
    new_caption: str = "",
    file_id: Optional[str] = None
) -> Tuple[bool, str]:

    try:

        new_quality = extract_quality_info(
            new_filename,
            new_caption or ""
        )

        new_source = (
            new_quality.get("source")
            or ""
        ).lower().strip()

        # Sirf HIGH/MEDIUM new files cleanup trigger karein.
        if (
            new_source not in HIGH_QUALITY_SOURCES
            and
            new_source not in MEDIUM_QUALITY_SOURCES
        ):

            return True, "New file is low quality"

        base_title = get_base_title(
            new_filename
        )

        if not base_title:

            return True, "Could not extract title"

        new_langs = extract_language(
            f"{new_filename} {new_caption or ''}"
        )

        words = [
            w
            for w in base_title.split()
            if len(w) > 2
        ]

        if not words:

            return True, "No significant words"

        # Maximum 4 words for search.
        pattern = ".*".join(
            re.escape(w)
            for w in words[:4]
        )

        search_query = {
            "file_name": {
                "$regex": pattern,
                "$options": "i"
            }
        }

        if file_id:

            search_query["_id"] = {
                "$ne": file_id
            }

        cursor = db_collection.find(
            search_query,
            projection={
                "_id": 1,
                "file_name": 1,
                "caption": 1
            },
            batch_size=100
        ).limit(300)

        processed = 0
        deleted_count = 0

        try:

            async for file_in_db in cursor:

                processed += 1

                if processed >= 500:
                    break

                existing_filename = (
                    file_in_db.get(
                        "file_name",
                        ""
                    )
                )

                existing_caption = (
                    file_in_db.get(
                        "caption",
                        ""
                    )
                    or ""
                )

                existing_base_title = get_base_title(
                    existing_filename
                )

                existing_words = set(
                    w
                    for w in existing_base_title.split()
                    if len(w) > 2
                )

                new_words = set(words)

                common_words = (
                    existing_words
                    & new_words
                )

                # At least 2 common words.
                if len(common_words) < 2:
                    await quality_yield(
                        processed,
                        50
                    )
                    continue

                existing_quality = extract_quality_info(
                    existing_filename,
                    existing_caption
                )

                existing_source = (
                    existing_quality.get("source")
                    or ""
                ).lower().strip()

                # HIGH NEVER DELETE.
                if (
                    existing_source
                    in HIGH_QUALITY_SOURCES
                ):
                    await quality_yield(
                        processed,
                        50
                    )
                    continue

                existing_langs = extract_language(
                    f"{existing_filename} "
                    f"{existing_caption}"
                )

                should_delete = (
                    should_delete_existing(
                        existing_quality,
                        new_quality,
                        existing_langs,
                        new_langs
                    )
                )

                if should_delete:

                    try:

                        result = await (
                            db_collection.delete_one(
                                {
                                    "_id":
                                    file_in_db["_id"]
                                }
                            )
                        )

                        if result.deleted_count:

                            deleted_count += 1

                            logger.warning(
                                "[QUALITY] Deleted %s: %s "
                                "-> %s",
                                existing_source.upper(),
                                existing_filename[:70],
                                new_source.upper()
                            )

                    except Exception as e:

                        logger.error(
                            f"[QUALITY] Delete error: {e}"
                        )

                await quality_yield(
                    processed,
                    50
                )

        finally:

            try:
                await cursor.close()
            except Exception:
                pass

        if deleted_count:

            return (
                True,
                f"Deleted {deleted_count} "
                f"LOW/MEDIUM quality files"
            )

        return True, "No lower quality files"

    except Exception as e:

        logger.error(
            "[QUALITY] find_and_delete error: %s",
            e,
            exc_info=True
        )

        return False, str(e)


# =========================================================
# BACKGROUND AUTO CLEANUP
# =========================================================

async def run_quality_cleanup_background(
    media_dbs,
    file_name: str,
    caption: str
):

    async with QUALITY_CLEANUP_SEMAPHORE:

        try:

            for idx, media_cls in enumerate(
                media_dbs,
                start=1
            ):

                success, msg = (
                    await find_and_delete_lower_quality(
                        db_collection=media_cls.collection,
                        new_filename=file_name,
                        new_caption=caption
                    )
                )

                if (
                    success
                    and "Deleted" in msg
                ):

                    logger.warning(
                        "[QUALITY DB%d] %s -> %s",
                        idx,
                        file_name[:60],
                        msg
                    )

        except Exception as e:

            logger.error(
                "[QUALITY] Background cleanup failed: %s",
                e,
                exc_info=True
            )


# =========================================================
# STREAM MONGODB COLLECTION
# =========================================================

async def stream_collection_files(
    collection,
    task_id,
    projection=None,
    batch_size=200
):

    cursor = collection.find(
        {},
        projection=projection,
        batch_size=batch_size
    )

    counter = 0

    try:

        async for document in cursor:

            if CANCEL_Q_TASKS.get(task_id):

                break

            counter += 1

            yield document

            if counter % 100 == 0:

                await asyncio.sleep(0)

    finally:

        try:
            await cursor.close()
        except Exception:
            pass


# =========================================================
# BUILD MOVIE GROUPS
# =========================================================

async def build_movie_groups(
    collection,
    task_id,
    p_state,
    total_docs,
    msg=None,
    cancel_markup=None,
    dry_run=False
):

    movies = defaultdict(list)

    projection = {
        "_id": 1,
        "file_name": 1,
        "caption": 1
    }

    async for file in stream_collection_files(
        collection,
        task_id,
        projection=projection,
        batch_size=200
    ):

        if CANCEL_Q_TASKS.get(task_id):

            return None

        p_state["count"] += 1

        count = p_state["count"]

        file_name = file.get(
            "file_name",
            ""
        )

        caption = (
            file.get(
                "caption",
                ""
            )
            or ""
        )

        if not file_name:
            continue

        base_title = get_base_title(
            file_name
        )

        if not base_title:
            continue

        quality = extract_quality_info(
            file_name,
            caption
        )

        languages = extract_language(
            f"{file_name} {caption}"
        )

        movies[base_title].append({
            "file_id": file.get("_id"),
            "name": file_name,
            "quality": quality.get("source"),
            "resolution": quality.get("resolution"),
            "languages": languages,
            "score": quality.get(
                "quality_score",
                0
            )
        })

        # -------------------------------------------------
        # Event loop release
        # -------------------------------------------------

        if count % 100 == 0:

            await asyncio.sleep(0)

        # -------------------------------------------------
        # Progress
        # -------------------------------------------------

        if (
            msg
            and count % 5000 == 0
        ):

            percent = (
                count / total_docs * 100
                if total_docs
                else 0
            )

            try:

                mode = (
                    "DRY RUN"
                    if dry_run
                    else "DELETE"
                )

                delete_text = (
                    "🗑️ Delete: DISABLED"
                    if dry_run
                    else "🗑️ Delete: ACTIVE"
                )

                await msg.edit_text(
                    f"🔍 **QUALITY SCAN - {mode}**\n\n"
                    f"📁 Scanned: "
                    f"**{count:,} / {total_docs:,}**\n"
                    f"⏳ Progress: "
                    f"**{percent:.1f}%**\n\n"
                    "🧠 Memory: Streaming Mode\n"
                    "🤖 Bot: Online\n"
                    f"{delete_text}",
                    reply_markup=cancel_markup
                )

            except Exception:
                pass

    return movies


# =========================================================
# ANALYZE MOVIE GROUPS
# =========================================================

async def analyze_movie_groups(
    movies,
    task_id
):

    total_delete = 0
    duplicate_movies = []

    if not movies:

        return 0, []

    checked = 0

    for base_title, files in movies.items():

        if CANCEL_Q_TASKS.get(task_id):

            return None, None

        if len(files) <= 1:

            continue

        movie_delete = 0

        for file in files:

            source = (
                file.get("quality")
                or ""
            ).lower().strip()

            # HIGH NEVER DELETE.
            if source in HIGH_QUALITY_SOURCES:

                continue

            if can_delete_quality_file(
                source,
                file.get("languages", []),
                files
            ):

                movie_delete += 1

            checked += 1

            if checked % 100 == 0:

                await asyncio.sleep(0)

        if movie_delete > 0:

            total_delete += movie_delete

            duplicate_movies.append({
                "title": base_title,
                "count": len(files),
                "to_delete": movie_delete
            })

    return (
        total_delete,
        duplicate_movies
    )


# =========================================================
# SINGLE MOVIE FINDER
# =========================================================

async def find_single_movie_files(
    collection,
    movie_name,
    task_id,
    max_files=1000
):

    base_title = get_base_title(
        movie_name
    )

    if not base_title:

        return []

    words = [
        w
        for w in base_title.split()
        if len(w) > 1
    ]

    if not words:

        return []

    pattern = ".*".join(
        rf"\b{re.escape(w)}\b"
        for w in words[:5]
    )

    cursor = collection.find(
        {
            "file_name": {
                "$regex": pattern,
                "$options": "i"
            }
        },
        projection={
            "_id": 1,
            "file_name": 1,
            "caption": 1
        },
        batch_size=100
    )

    results = []

    try:

        async for file in cursor:

            if CANCEL_Q_TASKS.get(task_id):

                break

            results.append(file)

            if len(results) >= max_files:

                break

            if len(results) % 50 == 0:

                await asyncio.sleep(0)

    finally:

        try:
            await cursor.close()
        except Exception:
            pass

    return results


# =========================================================
# BACKGROUND TASK RUNNER
# =========================================================

async def run_quality_task(
    task_id,
    worker
):

    global QUALITY_ACTIVE_TASK

    async with QUALITY_TASK_LOCK:

        QUALITY_ACTIVE_TASK = task_id

        QUALITY_TASKS[
            task_id
        ] = asyncio.current_task()

        try:

            await worker()

        except asyncio.CancelledError:

            logger.warning(
                "[QUALITY] Task cancelled: %s",
                task_id
            )

        except Exception as e:

            logger.error(
                "[QUALITY] Background task error: %s",
                e,
                exc_info=True
            )

        finally:

            QUALITY_TASKS.pop(
                task_id,
                None
            )

            CANCEL_Q_TASKS.pop(
                task_id,
                None
            )

            QUALITY_ACTIVE_TASK = None


# =========================================================
# CANCEL CALLBACK
# =========================================================

@Client.on_callback_query(
    filters.regex(r"^cancel_q_task_(.*)")
)
async def cancel_q_task(
    client,
    query
):

    task_id = query.data.split(
        "cancel_q_task_",
        1
    )[-1]

    if task_id in CANCEL_Q_TASKS:

        CANCEL_Q_TASKS[
            task_id
        ] = True

        await query.answer(
            "🛑 Cancellation requested...",
            show_alert=True
        )

    else:

        await query.answer(
            "⚠️ Task already finished.",
            show_alert=True
        )


# =========================================================
# AUTO DELETE DRY RUN MESSAGE
# =========================================================

async def auto_delete_msg(
    msg,
    command_msg,
    task_id,
    delay=300
):

    try:

        await asyncio.sleep(delay)

        DRY_RUN_CACHE.pop(
            task_id,
            None
        )

        await msg.delete()

        await command_msg.delete()

    except Exception:
        pass


# =========================================================
# DRY RUN PAGINATION
# =========================================================

async def send_dry_page(
    msg,
    task_id,
    page
):

    data = DRY_RUN_CACHE.get(
        task_id
    )

    if not data:

        if hasattr(msg, "edit_text"):

            return await msg.edit_text(
                "❌ Data expired or auto-deleted.\n"
                "Please run command again."
            )

        return await msg.message.edit_text(
            "❌ Data expired or auto-deleted.\n"
            "Please run command again."
        )

    ITEMS_PER_PAGE = 15

    total_files = len(
        data["files"]
    )

    total_pages = (
        math.ceil(
            total_files / ITEMS_PER_PAGE
        )
        if total_files > 0
        else 1
    )

    page = max(
        0,
        min(page, total_pages - 1)
    )

    start_idx = (
        page * ITEMS_PER_PAGE
    )

    end_idx = (
        start_idx
        + ITEMS_PER_PAGE
    )

    chunk = data["files"][
        start_idx:end_idx
    ]

    report = (
        "📊 **DRY RUN - SINGLE MOVIE**\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
        f"🎬 Movie: **{data['movie_name']}**\n"
        f"📁 Found: **{total_files}** files\n\n"
        f"📋 **Page {page + 1}/{total_pages}**\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
    )

    for f_text in chunk:

        report += f_text

    report += (
        "\n━━━━━━━━━━━━━━━━━━━━\n"
        "⚠️ **PREVIEW SUMMARY**\n"
        f"✅ Will KEEP: **{data['keep']}**\n"
        f"❌ Will DELETE: **{data['delete']}**\n\n"
    )

    if data["delete"] > 0:

        report += (
            "👉 **Confirm & Delete:**\n"
            f"`/cleanup_confirm_single "
            f"{data['movie_name']}`\n\n"
        )

    else:

        report += (
            "ℹ️ No files to delete.\n\n"
        )

    report += (
        "⏱️ Auto-delete in 5 minutes."
    )

    buttons = []

    if page > 0:

        buttons.append(
            InlineKeyboardButton(
                "⬅️ Previous",
                callback_data=(
                    f"dry_page_"
                    f"{task_id}_"
                    f"{page - 1}"
                )
            )
        )

    if page < total_pages - 1:

        buttons.append(
            InlineKeyboardButton(
                "Next ➡️",
                callback_data=(
                    f"dry_page_"
                    f"{task_id}_"
                    f"{page + 1}"
                )
            )
        )

    reply_markup = (
        InlineKeyboardMarkup([buttons])
        if buttons
        else None
    )

    try:

        if hasattr(msg, "edit_text"):

            await msg.edit_text(
                report,
                reply_markup=reply_markup
            )

        else:

            await msg.message.edit_text(
                report,
                reply_markup=reply_markup
            )

    except Exception as e:

        logger.error(
            "[QUALITY] Pagination error: %s",
            e
        )


# =========================================================
# PAGINATION CALLBACK
# =========================================================

@Client.on_callback_query(
    filters.regex(r"^dry_page_")
)
async def dry_page_callback(
    client,
    query
):

    try:

        parts = query.data.split("_")

        task_id = parts[2]

        page = int(parts[3])

        await send_dry_page(
            query,
            task_id,
            page
        )

        await query.answer()

    except Exception:

        await query.answer(
            "❌ Page expired.",
            show_alert=True
        )


# =========================================================
# /QUALITY_REPORT
# =========================================================

@Client.on_message(
    filters.command("quality_report")
    & filters.user(ADMINS)
)
async def quality_report_cmd(
    bot,
    message
):

    global QUALITY_ACTIVE_TASK

    if QUALITY_ACTIVE_TASK:

        return await message.reply_text(
            "⏳ **QUALITY TASK ALREADY RUNNING**\n\n"
            "Pehle current quality process complete "
            "hone do."
        )

    task_id = str(
        message.id
    )

    CANCEL_Q_TASKS[
        task_id
    ] = False

    cancel_markup = InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                "🛑 CANCEL",
                callback_data=(
                    f"cancel_q_task_{task_id}"
                )
            )
        ]
    ])

    msg = await message.reply_text(
        "📊 **QUALITY REPORT STARTED**\n\n"
        "🧠 Streaming mode enabled.\n"
        "🤖 Bot normal kaam karta rahega.\n\n"
        "⏳ Scanning database...",
        reply_markup=cancel_markup
    )

    async def worker():

        try:

            total_docs = 0

            for media_cls in MEDIA_DBS:

                total_docs += await (
                    media_cls.collection
                    .estimated_document_count()
                )

            if total_docs == 0:

                return await msg.edit_text(
                    "❌ **DATABASE EMPTY**"
                )

            processed = 0

            quality_dist = defaultdict(int)
            resolution_dist = defaultdict(int)

            for media_cls in MEDIA_DBS:

                cursor = media_cls.collection.find(
                    {},
                    projection={
                        "file_name": 1,
                        "caption": 1
                    },
                    batch_size=200
                )

                try:

                    async for file in cursor:

                        if CANCEL_Q_TASKS.get(
                            task_id
                        ):

                            return await msg.edit_text(
                                "🛑 **QUALITY REPORT CANCELLED**"
                            )

                        processed += 1

                        filename = file.get(
                            "file_name",
                            ""
                        )

                        caption = (
                            file.get(
                                "caption",
                                ""
                            )
                            or ""
                        )

                        quality_info = (
                            extract_quality_info(
                                filename,
                                caption
                            )
                        )

                        source = (
                            quality_info.get(
                                "source"
                            )
                            or "unknown"
                        )

                        resolution = (
                            quality_info.get(
                                "resolution"
                            )
                            or "unknown"
                        )

                        quality_dist[
                            source
                        ] += 1

                        resolution_dist[
                            resolution
                        ] += 1

                        if processed % 100 == 0:

                            await asyncio.sleep(0)

                        if processed % 5000 == 0:

                            percent = (
                                processed
                                / total_docs
                                * 100
                            )

                            try:

                                await msg.edit_text(
                                    "📊 **QUALITY REPORT**\n\n"
                                    f"📁 Scanned: "
                                    f"**{processed:,} / "
                                    f"{total_docs:,}**\n"
                                    f"⏳ Progress: "
                                    f"**{percent:.1f}%**\n\n"
                                    "🧠 Streaming Mode\n"
                                    "🤖 Bot Online",
                                    reply_markup=cancel_markup
                                )

                            except Exception:
                                pass

                finally:

                    try:
                        await cursor.close()
                    except Exception:
                        pass

            report = (
                f"📊 **QUALITY REPORT "
                f"({len(MEDIA_DBS)} DB)"
                f"{'s' if len(MEDIA_DBS) > 1 else ''}**\n"
                "━━━━━━━━━━━━━━━━━━━━\n\n"
                f"📁 **Total Files:** "
                f"{total_docs:,}\n\n"
                "🎬 **SOURCE QUALITY**\n"
                "━━━━━━━━━━━━━━━━━━━━\n"
            )

            quality_order = [
                "camrip",
                "hdcam",
                "hdtc",
                "hdts",
                "ts",
                "tc",
                "predvd",
                "dvdscr",
                "dvdrip",
                "tvrip",
                "hdtv",
                "webrip",
                "web-dl",
                "webdl",
                "hdrip",
                "bluray",
                "bdrip",
                "brrip",
                "unknown"
            ]

            for quality in quality_order:

                if (
                    quality
                    not in quality_dist
                ):
                    continue

                count = quality_dist[
                    quality
                ]

                percent = (
                    count
                    / total_docs
                    * 100
                    if total_docs
                    else 0
                )

                if (
                    quality
                    in LOW_QUALITY_SOURCES
                ):

                    emoji = "⚠️"

                elif (
                    quality
                    in HIGH_QUALITY_SOURCES
                ):

                    emoji = "✨"

                else:

                    emoji = "⭐"

                report += (
                    f"{emoji} "
                    f"{quality.upper()}: "
                    f"{count:,} "
                    f"({percent:.1f}%)\n"
                )

            report += (
                "\n📐 **RESOLUTION**\n"
                "━━━━━━━━━━━━━━━━━━━━\n"
            )

            for res in [
                "240p",
                "360p",
                "480p",
                "540p",
                "720p",
                "1080p",
                "1440p",
                "2160p",
                "unknown"
            ]:

                if res not in resolution_dist:
                    continue

                count = resolution_dist[
                    res
                ]

                percent = (
                    count
                    / total_docs
                    * 100
                    if total_docs
                    else 0
                )

                report += (
                    f"📹 {res}: "
                    f"{count:,} "
                    f"({percent:.1f}%)\n"
                )

            low_count = sum(
                quality_dist.get(
                    q,
                    0
                )
                for q in LOW_QUALITY_SOURCES
            )

            medium_count = sum(
                quality_dist.get(
                    q,
                    0
                )
                for q in MEDIUM_QUALITY_SOURCES
            )

            high_count = sum(
                quality_dist.get(
                    q,
                    0
                )
                for q in HIGH_QUALITY_SOURCES
            )

            report += (
                "\n━━━━━━━━━━━━━━━━━━━━\n"
                f"⚠️ Low Quality: "
                f"**{low_count:,}**\n"
                f"⭐ Medium Quality: "
                f"**{medium_count:,}**\n"
                f"✨ High Quality: "
                f"**{high_count:,}**\n"
            )

            await msg.edit_text(
                report,
                reply_markup=None
            )

        except Exception as e:

            logger.error(
                "[QUALITY] Report error: %s",
                e,
                exc_info=True
            )

            try:

                await msg.edit_text(
                    f"❌ **ERROR**\n\n"
                    f"`{str(e)[:1000]}`"
                )

            except Exception:
                pass

    asyncio.create_task(
        run_quality_task(
            task_id,
            worker
        )
    )


# =========================================================
# /CLEANUP_DRY_SINGLE
# =========================================================

@Client.on_message(
    filters.command("cleanup_dry_single")
    & filters.user(ADMINS)
)
async def cleanup_dry_single_cmd(
    bot,
    message
):

    global QUALITY_ACTIVE_TASK

    if len(message.command) < 2:

        return await message.reply_text(
            "❌ **Wrong Usage**\n\n"
            "Example:\n"
            "`/cleanup_dry_single Prem Prakaran 2026`"
        )

    if QUALITY_ACTIVE_TASK:

        return await message.reply_text(
            "⏳ **QUALITY TASK ALREADY RUNNING**\n\n"
            "Current process complete hone do."
        )

    movie_name = " ".join(
        message.command[1:]
    )

    task_id = str(
        message.id
    )

    CANCEL_Q_TASKS[
        task_id
    ] = False

    cancel_markup = InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                "🛑 CANCEL",
                callback_data=(
                    f"cancel_q_task_{task_id}"
                )
            )
        ]
    ])

    msg = await message.reply_text(
        "🔍 **SINGLE MOVIE DRY RUN**\n\n"
        f"🎬 Movie: **{movie_name}**\n\n"
        "🗑️ Nothing will be deleted.\n"
        "⏳ Scanning...",
        reply_markup=cancel_markup
    )

    async def worker():

        try:

            all_files = []

            for media_cls in MEDIA_DBS:

                files = await find_single_movie_files(
                    media_cls.collection,
                    movie_name,
                    task_id,
                    max_files=1000
                )

                all_files.extend(
                    files
                )

            if CANCEL_Q_TASKS.get(
                task_id
            ):

                return await msg.edit_text(
                    "🛑 **PROCESS CANCELLED**"
                )

            if not all_files:

                return await msg.edit_text(
                    f"❌ No files found for:\n"
                    f"**{movie_name}**"
                )

            files_info = []

            for file in all_files:

                filename = file.get(
                    "file_name",
                    "Unknown"
                )

                caption = (
                    file.get(
                        "caption",
                        ""
                    )
                    or ""
                )

                quality = extract_quality_info(
                    filename,
                    caption
                )

                languages = extract_language(
                    f"{filename} {caption}"
                )

                files_info.append({
                    "name": filename,
                    "quality": quality.get(
                        "source"
                    ),
                    "resolution": quality.get(
                        "resolution"
                    ),
                    "languages": languages,
                    "score": quality.get(
                        "quality_score",
                        0
                    )
                })

                if len(files_info) % 50 == 0:

                    await asyncio.sleep(0)

            to_delete = 0

            for file in files_info:

                if file["quality"] in (
                    HIGH_QUALITY_SOURCES
                ):

                    continue

                if can_delete_quality_file(
                    file["quality"],
                    file["languages"],
                    files_info
                ):

                    to_delete += 1

            formatted_files = []

            for idx, file in enumerate(
                files_info,
                1
            ):

                should_delete = (
                    file["quality"]
                    not in HIGH_QUALITY_SOURCES
                    and
                    can_delete_quality_file(
                        file["quality"],
                        file["languages"],
                        files_info
                    )
                )

                status = (
                    "❌ DELETE"
                    if should_delete
                    else
                    "✅ KEEP"
                )

                quality_str = (
                    file["quality"]
                    or "N/A"
                ).upper()

                resolution_str = (
                    file["resolution"]
                    or "N/A"
                ).upper()

                lang_str = ", ".join(
                    file["languages"]
                ).upper()

                formatted_files.append(
                    f"\n**{idx}. {status}**\n"
                    f"📄 {file['name'][:70]}\n"
                    f"🎞️ Quality: {quality_str}\n"
                    f"📐 Resolution: "
                    f"{resolution_str}\n"
                    f"🌐 Language: {lang_str}\n"
                )

            DRY_RUN_CACHE[
                task_id
            ] = {
                "movie_name": movie_name,
                "files": formatted_files,
                "keep": (
                    len(files_info)
                    - to_delete
                ),
                "delete": to_delete
            }

            await send_dry_page(
                msg,
                task_id,
                0
            )

            asyncio.create_task(
                auto_delete_msg(
                    msg,
                    message,
                    task_id,
                    300
                )
            )

        except Exception as e:

            logger.error(
                "[QUALITY] Single dry error: %s",
                e,
                exc_info=True
            )

            try:

                await msg.edit_text(
                    f"❌ **ERROR**\n\n"
                    f"`{str(e)[:1000]}`"
                )

            except Exception:
                pass

    asyncio.create_task(
        run_quality_task(
            task_id,
            worker
        )
    )


# =========================================================
# /CLEANUP_CONFIRM_SINGLE
# =========================================================

@Client.on_message(
    filters.command("cleanup_confirm_single")
    & filters.user(ADMINS)
)
async def cleanup_confirm_single_cmd(
    bot,
    message
):

    global QUALITY_ACTIVE_TASK

    if len(message.command) < 2:

        return await message.reply_text(
            "❌ **Wrong Usage**\n\n"
            "Example:\n"
            "`/cleanup_confirm_single Prem Prakaran 2026`"
        )

    if QUALITY_ACTIVE_TASK:

        return await message.reply_text(
            "⏳ **QUALITY TASK ALREADY RUNNING**\n\n"
            "Current process complete hone do."
        )

    movie_name = " ".join(
        message.command[1:]
    )

    task_id = str(
        message.id
    )

    CANCEL_Q_TASKS[
        task_id
    ] = False

    cancel_markup = InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                "🛑 CANCEL DELETE",
                callback_data=(
                    f"cancel_q_task_{task_id}"
                )
            )
        ]
    ])

    msg = await message.reply_text(
        "⚠️ **SINGLE MOVIE DELETE**\n\n"
        f"🎬 Movie: **{movie_name}**\n\n"
        "🛡️ HIGH quality protected.\n"
        "⏳ Processing...",
        reply_markup=cancel_markup
    )

    async def worker():

        try:

            total_deleted = 0
            deleted_files = []

            for media_cls in MEDIA_DBS:

                collection = (
                    media_cls.collection
                )

                files = await find_single_movie_files(
                    collection,
                    movie_name,
                    task_id,
                    max_files=1000
                )

                if not files:
                    continue

                files_info = []

                for file in files:

                    filename = file.get(
                        "file_name",
                        "Unknown"
                    )

                    caption = (
                        file.get(
                            "caption",
                            ""
                        )
                        or ""
                    )

                    quality = extract_quality_info(
                        filename,
                        caption
                    )

                    languages = extract_language(
                        f"{filename} {caption}"
                    )

                    files_info.append({
                        "file_id": file.get(
                            "_id"
                        ),
                        "name": filename,
                        "quality": quality.get(
                            "source"
                        ),
                        "languages": languages
                    })

                    if len(files_info) % 50 == 0:

                        await asyncio.sleep(0)

                for file in files_info:

                    if CANCEL_Q_TASKS.get(
                        task_id
                    ):

                        return await msg.edit_text(
                            "🛑 **DELETE CANCELLED**\n\n"
                            f"Deleted before cancellation: "
                            f"**{total_deleted}**"
                        )

                    source = (
                        file["quality"]
                        or ""
                    ).lower().strip()

                    # HIGH NEVER DELETE
                    if source in HIGH_QUALITY_SOURCES:
                        continue

                    if not can_delete_quality_file(
                        source,
                        file["languages"],
                        files_info
                    ):
                        continue

                    try:

                        result = (
                            await collection.delete_one(
                                {
                                    "_id":
                                    file["file_id"]
                                }
                            )
                        )

                        if result.deleted_count:

                            total_deleted += 1

                            deleted_files.append(
                                file["name"]
                            )

                    except Exception as e:

                        logger.error(
                            "[QUALITY] Single delete "
                            "error: %s",
                            e
                        )

                    if total_deleted % 25 == 0:

                        await asyncio.sleep(0)

            if total_deleted:

                preview = ""

                for i, filename in enumerate(
                    deleted_files[:10],
                    1
                ):

                    preview += (
                        f"{i}. "
                        f"{filename[:65]}\n"
                    )

                if len(deleted_files) > 10:

                    preview += (
                        f"\n... + "
                        f"{len(deleted_files) - 10} more"
                    )

                await msg.edit_text(
                    "✅ **SINGLE MOVIE CLEANUP DONE**\n"
                    "━━━━━━━━━━━━━━━━━━━━\n\n"
                    f"🎬 Movie: **{movie_name}**\n"
                    f"🗑️ Deleted: "
                    f"**{total_deleted}**\n\n"
                    "📋 **Sample Deleted:**\n"
                    f"{preview}\n\n"
                    "🛡️ HIGH quality files protected."
                )

            else:

                await msg.edit_text(
                    "ℹ️ **NOTHING DELETED**\n\n"
                    f"🎬 Movie: **{movie_name}**\n\n"
                    "No eligible LOW/MEDIUM "
                    "quality files found."
                )

        except Exception as e:

            logger.error(
                "[QUALITY] Single confirm error: %s",
                e,
                exc_info=True
            )

            await msg.edit_text(
                f"❌ **ERROR**\n\n"
                f"`{str(e)[:1000]}`"
            )

    asyncio.create_task(
        run_quality_task(
            task_id,
            worker
        )
    )


# =========================================================
# /CLEANUP_DRY_BATCH
# =========================================================

@Client.on_message(
    filters.command("cleanup_dry_batch")
    & filters.user(ADMINS)
)
async def cleanup_dry_batch_cmd(
    bot,
    message
):

    global QUALITY_ACTIVE_TASK

    if QUALITY_ACTIVE_TASK:

        return await message.reply_text(
            "⏳ **QUALITY CLEANUP ALREADY RUNNING**\n\n"
            "Ek time par sirf ek heavy quality "
            "process chalega."
        )

    task_id = str(
        message.id
    )

    CANCEL_Q_TASKS[
        task_id
    ] = False

    cancel_markup = InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                "🛑 CANCEL",
                callback_data=(
                    f"cancel_q_task_{task_id}"
                )
            )
        ]
    ])

    msg = await message.reply_text(
        "🔍 **DRY RUN STARTED**\n\n"
        "📊 Database background me scan hoga.\n"
        "🤖 Bot normal kaam karega.\n"
        "🗑️ Kuch bhi delete nahi hoga.\n\n"
        "⏳ Please wait...",
        reply_markup=cancel_markup
    )

    async def worker():

        try:

            total_docs = 0

            for media_cls in MEDIA_DBS:

                total_docs += await (
                    media_cls.collection
                    .estimated_document_count()
                )

            if total_docs == 0:

                return await msg.edit_text(
                    "❌ **DATABASE EMPTY**"
                )

            p_state = {
                "count": 0
            }

            total_movies = 0
            total_delete = 0
            all_duplicates = []

            for media_cls in MEDIA_DBS:

                if CANCEL_Q_TASKS.get(
                    task_id
                ):

                    return await msg.edit_text(
                        "🛑 **DRY RUN CANCELLED**"
                    )

                movies = await build_movie_groups(
                    media_cls.collection,
                    task_id,
                    p_state,
                    total_docs,
                    msg,
                    cancel_markup,
                    dry_run=True
                )

                if movies is None:

                    return await msg.edit_text(
                        "🛑 **DRY RUN CANCELLED**"
                    )

                delete_count, duplicates = (
                    await analyze_movie_groups(
                        movies,
                        task_id
                    )
                )

                if delete_count is None:

                    return await msg.edit_text(
                        "🛑 **DRY RUN CANCELLED**"
                    )

                total_movies += len(
                    movies
                )

                total_delete += (
                    delete_count
                )

                all_duplicates.extend(
                    duplicates
                )

            all_duplicates.sort(
                key=lambda x:
                x["to_delete"],
                reverse=True
            )

            report = (
                "📊 **QUALITY CLEANUP — DRY RUN**\n"
                "━━━━━━━━━━━━━━━━━━━━\n\n"
                f"📁 Files Scanned: "
                f"**{p_state['count']:,}**\n"
                f"🎬 Movie Groups: "
                f"**{total_movies:,}**\n"
                f"📋 Movies with Duplicates: "
                f"**{len(all_duplicates):,}**\n\n"
                f"⚠️ **Would Delete: "
                f"{total_delete:,} files**\n\n"
            )

            if all_duplicates:

                report += (
                    "📋 **TOP DUPLICATES**\n"
                    "━━━━━━━━━━━━━━━━━━━━\n\n"
                )

                for i, movie in enumerate(
                    all_duplicates[:15],
                    1
                ):

                    report += (
                        f"**{i}. "
                        f"{movie['title'][:45]}**\n"
                        f"   📁 Versions: "
                        f"{movie['count']}\n"
                        f"   🗑️ Delete: "
                        f"{movie['to_delete']}\n\n"
                    )

                if len(all_duplicates) > 15:

                    report += (
                        f"... + "
                        f"{len(all_duplicates) - 15} more\n\n"
                    )

                report += (
                    "━━━━━━━━━━━━━━━━━━━━\n"
                    "🛡️ HIGH quality files are SAFE.\n\n"
                    "👉 **Actual Delete:**\n"
                    "`/cleanup_confirm_batch`"
                )

            else:

                report += (
                    "✅ **NO CLEANUP REQUIRED**\n\n"
                    "HIGH quality files are safe."
                )

            await msg.edit_text(
                report,
                reply_markup=None
            )

        except Exception as e:

            logger.error(
                "[QUALITY] Dry batch error: %s",
                e,
                exc_info=True
            )

            try:

                await msg.edit_text(
                    "❌ **ERROR**\n\n"
                    f"`{str(e)[:1000]}`"
                )

            except Exception:
                pass

    asyncio.create_task(
        run_quality_task(
            task_id,
            worker
        )
    )


# =========================================================
# /CLEANUP_CONFIRM_BATCH
# =========================================================

@Client.on_message(
    filters.command("cleanup_confirm_batch")
    & filters.user(ADMINS)
)
async def cleanup_confirm_batch_cmd(
    bot,
    message
):

    global QUALITY_ACTIVE_TASK

    if QUALITY_ACTIVE_TASK:

        return await message.reply_text(
            "⏳ **QUALITY CLEANUP ALREADY RUNNING**\n\n"
            "Pehle current process complete hone do."
        )

    task_id = str(
        message.id
    )

    CANCEL_Q_TASKS[
        task_id
    ] = False

    cancel_markup = InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                "🛑 CANCEL DELETE",
                callback_data=(
                    f"cancel_q_task_{task_id}"
                )
            )
        ]
    ])

    msg = await message.reply_text(
        "⚠️ **BATCH DELETE STARTED**\n\n"
        "🗑️ LOW/MEDIUM eligible files delete hongi.\n"
        "🛡️ HIGH quality files protected hain.\n"
        "🤖 Bot background me kaam karega.\n\n"
        "⏳ Please wait...",
        reply_markup=cancel_markup
    )

    async def worker():

        try:

            total_docs = 0

            for media_cls in MEDIA_DBS:

                total_docs += await (
                    media_cls.collection
                    .estimated_document_count()
                )

            if total_docs == 0:

                return await msg.edit_text(
                    "❌ **DATABASE EMPTY**"
                )

            p_state = {
                "count": 0
            }

            total_deleted = 0
            deleted_files = []
            movies_cleaned_set = set()

            for media_cls in MEDIA_DBS:

                if CANCEL_Q_TASKS.get(
                    task_id
                ):

                    return await msg.edit_text(
                        "🛑 **DELETE CANCELLED**\n\n"
                        f"Already deleted: "
                        f"**{total_deleted}**"
                    )

                collection = (
                    media_cls.collection
                )

                movies = await build_movie_groups(
                    collection,
                    task_id,
                    p_state,
                    total_docs,
                    msg,
                    cancel_markup,
                    dry_run=False
                )

                if movies is None:

                    return await msg.edit_text(
                        "🛑 **DELETE CANCELLED**\n\n"
                        f"Already deleted: "
                        f"**{total_deleted}**"
                    )

                for base_title, files in movies.items():

                    if CANCEL_Q_TASKS.get(
                        task_id
                    ):

                        return await msg.edit_text(
                            "🛑 **DELETE CANCELLED**\n\n"
                            f"Already deleted: "
                            f"**{total_deleted}**"
                        )

                    if len(files) <= 1:

                        continue

                    cleaned_movie = False

                    for file in files:

                        if CANCEL_Q_TASKS.get(
                            task_id
                        ):

                            return await msg.edit_text(
                                "🛑 **DELETE CANCELLED**\n\n"
                                f"Already deleted: "
                                f"**{total_deleted}**"
                            )

                        source = (
                            file.get("quality")
                            or ""
                        ).lower().strip()

                        # HIGH NEVER DELETE
                        if source in HIGH_QUALITY_SOURCES:

                            continue

                        can_delete = (
                            can_delete_quality_file(
                                source,
                                file.get(
                                    "languages",
                                    []
                                ),
                                files
                            )
                        )

                        if not can_delete:

                            continue

                        try:

                            result = (
                                await collection.delete_one(
                                    {
                                        "_id":
                                        file["file_id"]
                                    }
                                )
                            )

                            if result.deleted_count:

                                total_deleted += 1

                                cleaned_movie = True

                                deleted_files.append(
                                    file.get(
                                        "name",
                                        "Unknown"
                                    )
                                )

                        except Exception as e:

                            logger.error(
                                "[QUALITY] Batch delete "
                                "error: %s",
                                e
                            )

                        if (
                            total_deleted % 25
                            == 0
                        ):

                            await asyncio.sleep(0)

                    if cleaned_movie:

                        movies_cleaned_set.add(
                            base_title
                        )

            if total_deleted:

                preview = ""

                for i, filename in enumerate(
                    deleted_files[:10],
                    1
                ):

                    preview += (
                        f"{i}. "
                        f"{filename[:65]}\n"
                    )

                if len(deleted_files) > 10:

                    preview += (
                        f"\n... + "
                        f"{len(deleted_files) - 10} more"
                    )

                report = (
                    "✅ **BATCH CLEANUP COMPLETED**\n"
                    "━━━━━━━━━━━━━━━━━━━━\n\n"
                    f"🗑️ Total Deleted: "
                    f"**{total_deleted:,}**\n"
                    f"🎬 Movies Cleaned: "
                    f"**{len(movies_cleaned_set):,}**\n\n"
                    "📋 **Sample Deleted:**\n"
                    f"{preview}\n\n"
                    "🛡️ HIGH quality files protected."
                )

            else:

                report = (
                    "ℹ️ **NOTHING DELETED**\n\n"
                    "No eligible LOW/MEDIUM "
                    "quality duplicates found.\n\n"
                    "🛡️ HIGH quality files are safe."
                )

            await msg.edit_text(
                report,
                reply_markup=None
            )

        except Exception as e:

            logger.error(
                "[QUALITY] Confirm batch error: %s",
                e,
                exc_info=True
            )

            try:

                await msg.edit_text(
                    "❌ **DELETE ERROR**\n\n"
                    f"`{str(e)[:1000]}`"
                )

            except Exception:
                pass

    asyncio.create_task(
        run_quality_task(
            task_id,
            worker
        )
    )


# =========================================================
# COMMAND HELP / EXAMPLES
# =========================================================

@Client.on_message(
    filters.command("quality_help")
    & filters.user(ADMINS)
)
async def quality_help_cmd(
    bot,
    message
):

    help_text = """
🛠️ **QUALITY MANAGER**

━━━━━━━━━━━━━━━━━━━━

📊 **1. QUALITY REPORT**

Command:
`/quality_report`

Example:
`/quality_report`

➡️ Database ki quality/resolution report.
➡️ Kuch delete nahi hota.

━━━━━━━━━━━━━━━━━━━━

🔍 **2. SINGLE MOVIE DRY RUN**

Command:
`/cleanup_dry_single <movie name>`

Example:
`/cleanup_dry_single Prem Prakaran 2026`

➡️ Movie ki files check karega.
➡️ Kya delete hoga / kya keep hoga dikhayega.
➡️ Kuch delete nahi hoga.

━━━━━━━━━━━━━━━━━━━━

🗑️ **3. SINGLE MOVIE DELETE**

Command:
`/cleanup_confirm_single <movie name>`

Example:
`/cleanup_confirm_single Prem Prakaran 2026`

➡️ Eligible LOW/MEDIUM files delete karega.
➡️ HIGH quality protected rahegi.

━━━━━━━━━━━━━━━━━━━━

🔍 **4. FULL DATABASE DRY RUN**

Command:
`/cleanup_dry_batch`

Example:
`/cleanup_dry_batch`

➡️ Pura database scan karega.
➡️ Kuch delete nahi karega.
➡️ Estimated deletion report dega.

━━━━━━━━━━━━━━━━━━━━

🗑️ **5. FULL DATABASE DELETE**

Command:
`/cleanup_confirm_batch`

Example:
`/cleanup_confirm_batch`

➡️ LOW/MEDIUM duplicate files delete karega.
➡️ HIGH quality files protected rahengi.

━━━━━━━━━━━━━━━━━━━━

🛑 **6. CANCEL**

Cleanup chalne par:

`🛑 CANCEL`

button dabao.

━━━━━━━━━━━━━━━━━━━━

🛡️ **QUALITY RULES**

✨ WEBRip
✨ WEB-DL
✨ HDRip
✨ BluRay
✨ BDRip
✨ BRRip

➡️ HIGH quality files NEVER delete.

⭐ DVDRip
⭐ TVRip
⭐ HDTV

➡️ HIGH quality same-language version available ho
to delete ho sakti hain.

⚠️ CAMRip
⚠️ HDTS
⚠️ HDTC
⚠️ TS
⚠️ TC
⚠️ DVDScr
⚠️ PreDVD

➡️ Better same-language version available ho
to delete ho sakti hain.

━━━━━━━━━━━━━━━━━━━━

🤖 **BOT STATUS**

Cleanup background me chalega.
Search / file sending / normal bot functions
continue rahenge.
"""

    await message.reply_text(
        help_text
    )