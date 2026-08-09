import re
import logging
import asyncio
from datetime import datetime
from collections import defaultdict
from plugins.Dreamxfutures.Imdbposter import get_movie_detailsx, fetch_image, get_movie_details
from database.users_chats_db import db
from plugins.quality_manager import extract_quality_info, is_high_quality, run_quality_cleanup_background

from pyrogram import Client, filters, enums
from info import CHANNELS, MOVIE_UPDATE_CHANNEL, LINK_PREVIEW, ABOVE_PREVIEW, BAD_WORDS, ADMINS, LANDSCAPE_POSTER, TMDB_POSTER, MULTIPLE_DB
from Script import script
from database.ia_filterdb import save_file, Media, Media2, MEDIA_DBS
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from utils import temp
from pymongo.errors import PyMongoError, DuplicateKeyError
from pyrogram.errors import MessageIdInvalid, MessageNotModified, FloodWait
from typing import Optional, Tuple

logger = logging.getLogger(__name__)

# Precomputed sets for faster lookups
IGNORE_WORDS = {
    "rarbg", "dub", "sub", "sample", "mkv", "aac", "combined",
    "action", "adventure", "animation", "biography", "comedy", "crime", 
    "documentary", "drama", "fantasy", "film-noir", "history", 
    "horror", "music", "musical", "mystery", "romance", "sci-fi", "sport", 
    "thriller", "war", "western", "hdcam", "hdtc", "camrip", "ts", "tc", 
    "telesync", "dvdscr", "dvdrip", "predvd", "webrip", "web-dl", "tvrip", 
    "hdtv", "web dl", "webdl", "bluray", "brrip", "bdrip", "360p", "480p", 
    "720p", "1080p", "2160p", "4k", "1440p", "540p", "240p", "140p", "hevc", 
    "hdrip", "hin", "hindi", "tam", "tamil", "kan", "kannada", "tel", "telugu", "hd",
    "mal", "malayalam", "eng", "english", "pun", "punjabi", "ben", "bengali", 
    "mar", "marathi", "guj", "gujarati", "urd", "urdu", "kor", "korean", "jpn", 
    "japanese", "nf", "netflix", "sonyliv", "sony", "sliv", "amzn", "prime", 
    "primevideo", "hotstar", "zee5", "jio", "jhs", "aha", "hbo", "paramount", 
    "apple", "hoichoi", "sunnxt", "viki"
}|BAD_WORDS

# Constants
CAPTION_LANGUAGES = {
    r"\bhin\b": "Hindi", r"\bhindi\b": "Hindi",
    r"\btam\b": "Tamil", r"\btamil\b": "Tamil",
    r"\bkan\b": "Kannada", r"\bkannada\b": "Kannada",
    r"\btel\b": "Telugu", r"\btelugu\b": "Telugu",
    r"\bmal\b": "Malayalam", r"\bmalayalam\b": "Malayalam",
    r"\beng\b": "English", r"\benglish\b": "English",
    r"\bpun\b": "Punjabi", r"\bpunjabi\b": "Punjabi",
    r"\bben\b": "Bengali", r"\bbengali\b": "Bengali",
    r"\bmar\b": "Marathi", r"\bmarathi\b": "Marathi",
    r"\bguj\b": "Gujarati", r"\bgujarati\b": "Gujarati",
    r"\burd\b": "Urdu", r"\burdu\b": "Urdu",
    r"\bkor\b": "Korean", r"\bkorean\b": "Korean",
    r"\bjpn\b": "Japanese", r"\bjapanese\b": "Japanese",
}

OTT_PLATFORMS = {
    "nf": "Netflix", "netflix": "Netflix",
    "sonyliv": "SonyLiv", "sony": "SonyLiv", "sliv": "SonyLiv",
    "amzn": "Amazon Prime Video", "prime": "Amazon Prime Video", "primevideo": "Amazon Prime Video",
    "hotstar": "Disney+ Hotstar", "zee5": "Zee5",
    "jio": "JioHotstar", "jhs": "JioHotstar",
    "aha": "Aha", "hbo": "HBO Max", "paramount": "Paramount+",
    "apple": "Apple TV+", "hoichoi": "Hoichoi", "sunnxt": "Sun NXT", "viki": "Viki"
}

