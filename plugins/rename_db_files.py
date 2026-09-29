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
    MEDIA_DBS,
    extract_pure_title,
    extract_languages_quality,
    RELEASE_TAG,
    unpack_new_file_id,
)
from info import ADMINS

logger = logging.getLogger(__name__)

# ── Per-admin cancel flag ─────────────────────────────────────
_cancel_flags: dict[int, bool] = {}

# Single-file rename state: {user_id: {"action": ..., "message_id": ...}}
_single_states: dict[int, dict] = {}


def _rename_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("📄 Single File Rename", callback_data="rename_single"),
            InlineKeyboardButton("🗄️ All DB Rename", callback_data="rename_all_db"),
        ],
        [InlineKeyboardButton("ℹ️ Rename Help", callback_data="rename_help")],
    ])

def _single_actions() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("✨ Auto Clean", callback_data="rename_action:auto")],
        [
            InlineKeyboardButton("✏️ Full Name", callback_data="rename_action:full"),
            InlineKeyboardButton("🎬 Change Title", callback_data="rename_action:title"),
        ],
        [
            InlineKeyboardButton("🧹 Remove Word", callback_data="rename_action:remove"),
            InlineKeyboardButton("➕ Add Language", callback_data="rename_action:addlang"),
        ],
        [InlineKeyboardButton("➖ Remove Language", callback_data="rename_action:remlang")],
        [InlineKeyboardButton("🔙 Rename Menu", callback_data="rename_menu")],
    ])

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

    _db_labels = ["Primary DB", "Secondary DB", "Tertiary DB", "Quaternary DB", "Quinary DB"]
    collections_to_process = [
        (_db_labels[i] if i < len(_db_labels) else f"DB {i + 1}", media_cls.collection)
        for i, media_cls in enumerate(MEDIA_DBS)
    ]

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
# /rename command + interactive single-file rename
# ============================================================

@Client.on_message(filters.command(["rename", "rename_db"]) & filters.user(ADMINS))
async def rename_db_files(client: Client, message: Message):
    """Open the rename manager. /rename is the primary command; /rename_db remains an alias."""
    if len(message.command) > 1 and message.command[1].lower() == "confirm":
        return
    await message.reply_text(
        "<b>🛠️ FILE RENAME MANAGER</b>\n\n"
        "Yahan se single file ya configured <code>MEDIA_DBS</code> ki saari files rename kar sakte ho.\n\n"
        "📄 <b>Single File</b> → file par Reply karke <code>/renamefile</code> bhejo.\n"
        "🗄️ <b>All DB</b> → ia_filterdb ke current naming sequence ke according batch rename\n\n"
        "<i>Single file ke liye pehle file ko bot ke kisi chat me send/forward karo.</i>",
        reply_markup=_rename_menu()
    )


@Client.on_callback_query(filters.regex(r"^rename_menu$") & filters.user(ADMINS))
async def rename_menu_cb(client: Client, query: CallbackQuery):
    _single_states.pop(query.from_user.id, None)
    await query.answer()
    await query.message.edit_text(
        "<b>🛠️ FILE RENAME MANAGER</b>\n\n"
        "📄 Single file ko custom rename karo ya 🗄️ saari configured DBs ko canonical format me rename karo.",
        reply_markup=_rename_menu()
    )


@Client.on_callback_query(filters.regex(r"^rename_single$") & filters.user(ADMINS))
async def rename_single_cb(client: Client, query: CallbackQuery):
    _single_states.pop(query.from_user.id, None)
    await query.answer()
    await query.message.edit_text(
        "<b>📄 SINGLE FILE RENAME</b>\n\n"
        "Ab jis file ko rename karna hai us message par <b>Reply</b> karo aur\n"
        "<code>/renamefile</code> command bhejo.\n\n"
        "✅ Original file ho ya forwarded file — dono chalegi.\n"
        "❌ Is mode me normal search/message disturb nahi hoga.\n\n"
        "Example: file par Reply → <code>/renamefile</code>",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data="rename_menu")]])
    )


