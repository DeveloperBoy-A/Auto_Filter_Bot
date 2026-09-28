import re
import os
import time
import logging
import asyncio
import uuid

from pyrogram import Client, filters
from pyrogram.errors import FloodWait
from pyrogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery

from database.ia_filterdb import (
    MEDIA_DBS,
    RELEASE_TAG,
    LANGUAGE_ALIASES,
    OTT_MAP,
    extract_pure_title,
    extract_languages_quality,
    apply_dual_multi_audio_tag,
)
from info import ADMINS

logger = logging.getLogger(__name__)

# ============================================================
# RENAME UI STATE
# ============================================================
_cancel_flags: dict[int, bool] = {}
_single_sessions: dict[int, dict] = {}
_single_tokens: dict[str, tuple[int, str, int]] = {}

_DB_LABELS = ["Primary DB", "Secondary DB", "Tertiary DB", "Quaternary DB", "Quinary DB"]


def _menu_button() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("📚 ᴀʟʟ ᴅʙ ʀᴇɴᴀᴍᴇ", callback_data="rename_menu_all"),
            InlineKeyboardButton("📄 sɪɴɢʟᴇ ғɪʟᴇ", callback_data="rename_menu_single"),
        ],
        [InlineKeyboardButton("❌ ᴄʟᴏsᴇ", callback_data="rename_menu_close")]
    ])


def _all_mode_buttons() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🔍 ᴘʀᴇᴠɪᴇᴡ", callback_data="rename_all_preview"),
            InlineKeyboardButton("✏️ ʀᴇɴᴀᴍᴇ ᴀʟʟ", callback_data="rename_all_confirm"),
        ],
        [InlineKeyboardButton("🔙 ʙᴀᴄᴋ", callback_data="rename_menu_back")]
    ])


def _cancel_button() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton("🛑 ᴄᴀɴᴄᴇʟ", callback_data="rename_db_cancel")]]
    )


def _done_button() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton("✅ ᴅᴏɴᴇ", callback_data="rename_db_done")]]
    )


def _single_options(token: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✨ ᴀᴜᴛᴏ ᴄʟᴇᴀɴ", callback_data=f"rename_single_auto:{token}"),
            InlineKeyboardButton("✏️ ғᴜʟʟ ɴᴀᴍᴇ", callback_data=f"rename_single_full:{token}"),
        ],
        [
            InlineKeyboardButton("🧹 ʀᴇᴍᴏᴠᴇ ᴡᴏʀᴅ", callback_data=f"rename_single_remove:{token}"),
            InlineKeyboardButton("🎬 ᴄʜᴀɴɢᴇ ᴛɪᴛʟᴇ", callback_data=f"rename_single_title:{token}"),
        ],
        [
            InlineKeyboardButton("➕ ᴀᴅᴅ ʟᴀɴɢᴜᴀɢᴇ", callback_data=f"rename_single_addlang:{token}"),
            InlineKeyboardButton("➖ ʀᴇᴍᴏᴠᴇ ʟᴀɴɢᴜᴀɢᴇ", callback_data=f"rename_single_remlang:{token}"),
        ],
        [InlineKeyboardButton("❌ ᴄᴀɴᴄᴇʟ", callback_data="rename_single_cancel")]
    ])


def _is_media_message(message: Message) -> bool:
    return bool(message.document or message.video or message.audio)


def _message_file_id(message: Message):
    media = message.document or message.video or message.audio
    if not media:
        return None, None
    return media.file_id, getattr(media, "file_name", None)


def _canonical_language(value: str):
    value = value.strip()
    if not value:
        return None

    low = value.lower()
    for name, aliases in LANGUAGE_ALIASES.items():
        if low == name.lower():
            return name
        for alias in aliases:
            alias_clean = re.sub(r"\\b|\\", "", alias).lower()
            if low == alias_clean:
                return name
    return value.title()


def _language_matches(existing: str, wanted: str) -> bool:
    return str(existing).strip().lower() == str(wanted).strip().lower()


def _format_title(title: str) -> str:
    words = []
    for word in re.sub(r"\s+", " ", title.strip()).split():
        words.append(word[0].upper() + word[1:] if len(word) > 1 else word.upper())
    return " ".join(words)