STANDARD_GENRES = {
    'Action', 'Adventure', 'Animation', 'Biography', 'Comedy', 'Crime', 'Documentary',
    'Drama', 'Family', 'Fantasy', 'Film-Noir', 'History', 'Horror', 'Music',
    'Musical', 'Mystery', 'Romance', 'Sci-Fi', 'Sport', 'Thriller', 'War', 'Western'
}

# Precompiled regex patterns
CLEAN_PATTERN = re.compile(r'@[^ \n\r\t\.,:;!?()\[\]{}<>\\/"\'=_%]+|\bwww\.[^\s\]\)]+|\([\@^]+\)|\[[\@^]+-\]')
NORMALIZE_PATTERN = re.compile(r"[._]+|[()\[\]{}:;'–!,.?_-]")
QUALITY_PATTERN = re.compile(
    r"\b(?:HDCam|HDTC|CamRip|TS|TC|TeleSync|DVDScr|DVDRip|PreDVD|"
    r"WEBRip|WEB-DL|TVRip|HDTV|WEB DL|WebDl|BluRay|BRRip|BDRip|"
    r"360p|480p|720p|1080p|2160p|4K|1440p|540p|240p|140p|HEVC|HDRip)\b", 
    re.IGNORECASE
)
YEAR_PATTERN = re.compile(r"(?<![A-Za-z0-9])(?:19|20)\d{2}(?![A-Za-z0-9])")

# 🎯 NEW: Improved Regex for Underscores & Ranges
RANGE_REGEX = re.compile(r'\bS(\d{1,2})[\s_.-]*E(?:p(?:isode)?)?[\s_.-]*0*(\d{1,3})[\s_.-]*(?:to|-|_)[\s_.-]*(?:E(?:p(?:isode)?)?)?[\s_.-]*0*(\d{1,3})', re.IGNORECASE)
SINGLE_REGEX = re.compile(r'\bS(\d{1,2})[\s_.-]*E(?:p(?:isode)?)?[\s_.-]*0*(\d{1,3})', re.IGNORECASE)
NAMED_REGEX = re.compile(r'Season[\s_.-]*0*(\d{1,2})[\s_.-]*Ep(?:isode)?[\s_.-]*0*(\d{1,3})', re.IGNORECASE)
EP_ONLY_RANGE = re.compile(r'\b(?:EP|Episode)[\s_.-]*0*(\d{1,3})[\s_.-]*(?:to|-|_)[\s_.-]*(?:EP|Episode)?[\s_.-]*0*(\d{1,3})\b', re.IGNORECASE)


MEDIA_FILTER = filters.document | filters.video | filters.audio
locks = defaultdict(asyncio.Lock)
pending_updates = {}
error_tmdb = False

def clean_mentions_links(text: str) -> str:
    return CLEAN_PATTERN.sub("", text or "").strip()

def normalize(s: str) -> str:
    s = NORMALIZE_PATTERN.sub(" ", s)
    return re.sub(r"\s+", " ", s).strip()

def remove_ignored_words(text: str) -> str:
    IGNORE_WORDS_LOWER = {w.lower() for w in IGNORE_WORDS}
    return " ".join(word for word in text.split() if word.lower() not in IGNORE_WORDS_LOWER)

def get_qualities(text: str) -> str:
    qualities = QUALITY_PATTERN.findall(text)
    return ", ".join(qualities) if qualities else "N/A"

def extract_ott_platform(text: str) -> str:
    text = text.lower()
    platforms = {plat for key, plat in OTT_PLATFORMS.items() if key in text}
    return " | ".join(platforms) if platforms else "N/A"

