import re
import logging
import asyncio
import math
from typing import Optional, Tuple, List
from database.ia_filterdb import Media, Media2
from info import MULTIPLE_DB, ADMINS
from pyrogram import Client, filters
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from collections import defaultdict

logger = logging.getLogger(__name__)

# Quality hierarchy
QUALITY_HIERARCHY = {
    "camrip": 1, "cam rip": 1, "hdcam": 1, "hd cam": 1, 
    "hdtc": 2, "hd tc": 2, "hdts": 2, "hd ts": 2, 
    "ts": 2, "tc": 2, "telesync": 2, 
    "predvd": 3, "predvdrip": 3, "pre dvd": 3, "dvdscr": 3, "dvd scr": 3,
    "dvdrip": 4, "dvd rip": 4, "tvrip": 5, "tv rip": 5, "hdtv": 5, "hd tv": 5,
    "webrip": 6, "web rip": 6, "web-dl": 7, "web dl": 7, "webdl": 7, 
    "hdrip": 8, "hd rip": 8, "bluray": 9, "blu ray": 9, "bdrip": 9, "bd rip": 9, "brrip": 9, "br rip": 9
}

RESOLUTION_HIERARCHY = {
    "240p": 1, "140p": 1, "360p": 2, "480p": 3, "540p": 4, "720p": 5, "1080p": 6, "1440p": 7, "2160p": 8, "4k": 8,
}

# 🔧 IMPROVED LANGUAGES WITH MORE SHORT FORMS
LANGUAGES = {
    "hindi": [r"\bhindi\b", r"\bhin\b", r"\bhi\b"],
    "english": [r"\benglish\b", r"\beng\b", r"\ben\b"],
    "tamil": [r"\btamil\b", r"\btam\b", r"\bta\b"],
    "telugu": [r"\btelugu\b", r"\btel\b", r"\nte\b"],
    "malayalam": [r"\bmalayalam\b", r"\bmal\b", r"\bml\b"],
    "kannada": [r"\bkannada\b", r"\bkan\b", r"\bkn\b"],
    "punjabi": [r"\bpunjabi\b", r"\bpan\b", r"\bpbi\b", r"\bpa\b"],
    "bengali": [r"\bbengali\b", r"\bben\b", r"\bbn\b"],
    "marathi": [r"\bmarathi\b", r"\bmar\b", r"\bmr\b"],
    "gujarati": [r"\bgujarati\b", r"\bguj\b", r"\bgujrat\b", r"\bgu\b"]
}

LOW_QUALITY_SOURCES = [
    'camrip', 'cam rip', 'hdcam', 'hd cam', 'hdtc', 'hd tc', 
    'hdts', 'hd ts', 'ts', 'tc', 'telesync', 'predvd', 'predvdrip', 'pre dvd', 'dvdscr', 'dvd scr'
]

HIGH_QUALITY_SOURCES = [
    'webrip', 'web rip', 'web-dl', 'web dl', 'webdl', 
    'hdrip', 'hd rip', 'bluray', 'blu ray', 'bdrip', 'bd rip', 'brrip', 'br rip'
]

# 🔧 IMPROVED: Returns ALL languages found (not just first one)
def extract_language(text: str) -> List[str]:
    """Extract ALL languages from text. Returns list of languages found."""
    text = text.lower()
    found_languages = []
    
    # Har language (key) aur uske patterns (list) ke liye
    for lang, patterns in LANGUAGES.items():
        for pattern in patterns:
            if re.search(pattern, text):
                if lang not in found_languages:
                    found_languages.append(lang)
                break  # Move to next language once found
    
    return found_languages if found_languages else ["unknown"]

def extract_quality_info(filename: str, caption: str = "") -> dict:
    text = f"{filename} {caption}".lower()
    quality_info = {
        'source': None, 'resolution': None, 'quality_score': 0, 'source_score': 0, 'resolution_score': 0
    }

    for source, score in QUALITY_HIERARCHY.items():
        # Yahan change kiya hai: \b hata kar [._\-\s] lagaya hai
        # Ye space, dash, dot, aur underscore sabko handle karega
        pattern = rf'[._\-\s]{re.escape(source)}[._\-\s]|^{re.escape(source)}[._\-\s]|[._\-\s]{re.escape(source)}$'
        if re.search(pattern, text):
            quality_info['source'] = source
            quality_info['source_score'] = score
            break

    for res, score in RESOLUTION_HIERARCHY.items():
        # Yahan bhi wahi change
        pattern = rf'[._\-\s]{re.escape(res)}[._\-\s]|^{re.escape(res)}[._\-\s]|[._\-\s]{re.escape(res)}$'
        if re.search(pattern, text):
            quality_info['resolution'] = res
            quality_info['resolution_score'] = score
            break

    quality_info['quality_score'] = (quality_info['source_score'] * 0.7) + (quality_info['resolution_score'] * 0.3)
    return quality_info