def build_new_name(doc) -> str | None:
    """
    EXACT filename assembly used by ia_filterdb.save_file():
    title -> year -> part -> S/E -> episode title -> status -> resolution
    -> languages -> qualifiers -> HDR -> OTT -> source -> codec -> audio
    -> subtitles -> kbps -> split part -> RELEASE_TAG
    """
    original_name = str(doc.get("file_name") or "Unnamed File")
    base_name, ext = os.path.splitext(original_name)
    caption_text = doc.get("caption") or ""
    text_to_scan = f"{original_name} {caption_text}"

    extracted = extract_languages_quality(text_to_scan)

    if not extracted.get("resolution"):
        extracted["resolution"] = "720P"
    if not extracted.get("source"):
        extracted["source"] = "WEB-DL"

    audio_codecs = [
        "Dolby TrueHD", "Dolby Atmos", "DTS-X", "DTS-HD",
        "DDP 7.1", "DDP 5.1", "DD 5.1", "DD 2.0",
        "DTS 5.1", "AAC 5.1", "AAC"
    ]
    audio_tags = extracted.get("extra_tags", [])
    if not any(codec in audio_tags for codec in audio_codecs):
        if "AAC" not in audio_tags:
            audio_tags.append("AAC")
        extracted["extra_tags"] = audio_tags

    cleaned_title = extract_pure_title(base_name)

    # Same fallback as save_file: title can come from caption.
    if not cleaned_title.strip() and caption_text:
        caption_plain = re.sub(r"<[^>]+>", " ", str(caption_text))
        caption_plain = re.sub(r"[\U0001F000-\U0001FFFF\u2600-\u27BF]+", " ", caption_plain)
        caption_plain = caption_plain.splitlines()[0] if caption_plain.strip() else caption_plain
        cleaned_title = extract_pure_title(caption_plain)

    final_title = _format_title(cleaned_title)
    parts = []

    def add_unique(value):
        if value and str(value).lower() not in " ".join(map(str, parts)).lower():
            parts.append(value)

    if final_title:
        add_unique(final_title)

    if extracted.get("year"):
        add_unique(extracted["year"])

    if extracted.get("title_part"):
        add_unique(extracted["title_part"])

    if extracted.get("season_episode"):
        add_unique(extracted["season_episode"])

    if extracted.get("season_episode") and extracted.get("episode_title"):
        add_unique(extracted["episode_title"])

    if extracted.get("series_status"):
        add_unique(extracted["series_status"])

    if extracted.get("resolution"):
        add_unique(extracted["resolution"])

    for lang in extracted.get("languages", []):
        add_unique(lang)

    for qual in extracted.get("custom_qualifiers", []):
        add_unique(qual)

    for tag in ["10Bit", "12Bit", "SDR", "HDR", "Dolby Vision", "IMAX", "60FPS"]:
        if tag in extracted.get("extra_tags", []):
            add_unique(tag)

    if extracted.get("ott") and extracted["ott"] not in parts:
        parts.append(extracted["ott"])

    if extracted.get("source"):
        add_unique(extracted["source"])

    for vcodec in ["AV1", "HEVC X265", "AVC X264"]:
        if vcodec in extracted.get("extra_tags", []):
            add_unique(vcodec)

    audio_tags = extracted.get("extra_tags", [])
    if "DDP 5.1" in audio_tags and "DD 5.1" in audio_tags:
        audio_tags.remove("DD 5.1")
    if "DDP 7.1" in audio_tags and "DD 5.1" in audio_tags:
        audio_tags.remove("DD 5.1")
    if "AAC 5.1" in audio_tags and "AAC" in audio_tags:
        audio_tags.remove("AAC")

    for acodec in [
        "Dolby TrueHD", "Dolby Atmos", "DTS-X", "DTS-HD",
        "DDP 7.1", "DDP 5.1", "DD 5.1", "DD 2.0",
        "DTS 5.1", "AAC 5.1", "AAC"
    ]:
        if acodec in audio_tags:
            add_unique(acodec)

    for sub in ["ESubs", "HardSubs", "MSubs"]:
        if sub in extracted.get("extra_tags", []):
            add_unique(sub)

    if extracted.get("kbps"):
        add_unique(extracted["kbps"])

    if extracted.get("split_part"):
        add_unique(extracted["split_part"])

    parts = [p for p in parts if p and "Tokyo_Updates" not in str(p)]
    parts.append(RELEASE_TAG)

    file_name = " ".join(map(str, parts)).strip()
    file_name = re.sub(r"\s+", " ", file_name)
    file_name = file_name + ext.lower()
    file_name = re.sub(r"\s+\.", ".", file_name)

    return file_name if file_name != original_name else None