def extract_season_episode(filename: str) -> Tuple[Optional[int], Optional[str]]:
    """🎯 IMPROVED: Better episode extraction"""
    if m := EP_ONLY_RANGE.search(filename):
        return 1, f"{int(m.group(1))}-{int(m.group(2))}"
    for pattern in (RANGE_REGEX, SINGLE_REGEX, NAMED_REGEX):
        if m := pattern.search(filename):
            season = int(m.group(1))
            if pattern == RANGE_REGEX:
                ep = f"{int(m.group(2)):02d}-{int(m.group(3)):02d}"
            else:
                ep = f"{int(m.group(2)):02d}"
            return season, ep
    return None, None

def extract_language(filename: str, caption: str = "") -> str:
    """🎯 IMPROVED: Better language detection"""
    combined_text = f"{filename} {caption}".lower()

    languages = set()
    for pattern, lang_name in CAPTION_LANGUAGES.items():
        if re.search(pattern, combined_text):
            languages.add(lang_name)

    if languages:
        return ", ".join(sorted(languages))
    return "N/A"

def schedule_update(bot, base_name, delay=5):
    if handle := pending_updates.get(base_name):
        if not handle.cancelled():
            handle.cancel()

    loop = asyncio.get_event_loop()
    pending_updates[base_name] = loop.call_later(
        delay,
        lambda: asyncio.create_task(update_movie_message(bot, base_name))
    )

def extract_media_info(filename: str, caption: str):
    """🎯 IMPROVED: Cleaner base_name extraction safely supporting ranges & underscores"""
    # 1. Mentions/Links clean karo, par Hyphens aur Underscores ko Regex ke liye bacha kar rakho
    clean_raw = clean_mentions_links(filename).title()
    caption_clean = clean_mentions_links(caption).lower() if caption else ""

    # 2. Episodes ko extract karo PEHLE, normalize hone se pehle!
    season, episode = extract_season_episode(clean_raw)

    # 3. Ab normalize karo baaki strings/quality nikalne ke liye
    filename_norm = normalize(clean_raw)
    unified = f"{caption_clean} {filename_norm.lower()}".strip()

    year = None
    tag = "#MOVIE"
    processed_raw = clean_raw
    base_raw = clean_raw
    quality = get_qualities(caption_clean) or get_qualities(filename_norm.lower()) or "N/A"
    ott_platform = extract_ott_platform(f"{filename_norm} {caption_clean}")
    language = extract_language(filename_norm, caption_clean)

    if season is not None:
        tag = "#SERIES"
        # Cut-off point raw string me dhundho (jisme abhi underscores/hyphens safe hain)
        if m := (RANGE_REGEX.search(clean_raw) or SINGLE_REGEX.search(clean_raw) or 
                 NAMED_REGEX.search(clean_raw) or EP_ONLY_RANGE.search(clean_raw)):
            match_str = m.group(0)
            end_idx = clean_raw.lower().find(match_str.lower()) + len(match_str)

            base_raw = clean_raw[:clean_raw.lower().find(match_str.lower())].strip()
            processed_raw = clean_raw[:end_idx]

            # Try to find year after season/episode
            if year_match := YEAR_PATTERN.search(clean_raw[end_idx:]):
                year = year_match.group(0)
                year_idx = clean_raw.lower().find(year, end_idx)
                if year_idx != -1:
                    processed_raw = clean_raw[:year_idx + 4]
    else:
        # Movie mode: find year or quality cutoff
        if year_match := YEAR_PATTERN.search(unified):
            year = year_match.group(0)
            year_idx = filename_norm.lower().find(year.lower())
            if year_idx != -1:
                processed_raw = filename_norm[:year_idx + 4]
                base_raw = processed_raw
        else:
            if qual_match := QUALITY_PATTERN.search(unified):
                qual_str = qual_match.group(0)
                qual_idx = filename_norm.lower().find(qual_str.lower())
                if qual_idx != -1:
                    processed_raw = filename_norm[:qual_idx].strip()
                    base_raw = processed_raw

    # Clean base_name (Yahan normalize karo taaki extra symbols space ban jayein)
    base_name = normalize(remove_ignored_words(normalize(base_raw)))

    # Remove leading symbols like -, _, :, etc.
    base_name = re.sub(r"^[\s\-–—_:]+", "", base_name)

    # Remove wrapping brackets
    base_name = re.sub(r"^[【\[\(\{<]+", "", base_name)
    base_name = re.sub(r"[】\]\)\}>]+$", "", base_name)

    # Add year if not present
    if year and year not in base_name:
        base_name += f" {year}"

    # Remove trailing "(2026)"
    base_name = re.sub(r"\s*\(\d{4}\)\s*$", "", base_name).strip()

    # Final cleanup
    base_name = re.sub(r"\s+", " ", base_name).strip()

    if not base_name or base_name.lower() == "n/a":
        base_name = normalize(remove_ignored_words(filename_norm)).strip() or filename_norm

    return {
        "processed": normalize(processed_raw),
        "base_name": base_name,
        "tag": tag,
        "season": season,
        "episode": episode,
        "year": year,
        "quality": quality,
        "ott_platform": ott_platform,
        "language": language
    }