def is_low_quality_print(quality_info: dict) -> bool:
    return quality_info.get('source') in LOW_QUALITY_SOURCES

def is_high_quality(quality_info: dict) -> bool:
    return quality_info.get('source') in HIGH_QUALITY_SOURCES

# 🔧 RULE 3: SUBSET MATCH (Corrected)
def should_delete_existing(existing_quality: dict, new_quality: dict, existing_langs: List[str], new_langs: List[str]) -> bool:
    try:
        # RULE 3 (CORRECTED): SUBSET CHECK
        # LOW languages must be a subset of HIGH languages
        # i.e., HIGH must have ALL languages that LOW has (and can have extra)
        # 
        # Case A: LOW={Tamil}, HIGH={Hindi, Tamil} → {Tamil} ⊆ {Hindi, Tamil} ✅ DELETE
        # Case B: LOW={Hindi}, HIGH={Hindi, English} → {Hindi} ⊆ {Hindi, English} ✅ DELETE
        # Case C: LOW={Hindi, English}, HIGH={Hindi} → {Hindi, English} ⊄ {Hindi} ❌ KEEP
        # Case D: LOW={Hindi, Tamil}, HIGH={Hindi, English} → {Hindi, Tamil} ⊄ {Hindi, English} ❌ KEEP
        
        existing_set = set(existing_langs)
        new_set = set(new_langs)
        
        # Check if existing (LOW) is a subset of new (HIGH)
        # i.e., HIGH has all languages of LOW
        if not (existing_set <= new_set):
            return False  # LOW has languages not in HIGH, can't delete

        existing_source = existing_quality.get('source')
        new_source = new_quality.get('source')

        if not existing_source or not new_source:
            return False

        # QUALITY CHECK: Low Quality delete hogi ONLY jab:
        # 1. LOW language set is SUBSET of HIGH language set (rule above)
        # 2. Existing is LOW quality AND New is HIGH quality
        if existing_source in LOW_QUALITY_SOURCES and new_source in HIGH_QUALITY_SOURCES:
            return True

        return False
    except Exception as e:
        logger.error(f"Error in should_delete_existing: {e}")
        return False

# 🔧 IMPROVED: Extract title with Season/Episode/Year handling
def get_base_title(filename: str) -> str:
    text = filename.lower()
    text = re.sub(r'\.(mkv|mp4|avi|mov|wmv|flv|webm)$', '', text, flags=re.I)
    text = re.sub(r'[._-]+', ' ', text)

    # Remove season/episode info but remember we extracted title
    # s01, s001, season 1, season 01 etc
    text = re.sub(r'\bs\d{1,2}\b|\bseason\s*\d{1,2}\b', '', text, flags=re.I)
    # e01, e001, episode 1, episode 01 etc
    text = re.sub(r'\be\d{1,2}\b|\bepisode\s*\d{1,2}\b', '', text, flags=re.I)

    for quality in list(QUALITY_HIERARCHY.keys()) + list(RESOLUTION_HIERARCHY.keys()):
        text = re.sub(rf'\b{re.escape(quality)}\b', '', text, flags=re.I)

    text = re.sub(
        r'\b(hevc|x265|x264|h264|avc|av1|aac|flac|dts|ac3|eac3|ddp|ddp5\.1|dd5\.1|5\.1|7\.1|2\.0|'
        r'dub|sub|esub|esubs|multi|proper|uncut|'
        r'hindi|english|tamil|telugu|malayalam|kannada|punjabi|'
        r'bengali|marathi|gujarati|movies4u|tokyo_updates|telly|amzn|nf|dsnp|'
        r'hin|eng|tam|tel|mal|kan|pan|ben|mar|guj|'
        r'hi|en|ta|te|ml|kn|pa|bn|mr|gu)\b', '', text, flags=re.I
    )
    text = re.sub(r'[\[\(\{].*?[\]\)\}]', '', text)
    text = re.sub(r'\s+', ' ', text).strip()
    return text