def _build_from_parts(
    original_name: str,
    caption: str = "",
    forced_title: str | None = None,
    forced_languages: list[str] | None = None,
) -> str:
    """
    Rebuild filename with the exact ia_filterdb ordering while allowing
    title/language changes for the single-file editor.
    """
    doc = {"file_name": original_name, "caption": caption}
    base_name, ext = os.path.splitext(original_name)
    text_to_scan = f"{original_name} {caption or ''}"
    extracted = extract_languages_quality(text_to_scan)

    if forced_languages is not None:
        extracted["languages"] = forced_languages

    if not extracted.get("resolution"):
        extracted["resolution"] = "720P"
    if not extracted.get("source"):
        extracted["source"] = "WEB-DL"

    audio_codecs = [
        "Dolby TrueHD", "Dolby Atmos", "DTS-X", "DTS-HD",
        "DDP 7.1", "DDP 5.1", "DD 5.1", "DD 2.0",
        "DTS 5.1", "AAC 5.1", "AAC"
    ]
    audio_tags = extracted.get("extra_tags", [])
    if not any(codec in audio_tags for codec in audio_codecs):
        if "AAC" not in audio_tags:
            audio_tags.append("AAC")
        extracted["extra_tags"] = audio_tags

    title = forced_title if forced_title is not None else extract_pure_title(base_name)
    final_title = _format_title(title)

    parts = []
    def add_unique(value):
        if value and str(value).lower() not in " ".join(map(str, parts)).lower():
            parts.append(value)

    if final_title: add_unique(final_title)
    if extracted.get("year"): add_unique(extracted["year"])
    if extracted.get("title_part"): add_unique(extracted["title_part"])
    if extracted.get("season_episode"): add_unique(extracted["season_episode"])
    if extracted.get("season_episode") and extracted.get("episode_title"): add_unique(extracted["episode_title"])
    if extracted.get("series_status"): add_unique(extracted["series_status"])
    if extracted.get("resolution"): add_unique(extracted["resolution"])
    for lang in extracted.get("languages", []): add_unique(lang)
    for qual in extracted.get("custom_qualifiers", []): add_unique(qual)
    for tag in ["10Bit", "12Bit", "SDR", "HDR", "Dolby Vision", "IMAX", "60FPS"]:
        if tag in extracted.get("extra_tags", []): add_unique(tag)
    if extracted.get("ott"): add_unique(extracted["ott"])
    if extracted.get("source"): add_unique(extracted["source"])
    for vcodec in ["AV1", "HEVC X265", "AVC X264"]:
        if vcodec in extracted.get("extra_tags", []): add_unique(vcodec)

    audio_tags = extracted.get("extra_tags", [])
    if "DDP 5.1" in audio_tags and "DD 5.1" in audio_tags: audio_tags.remove("DD 5.1")
    if "DDP 7.1" in audio_tags and "DD 5.1" in audio_tags: audio_tags.remove("DD 5.1")
    if "AAC 5.1" in audio_tags and "AAC" in audio_tags: audio_tags.remove("AAC")
    for acodec in ["Dolby TrueHD", "Dolby Atmos", "DTS-X", "DTS-HD", "DDP 7.1", "DDP 5.1", "DD 5.1", "DD 2.0", "DTS 5.1", "AAC 5.1", "AAC"]:
        if acodec in audio_tags: add_unique(acodec)
    for sub in ["ESubs", "HardSubs", "MSubs"]:
        if sub in extracted.get("extra_tags", []): add_unique(sub)
    if extracted.get("kbps"): add_unique(extracted["kbps"])
    if extracted.get("split_part"): add_unique(extracted["split_part"])

    parts = [p for p in parts if p and "Tokyo_Updates" not in str(p)]
    parts.append(RELEASE_TAG)

    result = re.sub(r"\s+", " ", " ".join(map(str, parts)).strip())
    result = result + ext.lower()
    return re.sub(r"\s+\.", ".", result)


