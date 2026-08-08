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


# =========================================================
# GLOBAL TASK CONTROL
# =========================================================

QUALITY_CLEANUP_SEMAPHORE = asyncio.Semaphore(2)
QUALITY_TASK_LOCK = asyncio.Lock()

QUALITY_ACTIVE_TASK = None
QUALITY_TASKS = {}
CANCEL_Q_TASKS = {}
DRY_RUN_CACHE = {}


# =========================================================
# QUALITY HIERARCHY
# =========================================================
#
# IMPORTANT:
# HIGH_QUALITY_SOURCES are PROTECTED.
#
# Their hierarchy score is still used to classify quality,
# BUT HIGH files are NEVER deleted.
#
# Example:
# HDRip -> BluRay
# HDRip will NOT be deleted.
#
# WEBRip -> BluRay
# WEBRip will NOT be deleted.
#
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
    "140p": 1,
    "240p": 1,
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

    "urdu": [
        r"\burdu\b",
        r"\burd\b",
    ],

    "korean": [
        r"\bkorean\b",
        r"\bkor\b",
    ],

    "japanese": [
        r"\bjapanese\b",
        r"\bjpn\b",
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


# =========================================================
# HIGH QUALITY
# =========================================================
#
# !!! NEVER DELETE !!!
#
# DO NOT REMOVE THESE.
#
# HDRip is independent from BluRay.
# BluRay must NOT delete HDRip.
#
# =========================================================

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
# TITLE NOISE
# =========================================================

TITLE_NOISE_WORDS = {
    "hevc",
    "x265",
    "x264",
    "h264",
    "avc",
    "av1",
    "aac",
    "flac",
    "dts",
    "ac3",
    "eac3",
    "ddp",
    "ddp5",
    "ddp51",
    "dd5",
    "dd51",
    "51",
    "71",
    "20",

    "dub",
    "dubbed",
    "sub",
    "subs",
    "esub",
    "esubs",
    "multi",
    "proper",
    "uncut",
    "repack",
    "extended",
    "complete",

    "season",
    "episode",
    "ep",

    "nf",
    "netflix",
    "amzn",
    "amazon",
    "prime",
    "primevideo",
    "sonyliv",
    "sony",
    "sliv",
    "hotstar",
    "jio",
    "jhs",
    "zee5",
    "aha",
    "hbo",
    "paramount",
    "apple",
    "hoichoi",
    "sunnxt",
    "viki",

    "movies4u",
    "tokyo",
    "updates",
    "telly",
    "www",

    "web",
    "dl",
    "rip",
    "ray",
    "blu",
    "bd",
    "br",
    "cam",
    "tc",
    "ts",
    "hd",
    "dvd",
    "scr",
    "tv",
    "pre",
}


QUALITY_ALIASES = sorted(
    QUALITY_HIERARCHY.keys(),
    key=len,
    reverse=True,
)

RESOLUTION_ALIASES = sorted(
    RESOLUTION_HIERARCHY.keys(),
    key=len,
    reverse=True,
)


# =========================================================
# LANGUAGE EXTRACTION
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


# =========================================================
# QUALITY EXTRACTION
# =========================================================

def extract_quality_info(
    filename: str,
    caption: str = ""
) -> dict:

    text = f"{filename or ''} {caption or ''}".lower()

    info = {
        "source": None,
        "resolution": None,
        "quality_score": 0,
        "source_score": 0,
        "resolution_score": 0,
    }

    # -----------------------------------------------------
    # SOURCE
    # -----------------------------------------------------

    matches = []

    for source, score in QUALITY_HIERARCHY.items():

        pattern = (
            rf"(?<![a-z0-9])"
            rf"{re.escape(source)}"
            rf"(?![a-z0-9])"
        )

        if re.search(pattern, text, re.I):
            matches.append((score, source))

    if matches:
        score, source = max(
            matches,
            key=lambda x: x[0]
        )

        info["source"] = source
        info["source_score"] = score

    # -----------------------------------------------------
    # RESOLUTION
    # -----------------------------------------------------

    res_matches = []

    for res, score in RESOLUTION_HIERARCHY.items():

        pattern = (
            rf"(?<![a-z0-9])"
            rf"{re.escape(res)}"
            rf"(?![a-z0-9])"
        )

        if re.search(pattern, text, re.I):
            res_matches.append((score, res))

    if res_matches:
        score, res = max(
            res_matches,
            key=lambda x: x[0]
        )

        info["resolution"] = res
        info["resolution_score"] = score

    # -----------------------------------------------------
    # FINAL SCORE
    # -----------------------------------------------------

    info["quality_score"] = (
        info["source_score"] * 0.7
        + info["resolution_score"] * 0.3
    )

    return info


# =========================================================
# QUALITY HELPERS
# =========================================================

def _normalize_source(source: str) -> str:
    return (source or "").lower().strip()


def _quality_score(source: str) -> int:
    return QUALITY_HIERARCHY.get(
        _normalize_source(source),
        0,
    )


def _resolution_score(resolution: str) -> int:
    return RESOLUTION_HIERARCHY.get(
        _normalize_source(resolution),
        0,
    )


def is_low_quality_print(
    quality_info: dict
) -> bool:

    source = _normalize_source(
        quality_info.get("source")
    )

    return source in LOW_QUALITY_SOURCES


def is_high_quality(
    quality_info: dict
) -> bool:

    source = _normalize_source(
        quality_info.get("source")
    )

    return source in HIGH_QUALITY_SOURCES


def is_protected_high_quality(
    source: str
) -> bool:

    return _normalize_source(source) in HIGH_QUALITY_SOURCES


# =========================================================
# SAFE TITLE / MOVIE IDENTITY
# =========================================================

def get_base_title(filename: str) -> str:
    """
    Conservative canonical title extraction.

    Only known release metadata is removed.

    IMPORTANT:
    High quality protection does NOT depend on title parsing.
    If a file is HIGH quality, it is never deleted.
    """

    text = (filename or "").strip().lower()

    # -----------------------------------------------------
    # Extension
    # -----------------------------------------------------

    text = re.sub(
        r"\.(mkv|mp4|avi|mov|wmv|flv|webm|m4v|mpg|mpeg)$",
        "",
        text,
        flags=re.I,
    )

    # -----------------------------------------------------
    # URLs / handles
    # -----------------------------------------------------

    text = re.sub(
        r"https?://\S+|www\.\S+|@\S+",
        " ",
        text,
        flags=re.I,
    )

    # -----------------------------------------------------
    # Brackets
    # -----------------------------------------------------

    text = re.sub(
        r"\[(?:[^\]]*)\]"
        r"|\((?!(?:19|20)\d{2}\s*$)[^\)]*\)"
        r"|\{[^\}]*\}",
        " ",
        text,
        flags=re.I,
    )

    # -----------------------------------------------------
    # Normalize separators
    # -----------------------------------------------------

    text = re.sub(
        r"[._\-–—]+",
        " ",
        text,
    )

    # -----------------------------------------------------
    # Season / Episode
    # -----------------------------------------------------

    patterns = [
        r"\bs\d{1,2}\s*e\d{1,3}"
        r"(?:\s*(?:to|-)\s*e?\d{1,3})?\b",

        r"\bs\d{1,2}\b",

        r"\bseason\s*\d{1,2}\b",

        r"\bepisode\s*\d{1,3}\b",

        r"\bep(?:isode)?\s*\d{1,3}"
        r"(?:\s*(?:to|-)\s*(?:ep(?:isode)?)?\s*\d{1,3})?\b",
    ]

    for pattern in patterns:
        text = re.sub(
            pattern,
            " ",
            text,
            flags=re.I,
        )

    # -----------------------------------------------------
    # Remove quality tokens
    # -----------------------------------------------------

    for quality in (
        QUALITY_ALIASES
        + RESOLUTION_ALIASES
    ):

        text = re.sub(
            rf"(?<![a-z0-9])"
            rf"{re.escape(quality)}"
            rf"(?![a-z0-9])",
            " ",
            text,
            flags=re.I,
        )

    # -----------------------------------------------------
    # Remove codec / release metadata
    # -----------------------------------------------------

    noise_pattern = (
        r"\b(?:"
        r"hevc|x265|x264|h264|avc|av1|"
        r"aac|flac|dts|ac3|eac3|"
        r"ddp(?:5\.1)?|dd(?:5\.1)?|"
        r"5\.1|7\.1|2\.0|"

        r"dub(?:bed)?|sub(?:s)?|esub(?:s)?|"
        r"multi|proper|uncut|repack|extended|complete|"

        r"hindi|english|tamil|telugu|malayalam|"
        r"kannada|punjabi|bengali|marathi|"
        r"gujarati|urdu|korean|japanese|"

        r"hin|eng|tam|tel|mal|kan|pan|pbi|"
        r"ben|mar|guj|urd|kor|jpn|"

        r"hi|en|ta|te|ml|kn|pa|bn|mr|gu|"

        r"nf|netflix|amzn|amazon|prime|primevideo|"
        r"sonyliv|sony|sliv|hotstar|jio|jhs|"
        r"zee5|aha|hbo|max|paramount|apple|"
        r"hoichoi|sunnxt|viki|"

        r"movies4u|telly|tokyo_updates|tokyoupdates"
        r")\b"
    )

    text = re.sub(
        noise_pattern,
        " ",
        text,
        flags=re.I,
    )

    # -----------------------------------------------------
    # Release group-ish tokens
    # -----------------------------------------------------

    text = re.sub(
        r"\b(?:web|dl|rip|bluray|bdrip|brrip|"
        r"hdrip|dvdrip|dvdscr)\b",
        " ",
        text,
        flags=re.I,
    )

    # -----------------------------------------------------
    # Keep normal title characters
    # -----------------------------------------------------

    text = re.sub(
        r"[^a-z0-9\u0900-\u097f]+",
        " ",
        text,
        flags=re.I,
    )

    text = re.sub(
        r"\s+",
        " ",
        text,
    ).strip()

    # -----------------------------------------------------
    # Generic movie / film
    # -----------------------------------------------------

    tokens = text.split()

    while tokens and tokens[0] in {
        "movie",
        "film",
    }:
        tokens.pop(0)

    while tokens and tokens[-1] in {
        "movie",
        "film",
    }:
        tokens.pop()

    return " ".join(tokens)


# =========================================================
# TITLE TOKENS
# =========================================================

def _title_tokens(title: str) -> List[str]:

    return [
        x
        for x in re.findall(
            r"[a-z0-9\u0900-\u097f]+",
            (title or "").lower(),
        )
        if x
    ]


# =========================================================
# SAME MOVIE TITLE
# =========================================================

def same_movie_title(
    title_a: str,
    title_b: str
) -> bool:

    a = get_base_title(title_a)
    b = get_base_title(title_b)

    if not a or not b:
        return False

    # Exact canonical title
    if a == b:
        return True

    ta = set(_title_tokens(a))
    tb = set(_title_tokens(b))

    if len(ta) < 2 or len(tb) < 2:
        return False

    intersection = ta & tb
    union = ta | tb

    jaccard = (
        len(intersection) / len(union)
        if union
        else 0
    )

    ratio = SequenceMatcher(
        None,
        a,
        b,
    ).ratio()

    # Very strong match only.
    if (
        jaccard >= 0.80
        and ratio >= 0.90
    ):
        return True

    if (
        min(len(a), len(b)) >= 10
        and ratio >= 0.94
        and jaccard >= 0.75
    ):
        return True

    return False


# =========================================================
# LANGUAGE MATCH
# =========================================================

def languages_match(
    old_langs,
    new_langs
) -> bool:

    old_set = set(old_langs or [])
    new_set = set(new_langs or [])

    # Unknown old language = SAFE
    if "unknown" in old_set:
        return False

    # Unknown new language = SAFE
    if "unknown" in new_set:
        return False

    # Existing language must be represented
    # by the new file.
    return old_set <= new_set


# =========================================================
# QUALITY DELETE DECISION
# =========================================================

def should_delete_existing(
    existing_quality: dict,
    new_quality: dict,
    existing_langs: List[str],
    new_langs: List[str],
) -> bool:

    try:

        old_source = _normalize_source(
            existing_quality.get("source")
        )

        new_source = _normalize_source(
            new_quality.get("source")
        )

        # -------------------------------------------------
        # Unknown quality = NEVER DELETE
        # -------------------------------------------------

        if not old_source or not new_source:
            return False

        # -------------------------------------------------
        # 🔒 HIGH QUALITY = ABSOLUTELY PROTECTED
        # -------------------------------------------------
        #
        # THIS IS THE MOST IMPORTANT CHANGE.
        #
        # HDRip will NOT be deleted by BluRay.
        # WEBRip will NOT be deleted by BluRay.
        # WEB-DL will NOT be deleted by BluRay.
        # BluRay will NOT be deleted by anything.
        #
        # -------------------------------------------------

        if is_protected_high_quality(old_source):
            return False

        # -------------------------------------------------
        # Language safety
        # -------------------------------------------------

        if not languages_match(
            existing_langs,
            new_langs,
        ):
            return False

        old_q = _quality_score(old_source)
        new_q = _quality_score(new_source)

        old_r = _resolution_score(
            existing_quality.get("resolution")
        )

        new_r = _resolution_score(
            new_quality.get("resolution")
        )

        if old_q == 0 or new_q == 0:
            return False

        # -------------------------------------------------
        # New HIGH quality can delete LOW/MEDIUM.
        # -------------------------------------------------

        if (
            is_protected_high_quality(new_source)
            and old_source not in HIGH_QUALITY_SOURCES
        ):
            return True

        # -------------------------------------------------
        # Source upgrade for LOW/MEDIUM
        # -------------------------------------------------

        if new_q > old_q:

            # Existing is guaranteed non-HIGH
            return True

        # -------------------------------------------------
        # SAME source:
        # Do NOT automatically delete based on resolution.
        #
        # This prevents:
        # CAMRip 720p -> CAMRip 1080p
        # DVDRip 720p -> DVDRip 1080p
        #
        # from deleting the old file.
        # -------------------------------------------------

        return False

    except Exception as e:

        logger.error(
            "[QUALITY] should_delete_existing error: %s",
            e,
            exc_info=True,
        )

        return False


# =========================================================
# MANUAL CLEANUP DECISION
# =========================================================

def should_delete_file_against_files(
    current: dict,
    all_files: List[dict],
) -> bool:

    source = _normalize_source(
        current.get("quality")
    )

    # -----------------------------------------------------
    # 🔒 HIGH QUALITY NEVER DELETE
    # -----------------------------------------------------

    if not source:
        return False

    if is_protected_high_quality(source):
        return False

    current_q = _quality_score(source)

    # -----------------------------------------------------
    # Compare against all other versions
    # -----------------------------------------------------

    for other in all_files:

        if other is current:
            continue

        other_source = _normalize_source(
            other.get("quality")
        )

        if not other_source:
            continue

        # -------------------------------------------------
        # Unknown other quality is ignored
        # -------------------------------------------------

        other_q = _quality_score(other_source)

        if other_q == 0:
            continue

        # -------------------------------------------------
        # Language safety
        # -------------------------------------------------

        if not languages_match(
            current.get("languages", []),
            other.get("languages", []),
        ):
            continue

        # -------------------------------------------------
        # Better source exists
        # -------------------------------------------------

        if other_q > current_q:

            # This includes:
            #
            # CAMRip -> HDRip       DELETE CAMRip
            # HDTS   -> WEBRip      DELETE HDTS
            # DVDRip -> WEB-DL      DELETE DVDRip
            #
            # But:
            #
            # HDRip -> BluRay       HDRip already protected
            # WEBRip -> BluRay      WEBRip already protected
            #
            return True

    return False


# =========================================================
# COMPATIBILITY HELPER
# =========================================================

def can_delete_quality_file(
    file_quality,
    file_langs,
    all_files
) -> bool:

    """
    Compatibility wrapper.

    HIGH quality files are ALWAYS protected.

    For LOW/MEDIUM files, deletion is allowed only when
    a strictly better source exists with compatible language.
    """

    source = _normalize_source(file_quality)

    if not source:
        return False

    # 🔒 HIGH QUALITY NEVER DELETE
    if is_protected_high_quality(source):
        return False

    current_q = _quality_score(source)

    if current_q == 0:
        return False

    current = {
        "quality": source,
        "languages": file_langs or [],
    }

    # Mark matching current files where possible.
    for item in all_files:

        item_source = _normalize_source(
            item.get("quality")
        )

        if item_source != source:
            continue

        item_langs = item.get(
            "languages",
            [],
        )

        if item_langs == (file_langs or []):

            current = item
            break

    return should_delete_file_against_files(
        current,
        all_files,
    )


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
    file_id: Optional[str] = None,
) -> Tuple[bool, str]:

    try:

        new_quality = extract_quality_info(
            new_filename,
            new_caption or "",
        )

        new_source = _normalize_source(
            new_quality.get("source")
        )

        # -------------------------------------------------
        # Only known LOW/MEDIUM/HIGH sources participate
        # -------------------------------------------------

        if (
            new_source not in HIGH_QUALITY_SOURCES
            and new_source not in MEDIUM_QUALITY_SOURCES
        ):
            return True, "New file is low quality or unsupported"

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
            for w in _title_tokens(base_title)
            if len(w) > 2
        ]

        if not words:
            return True, "No significant words"

        pattern = ".*".join(
            re.escape(w)
            for w in words[:5]
        )

        search_query = {
            "file_name": {
                "$regex": pattern,
                "$options": "i",
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
                "caption": 1,
            },
            batch_size=100,
        ).limit(500)

        processed = 0
        deleted_count = 0

        try:

            async for file_in_db in cursor:

                processed += 1

                existing_filename = file_in_db.get(
                    "file_name",
                    "",
                )

                existing_caption = file_in_db.get(
                    "caption",
                    ""
                ) or ""

                # -------------------------------------------------
                # SAME MOVIE CHECK
                # -------------------------------------------------

                if not same_movie_title(
                    new_filename,
                    existing_filename,
                ):

                    await quality_yield(
                        processed,
                        50,
                    )

                    continue

                existing_quality = extract_quality_info(
                    existing_filename,
                    existing_caption,
                )

                existing_source = _normalize_source(
                    existing_quality.get("source")
                )

                # -------------------------------------------------
                # Unknown quality = NEVER DELETE
                # -------------------------------------------------

                if not existing_source:

                    await quality_yield(
                        processed,
                        50,
                    )

                    continue

                # -------------------------------------------------
                # 🔒 HARD HIGH-QUALITY PROTECTION
                # -------------------------------------------------
                #
                # This check happens BEFORE all replacement logic.
                #
                # Therefore:
                #
                # HDRip -> BluRay = KEEP HDRip
                # WEBRip -> BluRay = KEEP WEBRip
                # WEB-DL -> BluRay = KEEP WEB-DL
                # BluRay -> anything = KEEP BluRay
                #
                # -------------------------------------------------

                if is_protected_high_quality(
                    existing_source
                ):

                    await quality_yield(
                        processed,
                        50,
                    )

                    continue

                existing_langs = extract_language(
                    f"{existing_filename} "
                    f"{existing_caption}"
                )

                # -------------------------------------------------
                # LANGUAGE SAFETY
                # -------------------------------------------------

                if not languages_match(
                    existing_langs,
                    new_langs,
                ):

                    await quality_yield(
                        processed,
                        50,
                    )

                    continue

                # -------------------------------------------------
                # FINAL DELETE DECISION
                # -------------------------------------------------

                if not should_delete_existing(
                    existing_quality,
                    new_quality,
                    existing_langs,
                    new_langs,
                ):

                    await quality_yield(
                        processed,
                        50,
                    )

                    continue

                # -------------------------------------------------
                # DELETE
                # -------------------------------------------------

                try:

                    result = await db_collection.delete_one(
                        {
                            "_id": file_in_db["_id"]
                        }
                    )

                    if result.deleted_count:

                        deleted_count += 1

                        logger.warning(
                            "[QUALITY] Deleted %s: %s -> %s",
                            existing_source.upper(),
                            existing_filename[:100],
                            new_source.upper(),
                        )

                except Exception as e:

                    logger.error(
                        "[QUALITY] Delete error: %s",
                        e,
                    )

                await quality_yield(
                    processed,
                    50,
                )

        finally:

            try:
                await cursor.close()
            except Exception:
                pass

        if deleted_count:

            return (
                True,
                f"Deleted {deleted_count} lower-quality files",
            )

        return (
            True,
            "No lower quality files",
        )

    except Exception as e:

        logger.error(
            "[QUALITY] find_and_delete error: %s",
            e,
            exc_info=True,
        )

        return False, str(e)


