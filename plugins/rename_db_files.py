import re
import os
import logging
import asyncio
from pyrogram import Client, filters
from pyrogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery
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


# ── build_new_name: save_file ka exact naming logic ───────────
def build_new_name(doc) -> str | None:
    """
    MongoDB document se naya file_name banata hai.
    save_file() ke saath 100% identical sequence.
    Agar naam same raha to None return karta hai.
    """
    original_name: str = doc.get("file_name") or "Unnamed File"
    base_name, ext = os.path.splitext(original_name)
    caption_text: str = doc.get("caption") or ""

    # save_file line: text_to_scan = f"{original_name} {getattr(media, 'caption', '') or ''}"
    text_to_scan = f"{original_name} {caption_text}"

    extracted     = extract_languages_quality(text_to_scan)
    cleaned_title = extract_pure_title(base_name)

    # Title case — save_file ke jaisa
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

    # === STRICT SEQUENCE ASSEMBLER (save_file se copy) ===

    # [1] Title
    if final_title:
        add_unique(final_title)

    # [2] Season & Episode
    if extracted["season_episode"]:
        add_unique(extracted["season_episode"])

    # [2.5] Series Status
    if extracted["series_status"]:
        add_unique(extracted["series_status"])

    # [3] Release Year
    if extracted["year"]:
        add_unique(extracted["year"])

    # [4] Video Resolution
    if extracted["resolution"]:
        add_unique(extracted["resolution"])

    # [5] Audio Languages
    for lang in extracted["languages"]:
        add_unique(lang)

    # [6] Custom Qualifiers
    for qual in extracted["custom_qualifiers"]:
        add_unique(qual)

    # [7] Color Depth
    if "10BIT" in extracted["extra_tags"]:
        add_unique("10BIT")

    # [8] OTT Platform Tag  ← save_file mein yahan direct append hai
    if extracted["ott"]:
        if extracted["ott"] not in parts:
            parts.append(extracted["ott"])

    # [9] Source Type
    if extracted["source"]:
        add_unique(extracted["source"])

    # [10] Video Codec
    for vcodec in ["HEVC X265", "AVC X264"]:
        if vcodec in extracted["extra_tags"]:
            add_unique(vcodec)

    # [11] Audio Codec & Channels
    for acodec in ["Atmos 7.1", "Dolby 5.1", "AAC"]:
        if acodec in extracted["extra_tags"]:
            add_unique(acodec)

    # [12] Subtitles
    for sub in ["ESubs", "HardSubs", "MSubs"]:
        if sub in extracted["extra_tags"]:
            add_unique(sub)

    # [13] Audio Bitrate
    if extracted["kbps"]:
        add_unique(extracted["kbps"])

    # [13.5] File Part
    if extracted.get("file_part"):
        add_unique(extracted["file_part"])

    # [14] Branding Signature
    parts = [p for p in parts if p and "Tokyo_Updates" not in str(p)]
    parts.append(RELEASE_TAG)

    # Assembly — save_file ke jaisa
    file_name = " ".join(map(str, parts)).strip()
    file_name = re.sub(r'\s+', ' ', file_name)
    file_name = file_name + ext.lower()
    file_name = re.sub(r'\s+\.', '.', file_name)

    return file_name if file_name != original_name else None


# ============================================================
# /rename_db command
# ============================================================

@Client.on_message(filters.command("rename_db") & filters.user(ADMINS))
async def rename_db_files(client: Client, message: Message):
    """
    Sirf ADMINS use kar sakte hain.
      /rename_db          → dry-run (preview only, DB change nahi)
      /rename_db confirm  → actual DB update
    """
    args = message.text.split()
    dry_run = not (len(args) > 1 and args[1].lower() == "confirm")
    user_id = message.from_user.id

    _cancel_flags[user_id] = False

    mode_label = "🔍 DRY-RUN (preview only)" if dry_run else "✏️ LIVE UPDATE"
    status_msg = await message.reply_text(
        f"<b>{mode_label}</b>\n\nDB scan shuru ho raha hai... ⏳",
        reply_markup=_cancel_button()
    )

    updated   = 0
    skipped   = 0
    errors    = 0
    total     = 0
    cancelled = False

    collections_to_process = [("Primary DB", Media.collection)]
    if MULTIPLE_DB:
        collections_to_process.append(("Secondary DB", Media2.collection))

    for db_label, collection in collections_to_process:

        cursor = collection.find({}, {"_id": 1, "file_name": 1, "caption": 1})

        async for doc in cursor:
            # Cancel check
            if _cancel_flags.get(user_id):
                cancelled = True
                break

            total += 1
            try:
                new_name = build_new_name(doc)

                if new_name is None:
                    skipped += 1
                    continue

                if not dry_run:
                    await collection.update_one(
                        {"_id": doc["_id"]},
                        {"$set": {"file_name": new_name}}
                    )

                updated += 1
                logger.info(
                    f"[RENAME] {db_label} | "
                    f"OLD: {doc.get('file_name')} → NEW: {new_name}"
                )

            except Exception as e:
                errors += 1
                logger.error(f"[RENAME ERROR] _id={doc.get('_id')} | {e}")

            # Har 500 files pe progress update
            if total % 500 == 0:
                try:
                    await status_msg.edit_text(
                        f"<b>{mode_label}</b>\n\n"
                        f"📁 {db_label}\n"
                        f"✅ Renamed : <b>{updated}</b>\n"
                        f"⏭️ Skipped : <b>{skipped}</b>\n"
                        f"❌ Errors  : <b>{errors}</b>\n"
                        f"📊 Total   : <b>{total}</b>",
                        reply_markup=_cancel_button()
                    )
                except Exception:
                    pass
                await asyncio.sleep(0.3)

        if cancelled:
            break

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

    await status_msg.edit_text(
        f"<b>{mode_label} — Complete ✅</b>\n\n"
        f"📊 Total scanned : <b>{total}</b>\n"
        f"✏️ {action_word}   : <b>{updated}</b>\n"
        f"⏭️ No change     : <b>{skipped}</b>\n"
        f"❌ Errors        : <b>{errors}</b>"
        + footer,
        reply_markup=_done_button()
    )


# ── Cancel callback ───────────────────────────────────────────
@Client.on_callback_query(
    filters.regex("^rename_db_cancel$") & filters.user(ADMINS)
)
async def rename_db_cancel_cb(client: Client, query: CallbackQuery):
    _cancel_flags[query.from_user.id] = True
    await query.answer("🛑 Cancel signal bheja gaya! Ruk raha hai...", show_alert=True)
    try:
        await query.message.edit_reply_markup(reply_markup=None)
    except Exception:
        pass


# ── Done button (dismiss) ─────────────────────────────────────
@Client.on_callback_query(
    filters.regex("^rename_db_done$") & filters.user(ADMINS)
)
async def rename_db_done_cb(client: Client, query: CallbackQuery):
    await query.answer("👍 Done!")
    try:
        await query.message.edit_reply_markup(reply_markup=None)
    except Exception:
        pass