def _metadata_from_name(file_name: str):
    extracted = extract_languages_quality(file_name)
    title = extract_pure_title(os.path.splitext(file_name)[0])
    media_type = "series" if re.search(r"\bS\d{1,2}\b|\bSeason\s*\d+", file_name, re.I) else "movie"
    return title, extracted.get("year"), media_type


async def _find_file(file_id: str, file_name: str | None = None):
    for index, media_cls in enumerate(MEDIA_DBS):
        doc = await media_cls.collection.find_one({"_id": file_id})
        if doc:
            return index, media_cls.collection, doc

    if file_name:
        for index, media_cls in enumerate(MEDIA_DBS):
            doc = await media_cls.collection.find_one({"file_name": file_name})
            if doc:
                return index, media_cls.collection, doc

    return None, None, None


async def _update_single(collection, doc, new_name: str):
    old_name = doc.get("file_name") or ""

    if not new_name:
        return False, "❌ New filename empty hai."

    # Keep the existing extension when admin gives a name without one.
    old_ext = os.path.splitext(old_name)[1]
    if not os.path.splitext(new_name)[1] and old_ext:
        new_name += old_ext

    if new_name == old_name:
        return False, "⏭️ Filename already same hai."

    # Same DB / other DB duplicate protection, matching save_file's
    # file_name + file_size duplicate rule.
    for media_cls in MEDIA_DBS:
        duplicate = await media_cls.collection.find_one({
            "file_name": new_name,
            "file_size": doc.get("file_size"),
            "_id": {"$ne": doc.get("_id")}
        })
        if duplicate:
            return False, "⚠️ Same filename + same file size wali file DB me already hai."

    title, year, media_type = _metadata_from_name(new_name)

    result = await collection.update_one(
        {"_id": doc["_id"]},
        {"$set": {
            "file_name": new_name,
            "title": title or doc.get("title"),
            "year": year,
            "media_type": media_type,
        }}
    )

    if not result.modified_count:
        return False, "❌ Database update nahi hua."

    return True, (
        f"✅ <b>Rename successful</b>\n\n"
        f"📝 <b>Old:</b> <code>{old_name}</code>\n"
        f"🆕 <b>New:</b> <code>{new_name}</code>"
    )


async def _show_single_file(message: Message, file_id: str, file_name: str | None = None):
    db_index, collection, doc = await _find_file(file_id, file_name)
    if not doc:
        return await message.reply_text(
            "❌ Ye file <b>configured IA filter DB</b> me nahi mili.\n\n"
            "Bot DB wali file ko hi rename karega."
        )

    token = uuid.uuid4().hex[:16]
    _single_tokens[token] = (message.from_user.id, str(doc["_id"]), db_index)
    _single_sessions[message.from_user.id] = {
        "stage": "selected",
        "token": token,
        "file_id": str(doc["_id"]),
        "db_index": db_index,
    }

    await message.reply_text(
        f"📄 <b>Selected File</b>\n\n"
        f"🗄️ DB: <b>{_DB_LABELS[db_index]}</b>\n"
        f"📝 <b>Current:</b>\n<code>{doc.get('file_name', 'Unnamed File')}</code>\n\n"
        f"Neeche se choose karo:",
        reply_markup=_single_options(token)
    )


