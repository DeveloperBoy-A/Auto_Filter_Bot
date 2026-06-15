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

# =========================================================
# LANGUAGE PATTERNS
# =========================================================

LANGUAGE_PATTERNS = {
    "Hindi": [r'\bhindi\b', r'\bhin\b'],
    "English": [r'\benglish\b', r'\beng\b'],
    "Tamil": [r'\btamil\b', r'\btam\b'],
    "Telugu": [r'\btelugu\b', r'\btel\b'],
    "Malayalam": [r'\bmalayalam\b', r'\bmal\b'],
    "Kannada": [r'\bkannada\b', r'\bkan\b'],
    "Punjabi": [r'\bpunjabi\b', r'\bpan\b', r'\bpbi\b'],
    "Bengali": [r'\bbengali\b', r'\bben\b'],
    "Gujarati": [r'\bgujarati\b', r'\bguj\b', r'\bgujrat\b', r'\bgujrati\b'],
    "Marathi": [r'\bmarathi\b', r'\bmar\b'],
    "Korean": [r'\bkorean\b', r'\bkor\b', r'\bk[\s\-]?drama\b'],
    "Japanese": [r'\bjapanese\b', r'\bjap\b'],
    "Chinese": [r'\bchinese\b', r'\bmandarin\b', r'\bchi\b'],
    "Spanish": [r'\bspanish\b', r'\besp\b', r'\bspa\b'],
    "Russian": [r'\brussian\b', r'\brus\b'],
    "French": [r'\bfrench\b', r'\bfre\b', r'\bfra\b'],
    "Urdu": [r'\burdu\b'],
    "Bhojpuri": [r'\bbhojpuri\b', r'\bbho\b']
}

# =========================================================
# QUALITY PATTERNS
# =========================================================

QUALITY_PATTERNS = {
    "camrip": [
        r'\bcam[\s\-]?rip\b',
        r'\bcam\b',
        r'\bcinema\b'
    ],

    "hdcam": [
        r'\bhd[\s\-]?cam\b'
    ],

    "hdtc": [
        r'\bhd[\s\-]?tc\b',
        r'\btelecine\b'
    ],

    "hdts": [
        r'\bhd[\s\-]?ts\b',
        r'\bts\b',
        r'\btelesync\b'
    ],

    "predvd": [
        r'\bpre[\s\-]?dvd\b',
        r'\bpredvd\b'
    ],

    "dvdscr": [
        r'\bdvd[\s\-]?scr\b',
        r'\bscr\b'
    ],

    "dvdrip": [
        r'\bdvd[\s\-]?rip\b'
    ],

    "tvrip": [
        r'\btv[\s\-]?rip\b'
    ],

    "hdtv": [
        r'\bhd[\s\-]?tv\b'
    ],

    "webrip": [
        r'\bweb[\s\-]?rip\b'
    ],

    "web-dl": [
        r'\bweb[\s\-]?dl\b'
    ],

    "hdrip": [
        r'\bhd[\s\-]?rip\b'
    ],

    "bluray": [
        r'\bblu[\s\-]?ray\b',
        r'\bbdremux\b',
        r'\bbd[\s\-]?rip\b',
        r'\bbr[\s\-]?rip\b'
    ],

    "remux": [
        r'\bremux\b'
    ],

    "digital": [
        r'\bdigital\b'
    ]
}

# =========================================================
# QUALITY HIERARCHY
# =========================================================

QUALITY_HIERARCHY = {
    "camrip": 1,
    "hdcam": 2,
    "hdtc": 3,
    "hdts": 4,
    "predvd": 5,
    "dvdscr": 6,
    "dvdrip": 7,
    "tvrip": 8,
    "hdtv": 9,
    "webrip": 10,
    "web-dl": 11,
    "hdrip": 12,
    "bluray": 13,
    "remux": 14,
    "digital": 15
}

LOW_QUALITY_SOURCES = [
    "camrip",
    "hdcam",
    "hdtc",
    "hdts",
    "predvd",
    "dvdscr"
]

HIGH_QUALITY_SOURCES = [
    "webrip",
    "web-dl",
    "hdrip",
    "bluray",
    "remux",
    "digital"
]

# =========================================================
# MULTI LANGUAGE DETECTION
# =========================================================

def extract_languages(text: str) -> set:
    text = text.lower()
    found = set()

    for lang, patterns in LANGUAGE_PATTERNS.items():
        for pattern in patterns:
            if re.search(pattern, text):
                found.add(lang)
                break

    return found if found else {"Unknown"}

