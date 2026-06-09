import re
import os
import time
import logging
import asyncio
from pyrogram import Client, filters
from pyrogram.errors import FloodWait
from pyrogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery

# Tumhare main database aur info file se imports
from database.ia_filterdb import (
    Media, Media2,
    extract_pure_title,
    extract_languages_quality,
    RELEASE_TAG,
    MULTIPLE_DB,
)
from info import ADMINS

logger = logging.getLogger(__name__)

# ── Per-admin cancel flag ─────────────────────────────────────
_cancel_flags: dict[int, bool] = {}

# ── Inline buttons ────────────────────────────────────────────
def _cancel_button() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton("🛑 Cancel", callback_data="rename_db_cancel")]]
    )

def _done_button() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton("✅ Done", callback_data="rename_db_done")]]
    )

# ── build_new_name: Naye save_file ka EXACT sequence logic ────
def build_new_name(doc) -> str | None:
    original_name: str = doc.get("file_name") or "Unnamed File"
    base_name, ext = os.path.splitext(original_name)
    caption_text: str = doc.get("caption") or ""

    text_to_scan = f"{original_name} {caption_text}"

    # Import kiye gaye updated functions ka use ho raha hai
    extracted = extract_languages_quality(text_to_scan)

    # --- SMART AUTO-ADD LOGIC ---
    # Agar Resolution nahi mili, to automatically 720P add karo
    if not extracted.get("resolution"):
        extracted["resolution"] = "720P"

    # Agar Source nahi mila, to WEB-DL add karo
    if not extracted.get("source"):
        extracted["source"] = "WEB-DL"

    # Audio Codec Check - Sirf tabhi AAC add karo jab koi audio tag na ho
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

    cleaned_title = extract_pure_title(base_name)

    # Title ko proper Case mein format karna
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

    # ==========================================
    #      STRICT SEQUENCE ASSEMBLER (1 to 18)
    # ==========================================
    
    # [1] Title
    if final_title: add_unique(final_title)

    # [2] Title Part / Volume / Chapter
    if extracted.get("title_part"): add_unique(extracted["title_part"])

    # [3] Season & Episode
    if extracted.get("season_episode"): add_unique(extracted["season_episode"])

    # [4] Episode Title
    if extracted.get("season_episode") and extracted.get("episode_title"):
        add_unique(extracted["episode_title"])

    # [5] Series Status
    if extracted.get("series_status"): add_unique(extracted["series_status"])

    # [6] Release Year
    if extracted.get("year"): add_unique(extracted["year"])

    # [7] Video Resolution
    if extracted.get("resolution"): add_unique(extracted["resolution"])

    # [8] Audio Languages
    for lang in extracted.get("languages", []): add_unique(lang)

    # [9] Custom Qualifiers
    for qual in extracted.get("custom_qualifiers", []): add_unique(qual)

    # [10] Color Depth / HDR
    for tag in ["10Bit", "12Bit", "SDR", "HDR", "Dolby Vision", "IMAX", "60FPS"]:
        if tag in extracted.get("extra_tags", []): add_unique(tag)

    # [11] OTT Platform Tag
    if extracted.get("ott") and extracted["ott"] not in parts:
        parts.append(extracted["ott"])

    # [12] Source Type
    if extracted.get("source"): add_unique(extracted["source"])

    # [13] Video Codec
    for vcodec in ["AV1", "HEVC X265", "AVC X264"]:
        if vcodec in extracted.get("extra_tags", []): add_unique(vcodec)

    # [14] Audio Codec & Channels (Smart Overlap Handler)
    audio_tags = extracted.get("extra_tags", [])
    if "DDP 5.1" in audio_tags and "DD 5.1" in audio_tags:
        audio_tags.remove("DD 5.1")
    if "DDP 7.1" in audio_tags and "DD 5.1" in audio_tags:
        audio_tags.remove("DD 5.1")
    if "AAC 5.1" in audio_tags and "AAC" in audio_tags:
        audio_tags.remove("AAC")

    for acodec in ["Dolby TrueHD", "Dolby Atmos", "DTS-X", "DTS-HD", "DDP 7.1", "DDP 5.1", "DD 5.1", "DD 2.0", "DTS 5.1", "AAC 5.1", "AAC"]:
        if acodec in audio_tags: 
            add_unique(acodec)

    # [15] Subtitles
    for sub in ["ESubs", "HardSubs", "MSubs"]:
        if sub in extracted.get("extra_tags", []): add_unique(sub)

    # [16] Audio Bitrate
    if extracted.get("kbps"): add_unique(extracted["kbps"])

    # [17] File Split Part (e.g. part001)
    if extracted.get("split_part"): add_unique(extracted["split_part"])

    # [18] Branding Signature
    parts = [p for p in parts if p and "Tokyo_Updates" not in str(p)]
    parts.append(RELEASE_TAG)

    # Final Assembly
    file_name = " ".join(map(str, parts)).strip()
    file_name = re.sub(r'\s+', ' ', file_name)
    file_name = file_name + ext.lower()
    file_name = re.sub(r'\s+\.', '.', file_name)

    return file_name if file_name != original_name else None