@Client.on_callback_query(filters.regex(r"^rename_all_db$") & filters.user(ADMINS))
async def rename_all_db_cb(client: Client, query: CallbackQuery):
    user_id = query.from_user.id
    if _cancel_flags.get(user_id) is False:
        await query.answer("Ek rename process already chal raha hai!", show_alert=True)
        return
    _cancel_flags[user_id] = False
    await query.answer("DB scan start ho raha hai...")
    status_msg = await query.message.edit_text(
        "<b>🔍 DRY-RUN PREVIEW</b>\n\nConfigured MEDIA_DBS scan ho rahi hain... ⏳",
        reply_markup=_cancel_button()
    )
    asyncio.create_task(process_rename_db(client, status_msg, user_id, True))


@Client.on_callback_query(filters.regex(r"^rename_help$") & filters.user(ADMINS))
async def rename_help_cb(client: Client, query: CallbackQuery):
    await query.answer()
    await query.message.edit_text(
        "<b>ℹ️ RENAME HELP</b>\n\n"
        "📄 <b>Single File</b>\n"
        "• File par Reply → <code>/renamefile</code>\n"
        "• ✨ Auto Clean → ia_filterdb naming rules\n"
        "• ✏️ Full Name → poora filename manually set\n"
        "• 🎬 Change Title → sirf title replace\n"
        "• 🧹 Remove Word → filename se word/phrase remove\n"
        "• ➕ Add Language → language add\n"
        "• ➖ Remove Language → language remove\n\n"
        "🗄️ <b>All DB</b>\n"
        "Pehle preview chalega; actual update ke liye preview ke baad command <code>/rename confirm</code> use karna hai.\n\n"
        "<i>Extension automatically preserve hoti hai.</i>",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data="rename_menu")]])
    )


@Client.on_callback_query(filters.regex(r"^rename_action:(auto|full|title|remove|addlang|remlang)$") & filters.user(ADMINS))
async def rename_action_cb(client: Client, query: CallbackQuery):
    action = query.data.split(":", 1)[1]
    user_id = query.from_user.id
    state = _single_states.get(user_id)
    if not state or not state.get("file_id"):
        await query.answer("Pehle file select karo.", show_alert=True)
        return

    if action == "auto":
        await query.answer("Auto clean ho raha hai...")
        result = await _apply_single_rename(state, action, None)
        await query.message.edit_text(result, reply_markup=_single_actions())
        return

    prompts = {
        "full": "✏️ <b>Full Name</b>\n\nNaya poora filename bhejo, extension optional hai.",
        "title": "🎬 <b>Change Title</b>\n\nNaya title bhejo. Baaki quality/language/OTT etc. preserve rahenge.",
        "remove": "🧹 <b>Remove Word</b>\n\nJo word/phrase hatana hai woh bhejo.",
        "addlang": "➕ <b>Add Language</b>\n\nLanguage bhejo, jaise <code>Hindi</code> / <code>Tamil</code>.",
        "remlang": "➖ <b>Remove Language</b>\n\nLanguage bhejo, jaise <code>Hindi</code> / <code>Tamil</code>.",
    }
    _single_states[user_id]["action"] = action
    await query.answer()
    await query.message.edit_text(
        prompts[action] + "\n\n<i>Cancel ke liye /rename bhej sakte ho.</i>",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Rename Menu", callback_data="rename_menu")]])
    )


async def _find_single_doc(file_id: str | None = None, file_name: str | None = None, file_size: int | None = None):
    """Find the exact saved DB record using every safe identifier available.

    Telegram file_id can change when a file is forwarded/re-sent, so we try both
    the raw and normalized IDs, then fall back to filename + size.
    """
    candidates = []
    if file_id:
        candidates.append(file_id)
        normalized_id, _ = unpack_new_file_id(file_id)
        if normalized_id and normalized_id not in candidates:
            candidates.append(normalized_id)

    logger.info(
        f"[RENAME SINGLE] DB lookup started | file_id={'yes' if file_id else 'no'} "
        f"| file_name={file_name!r} | file_size={file_size}"
    )

    for media_cls in MEDIA_DBS:
        coll = media_cls.collection
        for candidate in candidates:
            try:
                doc = await coll.find_one({"_id": candidate})
                if doc:
                    logger.info(f"[RENAME SINGLE] Found by _id in {media_cls.__name__}: {candidate}")
                    return media_cls, doc
            except Exception as e:
                logger.debug(f"[RENAME SINGLE] _id lookup failed: {e}")

        if file_name:
            queries = []
            if file_size is not None:
                queries.append({"file_name": file_name, "file_size": file_size})
            queries.append({"file_name": file_name})
            for q in queries:
                doc = await coll.find_one(q)
                if doc:
                    logger.info(f"[RENAME SINGLE] Found by filename in {media_cls.__name__}: {q}")
                    return media_cls, doc

    logger.warning(
        f"[RENAME SINGLE] NOT FOUND | file_id={file_id!r} | file_name={file_name!r} | file_size={file_size}"
    )
    return None, None