def extract_quality_info(filename: str, caption: str = "") -> dict:
    text = f"{filename} {caption}".lower()

    quality_info = {
        'source': None,
        'resolution': None,
        'quality_score': 0,
        'source_score': 0,
        'resolution_score': 0
    }

    # =========================
    # QUALITY DETECTION (PATTERN BASED)
    # =========================
    for source, patterns in QUALITY_PATTERNS.items():
        for pat in patterns:
            if re.search(pat, text):
                quality_info['source'] = source
                quality_info['source_score'] = QUALITY_HIERARCHY.get(source, 0)
                break
        if quality_info['source']:
            break

    # =========================
    # RESOLUTION DETECTION
    # =========================
    for res, score in RESOLUTION_HIERARCHY.items():
        pattern = rf'\b{re.escape(res)}\b'
        if re.search(pattern, text):
            quality_info['resolution'] = res
            quality_info['resolution_score'] = score
            break

    # =========================
    # FINAL SCORE CALCULATION
    # =========================
    quality_info['quality_score'] = (
        quality_info['source_score'] * 0.7
    ) + (
        quality_info['resolution_score'] * 0.3
    )

    return quality_info


def is_high_quality(quality_info: dict) -> bool:
    try:
        source = quality_info.get('source')
        if not source:
            return False
        return source in HIGH_QUALITY_SOURCES
    except Exception:
        return False

# =========================================================
# DELETE DECISION LOGIC (UPDATED STRICT VERSION)
# =========================================================

def should_delete_existing(
    existing_quality: dict,
    new_quality: dict,
    existing_lang: str,
    new_lang: str
) -> bool:
    try:
        existing_source = existing_quality.get('source')
        new_source = new_quality.get('source')

        # =========================
        # 1. SAFETY: Missing data
        # =========================
        if not existing_source or not new_source:
            return False

        # =========================
        # 2. HIGH QUALITY PROTECTION (IMPORTANT RULE)
        # =========================
        # Agar existing file already high quality hai → NEVER DELETE
        if existing_source in HIGH_QUALITY_SOURCES:
            return False

        # =========================
        # 3. LANGUAGE PROTECTION (MULTI-LANGUAGE SAFE)
        # =========================
        # Agar dono languages different hain aur unknown nahi hain → DO NOT DELETE
        if (
            existing_lang != new_lang and
            existing_lang != "unknown" and
            new_lang != "unknown"
        ):
            return False

        # =========================
        # 4. QUALITY REPLACEMENT RULE
        # =========================
        # Sirf tab delete hoga jab:
        # old = low quality AND new = high quality
        if (
            existing_source in LOW_QUALITY_SOURCES and
            new_source in HIGH_QUALITY_SOURCES
        ):
            return True

        return False

    except Exception as e:
        logger.error(f"Error in should_delete_existing: {e}")
        return False


def get_base_title(filename: str) -> str:
    text = filename.lower()

    # =========================
    # EXTENSION CLEANUP
    # =========================
    text = re.sub(r'\.(mkv|mp4|avi|mov|wmv|flv|webm)$', '', text, flags=re.I)

    # normalize separators
    text = re.sub(r'[._-]+', ' ', text)

    # =========================
    # REMOVE QUALITY + RESOLUTION TOKENS
    # =========================
    for quality in list(QUALITY_HIERARCHY.keys()) + list(RESOLUTION_HIERARCHY.keys()):
        text = re.sub(rf'\b{re.escape(quality)}\b', '', text, flags=re.I)

    # =========================
    # REMOVE TECH + AUDIO + TAGS + LANGUAGES + SOURCES
    # =========================
    text = re.sub(
        r'\b('
        r'hevc|x265|x264|h264|avc|av1|aac|flac|dts|ac3|eac3|ddp|ddp5\.1|dd5\.1|5\.1|7\.1|2\.0|'
        r'dub|sub|esub|esubs|multi|proper|uncut|'
        r'hindi|english|tamil|telugu|malayalam|kannada|punjabi|'
        r'bengali|marathi|gujarati|korean|japanese|chinese|spanish|russian|french|urdu|bhojpuri|'
        r'movies4u|tokyo_updates|telly|amzn|nf|dsnp'
        r')\b',
        '',
        text,
        flags=re.I
    )

    # =========================
    # REMOVE BRACKET CONTENT
    # =========================
    text = re.sub(r'[\[\(\{].*?[\]\)\}]', '', text)

    # =========================
    # CLEAN SPACES
    # =========================
    text = re.sub(r'\s+', ' ', text).strip()

    return text


