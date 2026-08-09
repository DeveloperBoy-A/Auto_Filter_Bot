import re
import logging
import asyncio
import math
from typing import Optional, Tuple, List
from database.ia_filterdb import Media, Media2, MEDIA_DBS
from info import MULTIPLE_DB, ADMINS
from pyrogram import Client, filters
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from collections import defaultdict

logger = logging.getLogger(__name__)

# =========================================================
# QUALITY MANAGER SETTINGS
# =========================================================

logger.setLevel(logging.WARNING)

# Maximum 2 cleanup scans at the same time.
# This prevents bulk uploads from freezing the bot.
QUALITY_CLEANUP_SEMAPHORE = asyncio.Semaphore(2)

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
    "br rip": 9
}

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
    "hindi": [r"\bhindi\b", r"\bhin\b", r"\bhi\b"],
    "english": [r"\benglish\b", r"\beng\b", r"\ben\b"],
    "tamil": [r"\btamil\b", r"\btam\b", r"\bta\b"],
    "telugu": [r"\btelugu\b", r"\btel\b", r"\bte\b"],
    "malayalam": [r"\bmalayalam\b", r"\bmal\b", r"\bml\b"],
    "kannada": [r"\bkannada\b", r"\bkan\b", r"\bkn\b"],
    "punjabi": [r"\bpunjabi\b", r"\bpan\b", r"\bpbi\b", r"\bpa\b"],
    "bengali": [r"\bbengali\b", r"\bben\b", r"\bbn\b"],
    "marathi": [r"\bmarathi\b", r"\bmar\b", r"\bmr\b"],
    "gujarati": [r"\bgujarati\b", r"\bguj\b", r"\bgujrat\b", r"\bgu\b"]
}

# =========================================================
# QUALITY SOURCE GROUPS
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
    "dvd scr"
]

MEDIUM_QUALITY_SOURCES = [
    "dvdrip",
    "dvd rip",
    "tvrip",
    "tv rip",
    "hdtv",
    "hd tv"
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
    "br rip"
]

# =========================================================
# LANGUAGE EXTRACTION
# =========================================================

def extract_language(text: str) -> List[str]:
    """
    Extract ALL languages from filename/caption.
    """
    text = (text or "").lower()
    found_languages = []

    for lang, patterns in LANGUAGES.items():
        for pattern in patterns:
            if re.search(pattern, text):
                if lang not in found_languages:
                    found_languages.append(lang)
                break

    return found_languages if found_languages else ["unknown"]


# =========================================================
# QUALITY EXTRACTION
# =========================================================

def extract_quality_info(filename: str, caption: str = "") -> dict:
    text = f"{filename or ''} {caption or ''}".lower()

    quality_info = {
        "source": None,
        "resolution": None,
        "quality_score": 0,
        "source_score": 0,
        "resolution_score": 0
    }

    # Sort longest names first.
    # This prevents "web dl" / "web-dl" type partial matching issues.
    for source, score in sorted(
        QUALITY_HIERARCHY.items(),
        key=lambda x: len(x[0]),
        reverse=True
    ):
        pattern = (
            rf"(?<![a-z0-9])"
            rf"{re.escape(source)}"
            rf"(?![a-z0-9])"
        )

        if re.search(pattern, text):
            quality_info["source"] = source
            quality_info["source_score"] = score
            break

    for res, score in sorted(
        RESOLUTION_HIERARCHY.items(),
        key=lambda x: len(x[0]),
        reverse=True
    ):
        pattern = (
            rf"(?<![a-z0-9])"
            rf"{re.escape(res)}"
            rf"(?![a-z0-9])"
        )

        if re.search(pattern, text):
            quality_info["resolution"] = res
            quality_info["resolution_score"] = score
            break

    quality_info["quality_score"] = (
        quality_info["source_score"] * 0.7
        + quality_info["resolution_score"] * 0.3
    )

    return quality_info


def is_low_quality_print(quality_info: dict) -> bool:
    return quality_info.get("source") in LOW_QUALITY_SOURCES


def is_high_quality(quality_info: dict) -> bool:
    return quality_info.get("source") in HIGH_QUALITY_SOURCES


# =========================================================
# QUALITY DELETE DECISION
# =========================================================

def should_delete_existing(
    existing_quality: dict,
    new_quality: dict,
    existing_langs: List[str],
    new_langs: List[str]
) -> bool:
    """
    Strict quality deletion rules.

    HIGH quality is NEVER deleted.

    LOW:
        LOW -> MEDIUM = delete
        LOW -> HIGH   = delete

    MEDIUM:
        MEDIUM -> HIGH = delete

    Language must match/subset.
    """

    try:
        existing_source = (
            existing_quality.get("source") or ""
        ).lower().strip()

        new_source = (
            new_quality.get("source") or ""
        ).lower().strip()

        if not existing_source or not new_source:
            return False

        # HIGH QUALITY = NEVER DELETE
        if existing_source in HIGH_QUALITY_SOURCES:
            return False

        existing_set = set(existing_langs or ["unknown"])
        new_set = set(new_langs or ["unknown"])

        # Unknown language is never treated as a safe match.
        if "unknown" in existing_set or "unknown" in new_set:
            if existing_set != new_set:
                return False

        if not existing_set <= new_set:
            return False

        # LOW -> HIGH
        if existing_source in LOW_QUALITY_SOURCES:
            if new_source in HIGH_QUALITY_SOURCES:
                return True

            if new_source in MEDIUM_QUALITY_SOURCES:
                return True

        # MEDIUM -> HIGH
        elif existing_source in MEDIUM_QUALITY_SOURCES:
            if new_source in HIGH_QUALITY_SOURCES:
                return True

        return False

    except Exception as e:
        logger.error(
            f"[QUALITY] Error in should_delete_existing: {e}",
            exc_info=True
        )
        return False


# =========================================================
# BASE TITLE EXTRACTION
# =========================================================