@Client.on_message(filters.chat(CHANNELS) & MEDIA_FILTER)
async def media_handler(bot, message):
    media = next(
        (getattr(message, ft) for ft in ("document", "video", "audio")
         if getattr(message, ft, None)),
        None
    )
    if not media:
        return

    media.file_type = next(ft for ft in ("document", "video", "audio") if hasattr(message, ft))
    media.caption = message.caption or ""

    # Extract language info BEFORE saving
    extracted_info = extract_media_info(media.file_name, media.caption or "")

    # Pass extracted_info to save_file
    # ✅ FIX: save_file ab teesra value bhi deta hai — real/final filename jo
    # actually DB me save hua (renamed, cleaned). Aage jahan bhi filename ka use
    # hoga (quality check, cleanup, movie-update post), wahi asli naam use hoga —
    # raw telegram filename (media.file_name) nahi.
    success, info, real_file_name = await save_file(media, bot=bot, extracted_info=extracted_info)
    if not success:
        return

    # Fallback safety: agar kisi reason se real_file_name na mile, raw naam use karo
    real_file_name = real_file_name or media.file_name

    # Note: Save confirmation ab sirf database/ia_filterdb.py ke [SAVED] log se aata hai
    # (wahi real/final filename hota hai jo DB me store hua). Yahan duplicate log nahi lagate.

    # === Quality Management Start ===
    try:
        quality_info = extract_quality_info(real_file_name, media.caption)

        # Detailed breakdown sirf DEBUG level pe (default me suppressed) — console/file
        # clean rehta hai, chahiye ho to logger level DEBUG karke dekha ja sakta hai.
        logger.debug(
            f"[QUALITY] {real_file_name[:70]} | "
            f"source={quality_info.get('source')} | "
            f"resolution={quality_info.get('resolution')} | "
            f"score={quality_info.get('quality_score', 0):.1f} | "
            f"lang={extracted_info.get('language', 'N/A')}"
        )

        if is_high_quality(quality_info):
            # ✅ FIX: Ab ye await nahi hota — background task ki tarah fire hota hai,
            # taaki heavy DB regex scan agli files ke save hone ko block na kare.
            # QUALITY_CLEANUP_SEMAPHORE (max 2 parallel) DB/CPU overload se bachata hai.
            asyncio.create_task(
                run_quality_cleanup_background(MEDIA_DBS, real_file_name, media.caption)
            )

    except Exception as e:
        logger.error(f"[QUALITY] Error in quality management: {e}", exc_info=True)

    # === Update Processing Start ===
    try:
        if await db.movie_update_status(bot.me.id):
            await process_and_send_update(bot, real_file_name, media.caption)
    except Exception:
        logger.exception("Error processing media")