# =========================================================
# MAIN CLEANUP FUNCTION (IMPROVED)
# =========================================================

async def find_and_delete_lower_quality(
    db_collection,
    new_filename: str,
    new_caption: str = "",
    file_id: Optional[str] = None
) -> Tuple[bool, str]:

    try:
        if not new_filename or not isinstance(new_filename, str):
            return False, "Invalid filename provided"

        # =========================
        # NEW FILE QUALITY
        # =========================
        new_quality = extract_quality_info(new_filename, new_caption or "")

        if not new_quality['source']:
            return True, "No quality info found in new file, skipping cleanup"

        # =========================
        # BASE TITLE EXTRACTION
        # =========================
        base_title = get_base_title(new_filename)

        if not base_title:
            return True, "Could not extract title for comparison"

        # =========================
        # SMART REGEX PATTERN
        # =========================
        try:
            words = [w for w in base_title.split() if len(w) > 1]
            pattern = (
                ".*".join([rf"\b{re.escape(w)}\b" for w in words[:5]])
                if words
                else re.escape(base_title)
            )
        except Exception as e:
            return False, f"Error building search pattern: {str(e)}"

        search_query = {
            'file_name': {'$regex': pattern, '$options': 'i'}
        }

        if file_id:
            search_query['_id'] = {'$ne': file_id}

        similar_files = await db_collection.find(search_query).to_list(None)

        deleted_count = 0
        kept_files = []

        # =========================
        # FILE COMPARISON LOOP
        # =========================
        for existing_file in similar_files:
            try:
                existing_filename = existing_file.get('file_name', '')
                existing_caption = existing_file.get('caption', '')

                existing_quality = extract_quality_info(
                    existing_filename,
                    existing_caption or ""
                )

                existing_lang = extract_language(
                    f"{existing_filename} {existing_caption or ''}"
                )
                new_lang = extract_language(
                    f"{new_filename} {new_caption or ''}"
                )

                # =========================
                # DECISION ENGINE
                # =========================
                if should_delete_existing(
                    existing_quality,
                    new_quality,
                    existing_lang,
                    new_lang
                ):
                    try:
                        await db_collection.delete_one({'_id': existing_file['_id']})
                        deleted_count += 1

                        logger.warning(
                            f"[QUALITY CLEANUP] DELETED:\n"
                            f"  📄 {existing_filename[:70]}\n"
                            f"  🎬 {existing_quality.get('source','N/A').upper()} | LANG: {existing_lang}\n"
                        )

                    except Exception as e:
                        logger.error(f"Delete error: {e}")

                else:
                    kept_files.append(existing_filename)

            except Exception:
                continue

        # =========================
        # RESULT
        # =========================
        if deleted_count > 0:
            return True, (
                f"✅ Cleanup completed:\n"
                f"🗑️ Deleted: {deleted_count} file(s)\n"
                f"✨ Kept: {len(kept_files)} file(s)"
            )

        return True, "ℹ️ No lower quality files to delete"

    except Exception as e:
        logger.error(f"[QUALITY CLEANUP ERROR] {e}", exc_info=True)
        return False, f"Error during quality cleanup: {str(e)}"