async def find_and_delete_lower_quality(
    db_collection, new_filename: str, new_caption: str = "", file_id: Optional[str] = None
) -> Tuple[bool, str]:
    try:
        if not new_filename or not isinstance(new_filename, str):
            return False, "Invalid filename provided"

        new_quality = extract_quality_info(new_filename, new_caption or "")

        if not new_quality['source']:
            return True, "No quality info found in new file, skipping cleanup"

        base_title = get_base_title(new_filename)
        if not base_title:
            return True, "Could not extract title for comparison"

        try:
            words = [w for w in base_title.split() if len(w) > 1]
            pattern = ".*".join([rf"\b{re.escape(w)}\b" for w in words[:5]]) if words else re.escape(base_title)
        except Exception as e:
            return False, f"Error building search pattern: {str(e)}"

        search_query = {'file_name': {'$regex': pattern, '$options': 'i'}}
        if file_id:
            search_query['_id'] = {'$ne': file_id}

        similar_files = await db_collection.find(search_query).to_list(None)
        deleted_count = 0
        kept_files = []

        new_langs = extract_language(f"{new_filename} {new_caption or ''}")

        for existing_file in similar_files:
            try:
                existing_filename = existing_file.get('file_name', '')
                existing_caption = existing_file.get('caption', '')
                existing_quality = extract_quality_info(existing_filename, existing_caption or "")

                existing_langs = extract_language(f"{existing_filename} {existing_caption or ''}")

                if should_delete_existing(existing_quality, new_quality, existing_langs, new_langs):
                    try:
                        await db_collection.delete_one({'_id': existing_file['_id']})
                        deleted_count += 1
                        langs_str = ", ".join(existing_langs)
                        logger.warning(
                            f"[QUALITY] ✅ DELETED lower quality file:\n"
                            f"  📄 {existing_filename[:70]}\n"
                            f"  🎬 Source: {existing_quality.get('source', 'N/A').upper()} | Langs: {langs_str}\n"
                        )
                    except Exception as e:
                        logger.error(f"[QUALITY] ❌ Error deleting file {existing_filename}: {e}")
                else:
                    kept_files.append(existing_filename)
            except Exception as e:
                continue

        if deleted_count > 0:
            return True, f"✅ Cleanup completed:\n 🗑️ Deleted: {deleted_count} file(s)\n ✨ Kept: {len(kept_files)} file(s)"
        else:
            return True, "ℹ️ No lower quality files to delete"

    except Exception as e:
        logger.error(f"[QUALITY] ❌ Error in find_and_delete_lower_quality: {e}", exc_info=True)
        return False, f"Error during quality cleanup: {str(e)}"

async def cleanup_duplicates(db_collection, base_title: str, keep_highest_quality: bool = True) -> Tuple[int, List[str]]:
    try:
        words = [w for w in base_title.split() if len(w) > 1]
        pattern = ".*".join([rf"\b{re.escape(w)}\b" for w in words[:5]]) if words else re.escape(base_title)

        search_query = {'file_name': {'$regex': pattern, '$options': 'i'}}
        files = await db_collection.find(search_query).to_list(None)

        if len(files) <= 1:
            return 0, []

        scored_files = []
        for file in files:
            file_name = file.get('file_name', '')
            quality = extract_quality_info(file_name, file.get('caption', ''))
            langs = extract_language(file_name)
            scored_files.append({
                'file': file, 
                'quality': quality, 
                'languages': langs,
                'score': quality['quality_score']
            })

        deleted_count = 0
        deleted_files = []

        if keep_highest_quality:
            for scored_file in scored_files:
                file_source = scored_file['quality'].get('source')

                if file_source in LOW_QUALITY_SOURCES:
                    # RULE 3: SUBSET CHECK
                    # LOW language set must be subset of HIGH language set
                    is_replaceable = False
                    low_lang_set = set(scored_file['languages'])
                    
                    for hq_file in scored_files:
                        if hq_file['quality'].get('source') in HIGH_QUALITY_SOURCES:
                            high_lang_set = set(hq_file['languages'])
                            
                            # Check if LOW is subset of HIGH
                            # i.e., HIGH has all languages of LOW
                            if low_lang_set <= high_lang_set:
                                is_replaceable = True
                                break

                    if is_replaceable:
                        try:
                            file_to_delete = scored_file['file'].get('file_name', 'Unknown')
                            await db_collection.delete_one({'_id': scored_file['file']['_id']})
                            deleted_count += 1
                            deleted_files.append(file_to_delete)

                            # 🔥 LOGS KE LIYE YAHAN ADD KIYA GAYA HAI
                            logger.info(f"[CLEANUP SINGLE] 🗑️ Deleted File: {file_to_delete}")

                        except Exception as e:
                            logger.error(f"[CLEANUP SINGLE] ❌ Error deleting file: {e}")

        return deleted_count, deleted_files
    except Exception as e:
        logger.error(f"Error in cleanup_duplicates: {e}")
        return 0, []


