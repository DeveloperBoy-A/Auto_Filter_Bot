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
    extracted     = extract_languages_quality(text_to_scan)
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

    # === STRICT SEQUENCE ASSEMBLER ===
    
    # [1] Title (e.g., Pushpa 2)
    if final_title: 
        add_unique(final_title)

    # [2] Title Part / Volume / Chapter (e.g., Part 1, Vol 2)
    if extracted.get("title_part"): 
        add_unique(extracted["title_part"])

    # [3] Season & Episode (e.g., S01 E05)
    if extracted.get("season_episode"): 
        add_unique(extracted["season_episode"])

    # [4] Series Status (e.g., COMPLETE)
    if extracted.get("series_status"): 
        add_unique(extracted["series_status"])

    # [5] Release Year (e.g., 2024)
    if extracted.get("year"): 
        add_unique(extracted["year"])

    # [6] Video Resolution (e.g., 1080P)
    if extracted.get("resolution"): 
        add_unique(extracted["resolution"])

    # [7] Audio Languages (e.g., Hindi Tamil Dual Audio)
    for lang in extracted.get("languages", []): 
        add_unique(lang)

    # [8] Custom Qualifiers (e.g., Uncut, Director's Cut)
    for qual in extracted.get("custom_qualifiers", []): 
        add_unique(qual)

    # [9] Color Depth / HDR (e.g., 10Bit, Dolby Vision)
    for tag in ["10Bit", "12Bit", "SDR", "HDR", "Dolby Vision", "IMAX", "60FPS"]:
        if tag in extracted.get("extra_tags", []): 
            add_unique(tag)

    # [10] OTT Platform Tag (e.g., Netflix, JioCinema)
    if extracted.get("ott") and extracted["ott"] not in parts:
        parts.append(extracted["ott"])

    # [11] Source Type (e.g., WEB DL, BluRay)
    if extracted.get("source"): 
        add_unique(extracted["source"])

    # [12] Video Codec (e.g., HEVC, AV1)
    for vcodec in ["AV1", "HEVC X265", "AVC X264"]:
        if vcodec in extracted.get("extra_tags", []): 
            add_unique(vcodec)

    # [13] Audio Codec & Channels (e.g., DD 5.1, Atmos)
    audio_tags = extracted.get("extra_tags", [])
    
    # Smart Overlap Handler (DDP 5.1 aur DD 5.1 ek sath na aaye)
    if "DDP 5.1" in audio_tags and "DD 5.1" in audio_tags:
        audio_tags.remove("DD 5.1")
    if "DDP 7.1" in audio_tags and "DD 5.1" in audio_tags:
        audio_tags.remove("DD 5.1")
    if "AAC 5.1" in audio_tags and "AAC" in audio_tags:
        audio_tags.remove("AAC")

    for acodec in ["Dolby TrueHD", "Dolby Atmos", "DTS-X", "DTS-HD", "DDP 7.1", "DDP 5.1", "DD 5.1", "DD 2.0", "DTS 5.1", "AAC 5.1", "AAC"]:
        if acodec in audio_tags: 
            add_unique(acodec)

    # [14] Subtitles (e.g., ESubs)
    for sub in ["ESubs", "HardSubs", "MSubs"]:
        if sub in extracted.get("extra_tags", []): 
            add_unique(sub)

    # [15] Audio Bitrate (e.g., 192kbps)
    if extracted.get("kbps"): 
        add_unique(extracted["kbps"])

    # [16] File Split Part (e.g., Part 001)
    if extracted.get("split_part"): 
        add_unique(extracted["split_part"])

    # [17] Branding Signature (Sabse aakhir mein tumhara tag)
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
    updated = 0
    skipped = 0
    errors = 0
    total = 0
    cancelled = False

    collections_to_process = [("Primary DB", Media.collection)]
    if MULTIPLE_DB:
        collections_to_process.append(("Secondary DB", Media2.collection))

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
            # Cancel Check
            if _cancel_flags.get(user_id):
                cancelled = True
                break

            total += 1
            try:
                new_name = build_new_name(doc)

                if new_name is None:
                    skipped += 1
                else:
                    if not dry_run:
                        await collection.update_one(
                            {"_id": doc["_id"]},
                            {"$set": {"file_name": new_name}}
                        )
                    updated += 1

            except Exception as e:
                errors += 1
                logger.error(f"[RENAME ERROR] _id={doc.get('_id')} | {e}")

            # Bot ko background processes sambhalne ke liye thodi der saans lene do
            if total % 100 == 0:
                await asyncio.sleep(0.05)

            # FloodWait Protection: Har 5 seconds me hi Telegram API par message update karo
            if time.time() - last_edit_time > 5:
                percentage = (total / total_docs) * 100
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
        await status_msg.edit_text(
            f"<b>🛑 Cancelled by admin</b>\n\n"
            f"📊 Processed : <b>{total}</b>\n"
            f"✏️ {'Would rename' if dry_run else 'Renamed'} : <b>{updated}</b>\n"
            f"⏭️ No change  : <b>{skipped}</b>\n"
            f"❌ Errors     : <b>{errors}</b>",
            reply_markup=None
        )
        return

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