# ============================================================
# ALL DATABASE RENAME
# ============================================================
async def process_rename_db(client: Client, status_msg: Message, user_id: int, dry_run: bool):
    mode_label = "🔍 DRY-RUN" if dry_run else "✏️ LIVE UPDATE"
    _cancel_flags[user_id] = False

    updated = skipped = errors = total = 0
    cancelled = False
    last_edit_time = time.time()

    for db_index, media_cls in enumerate(MEDIA_DBS):
        if _cancel_flags.get(user_id):
            cancelled = True
            break

        db_label = _DB_LABELS[db_index] if db_index < len(_DB_LABELS) else f"DB {db_index + 1}"
        total_docs = await media_cls.collection.count_documents({})

        if not total_docs:
            continue

        cursor = media_cls.collection.find(
            {}, {"_id": 1, "file_name": 1, "caption": 1}
        )

        async for doc in cursor:
            if _cancel_flags.get(user_id):
                cancelled = True
                break

            total += 1
            try:
                old_name = doc.get("file_name")
                new_name = build_new_name(doc)

                if not new_name:
                    skipped += 1
                else:
                    if not dry_run:
                        await media_cls.collection.update_one(
                            {"_id": doc["_id"]},
                            {"$set": {
                                "file_name": new_name,
                                "title": extract_pure_title(os.path.splitext(new_name)[0]),
                                "year": extract_languages_quality(new_name).get("year"),
                                "media_type": (
                                    "series" if re.search(
                                        r"\bS\d{1,2}\b|\bSeason\s*\d+",
                                        new_name, re.I
                                    ) else "movie"
                                )
                            }}
                        )
                    updated += 1

            except Exception as e:
                errors += 1
                logger.error(
                    f"Rename error | DB={db_label} | _id={doc.get('_id')} | {e}",
                    exc_info=True
                )

            if total % 100 == 0:
                await asyncio.sleep(0.05)

            if time.time() - last_edit_time > 5:
                progress_text = (
                    f"<b>{mode_label}</b>\n\n"
                    f"📁 <b>{db_label}</b>\n"
                    f"📊 Processed: <b>{total}</b>\n"
                    f"✏️ Renamed: <b>{updated}</b>\n"
                    f"⏭️ Skipped: <b>{skipped}</b>\n"
                    f"❌ Errors: <b>{errors}</b>"
                )
                try:
                    await status_msg.edit_text(
                        progress_text,
                        reply_markup=_cancel_button()
                    )
                    last_edit_time = time.time()
                except FloodWait as e:
                    await asyncio.sleep(e.value)

    _cancel_flags.pop(user_id, None)

    if cancelled:
        await status_msg.edit_text(
            f"<b>🛑 Rename cancelled</b>\n\n"
            f"📊 Processed: <b>{total}</b>\n"
            f"✏️ Renamed: <b>{updated}</b>\n"
            f"⏭️ Skipped: <b>{skipped}</b>\n"
            f"❌ Errors: <b>{errors}</b>",
            reply_markup=None
        )
        return

    footer = (
        "\n\n⚠️ <i>Preview only tha. Actual rename ke liye "
        "<b>✏️ Rename All</b> button use karo.</i>"
        if dry_run else
        "\n\n✅ <i>All configured DBs process ho gayi.</i>"
    )

    await status_msg.edit_text(
        f"<b>{mode_label} — COMPLETE ✅</b>\n\n"
        f"📊 Total processed: <b>{total}</b>\n"
        f"✏️ Renamed: <b>{updated}</b>\n"
        f"⏭️ No change: <b>{skipped}</b>\n"
        f"❌ Errors: <b>{errors}</b>{footer}",
        reply_markup=_done_button()
    )


# ============================================================
# /rename_db
# ============================================================
@Client.on_message(filters.command("rename_db") & filters.user(ADMINS))
async def rename_db_files(client: Client, message: Message):
    await message.reply_text(
        "🛠️ <b>FILE RENAME MANAGER</b>\n\n"
        "📚 <b>All DB Rename</b> — ia_filterdb ke same filename rules se "
        "configured sabhi DBs ko rename karega.\n\n"
        "📄 <b>Single File</b> — ek file select karke full name, word remove, "
        "title change, language add/remove ya auto-clean kar sakte ho.",
        reply_markup=_menu_button()
    )


# ============================================================
# MENU CALLBACKS
# ============================================================
@Client.on_callback_query(filters.regex(r"^rename_menu_all$") & filters.user(ADMINS))
async def rename_menu_all_cb(client: Client, query: CallbackQuery):
    await query.answer()
    await query.message.edit_text(
        "📚 <b>ALL DATABASE RENAME</b>\n\n"
        "🔍 <b>Preview</b> pehle OLD → NEW logic check karega, DB change nahi karega.\n"
        "✏️ <b>Rename All</b> actual configured DBs update karega.",
        reply_markup=_all_mode_buttons()
    )


@Client.on_callback_query(filters.regex(r"^rename_all_(preview|confirm)$") & filters.user(ADMINS))
async def rename_all_cb(client: Client, query: CallbackQuery):
    await query.answer()
    user_id = query.from_user.id

    if _cancel_flags.get(user_id) is False:
        await query.message.reply_text("⏳ Ek rename process already chal rahi hai.")
        return

    dry_run = query.matches[0].group(1) == "preview"
    _cancel_flags[user_id] = False

    status_msg = await query.message.edit_text(
        f"{'🔍 Preview' if dry_run else '✏️ Live rename'} shuru ho raha hai... ⏳",
        reply_markup=_cancel_button()
    )
    asyncio.create_task(
        process_rename_db(client, status_msg, user_id, dry_run)
    )


