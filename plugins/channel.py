import logging
import os
import re
import asyncio
from datetime import datetime
from collections import defaultdict
from pyrogram import Client, filters, enums
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from pymongo.errors import PyMongoError, DuplicateKeyError
from pyrogram.errors import MessageIdInvalid, MessageNotModified, FloodWait

from info import CHANNELS, MOVIE_UPDATE_CHANNEL, LINK_PREVIEW, ABOVE_PREVIEW, BAD_WORDS, ADMINS, LANDSCAPE_POSTER, TMDB_POSTER, MULTIPLE_DB
from Script import script
from utils import temp
from database.users_chats_db import db
from plugins.quality_manager import extract_quality_info, is_high_quality, run_quality_cleanup_background
from plugins.Dreamxfutures.Imdbposter import get_movie_detailsx, fetch_image, get_movie_details

# 🎯 100% SYNCHRONIZED WITH ia_filterdb.py
from database.ia_filterdb import save_file, extract_pure_title, extract_languages_quality, is_series_file, MEDIA_DBS

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

# ==========================================
# CONSTANTS & GLOBALS
# ==========================================
STANDARD_GENRES = {
    'Action', 'Adventure', 'Animation', 'Biography', 'Comedy', 'Crime', 'Documentary',
    'Drama', 'Family', 'Fantasy', 'Film-Noir', 'History', 'Horror', 'Music',
    'Musical', 'Mystery', 'Romance', 'Sci-Fi', 'Sport', 'Thriller', 'War', 'Western'
}

MEDIA_FILTER = filters.document | filters.video | filters.audio
locks = defaultdict(asyncio.Lock)
pending_updates = {}


# ==========================================
# 🎯 SMART MEDIA INFO EXTRACTOR
# ==========================================
def extract_media_info(filename: str, caption: str):
    text_to_scan = f"{filename} {caption}"
    
    extracted = extract_languages_quality(text_to_scan)
    
    base_name, _ = os.path.splitext(filename)
    pure_title = extract_pure_title(base_name)

    if not pure_title.strip() and caption:
        caption_plain = re.sub(r'<[^>]+>', ' ', caption)
        caption_plain = re.sub(r'[\U0001F000-\U0001FFFF\u2600-\u27BF]+', ' ', caption_plain)
        caption_plain = caption_plain.splitlines()[0] if caption_plain.strip() else caption_plain
        pure_title = extract_pure_title(caption_plain)

    formatted_words = []
    for word in pure_title.split():
        if len(word) > 1:
            formatted_words.append(word[0].upper() + word[1:])
        else:
            formatted_words.append(word.upper())
            
    final_title = " ".join(formatted_words) if pure_title else "Unnamed"

    tag = "#SERIES" if is_series_file(filename) else "#MOVIE"

    # 🚀 FIX: Season and Episode Logic
    season = None
    episode = None
    se_str = extracted.get("season_episode")
    if se_str:
        s_match = re.search(r'S(\d+)', se_str, re.IGNORECASE)
        e_match = re.search(r'E([\d\-]+)', se_str, re.IGNORECASE)
        
        if s_match: 
            season = int(s_match.group(1))
        if e_match: 
            episode = e_match.group(1)
        elif s_match and not e_match:
            episode = "All" # Pack / Complete Season

    languages = ", ".join(extracted.get("languages", [])) if extracted.get("languages") else "N/A"
    ott_platform = extracted.get("ott") or "N/A"

    return {
        "processed": final_title,
        "base_name": final_title,  
        "tag": tag,
        "season": season,
        "episode": episode,
        "series_status": extracted.get("series_status"), 
        "year": extracted.get("year"),
        "resolution": extracted.get("resolution") or "N/A", # Pixels
        "source": extracted.get("source") or "N/A",         # Quality
        "ott_platform": ott_platform,
        "language": languages
    }

def schedule_update(bot, base_name, delay=5):
    if handle := pending_updates.get(base_name):
        if not handle.cancelled():
            handle.cancel()

    loop = asyncio.get_event_loop()
    pending_updates[base_name] = loop.call_later(
        delay,
        lambda: asyncio.create_task(update_movie_message(bot, base_name))
    )

# ==========================================
# FILE INTERCEPTION HANDLER
# ==========================================
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

    extracted_info = extract_media_info(media.file_name, media.caption or "")

    success, info, real_file_name = await save_file(media, bot=bot, extracted_info=extracted_info)
    if not success:
        return

    real_file_name = real_file_name or media.file_name

    try:
        quality_info = extract_quality_info(real_file_name, media.caption)

        logger.debug(
            f"[QUALITY] {real_file_name[:70]} | "
            f"source={quality_info.get('source')} | "
            f"resolution={quality_info.get('resolution')} | "
            f"score={quality_info.get('quality_score', 0):.1f} | "
            f"lang={extracted_info.get('language', 'N/A')}"
        )

        if is_high_quality(quality_info):
            asyncio.create_task(
                run_quality_cleanup_background(MEDIA_DBS, real_file_name, media.caption)
            )

    except Exception as e:
        logger.error(f"[QUALITY] Error in quality management: {e}", exc_info=True)

    try:
        if await db.movie_update_status(bot.me.id):
            await process_and_send_update(bot, real_file_name, media.caption)
    except Exception:
        logger.exception("Error processing media for channel update")