async def process_and_send_update(bot, filename, caption):
    try:
        media_info = extract_media_info(filename, caption)
        base_name = media_info["base_name"]
        processed = media_info["processed"]

        lock = locks[base_name]
        async with lock:
            await _process_with_lock(bot, filename, caption, media_info, base_name, processed)
    except PyMongoError as e:
        logger.error(f"Database error in process_and_send_update: {e}")
    except Exception as e:
        logger.exception(f"Processing failed in process_and_send_update: {e}")

async def _process_with_lock(bot, filename, caption, media_info, base_name, processed):
    if not hasattr(db, 'movie_updates'):
        db.movie_updates = db.db.movie_updates

    movie_doc = await db.movie_updates.find_one({"_id": base_name})
    error_tmdb = False

    file_data = {
        "filename": filename,
        "processed": processed,
        "quality": media_info["quality"],
        "language": media_info["language"],
        "ott_platform": media_info["ott_platform"],
        "timestamp": datetime.now(),
        "tag": media_info["tag"],
        "season": media_info["season"],
        "episode": media_info["episode"]
    }

    if not movie_doc:
        # Fetch movie details
        if TMDB_POSTER:
            details = await get_movie_detailsx(base_name)
            if not details or details.get("error") or (not details.get("poster_url") and not details.get("backdrop_url")):
                error_tmdb = True
                logger.info("TMDB error switching to IMDB")
                details = await get_movie_details(base_name) or {}
        else:
            details = await get_movie_details(base_name) or {}

        raw_genres = details.get("genres", "N/A")
        if isinstance(raw_genres, str):
            genre_list = [g.strip() for g in raw_genres.split(",")]
            genres = ", ".join(g for g in genre_list if g in STANDARD_GENRES) or "N/A"
        else:
            genres = ", ".join(g for g in raw_genres if g in STANDARD_GENRES) or "N/A"

        # 🎯 IMPROVED: Better poster selection logic
        if TMDB_POSTER and not error_tmdb and LANDSCAPE_POSTER and details.get("backdrop_url"):
            selected_poster = details.get("backdrop_url")
            is_backdrop = True
        else:
            selected_poster = details.get("poster_url") or ""
            is_backdrop = False

        movie_doc = {
            "_id": base_name,
            "files": [file_data],
            "poster_url": selected_poster,
            "genres": genres,
            "rating": details.get("rating", "N/A"),
            "imdb_url": details.get("tmdb_url") if (TMDB_POSTER and not error_tmdb) else details.get("url", ""),
            "year": media_info["year"] or details.get("year"),
            "tag": media_info["tag"],
            "ott_platform": media_info["ott_platform"],
            "message_id": None,
            "is_photo": False,
            "error_tmdb": error_tmdb,
            "is_backdrop": is_backdrop
        }
        try:
            await db.movie_updates.insert_one(movie_doc)
            await send_movie_update(bot, base_name)
            movie_doc = await db.movie_updates.find_one({"_id": base_name})
        except DuplicateKeyError:
            movie_doc = await db.movie_updates.find_one({"_id": base_name})
            if movie_doc:
                if any(f["filename"] == filename for f in movie_doc["files"]):
                    return
                await db.movie_updates.update_one(
                    {"_id": base_name},
                    {"$push": {"files": file_data}}
                )
                movie_doc["files"].append(file_data)
                schedule_update(bot, base_name)
    else:
        if any(f["filename"] == filename for f in movie_doc["files"]):
            return
        await db.movie_updates.update_one(
            {"_id": base_name},
            {"$push": {"files": file_data}}
        )
        movie_doc["files"].append(file_data)
        schedule_update(bot, base_name)

