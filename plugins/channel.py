import re
import logging
import asyncio
from datetime import datetime
from collections import defaultdict
from plugins.Dreamxfutures.Imdbposter import get_movie_detailsx, fetch_image, get_movie_details
from database.users_chats_db import db
from plugins.quality_manager import extract_quality_info, is_high_quality, find_and_delete_lower_quality


from pyrogram import Client, filters, enums
from info import CHANNELS, MOVIE_UPDATE_CHANNEL, LINK_PREVIEW, ABOVE_PREVIEW, BAD_WORDS, LANDSCAPE_POSTER, TMDB_POSTER
from Script import script
from database.ia_filterdb import save_file
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
    "hdrip", "hin", "hindi", "tam", "tamil", "kan", "kannada", "tel", "telugu", "HD"
    "mal", "malayalam", "eng", "english", "pun", "punjabi", "ben", "bengali", 
    "mar", "marathi", "guj", "gujarati", "urd", "urdu", "kor", "korean", "jpn", 
    "japanese", "nf", "netflix", "sonyliv", "sony", "sliv", "amzn", "prime", 
    "primevideo", "hotstar", "zee5", "jio", "jhs", "aha", "hbo", "paramount", 
    "apple", "hoichoi", "sunnxt", "viki"
}|BAD_WORDS