async def cleanup_duplicates(
    db_collection,
    base_title: str,
    keep_highest_quality: bool = True
) -> Tuple[int, List[str]]:

    try:
        # =========================
        # SMART SEARCH PATTERN
        # =========================
        words = [w for w in base_title.split() if len(w) > 1]

        pattern = (
            ".*".join([rf"\b{re.escape(w)}\b" for w in words[:5]])
            if words
            else re.escape(base_title)
        )

        search_query = {
            'file_name': {'$regex': pattern, '$options': 'i'}
        }

        files = await db_collection.find(search_query).to_list(None)

        if len(files) <= 1:
            return 0, []

        # =========================
        # SCORING PHASE
        # =========================
        scored_files = []

        for file in files:
            file_name = file.get('file_name', '')
            caption = file.get('caption', '')

            quality = extract_quality_info(file_name, caption)
            lang = extract_language(f"{file_name} {caption}")

            scored_files.append({
                'file': file,
                'quality': quality,
                'language': lang,
                'score': quality['quality_score']
            })

        deleted_count = 0
        deleted_files = []

        # =========================
        # KEEP BEST QUALITY LOGIC
        # =========================
        if keep_highest_quality:

            for low_file in scored_files:

                low_source = low_file['quality'].get('source')

                # only process low quality files
                if low_source in LOW_QUALITY_SOURCES:

                    is_replaceable = False

                    for high_file in scored_files:

                        high_source = high_file['quality'].get('source')

                        # high quality must exist
                        if high_source in HIGH_QUALITY_SOURCES:

                            lang_low = low_file['language']
                            lang_high = high_file['language']

                            # =========================
                            # LANGUAGE SAFE CHECK
                            # =========================
                            if (
                                lang_low == lang_high or
                                lang_low == "unknown" or
                                lang_high == "unknown"
                            ):
                                is_replaceable = True
                                break

                    # =========================
                    # DELETE CONDITION
                    # =========================
                    if is_replaceable:

                        try:
                            file_to_delete = low_file['file'].get('file_name', 'Unknown')

                            await db_collection.delete_one({
                                '_id': low_file['file']['_id']
                            })

                            deleted_count += 1
                            deleted_files.append(file_to_delete)

                            logger.info(
                                f"[CLEANUP] DELETED LOW QUALITY:\n"
                                f"📄 {file_to_delete}\n"
                                f"🎬 {low_source} | LANG: {low_file['language']}"
                            )

                        except Exception as e:
                            logger.error(f"[CLEANUP] Delete error: {e}")

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
    await query.answer(
        "🛑 Cancelling Process... Please wait a moment for the loop to stop.",
        show_alert=True
    )


# --- AUTO DELETE FUNCTION (5 MIN) ---
async def auto_delete_msg(msg, command_msg, task_id, delay=300):
    try:
        await asyncio.sleep(delay)

        # cache cleanup
        DRY_RUN_CACHE.pop(task_id, None)

        await msg.delete()
        await command_msg.delete()

    except Exception:
        pass


# --- PAGINATION HANDLER ---
async def send_dry_page(msg, task_id, page):
    data = DRY_RUN_CACHE.get(task_id)

    if not data:
        try:
            if hasattr(msg, "edit_text"):
                return await msg.edit_text(
                    "❌ Data expired or auto-deleted. Please run the command again."
                )
            return await msg.message.edit_text(
                "❌ Data expired or auto-deleted. Please run the command again."
            )
        except Exception:
            return

    ITEMS_PER_PAGE = 15
    total_files = len(data["files"])
    total_pages = math.ceil(total_files / ITEMS_PER_PAGE) if total_files else 1

    start_idx = page * ITEMS_PER_PAGE
    end_idx = start_idx + ITEMS_PER_PAGE
    chunk = data["files"][start_idx:end_idx]

    report = (
        f"📊 **DRY RUN - SINGLE MOVIE**\n"
        f"{'='*50}\n\n"
        f"🎬 Movie: {data['movie_name']}\n"
        f"📁 Found: {total_files} files\n\n"
        f"📋 **File Details (Page {page+1}/{total_pages}):**\n"
        f"{'─'*50}\n"
    )

    for f_text in chunk:
        report += f_text

    report += (
        f"\n{'─'*50}\n"
        f"⚠️ **PREVIEW SUMMARY:**\n"
        f"✅ Will KEEP: {data['keep']}\n"
        f"❌ Will DELETE: {data['delete']}\n\n"
    )

    if data["delete"] > 0:
        report += (
            f"👉 Confirm & Delete:\n"
            f"`/cleanup_confirm_single {data['movie_name']}`\n\n"
        )
    else:
        report += "ℹ️ No files to delete (Rules applied)\n\n"

    report += "⏱️ *This message will auto-delete in 5 mins.*"

    buttons = []

    if page > 0:
        buttons.append(
            InlineKeyboardButton(
                "⬅️ Previous",
                callback_data=f"dry_page_{task_id}_{page-1}"
            )
        )

    if page < total_pages - 1:
        buttons.append(
            InlineKeyboardButton(
                "Next ➡️",
                callback_data=f"dry_page_{task_id}_{page+1}"
            )
        )

    reply_markup = InlineKeyboardMarkup([buttons]) if buttons else None

    try:
        if hasattr(msg, "edit_text"):
            await msg.edit_text(report, reply_markup=reply_markup)
        else:
            await msg.message.edit_text(report, reply_markup=reply_markup)

    except Exception as e:
        logger.error(f"Error editing pagination message: {e}")