# =========================================================
# --- COMMAND HANDLERS (Live Status, Cancel, Anti-Hang, Pagination, Auto-Delete) ---
# =========================================================

CANCEL_Q_TASKS = {}
DRY_RUN_CACHE = {}

@Client.on_callback_query(filters.regex(r"^cancel_q_task_(.*)"))
async def cancel_q_task(client, query):
    task_id = query.data.split("cancel_q_task_")[-1]
    CANCEL_Q_TASKS[task_id] = True
    await query.answer("🛑 Cancelling Process... Please wait a moment for the loop to stop.", show_alert=True)


# --- 5 MIN AUTO DELETE FUNCTION ---
async def auto_delete_msg(msg, command_msg, task_id, delay=300):
    try:
        await asyncio.sleep(delay)
        if task_id in DRY_RUN_CACHE:
            del DRY_RUN_CACHE[task_id]
        await msg.delete()
        await command_msg.delete()
    except Exception:
        pass


async def send_dry_page(msg, task_id, page):
    data = DRY_RUN_CACHE.get(task_id)
    if not data:
        if hasattr(msg, 'edit_text'):
            return await msg.edit_text("❌ Data expired or auto-deleted. Please run the command again.")
        else:
            return await msg.message.edit_text("❌ Data expired or auto-deleted. Please run the command again.")

    ITEMS_PER_PAGE = 15
    total_files = len(data['files'])
    total_pages = math.ceil(total_files / ITEMS_PER_PAGE) if total_files > 0 else 1

    start_idx = page * ITEMS_PER_PAGE
    end_idx = start_idx + ITEMS_PER_PAGE
    chunk = data['files'][start_idx:end_idx]

    report = f"📊 **DRY RUN - SINGLE MOVIE**\n{'='*50}\n\n🎬 Movie: {data['movie_name']}\n📁 Found: {total_files} files\n\n📋 **File Details (Page {page+1}/{total_pages}):**\n{'─'*50}\n"

    for f_text in chunk:
        report += f_text

    report += f"\n{'─'*50}\n⚠️ **PREVIEW SUMMARY:**\n✅ Will KEEP: {data['keep']}\n❌ Will DELETE: {data['delete']}\n\n"

    if data['delete'] > 0:
        report += f"👉 Confirm & Delete:\n`/cleanup_confirm_single {data['movie_name']}`\n\n"
    else:
        report += f"ℹ️ No files to delete (Rules applied)\n\n"

    report += "⏱️ *This message will auto-delete in 5 mins.*"

    buttons = []
    if page > 0:
        buttons.append(InlineKeyboardButton("⬅️ Previous", callback_data=f"dry_page_{task_id}_{page-1}"))
    if page < total_pages - 1:
        buttons.append(InlineKeyboardButton("Next ➡️", callback_data=f"dry_page_{task_id}_{page+1}"))

    reply_markup = InlineKeyboardMarkup([buttons]) if buttons else None

    try:
        if hasattr(msg, 'edit_text'): 
            await msg.edit_text(report, reply_markup=reply_markup)
        else: 
            await msg.message.edit_text(report, reply_markup=reply_markup)
    except Exception as e:
        logger.error(f"Error editing pagination message: {e}")

@Client.on_callback_query(filters.regex(r"^dry_page_"))
async def dry_page_callback(client, query):
    parts = query.data.split("_")
    task_id = parts[2]
    page = int(parts[3])
    await send_dry_page(query, task_id, page)
    await query.answer()