def get_base_title(filename: str) -> str:
    """
    Extract base movie/series title.

    Removes:
    - extension
    - quality
    - resolution
    - season/episode
    - codec
    - audio
    - language
    - common release tags

    IMPORTANT:
    Year is NOT removed.
    This protects:
        Movie 2025
    from matching:
        Movie 2026
    """

    text = filename or ""
    text = text.lower()

    # Extension
    text = re.sub(
        r"\.(mkv|mp4|avi|mov|wmv|flv|webm)$",
        "",
        text,
        flags=re.I
    )

    # Convert separators
    text = re.sub(r"[._\-]+", " ", text)

    # Remove brackets containing release information
    text = re.sub(
        r"[\[\(\{].*?[\]\)\}]",
        " ",
        text
    )

    # Season
    text = re.sub(
        r"\bs\d{1,2}\b",
        " ",
        text,
        flags=re.I
    )

    text = re.sub(
        r"\bseason\s*\d{1,2}\b",
        " ",
        text,
        flags=re.I
    )

    # Episode
    text = re.sub(
        r"\be\d{1,3}\b",
        " ",
        text,
        flags=re.I
    )

    text = re.sub(
        r"\bepisode\s*\d{1,3}\b",
        " ",
        text,
        flags=re.I
    )

    # Quality / resolution
    for quality in QUALITY_HIERARCHY.keys():
        text = re.sub(
            rf"(?<![a-z0-9]){re.escape(quality)}(?![a-z0-9])",
            " ",
            text,
            flags=re.I
        )

    for res in RESOLUTION_HIERARCHY.keys():
        text = re.sub(
            rf"(?<![a-z0-9]){re.escape(res)}(?![a-z0-9])",
            " ",
            text,
            flags=re.I
        )

    # Technical / release tags
    technical_pattern = (
        r"\b("
        r"hevc|x265|x264|h264|avc|av1|"
        r"aac|flac|dts|ac3|eac3|ddp|ddp5\.1|"
        r"dd5\.1|5\.1|7\.1|2\.0|"
        r"dub|sub|esub|esubs|multi|proper|uncut|"
        r"amzn|nf|dsnp|"
        r"movies4u|tokyo_updates|telly"
        r")\b"
    )

    text = re.sub(
        technical_pattern,
        " ",
        text,
        flags=re.I
    )

    # Languages
    language_pattern = (
        r"\b("
        r"hindi|english|tamil|telugu|malayalam|kannada|"
        r"punjabi|bengali|marathi|gujarati|"
        r"hin|eng|tam|tel|mal|kan|pan|ben|mar|gu"
        r")\b"
    )

    text = re.sub(
        language_pattern,
        " ",
        text,
        flags=re.I
    )

    # Extra separators
    text = re.sub(r"[^a-z0-9\s]", " ", text)
    text = re.sub(r"\s+", " ", text).strip()

    return text


# =========================================================
# STRICT MOVIE TITLE MATCHING
# =========================================================

def extract_years(text: str) -> List[str]:
    """
    Extract release years.
    """
    if not text:
        return []

    years = re.findall(
        r"(?<!\d)(19\d{2}|20\d{2})(?!\d)",
        text
    )

    return list(dict.fromkeys(years))


def normalize_match_title(filename: str) -> str:
    """
    Final normalized title used only for matching.

    IMPORTANT:
    Year is preserved.
    """

    title = get_base_title(filename)

    if not title:
        return ""

    # Normalize spaces
    title = re.sub(r"\s+", " ", title).strip()

    return title


def titles_match_strict(
    new_filename: str,
    existing_filename: str
) -> bool:
    """
    VERY STRICT title matching.

    We intentionally prefer KEEP over DELETE.

    Matching rules:

    1. Empty title => no match
    2. Different years => no match
    3. Exact normalized title => match
    4. Otherwise both titles must have highly similar tokens
    5. Minimum 3 meaningful tokens required for fuzzy matching
    6. 90% bidirectional token coverage required

    This prevents:
        "Pushpa"
        "Pushpa 2"
        "Pushpa The Rule"
        "Pushpa Raj"
    from being accidentally treated as the same movie.
    """

    try:
        new_title = normalize_match_title(new_filename)
        old_title = normalize_match_title(existing_filename)

        if not new_title or not old_title:
            return False

        # -------------------------------------------------
        # YEAR PROTECTION
        # -------------------------------------------------

        new_years = extract_years(new_title)
        old_years = extract_years(old_title)

        if new_years and old_years:
            if set(new_years) != set(old_years):
                logger.debug(
                    f"[QUALITY] YEAR MISMATCH: "
                    f"{new_years} != {old_years}"
                )
                return False

        # If one has year and other doesn't,
        # don't make a risky deletion.
        if bool(new_years) != bool(old_years):
            return False

        # -------------------------------------------------
        # EXACT MATCH
        # -------------------------------------------------

        if new_title == old_title:
            return True

        # -------------------------------------------------
        # TOKEN MATCH
        # -------------------------------------------------

        new_words = [
            w for w in new_title.split()
            if len(w) > 1
        ]

        old_words = [
            w for w in old_title.split()
            if len(w) > 1
        ]

        # Too short => no fuzzy matching.
        if len(new_words) < 3 or len(old_words) < 3:
            return False

        new_set = set(new_words)
        old_set = set(old_words)

        common_words = new_set & old_set

        # At least 3 common words
        if len(common_words) < 3:
            return False

        new_ratio = len(common_words) / len(new_set)
        old_ratio = len(common_words) / len(old_set)

        # Strict bidirectional match.
        if new_ratio >= 0.90 and old_ratio >= 0.90:
            return True

        return False

    except Exception as e:
        logger.error(
            f"[QUALITY] Error in strict title matching: {e}",
            exc_info=True
        )
        return False