async def _apply_single_rename(state: dict, action: str, value: str | None):
    media_cls = state["media_cls"]
    doc = state["doc"]
    old_name = doc.get("file_name") or "Unnamed File"
    base, ext = os.path.splitext(old_name)

    if action == "full":
        new_name = (value or "").strip()
        if not new_name:
            return "❌ Filename empty nahi ho sakta."
        if not os.path.splitext(new_name)[1]:
            new_name += ext
        else:
            # User ne extension diya hai to exactly wahi extension rakho.
            pass
        new_name = re.sub(r"[\\/:*?\"<>|]", "", new_name).strip()

    elif action == "title":
        # Canonical rebuild ke liye title ko filename ke front me replace karo.
        if not value or not value.strip():
            return "❌ Title empty nahi ho sakta."
        current = build_new_name({"file_name": old_name, "caption": ""}) or old_name
        cbase, cext = os.path.splitext(current)
        # Metadata starts at first recognized token; preserve everything after it.
        extracted = extract_languages_quality(old_name)
        cut_tokens = []
        for key in ("title_part", "season_episode", "episode_title", "series_status", "year", "resolution"):
            val = extracted.get(key)
            if val:
                cut_tokens.append(str(val))
        # Safer: replace only current pure title span, leaving canonical metadata intact.
        old_title = extract_pure_title(base)
        if old_title.strip():
            new_name = current.replace(old_title, value.strip(), 1)
        else:
            new_name = f"{value.strip()} {cbase}".strip() + cext

    elif action == "remove":
        if not value or not value.strip():
            return "❌ Word empty nahi ho sakta."
        new_name = re.sub(re.escape(value.strip()), "", old_name, flags=re.I)
        new_name = re.sub(r"\s+", " ", new_name).strip()
        new_name = build_new_name({"file_name": new_name, "caption": ""}) or new_name

    elif action in ("addlang", "remlang"):
        if not value or not value.strip():
            return "❌ Language empty nahi ho sakti."
        lang = value.strip()
        if action == "addlang":
            working = f"{os.path.splitext(old_name)[0]} {lang}{ext}"
        else:
            working = re.sub(rf"(?i)(?<![A-Za-z]){re.escape(lang)}(?![A-Za-z])", "", old_name)
            working = re.sub(r"\s+", " ", working).strip()
        new_name = build_new_name({"file_name": working, "caption": ""}) or working

    else:  # auto
        new_name = build_new_name(doc) or old_name

    if new_name == old_name:
        return f"<b>ℹ️ No change</b>\n\n<code>{old_name}</code>"

    # Prevent duplicate filename for the same size when another record already has it.
    size = doc.get("file_size")
    duplicate_query = {"file_name": new_name}
    if size is not None:
        duplicate_query["file_size"] = size
    duplicate = await media_cls.collection.find_one({**duplicate_query, "_id": {"$ne": doc["_id"]}})
    if duplicate:
        return f"❌ <b>Duplicate file name already exists.</b>\n\n<code>{new_name}</code>"

    extracted = extract_languages_quality(new_name)
    title = extract_pure_title(os.path.splitext(new_name)[0]).strip()
    await media_cls.collection.update_one(
        {"_id": doc["_id"]},
        {"$set": {
            "file_name": new_name,
            "title": title or doc.get("title"),
            "year": extracted.get("year") or doc.get("year"),
            "media_type": "series" if re.search(r"\bS\d{1,3}(?:E\d{1,4})?\b", new_name, re.I) else doc.get("media_type", "movie")
        }}
    )
    doc["file_name"] = new_name
    return (
        f"<b>✅ Rename Successful</b>\n\n"
        f"OLD:\n<code>{old_name}</code>\n\n"
        f"NEW:\n<code>{new_name}</code>"
    )