@Client.on_callback_query(filters.regex(r"^rename_menu_single$") & filters.user(ADMINS))
async def rename_menu_single_cb(client: Client, query: CallbackQuery):
    await query.answer()
    _single_sessions[query.from_user.id] = {"stage": "await_file"}
    await query.message.edit_text(
        "📄 <b>Single File Rename</b>\n\n"
        "Jis file ko rename karna hai us message ko <b>reply/forward</b> karo "
        "ya file yahin send karo.\n\n"
        "⚠️ File configured <b>ia_filterdb MEDIA_DBS</b> me honi chahiye.",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("❌ ᴄᴀɴᴄᴇʟ", callback_data="rename_single_cancel")]
        ])
    )


@Client.on_callback_query(filters.regex(r"^rename_menu_back$") & filters.user(ADMINS))
async def rename_menu_back_cb(client: Client, query: CallbackQuery):
    await query.answer()
    await query.message.edit_text(
        "🛠️ <b>FILE RENAME MANAGER</b>\n\nChoose an option:",
        reply_markup=_menu_button()
    )


@Client.on_callback_query(filters.regex(r"^rename_menu_close$") & filters.user(ADMINS))
async def rename_menu_close_cb(client: Client, query: CallbackQuery):
    await query.answer()
    await query.message.delete()


@Client.on_callback_query(filters.regex(r"^rename_single_cancel$") & filters.user(ADMINS))
async def rename_single_cancel_cb(client: Client, query: CallbackQuery):
    _single_sessions.pop(query.from_user.id, None)
    await query.answer("Cancelled")
    try:
        await query.message.edit_text("❌ Single-file rename cancelled.")
    except Exception:
        pass


# ============================================================
# SINGLE FILE OPTIONS
# ============================================================
async def _single_action(query: CallbackQuery, action: str, token: str):
    user_id = query.from_user.id
    entry = _single_tokens.get(token)

    if not entry or entry[0] != user_id:
        await query.answer("⚠️ Session expired. /rename dobara kholo.", show_alert=True)
        return

    _single_sessions[user_id] = {
        "stage": "await_input",
        "action": action,
        "token": token,
        "file_id": entry[1],
        "db_index": entry[2],
    }

    prompts = {
        "full": "✏️ <b>Full filename bhejo</b>\n\nExample:\n<code>Movie Name 2026 Hindi 1080P WEB-DL.mkv</code>",
        "remove": "🧹 <b>Kaunsa word/phrase remove karna hai?</b>\n\nExact word bhejo.",
        "title": "🎬 <b>Naya title bhejo</b>\n\nBaaki year/quality/language/source automatically preserve honge.",
        "addlang": "➕ <b>Language add karo</b>\n\nExample: <code>Hindi</code>, <code>Telugu</code>, <code>English</code>",
        "remlang": "➖ <b>Language remove karo</b>\n\nExample: <code>Hindi</code>",
    }

    await query.answer()
    await query.message.edit_text(
        prompts[action],
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("❌ ᴄᴀɴᴄᴇʟ", callback_data="rename_single_cancel")]
        ])
    )


@Client.on_callback_query(filters.regex(r"^rename_single_(full|remove|title|addlang|remlang):") & filters.user(ADMINS))
async def rename_single_action_cb(client: Client, query: CallbackQuery):
    action, token = query.data.split(":", 1)
    action = action.replace("rename_single_", "")
    await _single_action(query, action, token)


@Client.on_callback_query(filters.regex(r"^rename_single_auto:") & filters.user(ADMINS))
async def rename_single_auto_cb(client: Client, query: CallbackQuery):
    user_id = query.from_user.id
    token = query.data.split(":", 1)[1]
    entry = _single_tokens.get(token)

    if not entry or entry[0] != user_id:
        await query.answer("⚠️ Session expired.", show_alert=True)
        return

    _, file_id, db_index = entry
    media_cls = MEDIA_DBS[db_index]
    doc = await media_cls.collection.find_one({"_id": file_id})

    if not doc:
        await query.answer("❌ File DB me nahi mili.", show_alert=True)
        return

    new_name = build_new_name(doc)
    if not new_name:
        await query.answer("⏭️ Is file me koi change nahi hai.", show_alert=True)
        return

    ok, text = await _update_single(media_cls.collection, doc, new_name)
    await query.answer("Done" if ok else "Failed", show_alert=not ok)
    await query.message.edit_text(text)