# =========================================================
# FIND & DELETE LOWER QUALITY
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
            new_quality.get("source") or ""
        ).lower().strip()

        # Only HIGH / MEDIUM trigger cleanup.
        if (
            new_source not in HIGH_QUALITY_SOURCES
            and new_source not in MEDIUM_QUALITY_SOURCES
        ):
            return True, "New file is low quality"

        base_title = get_base_title(new_filename)

        if not base_title:
            logger.warning(
                f"[QUALITY] Could not extract title: "
                f"{new_filename}"
            )
            return True, "Could not extract title"

        new_langs = extract_language(
            f"{new_filename} {new_caption or ''}"
        )

        # -------------------------------------------------
        # SEARCH
        # -------------------------------------------------

        words = [
            w for w in base_title.split()
            if len(w) > 1
        ]

        if not words:
            return True, "No significant words in title"

        # Use first meaningful words only for DB discovery.
        # FINAL deletion is protected by titles_match_strict().
        search_words = words[:5]

        search_pattern = ".*".join(
            re.escape(w)
            for w in search_words
        )

        search_query = {
            "file_name": {
                "$regex": search_pattern,
                "$options": "i"
            }
        }

        if file_id:
            search_query["_id"] = {
                "$ne": file_id
            }

        deleted_count = 0
        deleted_files = []
        processed_count = 0

        cursor = db_collection.find(
            search_query,
            projection={
                "_id": 1,
                "file_name": 1,
                "caption": 1
            }
        ).limit(300)

        async for file_in_db in cursor:

            try:
                processed_count += 1

                if processed_count % 50 == 0:
                    await asyncio.sleep(0)

                existing_filename = (
                    file_in_db.get("file_name") or ""
                )

                existing_caption = (
                    file_in_db.get("caption") or ""
                )

                # -------------------------------------------------
                # FINAL STRICT TITLE CHECK
                # -------------------------------------------------

                if not titles_match_strict(
                    new_filename,
                    existing_filename
                ):
                    continue

                existing_quality = extract_quality_info(
                    existing_filename,
                    existing_caption
                )

                existing_source = (
                    existing_quality.get("source") or ""
                ).lower().strip()

                existing_langs = extract_language(
                    f"{existing_filename} {existing_caption}"
                )

                # -------------------------------------------------
                # HIGH QUALITY NEVER DELETE
                # -------------------------------------------------

                if existing_source in HIGH_QUALITY_SOURCES:
                    continue

                # -------------------------------------------------
                # QUALITY + LANGUAGE DECISION
                # -------------------------------------------------

                can_delete = should_delete_existing(
                    existing_quality,
                    new_quality,
                    existing_langs,
                    new_langs
                )

                if not can_delete:
                    continue

                # -------------------------------------------------
                # FINAL SAFETY CHECK
                # -------------------------------------------------

                if not titles_match_strict(
                    new_filename,
                    existing_filename
                ):
                    logger.warning(
                        "[QUALITY] FINAL TITLE CHECK FAILED - KEEPING"
                    )
                    continue

                # -------------------------------------------------
                # DELETE
                # -------------------------------------------------

                try:
                    await db_collection.delete_one(
                        {
                            "_id": file_in_db["_id"]
                        }
                    )

                    deleted_count += 1
                    deleted_files.append(
                        existing_filename
                    )

                    logger.warning(
                        f"[QUALITY] 🗑️ DELETED: "
                        f"{existing_filename[:70]} | "
                        f"{existing_source.upper()} -> "
                        f"{new_source.upper()}"
                    )

                except Exception as e:
                    logger.error(
                        f"[QUALITY] Delete error: {e}"
                    )

            except Exception as e:
                logger.error(
                    f"[QUALITY] Error processing file: {e}"
                )
                continue

        if deleted_count > 0:
            return (
                True,
                f"✅ Deleted {deleted_count} LOW/MEDIUM quality files"
            )

        return True, "No lower quality files to delete"

    except Exception as e:
        logger.error(
            f"[QUALITY] Error in find_and_delete_lower_quality: {e}",
            exc_info=True
        )
        return False, f"Error: {str(e)}"


# =========================================================
# BACKGROUND CLEANUP
# =========================================================

async def run_quality_cleanup_background(
    media_dbs,
    file_name: str,
    caption: str
):
    """
    Background cleanup.
    Upload flow is never blocked by cleanup.
    """

    async with QUALITY_CLEANUP_SEMAPHORE:

        try:
            for idx, media_cls in enumerate(
                media_dbs,
                start=1
            ):

                cleanup_success, cleanup_msg = (
                    await find_and_delete_lower_quality(
                        db_collection=media_cls.collection,
                        new_filename=file_name,
                        new_caption=caption
                    )
                )

                if (
                    cleanup_success
                    and "Deleted" in cleanup_msg
                ):
                    logger.warning(
                        f"[QUALITY DB{idx}] "
                        f"{file_name[:60]} -> "
                        f"{cleanup_msg}"
                    )

        except Exception as e:
            logger.error(
                f"[QUALITY] Background cleanup failed "
                f"for {file_name[:60]}: {e}",
                exc_info=True
            )


# =========================================================
# CLEANUP DUPLICATES
# =========================================================