@Client.on_message(filters.command("quality_report") & filters.user(ADMINS))
async def quality_report_cmd(bot, message):
    try:
        task_id = str(message.id)
        CANCEL_Q_TASKS[task_id] = False
        cancel_markup = InlineKeyboardMarkup([[InlineKeyboardButton("🛑 CANCEL PROCESS", callback_data=f"cancel_q_task_{task_id}")]])

        msg = await message.reply_text("📊 Calculating total files...\n⏳ Please wait...", reply_markup=cancel_markup)

        total_docs = await Media.collection.estimated_document_count()
        if MULTIPLE_DB:
            total_docs += await Media2.collection.estimated_document_count()

        if total_docs == 0:
            return await msg.edit_text("❌ No files in database")

        processed = 0
        quality_dist = defaultdict(int)
        resolution_dist = defaultdict(int)

        async def process_cursor(collection):
            nonlocal processed
            async for file in collection.find({}, projection={'file_name': 1}):
                if CANCEL_Q_TASKS.get(task_id):
                    return False

                processed += 1
                file_name = file.get('file_name', '')
                quality_info = extract_quality_info(file_name)
                quality_dist[quality_info.get('source', 'unknown')] += 1
                resolution_dist[quality_info.get('resolution', 'unknown')] += 1

                if processed % 500 == 0:
                    await asyncio.sleep(0.1)

                if processed % 5000 == 0:
                    percent = (processed / total_docs) * 100
                    try:
                        await msg.edit_text(
                            f"📊 **Generating Quality Report...**\n\n"
                            f"📁 Scanned: **{processed} / {total_docs}** files\n"
                            f"⏳ Progress: **{percent:.1f}%**\n"
                            f"⚙️ Status: Memory Safe Mode (Relaxed)\n",
                            reply_markup=cancel_markup
                        )
                    except Exception:
                        pass
            return True

        if not await process_cursor(Media.collection):
            return await msg.edit_text("🛑 **Process Cancelled by Admin!**")

        if MULTIPLE_DB:
            if not await process_cursor(Media2.collection):
                return await msg.edit_text("🛑 **Process Cancelled by Admin!**")

        report = f"📊 **QUALITY REPORT (DB1 & DB2)**\n{'='*50}\n\n📁 **Total Files:** {total_docs}\n\n🎬 **Source Quality Distribution:**\n{'─'*50}\n"
        quality_order = ['camrip', 'hdcam', 'hdtc', 'hdts', 'ts', 'tc', 'predvd', 'dvdscr', 'dvdrip', 'tvrip', 'hdtv', 'webrip', 'web-dl', 'webdl', 'hdrip', 'bluray', 'bdrip', 'brrip', 'unknown']

        for quality in quality_order:
            if quality in quality_dist and quality_dist[quality] > 0:
                count = quality_dist[quality]
                percent = (count / total_docs * 100) if total_docs > 0 else 0
                if quality in LOW_QUALITY_SOURCES: emoji = "⚠️ "
                elif quality in HIGH_QUALITY_SOURCES: emoji = "✨"
                else: emoji = "⭐"
                report += f"{emoji} {quality.upper()}: {count} ({percent:.1f}%)\n"

        report += "\n📐 **Resolution Distribution:**\n"
        report += "─" * 50 + "\n"
        for res in ['240p', '360p', '480p', '540p', '720p', '1080p', '1440p', '2160p', 'unknown']:
            if res in resolution_dist and resolution_dist[res] > 0:
                count = resolution_dist[res]
                percent = (count / total_docs * 100) if total_docs > 0 else 0
                report += f"📹 {res}: {count} ({percent:.1f}%)\n"

        low_count = sum(quality_dist.get(q, 0) for q in LOW_QUALITY_SOURCES)
        report += f"\n⚠️ **Low Quality Count:** {low_count} files\n"

        await msg.edit_text(report)
    except Exception as e:
        await message.reply_text(f"❌ Error: {str(e)}")