@Client.on_message(filters.command("renamefile") & filters.user(ADMINS))
async def rename_single_select_command(client: Client, message: Message):
    """Select the file from a replied message. This never intercepts normal searches."""
    reply = message.reply_to_message
    if not reply:
        await message.reply_text(
            "❌ <b>File select nahi hui.</b>\n\n"
            "Jis document/video/audio ko rename karna hai, us message par <b>Reply</b> karo "
            "aur phir <code>/renamefile</code> bhejo.\n\n"
            "Example: <i>File Message → Reply → /renamefile</i>"
        )
        return

    media = reply.document or reply.video or reply.audio
    if not media:
        await message.reply_text("❌ Reply kiya hua message document/video/audio nahi hai.")
        return

    user_id = message.from_user.id
    media_name = getattr(media, "file_name", None)
    media_size = getattr(media, "file_size", None)
    media_id = getattr(media, "file_id", None)

    await message.reply_text("🔎 <b>Database me exact file search ho rahi hai...</b> ⏳")
    media_cls, doc = await _find_single_doc(media_id, media_name, media_size)

    if not doc:
        await message.reply_text(
            "❌ <b>Ye file configured MEDIA_DBS me nahi mili.</b>\n\n"
            f"📄 File: <code>{media_name or 'Unknown'}</code>\n"
            f"📦 Size: <code>{media_size or 'Unknown'}</code>\n\n"
            "Agar file bot ke DB me saved hai, usi saved file ko reply karke "
            "<code>/renamefile</code> dobara bhejo."
        )
        return

    _single_states[user_id] = {
        "action": "selected",
        "file_id": media_id,
        "media_cls": media_cls,
        "doc": doc,
    }

    await message.reply_text(
        f"<b>✅ FILE SELECTED</b>\n\n"
        f"🗄️ DB: <code>{media_cls.__name__}</code>\n"
        f"📄 Current Name:\n<code>{doc.get('file_name', 'Unknown')}</code>\n\n"
        "Ab neeche se rename operation choose karo:",
        reply_markup=_single_actions()
    )


@Client.on_message(filters.text & filters.incoming & filters.user(ADMINS), group=15)
async def rename_single_text_input(client: Client, message: Message):
    """Consume text only when a rename button explicitly requested input."""
    state = _single_states.get(message.from_user.id)
    if not state or state.get("action") not in {"full", "title", "remove", "addlang", "remlang"}:
        return
    value = message.text or ""
    if value.startswith("/"):
        return
    action = state["action"]
    result = await _apply_single_rename(state, action, value)
    state["action"] = "selected"
    await message.reply_text(result, reply_markup=_single_actions())


# ── /rename confirm remains available for actual batch update ─────────
@Client.on_message(filters.command("rename") & filters.user(ADMINS))
async def rename_confirm_command(client: Client, message: Message):
    args = message.command
    if len(args) < 2 or args[1].lower() != "confirm":
        return
    user_id = message.from_user.id
    if _cancel_flags.get(user_id) is False:
        await message.reply_text("⏳ Ek DB rename process already background mein chal rahi hai!")
        return
    _cancel_flags[user_id] = False
    status_msg = await message.reply_text(
        "<b>✏️ LIVE UPDATE</b>\n\nConfigured MEDIA_DBS me actual rename start ho raha hai... ⏳",
        reply_markup=_cancel_button()
    )
    asyncio.create_task(process_rename_db(client, status_msg, user_id, False))


# ── Cancel callback ───────────────────────────────────────────
@Client.on_callback_query(filters.regex("^rename_db_cancel$") & filters.user(ADMINS))
async def rename_db_cancel_cb(client: Client, query: CallbackQuery):
    _cancel_flags[query.from_user.id] = True
    await query.answer("🛑 Cancel signal bheja gaya! Task ruk raha hai...", show_alert=True)
    try:
        await query.message.edit_reply_markup(reply_markup=None)
    except Exception:
        pass


@Client.on_callback_query(filters.regex("^rename_db_done$") & filters.user(ADMINS))
async def rename_db_done_cb(client: Client, query: CallbackQuery):
    await query.answer("👍 Done!")
    try:
        await query.message.delete()
    except Exception:
        pass