# Constants
CAPTION_LANGUAGES = {
    "hin": "Hindi", "hindi": "Hindi",
    "tam": "Tamil", "tamil": "Tamil",
    "kan": "Kannada", "kannada": "Kannada",
    "tel": "Telugu", "telugu": "Telugu",
    "mal": "Malayalam", "malayalam": "Malayalam",
    "eng": "English", "english": "English",
    "pun": "Punjabi", "punjabi": "Punjabi",
    "ben": "Bengali", "bengali": "Bengali",
    "mar": "Marathi", "marathi": "Marathi",
    "guj": "Gujarati", "gujarati": "Gujarati",
    "urd": "Urdu", "urdu": "Urdu",
    "kor": "Korean", "korean": "Korean",
    "jpn": "Japanese", "japanese": "Japanese",
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
CLEAN_PATTERN = re.compile(r'@[^ \n\r\t\.,:;!?()\[\]{}<>\\/"\'=_%]+|\bwww\.[^\s\]\)]+|\([\@^]+\)|\[[\@^]+\]')
NORMALIZE_PATTERN = re.compile(r"[._]+|[()\[\]{}:;'–!,.?_]")
QUALITY_PATTERN = re.compile(
    r"\b(?:HDCam|HDTC|CamRip|TS|TC|TeleSync|DVDScr|DVDRip|PreDVD|"
    r"WEBRip|WEB-DL|TVRip|HDTV|WEB DL|WebDl|BluRay|BRRip|BDRip|"
    r"360p|480p|720p|1080p|2160p|4K|1440p|540p|240p|140p|HEVC|HDRip)\b", 
    re.IGNORECASE
)
YEAR_PATTERN = re.compile(r"(?<![A-Za-z0-9])(?:19|20)\d{2}(?![A-Za-z0-9])")
RANGE_REGEX = re.compile(r'\bS(\d{1,2})[^\w\n\r]*E(?:p(?:isode)?)?0*(\d{1,2})\s*(?:to|-)\s*(?:E(?:p(?:isode)?)?)?0*(\d{1,2})',re.IGNORECASE)
SINGLE_REGEX = re.compile(r'\bS(\d{1,2})[^\w\n\r]*E(?:p(?:isode)?)?0*(\d{1,3})', re.IGNORECASE)
NAMED_REGEX = re.compile(r'Season\s*0*(\d{1,2})[\s\-,:]*Ep(?:isode)?\s*0*(\d{1,3})', re.IGNORECASE)
EP_ONLY_RANGE = re.compile(r'\b(?:EP|Episode)0*(\d{1,3})\s*-\s*0*(\d{1,3})\b',re.IGNORECASE)


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
    if m := EP_ONLY_RANGE.search(filename):
        return 1, f"{int(m.group(1))}-{int(m.group(2))}"
    for pattern in (RANGE_REGEX, SINGLE_REGEX, NAMED_REGEX):
        if m := pattern.search(filename):
            season = int(m.group(1))
            if pattern == RANGE_REGEX:
                ep = f"{m.group(2)}-{m.group(3)}"
            else:
                ep = m.group(2)
            return season, ep
    return None, None

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
    filename = normalize(clean_mentions_links(filename).title())
    caption_clean = clean_mentions_links(caption).lower() if caption else ""
    unified = f"{caption_clean} {filename.lower()}".strip()

    season = episode = year = None
    tag = "#MOVIE"
    processed_raw = base_raw = filename
    quality = get_qualities(caption_clean) or get_qualities(filename.lower()) or "N/A"
    ott_platform = extract_ott_platform(f"{filename} {caption_clean}")

    lang_keys = {k for k in CAPTION_LANGUAGES if k in caption_clean or k in filename.lower()}
    language = ", ".join(sorted({CAPTION_LANGUAGES[k] for k in lang_keys})) if lang_keys else "N/A"

    season, episode = extract_season_episode(filename)
    if season is not None:
        tag = "#SERIES"
        if m := (RANGE_REGEX.search(filename) or SINGLE_REGEX.search(filename) or NAMED_REGEX.search(filename) or EP_ONLY_RANGE.search(filename)):
            match_str = m.group(0)
            start_idx = filename.lower().find(match_str.lower())
            end_idx = start_idx + len(match_str)
            processed_raw = filename[:end_idx]
            base_raw = filename[:start_idx]
            if year_match := YEAR_PATTERN.search(filename.lower()[end_idx:]):
                y = year_match.group(0)
                yi = filename.lower().find(y, end_idx)
                if yi != -1:
                    processed_raw = filename[:yi+4]
                    base_raw += f" {y}"
    else:
        if year_match := YEAR_PATTERN.search(unified):
            year = year_match.group(0)
            year_idx = filename.lower().find(year.lower())
            if year_idx != -1:
                processed_raw = filename[:year_idx + 4]
                base_raw = processed_raw
        else:
            if qual_match := QUALITY_PATTERN.search(unified):
                qual_str = qual_match.group(0)
                qual_idx = filename.lower().find(qual_str.lower())
                if qual_idx != -1:
                    processed_raw = filename[:qual_idx]
                    base_raw = processed_raw

    base_name = normalize(remove_ignored_words(normalize(base_raw)))
    if year and year not in base_name:
        base_name += f" {year}"

    if base_name.endswith(")"):
        base_name = re.sub(r"\s+\(\d{4}\)$", "", base_name)
        if year:
            base_name += f" {year}"

    # -------------------------
    # NEW: strip season/episode tokens from final base_name
    # -------------------------
    def _strip_season_episode_tokens(name: str) -> str:
        """
        Remove common season/episode markers from a title while preserving a trailing year.
        Examples removed: S01, s01e02, 1x02, season 1, ep 02, episode 2, part 1
        """
        if not name:
            return name

        # Preserve trailing year (e.g. "Title (2020)" or "Title 2020")
        year_match = re.search(r'\(?\b(19|20)\d{2}\b\)?\s*$', name)
        year_part = ""
        if year_match:
            year_part = year_match.group(0)
            name = name[:year_match.start()].strip()

        # Common patterns to remove
        patterns = [
            r'\bS\d{1,2}E\d{1,2}\b',     # S01E02
            r'\bS\d{1,2}\b',             # S01
            r'\bE\d{1,2}\b',             # E02
            r'\b\d{1,2}x\d{1,2}\b',      # 1x02
            r'\bSeason\s*\d{1,2}\b',     # Season 1
            r'\bEp(?:isode)?\.?\s*\d{1,3}\b',  # Ep02, Episode 2
            r'\bEpisode\s*\d{1,3}\b',
            r'\bPart\s*\d{1,2}\b'
        ]

        for p in patterns:
            name = re.sub(p, ' ', name, flags=re.IGNORECASE)

        # Remove leftover separators and extra whitespace
        name = re.sub(r'[_\.\-]+', ' ', name)     # underscores/dots/hyphens
        name = re.sub(r'\s+', ' ', name).strip()

        # Reattach year in canonical form if we removed it earlier
        if year_part:
            y = re.search(r'(19|20)\d{2}', year_part)
            if y:
                name = f"{name} {y.group(0)}"

        return name.strip()

    base_name = _strip_season_episode_tokens(base_name)
    # If stripping accidentally removed everything, fall back to a safer value
    if not base_name:
        base_name = normalize(remove_ignored_words(normalize(processed_raw))) or filename

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
    success, info = await save_file(media, bot=bot)
    if not success:
        return

    try:
        # === Quality Management Start ===
        quality_info = extract_quality_info(media.file_name, media.caption)
        if is_high_quality(quality_info):
            from database.config_db import mdb
            success, cleanup_msg = await find_and_delete_lower_quality(
                db_collection=mdb.db.media,
                new_filename=media.file_name,
                new_caption=media.caption
            )
            if success:
                logger.info(f"[QUALITY] {cleanup_msg}")
        # === Quality Management End ===
        if await db.movie_update_status(bot.me.id):
            await process_and_send_update(bot, media.file_name, media.caption)
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
    error_tmdb=False
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
        if TMDB_POSTER:
            details = await get_movie_detailsx(base_name)
            if details.get("error") or not details.get("poster_url") and not details.get("backdrop_url"):
                error_tmdb=True
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
        movie_doc = {
            "_id": base_name,
            "files": [file_data],
            "poster_url": details.get("backdrop_url") if LANDSCAPE_POSTER and TMDB_POSTER and details.get("backdrop_url") and not error_tmdb else details.get("poster_url"),
            "genres": genres,
            "rating": details.get("rating", "N/A"),
            "imdb_url": details.get("url", "")if not TMDB_POSTER or error_tmdb else details.get("tmdb_url"),
            "year": media_info["year"] or details.get("year"),
            "tag": media_info["tag"],
            "ott_platform": media_info["ott_platform"],
            "message_id": None,
            "is_photo": False,
            "error_tmdb": error_tmdb,
            "is_backdrop": details.get("backdrop_url")
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
            
            # Movie Search Group button hata diya gaya hai
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
        
        # Movie Search Group button hata diya gaya hai
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
    
    # -------------------------
    # SEASON & EPISODE STYLE
    # -------------------------
    epi_block = ""
    if episodes_by_season:
        episode_lines = []
        for season, episodes in sorted(episodes_by_season.items(), key=lambda x: int(x[0])):
            sorted_eps = sorted(list(episodes), key=lambda x: int(x.split('-')[0]) if '-' in x else int(x))
            ep_list = ", ".join(sorted_eps)
            line = (
                f"<b>┇ 💠 Season {int(season):02}</b>\n"
                f"<b>┇</b> ╰┈ ᴇᴘs: <code>{ep_list}</code>"
            )
            episode_lines.append(line)
        epi_str = "\n".join(episode_lines)
        if epi_str:
            epi_block = f"<b>┍━━━━━━━━━━━━━━━</b>\n{epi_str}\n<b>┕━━━━━━━━━━━━━━━</b>"

    # -------------------------
    # PREMIUM STYLING ENGINE
    # -------------------------
    # 1. Title -> 𝐏𝐮𝐬𝐡𝐩𝐚 𝟐 𝟐𝟎𝟐𝟒
    styled_title = get_styled_text(base_name, style_type="bold_serif")
    
    # 2. Genres -> ᴀᴄᴛɪᴏɴ, ᴅʀᴀᴍᴀ
    raw_genres = movie_doc.get("genres", "N/A")
    styled_genres = get_styled_text(raw_genres, style_type="small_caps")
    
    # 3. Languages -> ʜɪɴᴅɪ, ᴛᴀᴍɪʟ
    raw_languages = ", ".join(sorted(all_languages)) if all_languages else "N/A"
    styled_languages = get_styled_text(raw_languages, style_type="small_caps")

    # 4. Quality -> 𝟽𝟸𝟶ᴘ, 𝟷𝟶𝟾𝟶ᴘ
    raw_quality = ", ".join(sorted(all_qualities)) if all_qualities else "N/A"
    styled_quality = get_styled_text(raw_quality, style_type="small_caps")

    # 5. OTT Platform -> ɴᴇᴛꜰʟɪx, ᴀᴍᴀᴢᴏɴ (NEW ✨)
    raw_ott = ", ".join(sorted(all_ott_platforms)) if all_ott_platforms else "N/A"
    styled_ott = get_styled_text(raw_ott, style_type="small_caps")

    # Final Formatting
    return script.MOVIE_UPDATE_NOTIFY_TXT.format(
        poster_url=movie_doc.get("poster_url", ""),
        imdb_url=movie_doc.get("imdb_url", ""),
        filename=styled_title,
        tag=primary_tag,
        genres=styled_genres,
        ott=styled_ott,              # <--- Styled OTT
        quality=styled_quality,
        language=styled_languages,
        episodes=epi_block,
        rating=movie_doc.get("rating", "N/A"),
        search_link=temp.B_LINK
    )



# Replace this with your own channel ID
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
            # Add the button to the message
            await message.edit_reply_markup(reply_markup=button)
            await asyncio.sleep(0.5)  # Small delay to handle rapid messages
        except Exception as e:
            print(f"Failed to add button: {e}")



# ═══════════════════════════════════════════════════════════════
# DRY RUN + CONFIRM COMMANDS - SINGLE & BATCH MODE


# ═══════════════════════════════════════════════════════════════
# SINGLE MOVIE MODE
# ═══════════════════════════════════════════════════════════════

# ╔════════════════════════════════════════════════════════════════╗
# ║ COMMAND 1: /cleanup_dry_single <movie_name>                   ║
# ║ DRY RUN - ek movie ke liye dekho konse files delete honge     ║
# ╚════════════════════════════════════════════════════════════════╝

@Client.on_message(filters.command("cleanup_dry_single") & filters.user(ADMINS))
async def cleanup_dry_single(bot, message):
    """
    /cleanup_dry_single Avengers Endgame 2019
    
    DRY RUN - ek specific movie ke liye
    Dikhata h konse files delete honge
    """
    try:
        if len(message.command) < 2:
            return await message.reply_text(
                "❌ Usage: /cleanup_dry_single <movie_name>\n\n"
                "Example: /cleanup_dry_single Avengers Endgame 2019"
            )
        
        movie_name = " ".join(message.command[1:])
        msg = await message.reply_text(
            f"🔍 **DRY RUN - SINGLE MOVIE**\n\n"
            f"Movie: {movie_name}\n"
            f"Status: Scanning...\n\n"
            f"(DRY RUN - Nothing will be deleted)"
        )
        
        from plugins.quality_manager import extract_quality_info, get_base_title
        from database.config_db import mdb
        import re
        
        # Search for similar files
        base_title = get_base_title(movie_name)
        search_pattern = f".*{re.escape(base_title)}.*"
        similar_files = await mdb.db.media.find({
            'file_name': {'$regex': search_pattern, '$options': 'i'}
        }).to_list(None)
        
        if not similar_files or len(similar_files) == 0:
            return await msg.edit_text(f"❌ No files found for: {movie_name}")
        
        # Analyze each file
        files_info = []
        for file in similar_files:
            quality = extract_quality_info(file.get('file_name', ''))
            files_info.append({
                'name': file.get('file_name', 'Unknown'),
                'quality': quality['source'] or 'Unknown',
                'resolution': quality['resolution'] or 'Unknown',
                'score': quality['quality_score'],
                'id': str(file.get('_id', ''))
            })
        
        # Sort by quality score (highest first)
        files_info.sort(key=lambda x: x['score'], reverse=True)
        
        # Build report
        report = f"📊 **DRY RUN - SINGLE MOVIE**\n"
        report += f"{'='*50}\n\n"
        report += f"🎬 Movie: {movie_name}\n"
        report += f"📁 Found: {len(files_info)} files\n\n"
        
        report += "📋 **File Details:**\n"
        report += "─" * 50 + "\n"
        
        for idx, file in enumerate(files_info, 1):
            status = "✅ KEEP" if idx == 1 else "❌ DELETE"
            report += f"\n{idx}. {status}\n"
            report += f"   📄 {file['name'][:60]}\n"
            report += f"   🎬 {file['quality'].upper() if file['quality'] else 'N/A'} | "
            report += f"📐 {file['resolution'].upper() if file['resolution'] else 'N/A'} | "
            report += f"📊 Score: {file['score']:.1f}\n"
        
        report += "\n" + "─" * 50 + "\n"
        to_delete = len(files_info) - 1
        report += f"\n⚠️  **PREVIEW:**\n"
        report += f"✅ Will KEEP: 1 file\n"
        report += f"❌ Will DELETE: {to_delete} files\n\n"
        
        if to_delete > 0:
            report += f"👉 To CONFIRM & DELETE:\n"
            report += f"`/cleanup_confirm_single {movie_name}`"
        else:
            report += f"ℹ️  No files to delete (already optimal)"
        
        await msg.edit_text(report)
        
    except Exception as e:
        await message.reply_text(f"❌ Error: {str(e)}")
        logger.exception("Error in cleanup_dry_single")


# ╔════════════════════════════════════════════════════════════════╗
# ║ COMMAND 2: /cleanup_confirm_single <movie_name>               ║
# ║ CONFIRM DELETE - ek movie ke liye actual delete karo           ║
# ╚════════════════════════════════════════════════════════════════╝

@Client.on_message(filters.command("cleanup_confirm_single") & filters.user(ADMINS))
async def cleanup_confirm_single(bot, message):
    """
    /cleanup_confirm_single Avengers Endgame 2019
    
    CONFIRM DELETE - ek specific movie ke liye
    Actually delete kar dega
    """
    try:
        if len(message.command) < 2:
            return await message.reply_text(
                "❌ Usage: /cleanup_confirm_single <movie_name>\n\n"
                "⚠️  WARNING: This will PERMANENTLY DELETE files!\n\n"
                "Example: /cleanup_confirm_single Avengers Endgame 2019"
            )
        
        movie_name = " ".join(message.command[1:])
        msg = await message.reply_text(
            f"⚠️  **CONFIRMING DELETE - SINGLE MOVIE**\n\n"
            f"🎬 Movie: {movie_name}\n"
            f"Status: Processing...\n\n"
            f"🗑️  Deleting lower quality files..."
        )
        
        from plugins.quality_manager import cleanup_duplicates, get_base_title
        from database.config_db import mdb
        
        # Get base title
        base_title = get_base_title(movie_name)
        
        if not base_title:
            return await msg.edit_text(f"❌ Could not extract title from: {movie_name}")
        
        # Perform actual deletion
        deleted_count, deleted_files = await cleanup_duplicates(
            db_collection=mdb.db.media,
            base_title=base_title,
            keep_highest_quality=True
        )
        
        if deleted_count > 0:
            deleted_list = ""
            for idx, file in enumerate(deleted_files[:8], 1):
                deleted_list += f"{idx}. {file[:60]}\n"
            
            if len(deleted_files) > 8:
                deleted_list += f"... + {len(deleted_files) - 8} more\n"
            
            report = (
                f"✅ **DELETE COMPLETED!**\n"
                f"{'='*50}\n\n"
                f"🎬 Movie: {movie_name}\n"
                f"🗑️  Deleted: {deleted_count} files\n"
                f"✅ Kept: 1 file (highest quality)\n\n"
                f"📋 **Deleted Files:**\n"
                f"{deleted_list}"
            )
            
            await msg.edit_text(report)
            
            # Log
            logger.warning(
                f"[CLEANUP SINGLE CONFIRMED] Movie: {movie_name} | "
                f"Deleted: {deleted_count} files"
            )
        else:
            await msg.edit_text(
                f"ℹ️  **No files deleted**\n\n"
                f"🎬 Movie: {movie_name}\n"
                f"Reason: All files are optimal quality or no duplicates"
            )
        
    except Exception as e:
        await message.reply_text(f"❌ Error: {str(e)}")
        logger.exception("Error in cleanup_confirm_single")


# ═══════════════════════════════════════════════════════════════════
# BATCH MODE - SAARI MOVIES
# ═══════════════════════════════════════════════════════════════════

# ╔═══════════════════════════════════════════════════════════════╗
# ║ COMMAND 3: /cleanup_dry_batch                                 ║
# ║ DRY RUN - SAARI FILES KE LIYE                                 ║
# ║ Puri database me dekho konse files delete honge               ║
# ╚═══════════════════════════════════════════════════════════════╝

@Client.on_message(filters.command("cleanup_dry_batch") & filters.user(ADMINS))
async def cleanup_dry_batch(bot, message):
    """
    /cleanup_dry_batch
    
    DRY RUN - BATCH MODE
    SAARI movies ke liye dekho konse files delete honge
    """
    try:
        msg = await message.reply_text(
            f"🔍 **DRY RUN - BATCH MODE**\n\n"
            f"Status: Scanning ALL files...\n\n"
            f"(DRY RUN - Nothing will be deleted)"
        )
        
        from plugins.quality_manager import extract_quality_info, get_base_title
        from database.config_db import mdb
        from collections import defaultdict
        
        # Get all files
        all_files = await mdb.db.media.find({}).to_list(None)
        
        if not all_files or len(all_files) == 0:
            return await msg.edit_text("❌ No files found in database")
        
        # Group by base title
        movies = defaultdict(list)
        for file in all_files:
            base_title = get_base_title(file.get('file_name', ''))
            if base_title:
                quality = extract_quality_info(file.get('file_name', ''))
                movies[base_title].append({
                    'name': file.get('file_name', ''),
                    'quality': quality['source'],
                    'resolution': quality['resolution'],
                    'score': quality['quality_score']
                })
        
        # Count duplicates that would be deleted
        total_to_delete = 0
        duplicate_movies = []
        
        for base_title, files in movies.items():
            if len(files) > 1:
                # Sort by score
                files.sort(key=lambda x: x['score'], reverse=True)
                to_delete = len(files) - 1
                total_to_delete += to_delete
                duplicate_movies.append({
                    'title': base_title,
                    'count': len(files),
                    'to_delete': to_delete
                })
        
        # Sort by to_delete (highest first)
        duplicate_movies.sort(key=lambda x: x['to_delete'], reverse=True)
        
        # Build report
        report = f"📊 **DRY RUN - BATCH MODE**\n"
        report += f"{'='*50}\n\n"
        report += f"📁 Total Files: {len(all_files)}\n"
        report += f"🎬 Total Movies: {len(movies)}\n"
        report += f"📋 Movies with Duplicates: {len(duplicate_movies)}\n\n"
        
        report += f"⚠️  **WOULD BE DELETED: {total_to_delete} files**\n\n"
        
        if duplicate_movies:
            report += f"📋 **Movies with Lower Quality Duplicates:**\n"
            report += "─" * 50 + "\n"
            
            for idx, movie in enumerate(duplicate_movies[:15], 1):
                report += f"{idx}. {movie['title'][:45]}\n"
                report += f"   Total versions: {movie['count']} | "
                report += f"Would delete: {movie['to_delete']}\n\n"
            
            if len(duplicate_movies) > 15:
                remaining = len(duplicate_movies) - 15
                report += f"... + {remaining} more movies\n\n"
        
        report += "─" * 50 + "\n"
        report += f"\n👉 To CONFIRM & DELETE ALL:\n"
        report += f"`/cleanup_confirm_batch`"
        
        await msg.edit_text(report)
        
    except Exception as e:
        await message.reply_text(f"❌ Error: {str(e)}")
        logger.exception("Error in cleanup_dry_batch")


# ╔═══════════════════════════════════════════════════════════════╗
# ║ COMMAND 4: /cleanup_confirm_batch                             ║
# ║ CONFIRM DELETE - SAARI FILES KE LIYE                          ║
# ║ Puri database se lower quality delete kar dega                ║
# ╚═══════════════════════════════════════════════════════════════╝

@Client.on_message(filters.command("cleanup_confirm_batch") & filters.user(ADMINS))
async def cleanup_confirm_batch(bot, message):
    """
    /cleanup_confirm_batch
    
    CONFIRM DELETE - BATCH MODE
    SAARI movies ke liye lower quality delete kar dega
    """
    try:
        msg = await message.reply_text(
            f"⚠️  **CONFIRMING DELETE - BATCH MODE**\n\n"
            f"Status: Processing ALL files...\n\n"
            f"🗑️  Deleting lower quality files from entire database...\n"
            f"⏳ This may take a while..."
        )
        
        from plugins.quality_manager import extract_quality_info, get_base_title
        from database.config_db import mdb
        from collections import defaultdict
        
        # Get all files
        all_files = await mdb.db.media.find({}).to_list(None)
        
        if not all_files or len(all_files) == 0:
            return await msg.edit_text("❌ No files found in database")
        
        # Group by base title
        movies = defaultdict(list)
        for file in all_files:
            base_title = get_base_title(file.get('file_name', ''))
            if base_title:
                quality = extract_quality_info(file.get('file_name', ''))
                movies[base_title].append({
                    'file_obj': file,
                    'name': file.get('file_name', ''),
                    'quality': quality['source'],
                    'resolution': quality['resolution'],
                    'score': quality['quality_score']
                })
        
        # Process each movie group
        total_deleted = 0
        movies_cleaned = 0
        deleted_files_list = []
        
        for base_title, files in movies.items():
            if len(files) > 1:
                # Sort by score (highest first)
                files.sort(key=lambda x: x['score'], reverse=True)
                
                # Delete lower quality versions
                for file_data in files[1:]:  # Skip first (best quality)
                    try:
                        await mdb.db.media.delete_one({'_id': file_data['file_obj']['_id']})
                        total_deleted += 1
                        movies_cleaned += 1
                        deleted_files_list.append(file_data['name'])
                    except Exception as e:
                        logger.error(f"Error deleting {file_data['name']}: {e}")
        
        # Build report
        if total_deleted > 0:
            deleted_preview = ""
            for idx, file in enumerate(deleted_files_list[:8], 1):
                deleted_preview += f"{idx}. {file[:55]}\n"
            
            if len(deleted_files_list) > 8:
                deleted_preview += f"... + {len(deleted_files_list) - 8} more\n"
            
            report = (
                f"✅ **BATCH CLEANUP COMPLETED!**\n"
                f"{'='*50}\n\n"
                f"🗑️  Total Deleted: {total_deleted} files\n"
                f"🎬 Movies Cleaned: {movies_cleaned}\n"
                f"✅ Highest Quality Kept: {movies_cleaned} files\n\n"
                f"📋 **Sample Deleted Files:**\n"
                f"{deleted_preview}"
            )
        else:
            report = (
                f"ℹ️  **No files to delete**\n\n"
                f"All files are already optimal quality"
            )
        
        await msg.edit_text(report)
        
        # Log
        logger.warning(
            f"[CLEANUP BATCH CONFIRMED] Total Deleted: {total_deleted} files | "
            f"Movies Cleaned: {movies_cleaned}"
        )
        
    except Exception as e:
        await message.reply_text(f"❌ Error: {str(e)}")
        logger.exception("Error in cleanup_confirm_batch")