# ── Background Process ───────────────────────────────────────────
async def process_rename_db(client: Client, status_msg: Message, user_id: int, dry_run: bool):
    mode_label = "🔍 DRY-RUN (preview only)" if dry_run else "✏️ LIVE UPDATE"
    # ✅ Command received log
    logger.info(f"Command received: /rename_db | Mode: {mode_label} | User: {user_id}")
    
    updated = 0
    skipped = 0
    errors = 0
    total = 0
    cancelled = False

    collections_to_process = [("Primary DB", Media.collection)]
    if MULTIPLE_DB:
        collections_to_process.append(("Secondary DB", Media2.collection))

    # ✅ Process start log
    logger.info("✅ Process start log: Starting batch processing.")
    last_edit_time = time.time()

    for db_label, collection in collections_to_process:
        if cancelled:
            break

        # Total files count for percentage
        total_docs = await collection.count_documents({})
        if total_docs == 0:
            continue

        cursor = collection.find({}, {"_id": 1, "file_name": 1, "caption": 1})

        async for doc in cursor:
            # ✅ Cancel requested log (Check)
            if _cancel_flags.get(user_id):
                logger.warning(f"⚠️ Cancel requested log: Process stopped by user {user_id}")
                cancelled = True
                break

            total += 1
            try:
                old_name = doc.get("file_name")
                new_name = build_new_name(doc)

                if new_name is None:
                    skipped += 1
                else:
                    # ✅ OLD → NEW filename log
                    logger.info(f"🔄 OLD → NEW filename log | DB: {db_label} | OLD: {old_name} → NEW: {new_name}")
                    if not dry_run:
                        await collection.update_one(
                            {"_id": doc["_id"]},
                            {"$set": {"file_name": new_name}}
                        )
                    updated += 1

            except Exception as e:
                # ✅ Error log with traceback
                errors += 1
                logger.error(f"❌ Error log with traceback: DB: {db_label} | _id={doc.get('_id')} | {e}", exc_info=True)

            # Bot ko background processes sambhalne ke liye thodi der saans lene do
            if total % 100 == 0:
                await asyncio.sleep(0.05)

            # ✅ Progress log (every 500 files)
            if total % 500 == 0:
                logger.info(f"📊 Progress log: {total} files processed. (DB: {db_label} | Updated: {updated}, Skipped: {skipped}, Errors: {errors})")

            # FloodWait Protection: Har 5 seconds me hi Telegram API par message update karo
            if time.time() - last_edit_time > 5:
                percentage = (total / total_docs) * 100 if total_docs > 0 else 0
                progress_text = (
                    f"<b>{mode_label}</b>\n\n"
                    f"📁 <b>{db_label}</b>\n"
                    f"📊 Progress : <b>{percentage:.2f}%</b> ({total}/{total_docs})\n"
                    f"✅ Renamed  : <b>{updated}</b>\n"
                    f"⏭️ Skipped  : <b>{skipped}</b>\n"
                    f"❌ Errors   : <b>{errors}</b>"
                )
                try:
                    await status_msg.edit_text(progress_text, reply_markup=_cancel_button())
                    last_edit_time = time.time()
                except FloodWait as e:
                    logger.warning(f"FloodWait of {e.value} seconds encountered. Sleeping...")
                    await asyncio.sleep(e.value)
                except Exception:
                    pass

    # ── Final report ─────────────────────────────────────────
    _cancel_flags.pop(user_id, None)

    if cancelled:
        # ✅ Cancel completed log
        logger.info("✅ Cancel completed log: Process aborted successfully.")
        await status_msg.edit_text(
            f"<b>🛑 Cancelled by admin</b>\n\n"
            f"📊 Processed : <b>{total}</b>\n"
            f"✏️ {'Would rename' if dry_run else 'Renamed'} : <b>{updated}</b>\n"
            f"⏭️ No change  : <b>{skipped}</b>\n"
            f"❌ Errors     : <b>{errors}</b>",
            reply_markup=None
        )
        return

    # ✅ Final completion summary log
    logger.info(f"✅ Final completion summary log: Total: {total}, Updated: {updated}, Skipped: {skipped}, Errors: {errors}")
    action_word = "Would rename" if dry_run else "Renamed"
    footer = (
        "\n\n⚠️ <i>Ye sirf preview tha. Actual rename karne ke liye\n"
        "<code>/rename_db confirm</code> bhejo.</i>"
        if dry_run else
        "\n\n✅ <i>Sabhi files successfully rename ho gayi hain.</i>"
    )

    try:
        await status_msg.edit_text(
            f"<b>{mode_label} — Complete ✅</b>\n\n"
            f"📊 Total scanned : <b>{total}</b>\n"
            f"✏️ {action_word}   : <b>{updated}</b>\n"
            f"⏭️ No change     : <b>{skipped}</b>\n"
            f"❌ Errors        : <b>{errors}</b>"
            + footer,
            reply_markup=_done_button()
        )
    except Exception as e:
        logger.error(f"Final status update error: {e}")