# =========================================================
# BACKGROUND AUTO CLEANUP
# =========================================================

async def run_quality_cleanup_background(
    media_dbs,
    file_name: str,
    caption: str,
):

    async with QUALITY_CLEANUP_SEMAPHORE:

        try:

            for idx, media_cls in enumerate(
                media_dbs,
                start=1,
            ):

                success, msg = await find_and_delete_lower_quality(
                    db_collection=media_cls.collection,
                    new_filename=file_name,
                    new_caption=caption,
                )

                if (
                    success
                    and "Deleted" in msg
                ):

                    logger.warning(
                        "[QUALITY DB%d] %s -> %s",
                        idx,
                        file_name[:60],
                        msg,
                    )

        except Exception as e:

            logger.error(
                "[QUALITY] Background cleanup failed: %s",
                e,
                exc_info=True,
            )


# =========================================================
# STREAM MONGODB COLLECTION
# =========================================================

async def stream_collection_files(
    collection,
    task_id,
    projection=None,
    batch_size=200,
):

    cursor = collection.find(
        {},
        projection=projection,
        batch_size=batch_size,
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
    dry_run=False,
):

    movies = defaultdict(list)

    projection = {
        "_id": 1,
        "file_name": 1,
        "caption": 1,
    }

    async for file in stream_collection_files(
        collection,
        task_id,
        projection=projection,
        batch_size=200,
    ):

        if CANCEL_Q_TASKS.get(task_id):
            return None

        p_state["count"] += 1

        count = p_state["count"]

        file_name = file.get(
            "file_name",
            "",
        )

        caption = file.get(
            "caption",
            ""
        ) or ""

        if not file_name:
            continue

        base_title = get_base_title(
            file_name
        )

        if not base_title:
            continue

        quality = extract_quality_info(
            file_name,
            caption,
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
                0,
            ),
        })

        if count % 100 == 0:
            await asyncio.sleep(0)

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
                    f"📁 Scanned: **{count:,} / {total_docs:,}**\n"
                    f"⏳ Progress: **{percent:.1f}%**\n\n"
                    "🧠 Memory: Streaming Mode\n"
                    "🤖 Bot: Online\n"
                    f"{delete_text}",
                    reply_markup=cancel_markup,
                )

            except Exception:
                pass

    return movies