async def cleanup_duplicates(
    db_collection,
    base_title: str,
    keep_highest_quality: bool = True
) -> Tuple[int, List[str]]:

    try:
        if not base_title:
            return 0, []

        # Search is only discovery.
        # Actual delete decision uses strict title matching.
        words = [
            w for w in base_title.split()
            if len(w) > 1
        ]

        if not words:
            return 0, []

        pattern = ".*".join(
            re.escape(w)
            for w in words[:5]
        )

        search_query = {
            "file_name": {
                "$regex": pattern,
                "$options": "i"
            }
        }

        files = await db_collection.find(
            search_query,
            projection={
                "_id": 1,
                "file_name": 1,
                "caption": 1
            }
        ).to_list(500)

        if len(files) <= 1:
            return 0, []

        scored_files = []

        for file in files:

            file_name = (
                file.get("file_name") or ""
            )

            quality = extract_quality_info(
                file_name,
                file.get("caption") or ""
            )

            langs = extract_language(
                f"{file_name} {file.get('caption') or ''}"
            )

            # Strictly keep only same movie title.
            if not titles_match_strict(
                base_title,
                file_name
            ):
                continue

            scored_files.append({
                "file": file,
                "quality": quality,
                "languages": langs,
                "score": quality["quality_score"],
                "source": quality["source"]
            })

        if len(scored_files) <= 1:
            return 0, []

        deleted_count = 0
        deleted_files = []

        if keep_highest_quality:

            for scored_file in scored_files:

                file_source = (
                    scored_file["source"] or ""
                ).lower().strip()

                # HIGH = NEVER DELETE
                if file_source in HIGH_QUALITY_SOURCES:
                    continue

                can_delete = False

                # -------------------------------------------------
                # MEDIUM -> HIGH
                # -------------------------------------------------

                if file_source in MEDIUM_QUALITY_SOURCES:

                    for hq_file in scored_files:

                        if (
                            hq_file["source"]
                            not in HIGH_QUALITY_SOURCES
                        ):
                            continue

                        # Language protection
                        low_lang = set(
                            scored_file["languages"]
                        )

                        high_lang = set(
                            hq_file["languages"]
                        )

                        if (
                            "unknown" in low_lang
                            or "unknown" in high_lang
                        ):
                            continue

                        if low_lang <= high_lang:
                            can_delete = True
                            break

                # -------------------------------------------------
                # LOW -> HIGH / MEDIUM
                # -------------------------------------------------

                elif file_source in LOW_QUALITY_SOURCES:

                    for better_file in scored_files:

                        if (
                            better_file["source"]
                            not in HIGH_QUALITY_SOURCES
                            and better_file["source"]
                            not in MEDIUM_QUALITY_SOURCES
                        ):
                            continue

                        low_lang = set(
                            scored_file["languages"]
                        )

                        better_lang = set(
                            better_file["languages"]
                        )

                        if (
                            "unknown" in low_lang
                            or "unknown" in better_lang
                        ):
                            continue

                        if low_lang <= better_lang:
                            can_delete = True
                            break

                if not can_delete:
                    continue

                file_to_delete = (
                    scored_file["file"].get(
                        "file_name",
                        "Unknown"
                    )
                )

                try:
                    await db_collection.delete_one(
                        {
                            "_id":
                            scored_file["file"]["_id"]
                        }
                    )

                    deleted_count += 1
                    deleted_files.append(
                        file_to_delete
                    )

                    logger.warning(
                        f"[CLEANUP] DELETE: "
                        f"{file_to_delete[:70]}"
                    )

                except Exception as e:
                    logger.error(
                        f"[CLEANUP] Error deleting: {e}"
                    )

        return deleted_count, deleted_files

    except Exception as e:
        logger.error(
            f"Error in cleanup_duplicates: {e}"
        )
        return 0, []


# =========================================================
# COMMAND HANDLERS
# =========================================================

CANCEL_Q_TASKS = {}
DRY_RUN_CACHE = {}


# =========================================================
# CANCEL CALLBACK
# =========================================================

@Client.on_callback_query(
    filters.regex(r"^cancel_q_task_(.*)")
)
async def cancel_q_task(client, query):

    task_id = query.data.split(
        "cancel_q_task_"
    )[-1]

    CANCEL_Q_TASKS[task_id] = True

    await query.answer(
        "🛑 Cancelling Process...",
        show_alert=True
    )


# =========================================================
# AUTO DELETE
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

        try:
            await msg.delete()
        except Exception:
            pass

        try:
            await command_msg.delete()
        except Exception:
            pass

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

    data = DRY_RUN_CACHE.get(task_id)

    if not data:

        if hasattr(msg, "edit_text"):
            return await msg.edit_text(
                "❌ Data expired or auto-deleted. "
                "Please run the command again."
            )

        return await msg.message.edit_text(
            "❌ Data expired or auto-deleted. "
            "Please run the command again."
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
        start_idx + ITEMS_PER_PAGE
    )

    chunk = data["files"][
        start_idx:end_idx
    ]

    report = (
        f"📊 **DRY RUN - SINGLE MOVIE**\n"
        f"{'=' * 50}\n\n"
        f"🎬 Movie: {data['movie_name']}\n"
        f"📁 Found: {total_files} files\n\n"
        f"📋 **File Details "
        f"(Page {page + 1}/{total_pages}):**\n"
        f"{'─' * 50}\n"
    )

    for f_text in chunk:
        report += f_text

    report += (
        f"\n{'─' * 50}\n"
        f"⚠️ **PREVIEW SUMMARY:**\n"
        f"✅ Will KEEP: {data['keep']}\n"
        f"❌ Will DELETE: {data['delete']}\n\n"
    )

    if data["delete"] > 0:

        report += (
            f"👉 Confirm & Delete:\n"
            f"`/cleanup_confirm_single "
            f"{data['movie_name']}`\n\n"
        )

    else:

        report += (
            "ℹ️ No files to delete "
            "(HIGH quality files are safe)\n\n"
        )

    report += (
        "⏱️ *This message will auto-delete "
        "in 5 mins.*"
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
            f"Error editing pagination message: {e}"
        )


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

    except Exception as e:
        await query.answer(
            "❌ Page error",
            show_alert=True
        )


# =========================================================
# QUALITY REPORT
# =========================================================