async def send_movie_update(bot, base_name):
    max_retries = 3
    base_delay = 5
    for attempt in range(max_retries):
        try:
            movie_doc = await db.movie_updates.find_one({"_id": base_name})
            if not movie_doc:
                return None

            text = generate_movie_message(movie_doc, base_name)

            buttons = InlineKeyboardMarkup([
                [
                    InlineKeyboardButton(
                        '🗃️ ✦ 𝗚𝗘𝗧 𝗙𝗜𝗟𝗘 ✦ 🗃️',
                        url=f"https://t.me/{temp.U_NAME}?start=getfile-{base_name.replace(' ', '-')}"
                    )
                ],
                [
                    InlineKeyboardButton('♻️ Hᴏᴡ Tᴏ Dᴏᴡɴʟᴏᴀᴅ ♻️', url="https://t.me/newmovies_support/1236?single")
                ]
            ])

            if movie_doc.get("poster_url") and not LINK_PREVIEW:
                resized_poster = await fetch_image(movie_doc["poster_url"], (860,1200))
                msg = await bot.send_photo(
                    chat_id=MOVIE_UPDATE_CHANNEL,
                    photo=resized_poster,
                    caption=text,
                    reply_markup=buttons,
                    parse_mode=enums.ParseMode.HTML,
                    has_spoiler=True
                )
                is_photo = True
            else:
                send_params = {
                    "chat_id": MOVIE_UPDATE_CHANNEL,
                    "text": text,
                    "reply_markup": buttons,
                    "parse_mode": enums.ParseMode.HTML
                }
                if movie_doc.get("poster_url") and LINK_PREVIEW:
                    send_params["invert_media"] = ABOVE_PREVIEW
                msg = await bot.send_message(**send_params)
                is_photo = False

            await db.movie_updates.update_one(
                {"_id": base_name},
                {"$set": {"message_id": msg.id, "is_photo": is_photo}}
            )
            return msg
        except FloodWait as e:
            wait_time = e.value + 2
            await asyncio.sleep(wait_time)
        except Exception as e:
            logger.error(f"Failed to send movie update: {e}")
            break
    return None

async def update_movie_message(bot, base_name):
    try:
        movie_doc = await db.movie_updates.find_one({"_id": base_name})
        if not movie_doc:
            return

        text = generate_movie_message(movie_doc, base_name)

        buttons = InlineKeyboardMarkup([
            [
                InlineKeyboardButton(
                    '🗃️ ✦ 𝗚𝗘𝗧 𝗙𝗜𝗟𝗘 ✦ 🗃️',
                    url=f"https://t.me/{temp.U_NAME}?start=getfile-{base_name.replace(' ', '-')}"
                )
            ],
            [
                InlineKeyboardButton('♻️ Hᴏᴡ Tᴏ Dᴏᴡɴʟᴏᴀᴅ ♻️', url="https://t.me/newmovies_support/1236?single")
            ]
        ])

        message_id = movie_doc.get("message_id")
        is_photo = movie_doc.get("is_photo", False)

        if not message_id:
            await send_movie_update(bot, base_name)
            return

        try:
            if is_photo:
                await bot.edit_message_caption(
                    chat_id=MOVIE_UPDATE_CHANNEL,
                    message_id=message_id,
                    caption=text,
                    reply_markup=buttons,
                    parse_mode=enums.ParseMode.HTML,
                    has_spoiler=True
                )
            else:
                await bot.edit_message_text(
                    chat_id=MOVIE_UPDATE_CHANNEL,
                    message_id=message_id,
                    text=text,
                    reply_markup=buttons,
                    parse_mode=enums.ParseMode.HTML,
                    invert_media=ABOVE_PREVIEW,
                    disable_web_page_preview=not LINK_PREVIEW
                )
            return
        except (MessageIdInvalid, MessageNotModified) as e:
            logger.warning(f"Message update skipped due to error: {e}")
            pass
        except Exception:
            try:
                await bot.delete_messages(
                    chat_id=MOVIE_UPDATE_CHANNEL,
                    message_ids=message_id
                )
                await db.movie_updates.update_one(
                    {"_id": base_name},
                    {"$set": {"message_id": None, "is_photo": False}}
                )
            except Exception as e:
                logger.error(f"Error during message deletion/update in recovery: {e}")
                pass
            await send_movie_update(bot, base_name)
    except Exception as e:
        logger.error(f"Failed to update movie message for {base_name}: {e}")