# ============================================================
# /rename_db command
# ============================================================

@Client.on_message(filters.command("rename_db") & filters.user(ADMINS))
async def rename_db_files(client: Client, message: Message):
    """
    Sirf ADMINS use kar sakte hain.
      /rename_db          → dry-run (preview only)
      /rename_db confirm  → actual DB update (runs in background)
    """
    args = message.text.split()
    dry_run = not (len(args) > 1 and args[1].lower() == "confirm")
    user_id = message.from_user.id

    # Agar pehle se koi process chal rahi hai to block kar do
    if _cancel_flags.get(user_id) is False:
        await message.reply_text("⏳ Ek DB rename process already background mein chal rahi hai!")
        return

    _cancel_flags[user_id] = False
    mode_label = "🔍 DRY-RUN (preview only)" if dry_run else "✏️ LIVE UPDATE"

    status_msg = await message.reply_text(
        f"<b>{mode_label}</b>\n\nDB scan shuru ho raha hai background mein... ⏳",
        reply_markup=_cancel_button()
    )

    # Bot ko block kiye bina background me task start kar do
    asyncio.create_task(process_rename_db(client, status_msg, user_id, dry_run))


# ── Cancel callback ───────────────────────────────────────────
@Client.on_callback_query(filters.regex("^rename_db_cancel$") & filters.user(ADMINS))
async def rename_db_cancel_cb(client: Client, query: CallbackQuery):
    _cancel_flags[query.from_user.id] = True
    await query.answer("🛑 Cancel signal bheja gaya! Task ruk raha hai...", show_alert=True)
    try:
        await query.message.edit_reply_markup(reply_markup=None)
    except Exception:
        pass

# ── Done button (dismiss) ─────────────────────────────────────
@Client.on_callback_query(filters.regex("^rename_db_done$") & filters.user(ADMINS))
async def rename_db_done_cb(client: Client, query: CallbackQuery):
    await query.answer("👍 Done!")
    try:
        await query.message.delete()
    except Exception:
        pass