@Client.on_message(
    filters.command("quality_report")
    & filters.user(ADMINS)
)
async def quality_report_cmd(
    bot,
    message
):

    try:

        task_id = str(message.id)

        CANCEL_Q_TASKS[
            task_id
        ] = False

        cancel_markup = (
            InlineKeyboardMarkup(
                [[
                    InlineKeyboardButton(
                        "🛑 CANCEL PROCESS",
                        callback_data=(
                            f"cancel_q_task_{task_id}"
                        )
                    )
                ]]
            )
        )

        msg = await message.reply_text(
            "📊 Calculating total files...\n"
            "⏳ Please wait...",
            reply_markup=cancel_markup
        )

        total_docs = 0

        for media_cls in MEDIA_DBS:
            total_docs += (
                await media_cls.collection
                .estimated_document_count()
            )

        if total_docs == 0:
            return await msg.edit_text(
                "❌ No files in database"
            )

        processed = 0

        quality_dist = defaultdict(int)
        resolution_dist = defaultdict(int)

        async def process_cursor(
            collection
        ):

            nonlocal processed

            async for file in collection.find(
                {},
                projection={
                    "file_name": 1
                }
            ):

                if CANCEL_Q_TASKS.get(task_id):
                    return False

                processed += 1

                file_name = (
                    file.get("file_name") or ""
                )

                quality_info = (
                    extract_quality_info(
                        file_name
                    )
                )

                quality_dist[
                    quality_info.get(
                        "source",
                        "unknown"
                    )
                ] += 1

                resolution_dist[
                    quality_info.get(
                        "resolution",
                        "unknown"
                    )
                ] += 1

                if processed % 500 == 0:
                    await asyncio.sleep(0.1)

                if processed % 5000 == 0:

                    percent = (
                        processed
                        / total_docs
                        * 100
                    )

                    try:
                        await msg.edit_text(
                            f"📊 **Generating "
                            f"Quality Report...**\n\n"
                            f"📁 Scanned: "
                            f"**{processed} / "
                            f"{total_docs}** files\n"
                            f"⏳ Progress: "
                            f"**{percent:.1f}%**\n"
                            f"⚙️ Status: "
                            f"Memory Safe Mode",
                            reply_markup=cancel_markup
                        )
                    except Exception:
                        pass

            return True

        for media_cls in MEDIA_DBS:

            if not await process_cursor(
                media_cls.collection
            ):
                return await msg.edit_text(
                    "🛑 **Process Cancelled "
                    "by Admin!**"
                )

        report = (
            f"📊 **QUALITY REPORT "
            f"({len(MEDIA_DBS)} "
            f"DB{'s' if len(MEDIA_DBS) > 1 else ''})**\n"
            f"{'=' * 50}\n\n"
            f"📁 **Total Files:** "
            f"{total_docs}\n\n"
            f"🎬 **Source Quality "
            f"Distribution:**\n"
            f"{'─' * 50}\n"
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
                in quality_dist
                and quality_dist[quality] > 0
            ):

                count = quality_dist[
                    quality
                ]

                percent = (
                    count
                    / total_docs
                    * 100
                )

                if quality in LOW_QUALITY_SOURCES:
                    emoji = "⚠️"

                elif quality in HIGH_QUALITY_SOURCES:
                    emoji = "✨"

                else:
                    emoji = "⭐"

                report += (
                    f"{emoji} "
                    f"{quality.upper()}: "
                    f"{count} "
                    f"({percent:.1f}%)\n"
                )

        report += (
            "\n📐 **Resolution Distribution:**\n"
            f"{'─' * 50}\n"
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

            if (
                res in resolution_dist
                and resolution_dist[res] > 0
            ):

                count = resolution_dist[res]

                percent = (
                    count
                    / total_docs
                    * 100
                )

                report += (
                    f"📹 {res}: "
                    f"{count} "
                    f"({percent:.1f}%)\n"
                )

        low_count = sum(
            quality_dist.get(q, 0)
            for q in LOW_QUALITY_SOURCES
        )

        report += (
            f"\n⚠️ **Low Quality Count:** "
            f"{low_count} files\n"
        )

        await msg.edit_text(report)

    except Exception as e:

        await message.reply_text(
            f"❌ Error: {str(e)}"
        )


# =========================================================
# DRY RUN SINGLE
# =========================================================

@Client.on_message(
    filters.command("cleanup_dry_single")
    & filters.user(ADMINS)
)
async def cleanup_dry_single_cmd(
    bot,
    message
):

    try:

        if len(message.command) < 2:
            return await message.reply_text(
                "❌ Usage: "
                "/cleanup_dry_single <movie_name>"
            )

        movie_name = " ".join(
            message.command[1:]
        )

        msg = await message.reply_text(
            f"🔍 **DRY RUN - SINGLE**\n\n"
            f"Movie: {movie_name}\n"
            f"⏳ Scanning..."
        )

        base_title = get_base_title(
            movie_name
        )

        if not base_title:
            return await msg.edit_text(
                f"❌ Could not extract title "
                f"from: {movie_name}"
            )

        words = [
            w
            for w in base_title.split()
            if len(w) > 1
        ]

        search_pattern = (
            ".*".join(
                re.escape(w)
                for w in words[:5]
            )
            if words
            else re.escape(base_title)
        )

        similar_files = []

        for media_cls in MEDIA_DBS:

            found = await (
                media_cls.collection.find(
                    {
                        "file_name": {
                            "$regex": search_pattern,
                            "$options": "i"
                        }
                    },
                    projection={
                        "_id": 1,
                        "file_name": 1,
                        "caption": 1
                    }
                )
                .to_list(500)
            )

            for file in found:

                file_name = (
                    file.get("file_name") or ""
                )

                # STRICT MATCH
                if titles_match_strict(
                    movie_name,
                    file_name
                ):
                    similar_files.append(file)

        if not similar_files:

            return await msg.edit_text(
                f"❌ No exact/safe matching files "
                f"found for: {movie_name}"
            )

        files_info = []

        for file in similar_files:

            file_name = (
                file.get("file_name")
                or "Unknown"
            )

            caption = (
                file.get("caption")
                or ""
            )

            quality = extract_quality_info(
                file_name,
                caption
            )

            langs = extract_language(
                f"{file_name} {caption}"
            )

            files_info.append({
                "name": file_name,
                "quality": (
                    quality["source"]
                    or "Unknown"
                ),
                "resolution": (
                    quality["resolution"]
                    or "Unknown"
                ),
                "languages": langs,
                "score": quality[
                    "quality_score"
                ]
            })

        files_info.sort(
            key=lambda x: x["score"],
            reverse=True
        )

        formatted_files = []

        to_delete = 0

        for idx, file in enumerate(
            files_info
        ):

            will_delete = False

            # HIGH NEVER DELETE
            if (
                file["quality"]
                in HIGH_QUALITY_SOURCES
            ):
                will_delete = False

            # MEDIUM -> HIGH
            elif (
                file["quality"]
                in MEDIUM_QUALITY_SOURCES
            ):

                for hq in files_info:

                    if (
                        hq["quality"]
                        not in HIGH_QUALITY_SOURCES
                    ):
                        continue

                    low_lang = set(
                        file["languages"]
                    )

                    high_lang = set(
                        hq["languages"]
                    )

                    if (
                        "unknown" not in low_lang
                        and
                        "unknown" not in high_lang
                        and
                        low_lang <= high_lang
                    ):
                        will_delete = True
                        break

            # LOW -> HIGH / MEDIUM
            elif (
                file["quality"]
                in LOW_QUALITY_SOURCES
            ):

                for better in files_info:

                    if (
                        better["quality"]
                        not in HIGH_QUALITY_SOURCES
                        and
                        better["quality"]
                        not in MEDIUM_QUALITY_SOURCES
                    ):
                        continue

                    low_lang = set(
                        file["languages"]
                    )

                    better_lang = set(
                        better["languages"]
                    )

                    if (
                        "unknown" not in low_lang
                        and
                        "unknown" not in better_lang
                        and
                        low_lang <= better_lang
                    ):
                        will_delete = True
                        break

            if will_delete:
                to_delete += 1

            status = (
                "❌ DELETE"
                if will_delete
                else "✅ KEEP"
            )

            quality_str = (
                file["quality"].upper()
                if file["quality"] != "Unknown"
                else "N/A"
            )

            res_str = (
                file["resolution"].upper()
                if file["resolution"] != "Unknown"
                else "N/A"
            )

            lang_str = ", ".join(
                l.upper()
                for l in file["languages"]
            )

            file_text = (
                f"\n{idx + 1}. {status}\n"
                f"  📄 "
                f"{file['name'][:55]}...\n"
                f"  Quality: {quality_str} "
                f"| Res: {res_str} "
                f"| Langs: {lang_str}\n"
            )

            formatted_files.append(
                file_text
            )

        # -------------------------------------------------
        # CACHE
        # -------------------------------------------------

        task_id = str(message.id)

        DRY_RUN_CACHE[task_id] = {
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

        await message.reply_text(
            f"❌ Error: {str(e)}"
        )


# =========================================================
# CONFIRM SINGLE
# =========================================================

@Client.on_message(
    filters.command("cleanup_confirm_single")
    & filters.user(ADMINS)
)
async def cleanup_confirm_single_cmd(
    bot,
    message
):

    try:

        if len(message.command) < 2:
            return await message.reply_text(
                "❌ Usage: "
                "/cleanup_confirm_single <movie_name>"
            )

        movie_name = " ".join(
            message.command[1:]
        )

        msg = await message.reply_text(
            f"⚠️ **CONFIRMING DELETE**\n\n"
            f"Movie: {movie_name}\n"
            f"🗑️ Processing...\n\n"
            f"⏳ Please wait..."
        )

        base_title = get_base_title(
            movie_name
        )

        if not base_title:
            return await msg.edit_text(
                f"❌ Could not extract title "
                f"from: {movie_name}"
            )

        deleted_count = 0
        deleted_files = []

        for media_cls in MEDIA_DBS:

            d_count, d_files = (
                await cleanup_duplicates(
                    db_collection=media_cls.collection,
                    base_title=base_title,
                    keep_highest_quality=True
                )
            )

            deleted_count += d_count
            deleted_files.extend(
                d_files
            )

        if deleted_count > 0:

            deleted_preview = ""

            for idx, file in enumerate(
                deleted_files[:8],
                1
            ):
                deleted_preview += (
                    f"{idx}. "
                    f"{file[:55]}\n"
                )

            if len(deleted_files) > 8:
                deleted_preview += (
                    f"... + "
                    f"{len(deleted_files) - 8} "
                    f"more\n"
                )

            report = (
                f"✅ **DELETE COMPLETED!**\n"
                f"{'=' * 50}\n\n"
                f"🎬 Movie: {movie_name}\n"
                f"🗑️ Deleted: "
                f"{deleted_count} files "
                f"(Low/Medium Quality)\n\n"
                f"📋 **Deleted Files:**\n"
                f"{deleted_preview}"
            )

        else:

            report = (
                f"ℹ️ **No files deleted**\n\n"
                f"🎬 Movie: {movie_name}\n"
                f"Reason: No safe matching "
                f"low-quality files found. "
                f"HIGH quality files are safe."
            )

        await msg.edit_text(report)

    except Exception as e:

        await message.reply_text(
            f"❌ Error: {str(e)}"
        )


# =========================================================
# DRY BATCH PROCESS
# =========================================================

async def process_dry_batch(
    collection,
    task_id,
    msg,
    cancel_markup,
    total_docs,
    p_state
):

    movies = defaultdict(list)

    async for file in collection.find(
        {},
        projection={
            "file_name": 1,
            "caption": 1
        }
    ):

        if CANCEL_Q_TASKS.get(task_id):
            return False, 0, 0, []

        p_state["count"] += 1

        file_name = (
            file.get("file_name")
            or ""
        )

        caption = (
            file.get("caption")
            or ""
        )

        base_title = get_base_title(
            file_name
        )

        if base_title:

            quality = extract_quality_info(
                file_name,
                caption
            )

            langs = extract_language(
                f"{file_name} {caption}"
            )

            movies[base_title].append({
                "name": file_name,
                "quality": quality["source"],
                "languages": langs,
                "score": quality[
                    "quality_score"
                ]
            })

        if p_state["count"] % 500 == 0:
            await asyncio.sleep(0.1)

        if p_state["count"] % 5000 == 0:

            percent = (
                p_state["count"]
                / total_docs
                * 100
            )

            try:
                await msg.edit_text(
                    f"🔍 **DRY RUN - BATCH MODE**\n\n"
                    f"📁 Scanned: "
                    f"**{p_state['count']} / "
                    f"{total_docs}** files\n"
                    f"⏳ Progress: "
                    f"**{percent:.1f}%**\n"
                    f"⚙️ Status: "
                    f"Memory Safe Mode\n\n"
                    f"*(Nothing will be deleted "
                    f"in dry run)*",
                    reply_markup=cancel_markup
                )
            except Exception:
                pass

    total_to_delete = 0
    duplicate_movies = []

    for base_title, files in movies.items():

        if len(files) <= 1:
            continue

        to_delete = 0

        for f in files:

            # HIGH NEVER DELETE
            if (
                f["quality"]
                in HIGH_QUALITY_SOURCES
            ):
                continue

            can_delete = False

            # MEDIUM -> HIGH
            if (
                f["quality"]
                in MEDIUM_QUALITY_SOURCES
            ):

                for hq in files:

                    if (
                        hq["quality"]
                        not in HIGH_QUALITY_SOURCES
                    ):
                        continue

                    low_lang = set(
                        f["languages"]
                    )

                    high_lang = set(
                        hq["languages"]
                    )

                    if (
                        "unknown" not in low_lang
                        and
                        "unknown" not in high_lang
                        and
                        low_lang <= high_lang
                    ):
                        can_delete = True
                        break

            # LOW -> HIGH / MEDIUM
            elif (
                f["quality"]
                in LOW_QUALITY_SOURCES
            ):

                for better in files:

                    if (
                        better["quality"]
                        not in HIGH_QUALITY_SOURCES
                        and
                        better["quality"]
                        not in MEDIUM_QUALITY_SOURCES
                    ):
                        continue

                    low_lang = set(
                        f["languages"]
                    )

                    better_lang = set(
                        better["languages"]
                    )

                    if (
                        "unknown" not in low_lang
                        and
                        "unknown" not in better_lang
                        and
                        low_lang <= better_lang
                    ):
                        can_delete = True
                        break

            if can_delete:
                to_delete += 1

        if to_delete > 0:

            total_to_delete += to_delete

            duplicate_movies.append({
                "title": base_title,
                "count": len(files),
                "to_delete": to_delete
            })

    return (
        True,
        len(movies),
        total_to_delete,
        duplicate_movies
    )


# =========================================================
# DRY BATCH COMMAND
# =========================================================

@Client.on_message(
    filters.command("cleanup_dry_batch")
    & filters.user(ADMINS)
)
async def cleanup_dry_batch_cmd(
    bot,
    message
):

    try:

        task_id = str(message.id)

        CANCEL_Q_TASKS[
            task_id
        ] = False

        cancel_markup = (
            InlineKeyboardMarkup(
                [[
                    InlineKeyboardButton(
                        "🛑 CANCEL PROCESS",
                        callback_data=(
                            f"cancel_q_task_{task_id}"
                        )
                    )
                ]]
            )
        )

        msg = await message.reply_text(
            "📊 Calculating total files...\n"
            "⏳ Please wait...",
            reply_markup=cancel_markup
        )

        total_docs = 0

        for media_cls in MEDIA_DBS:
            total_docs += (
                await media_cls.collection
                .estimated_document_count()
            )

        if total_docs == 0:
            return await msg.edit_text(
                "❌ No files in database"
            )

        p_state = {
            "count": 0
        }

        t_movies = 0
        total_del = 0
        duplicate_movies = []

        for media_cls in MEDIA_DBS:

            status, mov, delc, dup = (
                await process_dry_batch(
                    media_cls.collection,
                    task_id,
                    msg,
                    cancel_markup,
                    total_docs,
                    p_state
                )
            )

            if not status:
                return await msg.edit_text(
                    "🛑 **Process Cancelled "
                    "by Admin!**"
                )

            t_movies += mov
            total_del += delc
            duplicate_movies.extend(dup)

        duplicate_movies.sort(
            key=lambda x: x["to_delete"],
            reverse=True
        )

        report = (
            f"📊 **DRY RUN - BATCH MODE**\n"
            f"{'=' * 50}\n\n"
            f"📁 Total Files Scanned: "
            f"{total_docs}\n"
            f"🎬 Total Unique Movies: "
            f"{t_movies}\n"
            f"📋 Movies with Low/Medium "
            f"Quality Duplicates: "
            f"{len(duplicate_movies)}\n\n"
            f"⚠️ **WOULD DELETE: "
            f"{total_del} files**\n\n"
        )

        if duplicate_movies:

            report += (
                f"📋 **Top Movies with "
                f"Duplicates:**\n"
                f"{'─' * 50}\n"
            )

            for idx, movie in enumerate(
                duplicate_movies[:10],
                1
            ):

                report += (
                    f"{idx}. "
                    f"{movie['title'][:45]}\n"
                    f"   Versions: "
                    f"{movie['count']} "
                    f"| Will Delete: "
                    f"{movie['to_delete']}\n\n"
                )

            if len(duplicate_movies) > 10:
                report += (
                    f"... + "
                    f"{len(duplicate_movies) - 10} "
                    f"more\n\n"
                )

            report += (
                f"{'─' * 50}\n\n"
                f"👉 Confirm & Delete ALL:\n"
                f"`/cleanup_confirm_batch`"
            )

        else:

            report += (
                "ℹ️ No safe low-quality "
                "duplicates found. "
                "HIGH quality files are safe."
            )

        await msg.edit_text(report)

    except Exception as e:

        await message.reply_text(
            f"❌ Error: {str(e)}"
        )


# =========================================================
# CONFIRM BATCH PROCESS
# =========================================================

async def process_confirm_batch(
    collection,
    task_id,
    msg,
    cancel_markup,
    total_docs,
    p_state
):

    movies = defaultdict(list)

    async for file in collection.find(
        {},
        projection={
            "_id": 1,
            "file_name": 1,
            "caption": 1
        }
    ):

        if CANCEL_Q_TASKS.get(task_id):
            return False, 0, []

        p_state["count"] += 1

        file_name = (
            file.get("file_name")
            or ""
        )

        caption = (
            file.get("caption")
            or ""
        )

        base_title = get_base_title(
            file_name
        )

        if base_title:

            quality = extract_quality_info(
                file_name,
                caption
            )

            langs = extract_language(
                f"{file_name} {caption}"
            )

            movies[base_title].append({
                "file_id": file["_id"],
                "name": file_name,
                "quality": quality["source"],
                "languages": langs,
                "score": quality[
                    "quality_score"
                ]
            })

        if p_state["count"] % 500 == 0:
            await asyncio.sleep(0.1)

        if p_state["count"] % 5000 == 0:

            percent = (
                p_state["count"]
                / total_docs
                * 100
            )

            try:
                await msg.edit_text(
                    f"⚠️ **CONFIRMING DELETE - "
                    f"BATCH**\n\n"
                    f"🗑️ Processing safely...\n"
                    f"📁 Scanned: "
                    f"**{p_state['count']} / "
                    f"{total_docs}** files\n"
                    f"⏳ Progress: "
                    f"**{percent:.1f}%**",
                    reply_markup=cancel_markup
                )
            except Exception:
                pass

    total_deleted = 0
    movies_cleaned = 0
    deleted_files_list = []

    for base_title, files in movies.items():

        if len(files) <= 1:
            continue

        cleaned_this_movie = False

        for f in files:

            # HIGH NEVER DELETE
            if (
                f["quality"]
                in HIGH_QUALITY_SOURCES
            ):
                continue

            can_delete = False

            # MEDIUM -> HIGH
            if (
                f["quality"]
                in MEDIUM_QUALITY_SOURCES
            ):

                for hq in files:

                    if (
                        hq["quality"]
                        not in HIGH_QUALITY_SOURCES
                    ):
                        continue

                    low_lang = set(
                        f["languages"]
                    )

                    high_lang = set(
                        hq["languages"]
                    )

                    if (
                        "unknown" not in low_lang
                        and
                        "unknown" not in high_lang
                        and
                        low_lang <= high_lang
                    ):
                        can_delete = True
                        break

            # LOW -> HIGH / MEDIUM
            elif (
                f["quality"]
                in LOW_QUALITY_SOURCES
            ):

                for better in files:

                    if (
                        better["quality"]
                        not in HIGH_QUALITY_SOURCES
                        and
                        better["quality"]
                        not in MEDIUM_QUALITY_SOURCES
                    ):
                        continue

                    low_lang = set(
                        f["languages"]
                    )

                    better_lang = set(
                        better["languages"]
                    )

                    if (
                        "unknown" not in low_lang
                        and
                        "unknown" not in better_lang
                        and
                        low_lang <= better_lang
                    ):
                        can_delete = True
                        break

            if not can_delete:
                continue

            # -------------------------------------------------
            # FINAL DELETE
            # -------------------------------------------------

            try:

                await collection.delete_one(
                    {
                        "_id": f["file_id"]
                    }
                )

                total_deleted += 1

                deleted_files_list.append(
                    f["name"]
                )

                cleaned_this_movie = True

            except Exception as e:

                logger.error(
                    f"[BATCH] Delete error: {e}"
                )

        if cleaned_this_movie:
            movies_cleaned += 1

    return (
        True,
        movies_cleaned,
        deleted_files_list
    )


# =========================================================
# CONFIRM BATCH COMMAND
# =========================================================

@Client.on_message(
    filters.command("cleanup_confirm_batch")
    & filters.user(ADMINS)
)
async def cleanup_confirm_batch_cmd(
    bot,
    message
):

    try:

        task_id = str(message.id)

        CANCEL_Q_TASKS[
            task_id
        ] = False

        cancel_markup = (
            InlineKeyboardMarkup(
                [[
                    InlineKeyboardButton(
                        "🛑 CANCEL PROCESS",
                        callback_data=(
                            f"cancel_q_task_{task_id}"
                        )
                    )
                ]]
            )
        )

        msg = await message.reply_text(
            "📊 Calculating total files...\n"
            "⏳ Please wait...",
            reply_markup=cancel_markup
        )

        total_docs = 0

        for media_cls in MEDIA_DBS:
            total_docs += (
                await media_cls.collection
                .estimated_document_count()
            )

        if total_docs == 0:
            return await msg.edit_text(
                "❌ No files in database"
            )

        p_state = {
            "count": 0
        }

        total_del = 0
        movies_clean = 0
        del_files = []

        for media_cls in MEDIA_DBS:

            status, clean, files = (
                await process_confirm_batch(
                    media_cls.collection,
                    task_id,
                    msg,
                    cancel_markup,
                    total_docs,
                    p_state
                )
            )

            if not status:
                return await msg.edit_text(
                    "🛑 **Process Cancelled "
                    "by Admin!**"
                )

            movies_clean += clean
            del_files.extend(files)
            total_del += len(files)

        if total_del > 0:

            deleted_preview = ""

            for idx, file in enumerate(
                del_files[:8],
                1
            ):

                deleted_preview += (
                    f"{idx}. "
                    f"{file[:55]}\n"
                )

            if len(del_files) > 8:
                deleted_preview += (
                    f"... + "
                    f"{len(del_files) - 8} "
                    f"more\n"
                )

            report = (
                f"✅ **BATCH DELETE "
                f"COMPLETED!**\n"
                f"{'=' * 50}\n\n"
                f"🗑️ Total Deleted: "
                f"{total_del} files\n"
                f"🎬 Movies Cleaned: "
                f"{movies_clean}\n\n"
                f"📋 **Sample Deleted:**\n"
                f"{deleted_preview}"
            )

        else:

            report = (
                f"ℹ️ **No files deleted**\n\n"
                f"All files are already optimal "
                f"quality.\n\n"
                f"🔒 HIGH quality files are "
                f"always protected."
            )

        await msg.edit_text(report)

    except Exception as e:

        await message.reply_text(
            f"❌ Error: {str(e)}"
        )