def get_styled_text(text: str, style_type="bold_serif") -> str:
    """
    Title: 𝐏𝐮𝐬𝐡𝐩𝐚 𝟐 (bold_serif)
    Details: ᴀᴄᴛɪᴏɴ, ʜɪɴᴅɪ, 𝟽𝟸𝟶ᴘ (small_caps)
    """
    if not text or text == "N/A":
        return "N/A"

    bold_serif = {
        'A': '𝐀', 'B': '𝐁', 'C': '𝐂', 'D': '𝐃', 'E': '𝐄', 'F': '𝐅', 'G': '𝐆', 'H': '𝐇',
        'I': '𝐈', 'J': '𝐉', 'K': '𝐊', 'L': '𝐋', 'M': '𝐌', 'N': '𝐍', 'O': '𝐎', 'P': '𝐏',
        'Q': '', 'R': '𝐑', 'S': '𝐒', 'T': '𝐓', 'U': '𝐔', 'V': '𝐕', 'W': '𝐖', 'X': '𝐗',
        'Y': '𝐘', 'Z': '𝙕',
        'a': '𝐚', 'b': '𝐛', 'c': '𝐜', 'd': '𝐝', 'e': '𝐞', 'f': '𝐟', 'g': '𝐠', 'h': '𝐡',
        'i': '𝐢', 'j': '𝐣', 'k': '𝐤', 'l': '𝐥', 'm': '𝐦', 'n': '𝐧', 'o': '𝐨', 'p': '𝐩',
        'q': '𝐪', 'r': '𝐫', 's': '𝐬', 't': '𝐭', 'u': '𝐮', 'v': '𝐯', 'w': '𝐰', 'x': '𝐱',
        'y': '𝐲', 'z': '𝐳',
        '0': '𝟎', '1': '𝟏', '2': '𝟐', '3': '𝟑', '4': '𝟒', '5': '𝟓', '6': '𝟔', '7': '𝟕', '8': '𝟖', '9': '𝟗'
    }

    small_caps = {
        'A': 'ᴀ', 'B': 'ʙ', 'C': 'ᴄ', 'D': 'ᴅ', 'E': 'ᴇ', 'F': 'ꜰ', 'G': 'ɢ', 'H': 'ʜ',
        'I': 'ɪ', 'J': 'ᴊ', 'K': 'ᴋ', 'L': 'ʟ', 'M': 'ᴍ', 'N': 'ɴ', 'O': 'ᴏ', 'P': 'ᴘ',
        'Q': 'ǫ', 'R': 'ʀ', 'S': 's', 'T': 'ᴛ', 'U': 'ᴜ', 'V': 'ᴠ', 'W': 'ᴡ', 'X': 'x',
        'Y': 'ʏ', 'Z': 'ᴢ',
        '0': '𝟶', '1': '𝟷', '2': '𝟸', '3': '𝟹', '4': '𝟺', '5': '𝟻', '6': '𝟼', '7': '𝟽', '8': '𝟾', '9': '𝟿'
    }

    target_map = bold_serif if style_type == "bold_serif" else small_caps
    styled = ""
    for char in text:
        if style_type == "bold_serif":
            styled += target_map.get(char, char)
        else:
            styled += target_map.get(char.upper(), char)
    return styled