# ==========================================
# CHANNEL POSTING & GROUPING LOGIC
# ==========================================
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

    # 🚀 FIX: Saving both resolution and source
    file_data = {
        "filename": filename,
        "processed": processed,
        "resolution": media_info["resolution"],
        "source": media_info["source"],
        "quality": media_info["resolution"], # Backwards Compatibility
        "language": media_info["language"],
        "ott_platform": media_info["ott_platform"],
        "timestamp": datetime.now(),
        "tag": media_info["tag"],
        "season": media_info["season"],
        "episode": media_info["episode"],
        "series_status": media_info.get("series_status")
    }

    if not movie_doc:
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

# ==========================================
# POST FORMATTING & STYLING
# ==========================================
def get_styled_text(text: str, style_type="bold_serif") -> str:
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
    all_resolutions = set()
    all_sources = set()
    all_languages = set()
    all_ott_platforms = set()
    all_tags = set()
    episodes_by_season = defaultdict(set)
    series_statuses = set()

    for file in movie_doc["files"]:
        # 🚀 FIX: Fetching both Resolution & Source separately
        if file.get("resolution") and file["resolution"] != "N/A":
            all_resolutions.update(r.strip() for r in file["resolution"].split(",") if r.strip())
        elif file.get("quality") and file["quality"] != "N/A": # Backwards compatibility
            all_resolutions.update(q.strip() for q in file.get("quality", "").split(",") if q.strip())
            
        if file.get("source") and file["source"] != "N/A":
            all_sources.update(s.strip() for s in file["source"].split(",") if s.strip())

        if file.get("language") and file["language"] != "N/A":
            all_languages.update(l.strip() for l in file["language"].split(",") if l.strip())
        
        if file.get("ott_platform") and file["ott_platform"] != "N/A":
            platforms = [p.strip() for p in file["ott_platform"].split("|") if p.strip()]
            all_ott_platforms.update(platforms)
            
        if file.get("tag"):
            all_tags.add(file["tag"])
            
        season = file.get("season")
        episode = file.get("episode")
        
        if season is not None:
            episodes_by_season[season].add(str(episode) if episode else "All")
        elif episode:
            episodes_by_season[1].add(str(episode))
            
        if file.get("series_status"):
            series_statuses.add(file["series_status"].title())

    primary_tag = "#SERIES" if "#SERIES" in all_tags else "#MOVIE"

    # ===== SEASON & EPISODE DISPLAY =====
    epi_block = ""
    if episodes_by_season or series_statuses:
        episode_lines = []
        for season in sorted(episodes_by_season.keys(), key=lambda x: int(x)):
            # Advanced sorting logic for "All", "01", "01-05"
            def ep_sort_key(x):
                if x == "All": return -1
                num_part = x.split('-')[0]
                return int(num_part) if num_part.isdigit() else 999

            episodes = sorted(list(episodes_by_season[season]), key=ep_sort_key)
            ep_list = ", ".join(episodes)

            line = (
                f"<b>┇ 💠 Season {int(season):02d}</b>\n"
                f"<b>┇ </b>ᴇᴘɪsᴏᴅᴇs: <code>{ep_list}</code>"
            )
            episode_lines.append(line)
            
        if series_statuses:
            status_str = ", ".join(series_statuses)
            episode_lines.append(f"<b>┇ </b>sᴛᴀᴛᴜs: <code>{status_str}</code>")

        if episode_lines:
            epi_str = "\n".join(episode_lines)
            epi_block = f"\n<b>━━━━━━━━━━━━━━━━━</b>\n{epi_str}\n<b>━━━━━━━━━━━━━━━━━</b>"


    # ===== QUALITY COMBINATION (Pixels + Source) =====
    res_str = ", ".join(sorted(all_resolutions))
    src_str = ", ".join(sorted(all_sources))
    
    if res_str and src_str:
        raw_quality = f"{res_str} | {src_str}"  # Output: 1080p, 720p | WEB-DL, HDRip
    elif res_str:
        raw_quality = res_str
    elif src_str:
        raw_quality = src_str
    else:
        raw_quality = "N/A"


    # ===== STYLING =====
    styled_title = get_styled_text(base_name, style_type="bold_serif")
    raw_genres = movie_doc.get("genres", "N/A")
    styled_genres = get_styled_text(raw_genres, style_type="small_caps")

    raw_languages = ", ".join(sorted(all_languages)) if all_languages else "N/A"
    styled_languages = get_styled_text(raw_languages, style_type="small_caps")

    styled_quality = get_styled_text(raw_quality, style_type="small_caps")

    raw_ott = ", ".join(sorted(all_ott_platforms)) if all_ott_platforms else "N/A"
    styled_ott = get_styled_text(raw_ott, style_type="small_caps")

    return script.MOVIE_UPDATE_NOTIFY_TXT.format(
        poster_url=movie_doc.get("poster_url", ""),
        imdb_url=movie_doc.get("imdb_url", ""),
        filename=styled_title,
        tag=primary_tag,
        genres=styled_genres,
        ott=styled_ott,
        quality=styled_quality, # 🚀 Now it contains both Pixels and Source
        language=styled_languages,
        episodes=epi_block,
        rating=movie_doc.get("rating", "N/A"),
        search_link=temp.B_LINK
    )

# ==========================================
# CHANNEL POST BUTTON INJECTOR
# ==========================================
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