# ============================================================
# SINGLE FILE INPUT HANDLER
# ============================================================
@Client.on_message(filters.user(ADMINS) & ~filters.command(["rename", "rename_db"]))
async def rename_single_input(client: Client, message: Message):
    user_id = message.from_user.id
    session = _single_sessions.get(user_id)

    if not session:
        return

    stage = session.get("stage")

    # File selection stage
    if stage == "await_file":
        file_id, file_name = _message_file_id(message)
        if not file_id:
            await message.reply_text("❌ Document/video/audio file bhejo ya us file ko reply karo.")
            return

        await _show_single_file(message, file_id, file_name)
        return

    if stage != "await_input":
        return

    token = session.get("token")
    entry = _single_tokens.get(token)
    if not entry:
        _single_sessions.pop(user_id, None)
        await message.reply_text("⚠️ Rename session expire ho gaya. /rename dobara kholo.")
        return

    _, file_id, db_index = entry
    media_cls = MEDIA_DBS[db_index]
    doc = await media_cls.collection.find_one({"_id": file_id})

    if not doc:
        _single_sessions.pop(user_id, None)
        await message.reply_text("❌ Selected file DB me nahi mili.")
        return

    action = session["action"]
    user_text = (message.text or message.caption or "").strip()

    if not user_text:
        await message.reply_text("❌ Text bhejo.")
        return

    old_name = doc.get("file_name") or "Unnamed File"
    caption = doc.get("caption") or ""

    try:
        if action == "full":
            # Full filename means exactly what admin enters.
            new_name = user_text
            if not os.path.splitext(new_name)[1]:
                old_ext = os.path.splitext(old_name)[1]
                new_name += old_ext

        elif action == "remove":
            new_name = re.sub(
                re.escape(user_text),
                "",
                old_name,
                flags=re.IGNORECASE
            )
            new_name = _build_from_parts(
                new_name,
                caption=caption,
            )

        elif action == "title":
            new_name = _build_from_parts(
                old_name,
                caption=caption,
                forced_title=user_text,
            )

        elif action in ("addlang", "remlang"):
            extracted = extract_languages_quality(old_name)
            languages = list(extracted.get("languages") or [])
            lang = _canonical_language(user_text)

            if action == "addlang":
                if any(_language_matches(x, lang) for x in languages):
                    await message.reply_text(f"⏭️ <b>{lang}</b> already present hai.")
                    return
                languages.append(lang)
            else:
                before = len(languages)
                languages = [x for x in languages if not _language_matches(x, lang)]
                if len(languages) == before:
                    await message.reply_text(f"⏭️ <b>{lang}</b> filename me nahi hai.")
                    return

            languages = apply_dual_multi_audio_tag(
                languages,
                old_name.lower()
            )
            new_name = _build_from_parts(
                old_name,
                caption=caption,
                forced_languages=languages,
            )

        else:
            await message.reply_text("❌ Unknown rename action.")
            return

        ok, text = await _update_single(media_cls.collection, doc, new_name)

    except Exception as e:
        logger.error(f"Single rename error: {e}", exc_info=True)
        ok, text = False, f"❌ Rename error: <code>{e}</code>"

    _single_sessions.pop(user_id, None)

    await message.reply_text(text)


# ============================================================
# OLD / COMMAND COMPATIBILITY
# ============================================================
@Client.on_message(filters.command("rename_db_confirm") & filters.user(ADMINS))
async def rename_db_confirm_legacy(client: Client, message: Message):
    """
    Backward compatibility: old scripts/users can still call
    /rename_db_confirm and get a live all-DB rename.
    """
    user_id = message.from_user.id
    status_msg = await message.reply_text(
        "✏️ Live DB rename shuru ho raha hai... ⏳",
        reply_markup=_cancel_button()
    )
    asyncio.create_task(process_rename_db(client, status_msg, user_id, False))


@Client.on_callback_query(filters.regex("^rename_db_cancel$") & filters.user(ADMINS))
async def rename_db_cancel_cb(client: Client, query: CallbackQuery):
    _cancel_flags[query.from_user.id] = True
    await query.answer("🛑 Cancel signal bhej diya.", show_alert=True)
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