@Client.on_callback_query(filters.regex(r"^dry_page_"))
async def dry_page_callback(client, query):
    try:
        parts = query.data.split("_")
        task_id = parts[2]
        page = int(parts[3])

        await send_dry_page(query, task_id, page)
        await query.answer()

    except Exception as e:
        logger.error(f"Pagination callback error: {e}")
        await query.answer("Error occurred", show_alert=True)

@Client.on_message(filters.command("quality_report") & filters.user(ADMINS))
async def quality_report_cmd(bot, message):
    try:
        task_id = str(message.id)
        CANCEL_Q_TASKS[task_id] = False

        cancel_markup = InlineKeyboardMarkup([[
            InlineKeyboardButton(
                "🛑 CANCEL PROCESS",
                callback_data=f"cancel_q_task_{task_id}"
            )
        ]])

        msg = await message.reply_text(
            "📊 Calculating total files...\n⏳ Please wait...",
            reply_markup=cancel_markup
        )

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

            async for file in collection.find({}, projection={"file_name": 1}):

                if CANCEL_Q_TASKS.get(task_id):
                    return False

                processed += 1
                file_name = file.get("file_name", "")

                quality_info = extract_quality_info(file_name)

                quality_dist[quality_info.get("source") or "unknown"] += 1
                resolution_dist[quality_info.get("resolution") or "unknown"] += 1

                if processed % 500 == 0:
                    await asyncio.sleep(0.1)

                if processed % 5000 == 0:
                    percent = (processed / total_docs) * 100
                    try:
                        await msg.edit_text(
                            "📊 **Generating Quality Report...**\n\n"
                            f"📁 Scanned: **{processed} / {total_docs}** files\n"
                            f"⏳ Progress: **{percent:.1f}%**\n"
                            "⚙️ Status: Memory Safe Mode",
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

        report = (
            "📊 **QUALITY REPORT (DB1 & DB2)**\n"
            f"{'='*50}\n\n"
            f"📁 **Total Files:** {total_docs}\n\n"
            "🎬 **Source Quality Distribution:**\n"
            f"{'─'*50}\n"
        )

        quality_order = [
            'camrip','hdcam','hdtc','hdts','ts','tc','predvd','dvdscr',
            'dvdrip','tvrip','hdtv','webrip','web-dl','webdl',
            'hdrip','bluray','bdrip','brrip','unknown'
        ]

        for q in quality_order:
            count = quality_dist.get(q, 0)
            if count > 0:
                percent = (count / total_docs) * 100

                if q in LOW_QUALITY_SOURCES:
                    emoji = "⚠️"
                elif q in HIGH_QUALITY_SOURCES:
                    emoji = "✨"
                else:
                    emoji = "⭐"

                report += f"{emoji} {q.upper()}: {count} ({percent:.1f}%)\n"

        report += "\n📐 **Resolution Distribution:**\n"
        report += "─" * 50 + "\n"

        for r in ['240p','360p','480p','540p','720p','1080p','1440p','2160p','unknown']:
            count = resolution_dist.get(r, 0)
            if count > 0:
                percent = (count / total_docs) * 100
                report += f"📹 {r}: {count} ({percent:.1f}%)\n"

        low_count = sum(quality_dist.get(q, 0) for q in LOW_QUALITY_SOURCES)
        report += f"\n⚠️ **Low Quality Count:** {low_count} files\n"

        await msg.edit_text(report)

    except Exception as e:
        await message.reply_text(f"❌ Error: {str(e)}")


# =========================================================
# --- DRY RUN SINGLE MOVIE CLEANUP ---
# =========================================================

@Client.on_message(filters.command("cleanup_dry_single") & filters.user(ADMINS))
async def cleanup_dry_single_cmd(bot, message):
    try:
        if len(message.command) < 2:
            return await message.reply_text(
                "❌ Usage: /cleanup_dry_single <movie_name>"
            )

        movie_name = " ".join(message.command[1:])
        msg = await message.reply_text(
            f"🔍 **DRY RUN - SINGLE**\n\n🎬 Movie: {movie_name}\n⏳ Scanning..."
        )

        base_title = get_base_title(movie_name)
        if not base_title:
            return await msg.edit_text("❌ Could not extract title")

        words = [w for w in base_title.split() if len(w) > 1]
        search_pattern = (
            ".*".join([rf"\b{re.escape(w)}\b" for w in words[:5]])
            if words else re.escape(base_title)
        )

        similar_files = await Media.collection.find(
            {"file_name": {"$regex": search_pattern, "$options": "i"}}
        ).to_list(None)

        if MULTIPLE_DB:
            similar_files2 = await Media2.collection.find(
                {"file_name": {"$regex": search_pattern, "$options": "i"}}
            ).to_list(None)
            similar_files.extend(similar_files2)

        if not similar_files:
            return await msg.edit_text("❌ No files found")

        files_info = []

        for file in similar_files:
            name = file.get("file_name", "")
            q = extract_quality_info(name)
            lang = extract_language(name)

            files_info.append({
                "name": name,
                "quality": q["source"] or "unknown",
                "resolution": q["resolution"] or "unknown",
                "language": lang,
                "score": q["quality_score"]
            })

        files_info.sort(key=lambda x: x["score"], reverse=True)

        formatted_files = []
        to_delete = 0

        for idx, file in enumerate(files_info):

            will_delete = False

            if file["quality"] in LOW_QUALITY_SOURCES:
                for hq in files_info:
                    if hq["quality"] in HIGH_QUALITY_SOURCES:
                        if (
                            file["language"] == hq["language"]
                            or file["language"] == "unknown"
                            or hq["language"] == "unknown"
                        ):
                            will_delete = True
                            break

            if will_delete:
                to_delete += 1

            status = "❌ DELETE (Low Q)" if will_delete else "✅ KEEP"

            file_text = (
                f"\n{idx+1}. {status}\n"
                f"📄 {file['name'][:55]}\n"
                f"Quality: {file['quality'].upper()} | "
                f"Res: {file['resolution'].upper()} | "
                f"Lang: {file['language'].upper()}\n"
            )

            formatted_files.append(file_text)

        task_id = str(message.id)

        DRY_RUN_CACHE[task_id] = {
            "movie_name": movie_name,
            "files": formatted_files,
            "keep": len(files_info) - to_delete,
            "delete": to_delete
        }

        await send_dry_page(msg, task_id, 0)

        asyncio.create_task(
            auto_delete_msg(msg, message, task_id, 300)
        )

    except Exception as e:
        await message.reply_text(f"❌ Error: {str(e)}")


# =========================================================
# --- SINGLE CONFIRM DELETE ---
# =========================================================

@Client.on_message(filters.command("cleanup_confirm_single") & filters.user(ADMINS))
async def cleanup_confirm_single_cmd(bot, message):
    try:
        if len(message.command) < 2:
            return await message.reply_text(
                "❌ Usage: /cleanup_confirm_single <movie_name>"
            )

        movie_name = " ".join(message.command[1:])
        msg = await message.reply_text(
            f"⚠️ **CONFIRMING DELETE**\n\n🎬 Movie: {movie_name}\n🗑️ Processing...\n⏳ Please wait..."
        )

        base_title = get_base_title(movie_name)
        if not base_title:
            return await msg.edit_text("❌ Could not extract title")

        deleted_count, deleted_files = await cleanup_duplicates(
            db_collection=Media.collection,
            base_title=base_title,
            keep_highest_quality=True
        )

        if MULTIPLE_DB:
            d_count2, d_files2 = await cleanup_duplicates(
                db_collection=Media2.collection,
                base_title=base_title,
                keep_highest_quality=True
            )
            deleted_count += d_count2
            deleted_files.extend(d_files2)

        if deleted_count > 0:
            preview = "\n".join(
                f"{i+1}. {f[:55]}"
                for i, f in enumerate(deleted_files[:8])
            )

            if len(deleted_files) > 8:
                preview += f"\n... + {len(deleted_files)-8} more"

            report = (
                "✅ **DELETE COMPLETED!**\n"
                f"{'='*50}\n\n"
                f"🎬 Movie: {movie_name}\n"
                f"🗑️ Deleted: {deleted_count} files\n\n"
                f"📋 **Deleted Files:**\n{preview}"
            )
        else:
            report = (
                "ℹ️ **No files deleted**\n\n"
                f"🎬 Movie: {movie_name}\n"
                "Reason: Already optimal quality"
            )

        await msg.edit_text(report)

    except Exception as e:
        await message.reply_text(f"❌ Error: {str(e)}")


# =========================================================
# --- DRY BATCH PROCESSOR ---
# =========================================================

async def process_dry_batch(collection, task_id, msg, cancel_markup, total_docs, p_state):
    movies = defaultdict(list)

    async for file in collection.find({}, projection={"file_name": 1}):

        if CANCEL_Q_TASKS.get(task_id):
            return False, 0, 0, []

        p_state["count"] += 1
        base_title = get_base_title(file.get("file_name", ""))

        if base_title:
            quality = extract_quality_info(file.get("file_name", ""))
            lang = extract_language(file.get("file_name", ""))

            movies[base_title].append({
                "name": file.get("file_name", ""),
                "quality": quality["source"],
                "language": lang,
                "score": quality["quality_score"]
            })

        if p_state["count"] % 500 == 0:
            await asyncio.sleep(0.1)

        if p_state["count"] % 5000 == 0:
            percent = (p_state["count"] / total_docs) * 100
            try:
                await msg.edit_text(
                    "🔍 **DRY RUN - BATCH MODE**\n\n"
                    f"📁 Scanned: **{p_state['count']} / {total_docs}**\n"
                    f"⏳ Progress: **{percent:.1f}%**\n"
                    "⚙️ Status: Safe Mode (No Delete)",
                    reply_markup=cancel_markup
                )
            except Exception:
                pass

    total_to_delete = 0
    duplicate_movies = []

    for title, files in movies.items():

        if len(files) <= 1:
            continue

        to_delete = 0

        for f in files:
            if f["quality"] in LOW_QUALITY_SOURCES:

                for hq in files:
                    if hq["quality"] in HIGH_QUALITY_SOURCES:

                        if (
                            f["language"] == hq["language"]
                            or f["language"] == "unknown"
                            or hq["language"] == "unknown"
                        ):
                            to_delete += 1
                            break

        if to_delete > 0:
            total_to_delete += to_delete
            duplicate_movies.append({
                "title": title,
                "count": len(files),
                "to_delete": to_delete
            })

    return True, len(movies), total_to_delete, duplicate_movies


# =========================================================
# --- DRY BATCH COMMAND ---
# =========================================================

@Client.on_message(filters.command("cleanup_dry_batch") & filters.user(ADMINS))
async def cleanup_dry_batch_cmd(bot, message):
    try:
        task_id = str(message.id)
        CANCEL_Q_TASKS[task_id] = False

        cancel_markup = InlineKeyboardMarkup([[
            InlineKeyboardButton("🛑 CANCEL PROCESS", callback_data=f"cancel_q_task_{task_id}")
        ]])

        msg = await message.reply_text(
            "📊 Calculating total files...\n⏳ Please wait...",
            reply_markup=cancel_markup
        )

        total_docs = await Media.collection.estimated_document_count()
        if MULTIPLE_DB:
            total_docs += await Media2.collection.estimated_document_count()

        if total_docs == 0:
            return await msg.edit_text("❌ No files in database")

        p_state = {"count": 0}
        t_movies = 0
        total_del = 0
        duplicate_movies = []

        status1, mov1, del1, dup1 = await process_dry_batch(
            Media.collection, task_id, msg, cancel_markup, total_docs, p_state
        )

        if not status1:
            return await msg.edit_text("🛑 Cancelled")

        t_movies += mov1
        total_del += del1
        duplicate_movies.extend(dup1)

        if MULTIPLE_DB:
            status2, mov2, del2, dup2 = await process_dry_batch(
                Media2.collection, task_id, msg, cancel_markup, total_docs, p_state
            )

            if not status2:
                return await msg.edit_text("🛑 Cancelled")

            t_movies += mov2
            total_del += del2
            duplicate_movies.extend(dup2)

        duplicate_movies.sort(key=lambda x: x["to_delete"], reverse=True)

        report = (
            "📊 **DRY RUN - BATCH MODE**\n"
            f"{'='*50}\n\n"
            f"📁 Files Scanned: {total_docs}\n"
            f"🎬 Movies Found: {t_movies}\n"
            f"📋 Duplicate Movies: {len(duplicate_movies)}\n\n"
            f"⚠️ Would Delete: {total_del} files\n\n"
        )

        if duplicate_movies:
            report += "📋 **Top Movies:**\n" + ("─"*50) + "\n"

            for i, m in enumerate(duplicate_movies[:10], 1):
                report += (
                    f"{i}. {m['title'][:45]}\n"
                    f"   Versions: {m['count']} | Delete: {m['to_delete']}\n\n"
                )

            if len(duplicate_movies) > 10:
                report += f"... + {len(duplicate_movies)-10} more\n\n"

            report += "👉 `/cleanup_confirm_batch`"

        await msg.edit_text(report)

    except Exception as e:
        await message.reply_text(f"❌ Error: {str(e)}")


# =========================================================
# --- CONFIRM BATCH DELETE ---
# =========================================================

async def process_confirm_batch(collection, task_id, msg, cancel_markup, total_docs, p_state):
    movies = defaultdict(list)

    async for file in collection.find({}, projection={"_id": 1, "file_name": 1}):

        if CANCEL_Q_TASKS.get(task_id):
            return False, 0, []

        p_state["count"] += 1
        base_title = get_base_title(file.get("file_name", ""))

        if base_title:
            quality = extract_quality_info(file.get("file_name", ""))
            lang = extract_language(file.get("file_name", ""))

            movies[base_title].append({
                "file_id": file["_id"],
                "name": file.get("file_name", ""),
                "quality": quality["source"],
                "language": lang,
                "score": quality["quality_score"]
            })

        if p_state["count"] % 5000 == 0:
            percent = (p_state["count"] / total_docs) * 100
            try:
                await msg.edit_text(
                    "⚠️ **CONFIRMING DELETE**\n\n"
                    f"📁 Scanned: {p_state['count']} / {total_docs}\n"
                    f"⏳ Progress: {percent:.1f}%"
                )
            except Exception:
                pass

    total_deleted = 0
    movies_cleaned = 0
    deleted_files = []

    for title, files in movies.items():

        cleaned = False

        for f in files:

            if f["quality"] in LOW_QUALITY_SOURCES:

                for hq in files:
                    if hq["quality"] in HIGH_QUALITY_SOURCES:

                        if (
                            f["language"] == hq["language"]
                            or f["language"] == "unknown"
                            or hq["language"] == "unknown"
                        ):
                            try:
                                await collection.delete_one({"_id": f["file_id"]})
                                total_deleted += 1
                                deleted_files.append(f["name"])
                                cleaned = True
                            except:
                                pass
                            break

        if cleaned:
            movies_cleaned += 1

    return True, movies_cleaned, deleted_files


# =========================================================
# --- CONFIRM BATCH COMMAND ---
# =========================================================

@Client.on_message(filters.command("cleanup_confirm_batch") & filters.user(ADMINS))
async def cleanup_confirm_batch_cmd(bot, message):
    try:
        task_id = str(message.id)
        CANCEL_Q_TASKS[task_id] = False

        cancel_markup = InlineKeyboardMarkup([[
            InlineKeyboardButton("🛑 CANCEL PROCESS", callback_data=f"cancel_q_task_{task_id}")
        ]])

        msg = await message.reply_text("📊 Calculating...\n⏳ Please wait...", reply_markup=cancel_markup)

        total_docs = await Media.collection.estimated_document_count()
        if MULTIPLE_DB:
            total_docs += await Media2.collection.estimated_document_count()

        p_state = {"count": 0}
        movies_clean = 0
        total_del = 0
        del_files = []

        s1, c1, f1 = await process_confirm_batch(Media.collection, task_id, msg, cancel_markup, total_docs, p_state)
        if not s1:
            return await msg.edit_text("🛑 Cancelled")

        movies_clean += c1
        del_files.extend(f1)
        total_del += len(f1)

        if MULTIPLE_DB:
            s2, c2, f2 = await process_confirm_batch(Media2.collection, task_id, msg, cancel_markup, total_docs, p_state)
            if not s2:
                return await msg.edit_text("🛑 Cancelled")

            movies_clean += c2
            del_files.extend(f2)
            total_del += len(f2)

        if total_del > 0:
            preview = "\n".join(f"{i+1}. {f[:55]}" for i, f in enumerate(del_files[:8]))
            if len(del_files) > 8:
                preview += f"\n... + {len(del_files)-8} more"

            report = (
                "✅ **BATCH CLEANED**\n"
                f"{'='*50}\n\n"
                f"🗑️ Deleted: {total_del}\n"
                f"🎬 Movies Cleaned: {movies_clean}\n\n"
                f"{preview}"
            )
        else:
            report = "ℹ️ Nothing to delete"

        await msg.edit_text(report)

    except Exception as e:
        await message.reply_text(f"❌ Error: {str(e)}")