# =========================================================
# ANALYZE MOVIE GROUPS
# =========================================================

async def analyze_movie_groups(
    movies,
    task_id,
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

            if CANCEL_Q_TASKS.get(task_id):
                return None, None

            if should_delete_file_against_files(
                file,
                files,
            ):

                movie_delete += 1

            checked += 1

            if checked % 100 == 0:
                await asyncio.sleep(0)

        if movie_delete:

            total_delete += movie_delete

            duplicate_movies.append({
                "title": base_title,
                "count": len(files),
                "to_delete": movie_delete,
            })

    return total_delete, duplicate_movies


# =========================================================
# SINGLE MOVIE FINDER
# =========================================================

async def find_single_movie_files(
    collection,
    movie_name,
    task_id,
    max_files=1000,
):

    base_title = get_base_title(
        movie_name
    )

    if not base_title:
        return []

    words = [
        w
        for w in _title_tokens(base_title)
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
                "$options": "i",
            }
        },
        projection={
            "_id": 1,
            "file_name": 1,
            "caption": 1,
        },
        batch_size=100,
    )

    results = []

    try:

        async for file in cursor:

            if CANCEL_Q_TASKS.get(task_id):
                break

            if same_movie_title(
                movie_name,
                file.get("file_name", ""),
            ):

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
    worker,
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
                task_id,
            )

        except Exception as e:

            logger.error(
                "[QUALITY] Background task error: %s",
                e,
                exc_info=True,
            )

        finally:

            QUALITY_TASKS.pop(
                task_id,
                None,
            )

            CANCEL_Q_TASKS.pop(
                task_id,
                None,
            )

            QUALITY_ACTIVE_TASK = None