@Client.on_message(filters.command("cleanup_dry_single") & filters.user(ADMINS))
async def cleanup_dry_single_cmd(bot, message):
    try:
        if len(message.command) < 2:
            return await message.reply_text("❌ Usage: /cleanup_dry_single <movie_name>")

        movie_name = " ".join(message.command[1:])
        msg = await message.reply_text(f"🔍 **DRY RUN - SINGLE**\n\nMovie: {movie_name}\n⏳ Scanning...")

        base_title = get_base_title(movie_name)
        if not base_title:
            return await msg.edit_text(f"❌ Could not extract title from: {movie_name}")

        words = [w for w in base_title.split() if len(w) > 1]
        search_pattern = ".*".join([rf"\b{re.escape(w)}\b" for w in words[:5]]) if words else re.escape(base_title)

        similar_files = await Media.collection.find({'file_name': {'$regex': search_pattern, '$options': 'i'}}).to_list(None)
        if MULTIPLE_DB:
            similar_files2 = await Media2.collection.find({'file_name': {'$regex': search_pattern, '$options': 'i'}}).to_list(None)
            similar_files.extend(similar_files2)

        if not similar_files:
            return await msg.edit_text(f"❌ No files found for: {movie_name}")

        files_info = []
        for file in similar_files:
            file_name = file.get('file_name', 'Unknown')
            quality = extract_quality_info(file_name)
            langs = extract_language(file_name)
            files_info.append({
                'name': file_name,
                'quality': quality['source'] or 'Unknown',
                'resolution': quality['resolution'] or 'Unknown',
                'languages': langs,
                'score': quality['quality_score']
            })

        files_info.sort(key=lambda x: x['score'], reverse=True)
        has_high_quality = any(f['quality'] in HIGH_QUALITY_SOURCES for f in files_info)

        formatted_files = []
        to_delete = 0

        for idx, file in enumerate(files_info):
            will_delete = False

            # 🔧 RULE 3: SUBSET CHECK
            # LOW languages must be subset of HIGH languages
            if file['quality'] in LOW_QUALITY_SOURCES:
                low_lang_set = set(file['languages'])
                
                for hq in files_info:
                    if hq['quality'] in HIGH_QUALITY_SOURCES:
                        high_lang_set = set(hq['languages'])
                        
                        # Check if LOW is subset of HIGH
                        if low_lang_set <= high_lang_set:
                            will_delete = True
                            break

            if will_delete:
                to_delete += 1

            status = "❌ DELETE (Low Q)" if will_delete else "✅ KEEP"
            quality_str = file['quality'].upper() if file['quality'] != 'Unknown' else 'N/A'
            res_str = file['resolution'].upper() if file['resolution'] != 'Unknown' else 'N/A'
            # Show all languages found
            lang_str = ", ".join([l.upper() for l in file['languages']])

            # Pre-format the text for this file
            file_text = f"\n{idx+1}. {status}\n  📄 {file['name'][:55]}...\n  Quality: {quality_str} | Res: {res_str} | Langs: {lang_str}\n"
            formatted_files.append(file_text)

        # Store in cache for pagination
        task_id = str(message.id)
        DRY_RUN_CACHE[task_id] = {
            'movie_name': movie_name,
            'files': formatted_files,
            'keep': len(files_info) - to_delete,
            'delete': to_delete
        }

        # Send the first page
        await send_dry_page(msg, task_id, 0)

        # ⏱️ START 5 MINUTE AUTO-DELETE TIMER (300 Seconds)
        asyncio.create_task(auto_delete_msg(msg, message, task_id, 300))

    except Exception as e:
        await message.reply_text(f"❌ Error: {str(e)}")


@Client.on_message(filters.command("cleanup_confirm_single") & filters.user(ADMINS))
async def cleanup_confirm_single_cmd(bot, message):
    try:
        if len(message.command) < 2:
            return await message.reply_text("❌ Usage: /cleanup_confirm_single <movie_name>")

        movie_name = " ".join(message.command[1:])
        msg = await message.reply_text(f"⚠️ **CONFIRMING DELETE**\n\nMovie: {movie_name}\n🗑️ Processing...\n\n⏳ Please wait...")

        base_title = get_base_title(movie_name)
        if not base_title:
            return await msg.edit_text(f"❌ Could not extract title from: {movie_name}")

        deleted_count, deleted_files = await cleanup_duplicates(db_collection=Media.collection, base_title=base_title, keep_highest_quality=True)

        if MULTIPLE_DB:
            d_count2, d_files2 = await cleanup_duplicates(db_collection=Media2.collection, base_title=base_title, keep_highest_quality=True)
            deleted_count += d_count2
            deleted_files.extend(d_files2)

        if deleted_count > 0:
            deleted_preview = ""
            for idx, file in enumerate(deleted_files[:8], 1):
                deleted_preview += f"{idx}. {file[:55]}\n"
            if len(deleted_files) > 8:
                deleted_preview += f"... + {len(deleted_files) - 8} more\n"

            report = (
                f"✅ **DELETE COMPLETED!**\n{'='*50}\n\n"
                f"🎬 Movie: {movie_name}\n"
                f"🗑️ Deleted: {deleted_count} files (Low Quality)\n\n"
                f"📋 **Deleted Files:**\n{deleted_preview}"
            )
        else:
            report = f"ℹ️ **No files deleted**\n\n🎬 Movie: {movie_name}\nReason: No low-quality duplicates found based on strict rules."
        await msg.edit_text(report)
    except Exception as e:
        await message.reply_text(f"❌ Error: {str(e)}")