def generate_movie_message(movie_doc, base_name):
    """🎯 IMPROVED: Better episode and language display"""
    all_qualities = set()
    all_languages = set()
    all_ott_platforms = set()
    all_tags = set()
    episodes_by_season = defaultdict(set)

    for file in movie_doc["files"]:
        if file["quality"] != "N/A":
            all_qualities.update(q.strip() for q in file["quality"].split(",") if q.strip())
        if file["language"] != "N/A":
            all_languages.update(l.strip() for l in file["language"].split(",") if l.strip())
        if file["ott_platform"] != "N/A":
            platforms = [p.strip() for p in file["ott_platform"].split("|") if p.strip()]
            all_ott_platforms.update(platforms)
        if file["tag"]:
            all_tags.add(file["tag"])
        if file.get("season") and file.get("episode"):
            season = file["season"]
            episode = file["episode"]
            episodes_by_season[season].add(episode)

    primary_tag = "#SERIES" if "#SERIES" in all_tags else "#MOVIE"

    # ===== SEASON & EPISODE DISPLAY (WITH BETTER FORMATTING) =====
    epi_block = ""
    if episodes_by_season:
        episode_lines = []
        for season in sorted(episodes_by_season.keys(), key=lambda x: int(x)):
            episodes = sorted(list(episodes_by_season[season]), 
                            key=lambda x: int(x.split('-')[0]) if '-' in x else int(x))
            ep_list = ", ".join(episodes)

            line = (
                f"<b>┇ 💠 Season {int(season):02d}</b>\n"
                f"<b>┇ </b>ᴇᴘɪsᴏᴅᴇs: <code>{ep_list}</code>"
            )
            episode_lines.append(line)

        if episode_lines:
            epi_str = "\n".join(episode_lines)
            epi_block = f"\n<b>━━━━━━━━━━━━━━━━━</b>\n{epi_str}\n<b>━━━━━━━━━━━━━━━━━</b>"

    # ===== STYLING =====
    styled_title = get_styled_text(base_name, style_type="bold_serif")
    raw_genres = movie_doc.get("genres", "N/A")
    styled_genres = get_styled_text(raw_genres, style_type="small_caps")

    raw_languages = ", ".join(sorted(all_languages)) if all_languages else "N/A"
    styled_languages = get_styled_text(raw_languages, style_type="small_caps")

    raw_quality = ", ".join(sorted(all_qualities)) if all_qualities else "N/A"
    styled_quality = get_styled_text(raw_quality, style_type="small_caps")

    raw_ott = ", ".join(sorted(all_ott_platforms)) if all_ott_platforms else "N/A"
    styled_ott = get_styled_text(raw_ott, style_type="small_caps")

    # Final formatting
    return script.MOVIE_UPDATE_NOTIFY_TXT.format(
        poster_url=movie_doc.get("poster_url", ""),
        imdb_url=movie_doc.get("imdb_url", ""),
        filename=styled_title,
        tag=primary_tag,
        genres=styled_genres,
        ott=styled_ott,
        quality=styled_quality,
        language=styled_languages,
        episodes=epi_block,  # Season/Episode section
        rating=movie_doc.get("rating", "N/A"),
        search_link=temp.B_LINK
    )


# Channel button handler
CHANNEL_ID = -1002413838031

@Client.on_message(filters.channel & filters.media)
async def add_button(client, message):
    if message.chat.id == CHANNEL_ID:
        button = InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton(
                        "🔰 ᴍᴏᴠɪᴇ ꜱᴇᴀʀᴄʜ ɢʀᴏᴜᴘ 🔰",
                        url="https://t.me/newmovieswebseries_group"
                    )
                ],
                [
                    InlineKeyboardButton(
                        "📢 ɴᴇᴡ ᴍᴏᴠɪᴇ ɴᴏᴛɪꜰɪᴄᴀᴛɪᴏɴ ᴄʜᴀɴɴᴇʟ 📢",
                        url="https://t.me/new_movie_update_2026"
                    )
                ]
            ]
        )

        try:
            await message.edit_reply_markup(reply_markup=button)
            await asyncio.sleep(0.5)
        except Exception as e:
            print(f"Failed to add button: {e}")