# =========================================================
# CANCEL CALLBACK
# =========================================================

@Client.on_callback_query(
    filters.regex(
        r"^cancel_q_task_(.*)"
    )
)
async def cancel_q_task(
    client,
    query,
):

    task_id = query.data.split(
        "cancel_q_task_",
        1,
    )[-1]

    if task_id in CANCEL_Q_TASKS:

        CANCEL_Q_TASKS[
            task_id
        ] = True

        await query.answer(
            "🛑 Cancellation requested...",
            show_alert=True,
        )

    else:

        await query.answer(
            "⚠️ Task already finished.",
            show_alert=True,
        )


# =========================================================
# AUTO DELETE DRY RUN MESSAGE
# =========================================================

async def auto_delete_msg(
    msg,
    command_msg,
    task_id,
    delay=300,
):

    try:

        await asyncio.sleep(delay)

        DRY_RUN_CACHE.pop(
            task_id,
            None,
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
    page,
):

    data = DRY_RUN_CACHE.get(task_id)

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
        if total_files
        else 1
    )

    page = max(
        0,
        min(
            page,
            total_pages - 1,
        ),
    )

    chunk = data["files"][
        page * ITEMS_PER_PAGE:
        (page + 1) * ITEMS_PER_PAGE
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

    report += "⏱️ Auto-delete in 5 minutes."

    buttons = []

    if page > 0:

        buttons.append(
            InlineKeyboardButton(
                "⬅️ Previous",
                callback_data=(
                    f"dry_page_"
                    f"{task_id}_"
                    f"{page - 1}"
                ),
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
                ),
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
                reply_markup=reply_markup,
            )

        else:

            await msg.message.edit_text(
                report,
                reply_markup=reply_markup,
            )

    except Exception as e:

        logger.error(
            "[QUALITY] Pagination error: %s",
            e,
        )


# =========================================================
# PAGINATION CALLBACK
# =========================================================

@Client.on_callback_query(
    filters.regex(r"^dry_page_")
)
async def dry_page_callback(
    client,
    query,
):

    try:

        parts = query.data.split("_")

        task_id = parts[2]
        page = int(parts[3])

        await send_dry_page(
            query,
            task_id,
            page,
        )

        await query.answer()

    except Exception:

        await query.answer(
            "❌ Page expired.",
            show_alert=True,
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
    message,
):

    global QUALITY_ACTIVE_TASK

    if QUALITY_ACTIVE_TASK:

        return await message.reply_text(
            "⏳ **QUALITY TASK ALREADY RUNNING**\n\n"
            "Pehle current quality process complete hone do."
        )

    task_id = str(message.id)

    CANCEL_Q_TASKS[
        task_id
    ] = False

    cancel_markup = InlineKeyboardMarkup([[
        InlineKeyboardButton(
            "🛑 CANCEL",
            callback_data=(
                f"cancel_q_task_{task_id}"
            ),
        )
    ]])

    msg = await message.reply_text(
        "📊 **QUALITY REPORT STARTED**\n\n"
        "🧠 Streaming mode enabled.\n"
        "🤖 Bot normal kaam karta rahega.\n\n"
        "⏳ Scanning database...",
        reply_markup=cancel_markup,
    )

    async def worker():

        try:

            total_docs = sum([
                await media_cls.collection.estimated_document_count()
                for media_cls in MEDIA_DBS
            ])

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
                        "caption": 1,
                    },
                    batch_size=200,
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
                            "",
                        )

                        caption = file.get(
                            "caption",
                            ""
                        ) or ""

                        qi = extract_quality_info(
                            filename,
                            caption,
                        )

                        quality_dist[
                            qi.get("source")
                            or "unknown"
                        ] += 1

                        resolution_dist[
                            qi.get("resolution")
                            or "unknown"
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
                                    f"📁 Scanned: **{processed:,} / {total_docs:,}**\n"
                                    f"⏳ Progress: **{percent:.1f}%**\n\n"
                                    "🧠 Streaming Mode\n"
                                    "🤖 Bot Online",
                                    reply_markup=cancel_markup,
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
                f"({len(MEDIA_DBS)} DB"
                f"{'s' if len(MEDIA_DBS) > 1 else ''})**\n"
                "━━━━━━━━━━━━━━━━━━━━\n\n"
                f"📁 **Total Files:** {total_docs:,}\n\n"
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
                "unknown",
            ]

            for quality in quality_order:

                if quality not in quality_dist:
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

                emoji = (
                    "⚠️"
                    if quality in LOW_QUALITY_SOURCES
                    else (
                        "✨"
                        if quality in HIGH_QUALITY_SOURCES
                        else "⭐"
                    )
                )

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
                "140p",
                "240p",
                "360p",
                "480p",
                "540p",
                "720p",
                "1080p",
                "1440p",
                "2160p",
                "unknown",
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
                quality_dist.get(q, 0)
                for q in LOW_QUALITY_SOURCES
            )

            medium_count = sum(
                quality_dist.get(q, 0)
                for q in MEDIUM_QUALITY_SOURCES
            )

            high_count = sum(
                quality_dist.get(q, 0)
                for q in HIGH_QUALITY_SOURCES
            )

            report += (
                "\n━━━━━━━━━━━━━━━━━━━━\n"
                f"⚠️ Low Quality: **{low_count:,}**\n"
                f"⭐ Medium Quality: **{medium_count:,}**\n"
                f"✨ High Quality: **{high_count:,}**\n\n"
                "🛡️ **HIGH QUALITY FILES ARE PROTECTED**\n"
                "HDRip / WEBRip / WEB-DL / BluRay / "
                "BDRip / BRRip will never be deleted."
            )

            await msg.edit_text(
                report,
                reply_markup=None,
            )

        except Exception as e:

            logger.error(
                "[QUALITY] Report error: %s",
                e,
                exc_info=True,
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
            worker,
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
    message,
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

    task_id = str(message.id)

    CANCEL_Q_TASKS[
        task_id
    ] = False

    cancel_markup = InlineKeyboardMarkup([[
        InlineKeyboardButton(
            "🛑 CANCEL",
            callback_data=(
                f"cancel_q_task_{task_id}"
            ),
        )
    ]])

    msg = await message.reply_text(
        "🔍 **SINGLE MOVIE DRY RUN**\n\n"
        f"🎬 Movie: **{movie_name}**\n\n"
        "🗑️ Nothing will be deleted.\n"
        "⏳ Scanning...",
        reply_markup=cancel_markup,
    )

    async def worker():

        try:

            all_files = []

            for media_cls in MEDIA_DBS:

                all_files.extend(
                    await find_single_movie_files(
                        media_cls.collection,
                        movie_name,
                        task_id,
                        max_files=1000,
                    )
                )

            if CANCEL_Q_TASKS.get(task_id):

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
                    "Unknown",
                )

                caption = file.get(
                    "caption",
                    ""
                ) or ""

                qi = extract_quality_info(
                    filename,
                    caption,
                )

                files_info.append({
                    "name": filename,
                    "quality": qi.get(
                        "source"
                    ),
                    "resolution": qi.get(
                        "resolution"
                    ),
                    "languages": extract_language(
                        f"{filename} {caption}"
                    ),
                    "score": qi.get(
                        "quality_score",
                        0,
                    ),
                })

                if len(files_info) % 50 == 0:
                    await asyncio.sleep(0)

            to_delete = sum(
                1
                for f in files_info
                if should_delete_file_against_files(
                    f,
                    files_info,
                )
            )

            formatted_files = []

            for idx, file in enumerate(
                files_info,
                1,
            ):

                should_delete = (
                    should_delete_file_against_files(
                        file,
                        files_info,
                    )
                )

                status = (
                    "❌ DELETE"
                    if should_delete
                    else "✅ KEEP"
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

                # Extra safety display
                if (
                    file["quality"]
                    and is_protected_high_quality(
                        file["quality"]
                    )
                ):

                    status = "🛡️ PROTECTED"

                formatted_files.append(
                    f"\n**{idx}. {status}**\n"
                    f"📄 {file['name'][:70]}\n"
                    f"🎞️ Quality: {quality_str}\n"
                    f"📐 Resolution: {resolution_str}\n"
                    f"🌐 Language: {lang_str}\n"
                )

            DRY_RUN_CACHE[
                task_id
            ] = {
                "movie_name": movie_name,
                "files": formatted_files,
                "keep": len(files_info) - to_delete,
                "delete": to_delete,
            }

            await send_dry_page(
                msg,
                task_id,
                0,
            )

            asyncio.create_task(
                auto_delete_msg(
                    msg,
                    message,
                    task_id,
                    300,
                )
            )

        except Exception as e:

            logger.error(
                "[QUALITY] Single dry error: %s",
                e,
                exc_info=True,
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
            worker,
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
    message,
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

    task_id = str(message.id)

    CANCEL_Q_TASKS[
        task_id
    ] = False

    cancel_markup = InlineKeyboardMarkup([[
        InlineKeyboardButton(
            "🛑 CANCEL DELETE",
            callback_data=(
                f"cancel_q_task_{task_id}"
            ),
        )
    ]])

    msg = await message.reply_text(
        "⚠️ **SINGLE MOVIE DELETE**\n\n"
        f"🎬 Movie: **{movie_name}**\n\n"
        "🛡️ HIGH quality protected.\n"
        "⏳ Processing...",
        reply_markup=cancel_markup,
    )

    async def worker():

        try:

            total_deleted = 0
            deleted_files = []

            for media_cls in MEDIA_DBS:

                collection = media_cls.collection

                files = await find_single_movie_files(
                    collection,
                    movie_name,
                    task_id,
                    max_files=1000,
                )

                if not files:
                    continue

                files_info = []

                for file in files:

                    filename = file.get(
                        "file_name",
                        "Unknown",
                    )

                    caption = file.get(
                        "caption",
                        ""
                    ) or ""

                    qi = extract_quality_info(
                        filename,
                        caption,
                    )

                    files_info.append({
                        "file_id": file.get("_id"),
                        "name": filename,
                        "quality": qi.get(
                            "source"
                        ),
                        "resolution": qi.get(
                            "resolution"
                        ),
                        "languages": extract_language(
                            f"{filename} {caption}"
                        ),
                    })

                    if len(files_info) % 50 == 0:
                        await asyncio.sleep(0)

                # -------------------------------------------------
                # Decide FIRST.
                # Never change decision after deletion.
                # -------------------------------------------------

                delete_candidates = [
                    file
                    for file in files_info
                    if should_delete_file_against_files(
                        file,
                        files_info,
                    )
                ]

                for file in delete_candidates:

                    if CANCEL_Q_TASKS.get(task_id):

                        return await msg.edit_text(
                            "🛑 **DELETE CANCELLED**\n\n"
                            f"Deleted before cancellation: "
                            f"**{total_deleted}**"
                        )

                    # -------------------------------------------------
                    # FINAL HARD SAFETY CHECK
                    # -------------------------------------------------

                    if is_protected_high_quality(
                        file.get("quality")
                    ):

                        logger.warning(
                            "[QUALITY] Protected HIGH skipped: %s",
                            file.get("name", "Unknown"),
                        )

                        continue

                    try:

                        result = await collection.delete_one(
                            {
                                "_id": file["file_id"]
                            }
                        )

                        if result.deleted_count:

                            total_deleted += 1

                            deleted_files.append(
                                file["name"]
                            )

                    except Exception as e:

                        logger.error(
                            "[QUALITY] Single delete error: %s",
                            e,
                        )

                    if (
                        total_deleted
                        and total_deleted % 25 == 0
                    ):

                        await asyncio.sleep(0)

            if total_deleted:

                preview = "".join(
                    f"{i}. {filename[:65]}\n"
                    for i, filename
                    in enumerate(
                        deleted_files[:10],
                        1,
                    )
                )

                if len(deleted_files) > 10:

                    preview += (
                        f"\n... + "
                        f"{len(deleted_files) - 10} "
                        f"more"
                    )

                await msg.edit_text(
                    "✅ **SINGLE MOVIE CLEANUP DONE**\n"
                    "━━━━━━━━━━━━━━━━━━━━\n\n"
                    f"🎬 Movie: **{movie_name}**\n"
                    f"🗑️ Deleted: **{total_deleted}**\n\n"
                    "📋 **Sample Deleted:**\n"
                    f"{preview}\n\n"
                    "🛡️ Same-title + language + quality "
                    "checks active.\n"
                    "✨ HIGH quality files were protected."
                )

            else:

                await msg.edit_text(
                    "ℹ️ **NOTHING DELETED**\n\n"
                    f"🎬 Movie: **{movie_name}**\n\n"
                    "No eligible lower-quality "
                    "versions found.\n\n"
                    "🛡️ HIGH quality files are safe."
                )

        except Exception as e:

            logger.error(
                "[QUALITY] Single confirm error: %s",
                e,
                exc_info=True,
            )

            await msg.edit_text(
                f"❌ **ERROR**\n\n"
                f"`{str(e)[:1000]}`"
            )

    asyncio.create_task(
        run_quality_task(
            task_id,
            worker,
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
    message,
):

    global QUALITY_ACTIVE_TASK

    if QUALITY_ACTIVE_TASK:

        return await message.reply_text(
            "⏳ **QUALITY CLEANUP ALREADY RUNNING**\n\n"
            "Ek time par sirf ek heavy quality process chalega."
        )

    task_id = str(message.id)

    CANCEL_Q_TASKS[
        task_id
    ] = False

    cancel_markup = InlineKeyboardMarkup([[
        InlineKeyboardButton(
            "🛑 CANCEL",
            callback_data=(
                f"cancel_q_task_{task_id}"
            ),
        )
    ]])

    msg = await message.reply_text(
        "🔍 **DRY RUN STARTED**\n\n"
        "📊 Database background me scan hoga.\n"
        "🤖 Bot normal kaam karega.\n"
        "🗑️ Kuch bhi delete nahi hoga.\n\n"
        "⏳ Please wait...",
        reply_markup=cancel_markup,
    )

    async def worker():

        try:

            total_docs = sum([
                await media_cls.collection.estimated_document_count()
                for media_cls in MEDIA_DBS
            ])

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

                if CANCEL_Q_TASKS.get(task_id):

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
                    dry_run=True,
                )

                if movies is None:

                    return await msg.edit_text(
                        "🛑 **DRY RUN CANCELLED**"
                    )

                delete_count, duplicates = (
                    await analyze_movie_groups(
                        movies,
                        task_id,
                    )
                )

                if delete_count is None:

                    return await msg.edit_text(
                        "🛑 **DRY RUN CANCELLED**"
                    )

                total_movies += len(movies)

                total_delete += delete_count

                all_duplicates.extend(
                    duplicates
                )

            all_duplicates.sort(
                key=lambda x: x["to_delete"],
                reverse=True,
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
                    1,
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
                        f"{len(all_duplicates) - 15} "
                        f"more\n\n"
                    )

                report += (
                    "━━━━━━━━━━━━━━━━━━━━\n"
                    "🛡️ Same-title + language + "
                    "quality checks active.\n"
                    "✨ HIGH quality protected.\n\n"
                    "👉 **Actual Delete:**\n"
                    "`/cleanup_confirm_batch`"
                )

            else:

                report += (
                    "✅ **NO CLEANUP REQUIRED**\n\n"
                    "No safe lower-quality "
                    "duplicates found."
                )

            await msg.edit_text(
                report,
                reply_markup=None,
            )

        except Exception as e:

            logger.error(
                "[QUALITY] Dry batch error: %s",
                e,
                exc_info=True,
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
            worker,
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
    message,
):

    global QUALITY_ACTIVE_TASK

    if QUALITY_ACTIVE_TASK:

        return await message.reply_text(
            "⏳ **QUALITY CLEANUP ALREADY RUNNING**\n\n"
            "Pehle current process complete hone do."
        )

    task_id = str(message.id)

    CANCEL_Q_TASKS[
        task_id
    ] = False

    cancel_markup = InlineKeyboardMarkup([[
        InlineKeyboardButton(
            "🛑 CANCEL DELETE",
            callback_data=(
                f"cancel_q_task_{task_id}"
            ),
        )
    ]])

    msg = await message.reply_text(
        "⚠️ **BATCH DELETE STARTED**\n\n"
        "🗑️ LOW/MEDIUM eligible files delete hongi.\n"
        "🛡️ HIGH quality files protected hain.\n"
        "🤖 Bot background me kaam karega.\n\n"
        "⏳ Please wait...",
        reply_markup=cancel_markup,
    )

    async def worker():

        try:

            total_docs = sum([
                await media_cls.collection.estimated_document_count()
                for media_cls in MEDIA_DBS
            ])

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

                if CANCEL_Q_TASKS.get(task_id):

                    return await msg.edit_text(
                        "🛑 **DELETE CANCELLED**\n\n"
                        f"Already deleted: "
                        f"**{total_deleted}**"
                    )

                collection = media_cls.collection

                movies = await build_movie_groups(
                    collection,
                    task_id,
                    p_state,
                    total_docs,
                    msg,
                    cancel_markup,
                    dry_run=False,
                )

                if movies is None:

                    return await msg.edit_text(
                        "🛑 **DELETE CANCELLED**\n\n"
                        f"Already deleted: "
                        f"**{total_deleted}**"
                    )

                for base_title, files in movies.items():

                    if CANCEL_Q_TASKS.get(task_id):

                        return await msg.edit_text(
                            "🛑 **DELETE CANCELLED**\n\n"
                            f"Already deleted: "
                            f"**{total_deleted}**"
                        )

                    if len(files) <= 1:
                        continue

                    # -------------------------------------------------
                    # Decide FIRST
                    # -------------------------------------------------

                    delete_candidates = [
                        f
                        for f in files
                        if should_delete_file_against_files(
                            f,
                            files,
                        )
                    ]

                    if not delete_candidates:
                        continue

                    cleaned_movie = False

                    for file in delete_candidates:

                        if CANCEL_Q_TASKS.get(task_id):

                            return await msg.edit_text(
                                "🛑 **DELETE CANCELLED**\n\n"
                                f"Already deleted: "
                                f"**{total_deleted}**"
                            )

                        # -------------------------------------------------
                        # 🔒 FINAL HARD HIGH QUALITY PROTECTION
                        # -------------------------------------------------

                        if is_protected_high_quality(
                            file.get("quality")
                        ):

                            logger.warning(
                                "[QUALITY] "
                                "HIGH PROTECTED - SKIP: %s",
                                file.get(
                                    "name",
                                    "Unknown",
                                ),
                            )

                            continue

                        try:

                            result = await collection.delete_one(
                                {
                                    "_id": file[
                                        "file_id"
                                    ]
                                }
                            )

                            if result.deleted_count:

                                total_deleted += 1

                                cleaned_movie = True

                                deleted_files.append(
                                    file.get(
                                        "name",
                                        "Unknown",
                                    )
                                )

                        except Exception as e:

                            logger.error(
                                "[QUALITY] "
                                "Batch delete error: %s",
                                e,
                            )

                        if (
                            total_deleted
                            and total_deleted % 25 == 0
                        ):

                            await asyncio.sleep(0)

                    if cleaned_movie:

                        movies_cleaned_set.add(
                            base_title
                        )

            if total_deleted:

                preview = "".join(
                    f"{i}. {filename[:65]}\n"
                    for i, filename
                    in enumerate(
                        deleted_files[:10],
                        1,
                    )
                )

                if len(deleted_files) > 10:

                    preview += (
                        f"\n... + "
                        f"{len(deleted_files) - 10} "
                        f"more"
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
                    "🛡️ Same-title + language + "
                    "quality checks active.\n"
                    "✨ HIGH quality files were protected."
                )

            else:

                report = (
                    "ℹ️ **NOTHING DELETED**\n\n"
                    "No safe LOW/MEDIUM "
                    "lower-quality duplicates found.\n\n"
                    "🛡️ HIGH quality files are safe."
                )

            await msg.edit_text(
                report,
                reply_markup=None,
            )

        except Exception as e:

            logger.error(
                "[QUALITY] Confirm batch error: %s",
                e,
                exc_info=True,
            )

            try:

                await msg.edit_text(
                    f"❌ **DELETE ERROR**\n\n"
                    f"`{str(e)[:1000]}`"
                )

            except Exception:
                pass

    asyncio.create_task(
        run_quality_task(
            task_id,
            worker,
        )
    )


# =========================================================
# COMMAND HELP
# =========================================================

@Client.on_message(
    filters.command("quality_help")
    & filters.user(ADMINS)
)
async def quality_help_cmd(
    bot,
    message,
):

    help_text = """
🛠️ **QUALITY MANAGER**

━━━━━━━━━━━━━━━━━━━━

📊 **1. QUALITY REPORT**
`/quality_report`

➡️ Database ki quality/resolution report.
➡️ Kuch delete nahi hota.

━━━━━━━━━━━━━━━━━━━━

🔍 **2. SINGLE MOVIE DRY RUN**
`/cleanup_dry_single <movie name>`

Example:
`/cleanup_dry_single Prem Prakaran 2026`

➡️ Exact/safe title matching ke saath preview.
➡️ Kuch delete nahi hoga.

━━━━━━━━━━━━━━━━━━━━

🗑️ **3. SINGLE MOVIE DELETE**
`/cleanup_confirm_single <movie name>`

➡️ Sirf same movie + compatible language.
➡️ LOW/MEDIUM lower-quality versions delete hongi.
➡️ HIGH quality files protected.

━━━━━━━━━━━━━━━━━━━━

🔍 **4. FULL DATABASE DRY RUN**
`/cleanup_dry_batch`

➡️ Pura database scan.
➡️ Kuch delete nahi karega.

━━━━━━━━━━━━━━━━━━━━

🗑️ **5. FULL DATABASE DELETE**
`/cleanup_confirm_batch`

➡️ Safe LOW/MEDIUM lower-quality duplicates delete karega.

━━━━━━━━━━━━━━━━━━━━

🛡️ **HIGH QUALITY PROTECTION**

❌ HDRip kabhi delete nahi hogi.
❌ WEBRip kabhi delete nahi hogi.
❌ WEB-DL kabhi delete nahi hogi.
❌ BluRay kabhi delete nahi hogi.
❌ BDRip kabhi delete nahi hogi.
❌ BRRip kabhi delete nahi hogi.

Example:

HDRip 720p
+
BluRay 1080p

➡️ **HDRip KEEP**
➡️ **BluRay KEEP**

WEBRip 720p
+
BluRay 1080p

➡️ **WEBRip KEEP**
➡️ **BluRay KEEP**

━━━━━━━━━━━━━━━━━━━━

🛡️ **OTHER SAFETY RULES**

1️⃣ Same canonical movie title required.
2️⃣ Sirf 2 common words enough NAHI hain.
3️⃣ Language compatible hona zaroori hai.
4️⃣ Unknown language automatically delete nahi hogi.
5️⃣ Unknown quality automatically delete nahi hogi.
6️⃣ LOW/MEDIUM ko better source replace kar sakta hai.
7️⃣ Same-source resolution upgrade se old file delete nahi hogi.
8️⃣ HIGH quality files NEVER delete hongi.
9️⃣ Delete se pehle final HIGH-quality safety check bhi hota hai.

━━━━━━━━━━━━━━━━━━━━

🤖 Cleanup background me chalega.
Search / file sending / normal bot functions continue rahenge.
"""

    await message.reply_text(
        help_text
    )