async def process_dry_batch(collection, task_id, msg, cancel_markup, total_docs, p_state):
    movies = defaultdict(list)
    async for file in collection.find({}, projection={'file_name': 1}):
        if CANCEL_Q_TASKS.get(task_id):
            return False, 0, 0, []

        p_state['count'] += 1
        base_title = get_base_title(file.get('file_name', ''))
        if base_title:
            quality = extract_quality_info(file.get('file_name', ''))
            langs = extract_language(file.get('file_name', ''))
            movies[base_title].append({
                'name': file.get('file_name', ''),
                'quality': quality['source'],
                'languages': langs,
                'score': quality['quality_score']
            })

        if p_state['count'] % 500 == 0:
            await asyncio.sleep(0.1)

        if p_state['count'] % 5000 == 0:
            percent = (p_state['count'] / total_docs) * 100
            try:
                await msg.edit_text(
                    f"🔍 **DRY RUN - BATCH MODE**\n\n"
                    f"📁 Scanned: **{p_state['count']} / {total_docs}** files\n"
                    f"⏳ Progress: **{percent:.1f}%**\n"
                    f"⚙️ Status: Memory Safe Mode (Relaxed)\n\n"
                    f"*(Nothing will be deleted in dry run)*",
                    reply_markup=cancel_markup
                )
            except Exception:
                pass

    total_to_delete = 0
    duplicate_movies = []

    for base_title, files in movies.items():
        if len(files) > 1:
            to_delete = 0
            for f in files:
                if f['quality'] in LOW_QUALITY_SOURCES:
                    low_lang_set = set(f['languages'])
                    
                    for hq in files:
                        if hq['quality'] in HIGH_QUALITY_SOURCES:
                            high_lang_set = set(hq['languages'])
                            
                            # RULE 3: SUBSET CHECK
                            # LOW languages must be subset of HIGH languages
                            if low_lang_set <= high_lang_set:
                                to_delete += 1
                                break

            if to_delete > 0:
                total_to_delete += to_delete
                duplicate_movies.append({'title': base_title, 'count': len(files), 'to_delete': to_delete})

    return True, len(movies), total_to_delete, duplicate_movies


@Client.on_message(filters.command("cleanup_dry_batch") & filters.user(ADMINS))
async def cleanup_dry_batch_cmd(bot, message):
    try:
        task_id = str(message.id)
        CANCEL_Q_TASKS[task_id] = False
        cancel_markup = InlineKeyboardMarkup([[InlineKeyboardButton("🛑 CANCEL PROCESS", callback_data=f"cancel_q_task_{task_id}")]])

        msg = await message.reply_text("📊 Calculating total files...\n⏳ Please wait...", reply_markup=cancel_markup)

        total_docs = await Media.collection.estimated_document_count()
        if MULTIPLE_DB:
            total_docs += await Media2.collection.estimated_document_count()

        if total_docs == 0:
            return await msg.edit_text("❌ No files in database")

        p_state = {'count': 0}
        t_movies = 0
        total_del = 0
        duplicate_movies = []

        status1, mov1, del1, dup1 = await process_dry_batch(Media.collection, task_id, msg, cancel_markup, total_docs, p_state)
        if not status1:
            return await msg.edit_text("🛑 **Process Cancelled by Admin!**")
        t_movies += mov1; total_del += del1; duplicate_movies.extend(dup1)

        if MULTIPLE_DB:
            status2, mov2, del2, dup2 = await process_dry_batch(Media2.collection, task_id, msg, cancel_markup, total_docs, p_state)
            if not status2:
                return await msg.edit_text("🛑 **Process Cancelled by Admin!**")
            t_movies += mov2; total_del += del2; duplicate_movies.extend(dup2)

        duplicate_movies.sort(key=lambda x: x['to_delete'], reverse=True)

        report = f"📊 **DRY RUN - BATCH MODE**\n{'='*50}\n\n"
        report += f"📁 Total Files Scanned: {total_docs}\n🎬 Total Unique Movies: {t_movies}\n"
        report += f"📋 Movies with Low-Q Duplicates: {len(duplicate_movies)}\n\n"
        report += f"⚠️ **WOULD DELETE: {total_del} files**\n\n"

        if duplicate_movies:
            report += f"📋 **Top Movies with Duplicates:**\n{'─'*50}\n"
            for idx, movie in enumerate(duplicate_movies[:10], 1):
                report += f"{idx}. {movie['title'][:45]}\n   Versions: {movie['count']} | Will Delete: {movie['to_delete']}\n\n"
            if len(duplicate_movies) > 10:
                report += f"... + {len(duplicate_movies) - 10} more\n\n"
            report += f"{'─'*50}\n\n👉 Confirm & Delete ALL:\n`/cleanup_confirm_batch`"

        await msg.edit_text(report)
    except Exception as e:
        await message.reply_text(f"❌ Error: {str(e)}")


async def process_confirm_batch(collection, task_id, msg, cancel_markup, total_docs, p_state):
    movies = defaultdict(list)
    async for file in collection.find({}, projection={'_id': 1, 'file_name': 1}):
        if CANCEL_Q_TASKS.get(task_id):
            return False, 0, []

        p_state['count'] += 1
        base_title = get_base_title(file.get('file_name', ''))
        if base_title:
            quality = extract_quality_info(file.get('file_name', ''))
            langs = extract_language(file.get('file_name', ''))
            movies[base_title].append({
                'file_id': file['_id'], 
                'name': file.get('file_name', ''),
                'quality': quality['source'], 
                'languages': langs,
                'score': quality['quality_score']
            })

        if p_state['count'] % 500 == 0:
            await asyncio.sleep(0.1)

        if p_state['count'] % 5000 == 0:
            percent = (p_state['count'] / total_docs) * 100
            try:
                await msg.edit_text(
                    f"⚠️ **CONFIRMING DELETE - BATCH**\n\n"
                    f"🗑️ Processing safely...\n"
                    f"📁 Scanned: **{p_state['count']} / {total_docs}** files\n"
                    f"⏳ Progress: **{percent:.1f}%**",
                    reply_markup=cancel_markup
                )
            except Exception:
                pass

    total_deleted = 0
    movies_cleaned = 0
    deleted_files_list = []

    for base_title, files in movies.items():
        if len(files) > 1:
            cleaned_this_movie = False
            for f in files:
                if f['quality'] in LOW_QUALITY_SOURCES:
                    is_replaceable = False
                    low_lang_set = set(f['languages'])
                    
                    for hq in files:
                        if hq['quality'] in HIGH_QUALITY_SOURCES:
                            high_lang_set = set(hq['languages'])
                            
                            # RULE 3: SUBSET CHECK
                            # LOW languages must be subset of HIGH languages
                            if low_lang_set <= high_lang_set:
                                is_replaceable = True
                                break

                    if is_replaceable:
                        try:
                            await collection.delete_one({'_id': f['file_id']})
                            total_deleted += 1
                            deleted_files_list.append(f['name'])
                            cleaned_this_movie = True
                        except Exception:
                            pass

            if cleaned_this_movie:
                movies_cleaned += 1

    return True, movies_cleaned, deleted_files_list


@Client.on_message(filters.command("cleanup_confirm_batch") & filters.user(ADMINS))
async def cleanup_confirm_batch_cmd(bot, message):
    try:
        task_id = str(message.id)
        CANCEL_Q_TASKS[task_id] = False
        cancel_markup = InlineKeyboardMarkup([[InlineKeyboardButton("🛑 CANCEL PROCESS", callback_data=f"cancel_q_task_{task_id}")]])

        msg = await message.reply_text("📊 Calculating total files...\n⏳ Please wait...", reply_markup=cancel_markup)

        total_docs = await Media.collection.estimated_document_count()
        if MULTIPLE_DB:
            total_docs += await Media2.collection.estimated_document_count()

        p_state = {'count': 0}
        total_del = 0
        movies_clean = 0
        del_files = []

        status1, clean1, files1 = await process_confirm_batch(Media.collection, task_id, msg, cancel_markup, total_docs, p_state)
        if not status1:
            return await msg.edit_text("🛑 **Process Cancelled by Admin!**")
        movies_clean += clean1; del_files.extend(files1); total_del += len(files1)

        if MULTIPLE_DB:
            status2, clean2, files2 = await process_confirm_batch(Media2.collection, task_id, msg, cancel_markup, total_docs, p_state)
            if not status2:
                return await msg.edit_text("🛑 **Process Cancelled by Admin!**")
            movies_clean += clean2; del_files.extend(files2); total_del += len(files2)

        if total_del > 0:
            deleted_preview = ""
            for idx, file in enumerate(del_files[:8], 1):
                deleted_preview += f"{idx}. {file[:55]}\n"
            if len(del_files) > 8:
                deleted_preview += f"... + {len(del_files) - 8} more\n"

            report = (
                f"✅ **BATCH DELETE COMPLETED!**\n{'='*50}\n\n"
                f"🗑️ Total Deleted: {total_del} files\n"
                f"🎬 Movies Cleaned: {movies_clean}\n\n"
                f"📋 **Sample Deleted:**\n{deleted_preview}"
            )
        else:
            report = f"ℹ️ **No files deleted**\n\nAll files are already optimal quality based on rules."

        await msg.edit_text(report)
    except Exception as e:
        await message.reply_text(f"❌ Error: {str(e)}")
