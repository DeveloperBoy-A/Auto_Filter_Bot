import re
import os
import html
import time
import string
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
    resolve_title,
    resolve_title_from_caption,
    is_series_file,
)
from info import ADMINS

try:
    from database.ia_filterdb import clear_search_cache
except Exception:  # pragma: no cover
    def clear_search_cache():
        return None

logger = logging.getLogger(__name__)

# ============================================================
#  FONT STYLE  →  𝐒ᴇᴀʀᴄʜ Gʀᴏᴜᴘ
#  (pehle word ka pehla letter Bold, baaki words ka pehla letter
#   Capital, baaki sab Small-Caps)
#  <code> / <pre> ke andar ka text (filenames, commands) as-it-is rehta hai.
# ============================================================
_SMALL_CAPS = dict(zip(
    "abcdefghijklmnopqrstuvwxyz",
    "ᴀʙᴄᴅᴇꜰɢʜɪᴊᴋʟᴍɴᴏᴘǫʀꜱᴛᴜᴠᴡxʏᴢ",
))
_BOLD_CAPS = {c: chr(0x1D400 + i) for i, c in enumerate(string.ascii_uppercase)}
_TAG_RE = re.compile(r"(<[^>]+>)")
_WORD_RE = re.compile(r"[A-Za-z]+")


def F(text: str) -> str:
    """Apply the bot font style to a message / button label (HTML-safe)."""
    if not text:
        return text
    state = {"first": True}

    def _word(m: re.Match) -> str:
        w = m.group(0)
        head, tail = w[0], w[1:]
        if state["first"]:
            state["first"] = False
            head = _BOLD_CAPS[head.upper()]
        else:
            head = head.upper()
        return head + "".join(_SMALL_CAPS.get(c.lower(), c) for c in tail)

    out = []
    skip = 0
    for part in _TAG_RE.split(text):
        if not part:
            continue
        if part.startswith("<") and part.endswith(">"):
            low = part.lower()
            if low.startswith(("<code", "<pre")):
                skip += 1
            elif low.startswith(("</code", "</pre")):
                skip = max(0, skip - 1)
            out.append(part)
        elif skip:
            out.append(part)
        else:
            out.append(_WORD_RE.sub(_word, part))
    return "".join(out)


def B(text: str, **kw) -> InlineKeyboardButton:
    """Styled InlineKeyboardButton."""
    return InlineKeyboardButton(F(text), **kw)


def _esc(value) -> str:
    return html.escape(str(value), quote=False)


# ── Per-admin cancel flag ─────────────────────────────────────
_cancel_flags: dict[int, bool] = {}

# Which single-file actions wait for a text message from the admin
INPUT_ACTIONS = {"full", "title", "year", "replace", "remove", "addlang", "remlang"}
INPUT_TIMEOUT = 300  # seconds


class _StateDict(dict):
    """Single-file rename state.

    Input-wait states auto-expire after INPUT_TIMEOUT. pmfilter.py reads this dict
    via .get(), so an expired state stops blocking normal PM search automatically.
    """

    def get(self, key, default=None):
        st = super().get(key, default)
        if st and st.get("action") in INPUT_ACTIONS:
            if time.time() - st.get("ts", 0) > INPUT_TIMEOUT:
                st["action"] = "selected"
        return st


# Single-file rename state: {user_id: {"action": ..., "doc": ..., "media_cls": ..., "ts": ...}}
_single_states: dict[int, dict] = _StateDict()

# Keyword-scoped batch rename: waiting for keyword text {user_id: timestamp}
_kw_wait: dict[int, float] = {}
# Scope used by the last dry-run (keyword or None for everything)
_batch_scope: dict[int, str | None] = {}
# Last single rename, for undo {user_id: {...}}
_undo: dict[int, dict] = {}
# Needs-review list shown to admin {user_id: [(media_cls, doc), ...]}
_review_lists: dict[int, list] = {}


# ── Keyboards ─────────────────────────────────────────────────
def _rename_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            B("📄 Single File Rename", callback_data="rename_single"),
            B("🗄️ All DB Rename", callback_data="rename_all_db"),
        ],
        [
            B("🔎 Keyword Rename", callback_data="rename_keyword"),
            B("📊 DB Stats", callback_data="rename_stats"),
        ],
        [
            B("📋 Needs Review", callback_data="rename_review"),
            B("ℹ️ Rename Help", callback_data="rename_help"),
        ],
    ])


def _single_actions() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            B("✨ Auto Clean", callback_data="rename_action:auto"),
            B("👁 Preview Auto", callback_data="rename_action:preview"),
        ],
        [
            B("✏️ Full Name", callback_data="rename_action:full"),
            B("🎬 Change Title", callback_data="rename_action:title"),
        ],
        [B("📝 Title From Caption", callback_data="rename_action:captitle")],
        [
            B("📅 Set Year", callback_data="rename_action:year"),
            B("🎞 Set Quality", callback_data="rename_quality"),
        ],
        [
            B("🧹 Remove Word", callback_data="rename_action:remove"),
            B("🔁 Replace Text", callback_data="rename_action:replace"),
        ],
        [
            B("➕ Add Language", callback_data="rename_action:addlang"),
            B("➖ Remove Language", callback_data="rename_action:remlang"),
        ],
        [
            B("ℹ️ File Info", callback_data="rename_info"),
            B("↩️ Undo Last", callback_data="rename_undo"),
        ],
        [B("🔙 Rename Menu", callback_data="rename_menu")],
    ])


def _quality_buttons() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            B("480P", callback_data="rename_q:480P"),
            B("720P", callback_data="rename_q:720P"),
            B("1080P", callback_data="rename_q:1080P"),
        ],
        [
            B("1440P", callback_data="rename_q:1440P"),
            B("2160P", callback_data="rename_q:2160P"),
        ],
        [B("🔙 Back", callback_data="rename_back_actions")],
    ])


def _cancel_button() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [[B("🛑 Cancel", callback_data="rename_db_cancel")]]
    )


def _done_button() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [[B("✅ Done", callback_data="rename_db_done")]]
    )


def _confirm_buttons() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [B("✅ Confirm Live Rename", callback_data="rename_db_confirm")],
        [B("✅ Done", callback_data="rename_db_done")],
    ])


def _back_menu_button() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[B("🔙 Back", callback_data="rename_menu")]])


# ── build_new_name: ia_filterdb.save_file ka EXACT sequence logic ────
def _build(doc, title_override: str | None = None):
    """Returns (new_name | None, final_title, title_source).

    title_source: 'filename' | 'caption' | 'override' | 'review'.
    'review' = title detect nahi hui -> naam ban hi nahi sakta (new_name None).
    """
    original_name: str = doc.get("file_name") or "Unnamed File"
    base_name, ext = os.path.splitext(original_name)
    caption_text: str = doc.get("caption") or ""

    # Already-renamed files: purana release tag hata ke scan karo (title me na ghuse)
    base_name = re.sub(re.escape(RELEASE_TAG), " ", base_name).strip()

    text_to_scan = f"{base_name}{ext} {caption_text}"

    extracted = extract_languages_quality(text_to_scan)

    # --- Smart defaults (same as save_file) ---
    if not extracted.get("resolution"):
        extracted["resolution"] = "720P"

    if not extracted.get("source"):
        extracted["source"] = "WEB-DL"

    audio_codecs = [
        "Dolby TrueHD", "Dolby Atmos", "DTS-X", "DTS-HD",
        "DDP 7.1", "DDP 5.1", "DD 5.1", "DD 2.0",
        "DTS 5.1", "AAC 5.1", "AAC",
    ]
    audio_tags = extracted.get("extra_tags", [])
    has_audio = any(codec in audio_tags for codec in audio_codecs)

    if not has_audio:
        if "AAC" not in audio_tags:
            audio_tags.append("AAC")
        extracted["extra_tags"] = audio_tags

    # Smart title (save_file jaisa): filename -> caption -> review
    if title_override and title_override.strip():
        cleaned_title, title_source = title_override.strip(), "override"
    else:
        cleaned_title, title_source = resolve_title(original_name, caption_text)
    if title_source == "review":
        return None, "", "review"

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
    #      (ia_filterdb.save_file ke same order me)
    # ==========================================

    # [1] Title
    if final_title: add_unique(final_title)

    # [2] Release Year (Title ke turant baad)
    if extracted.get("year"): add_unique(extracted["year"])

    # [3] Title Part / Volume / Chapter
    if extracted.get("title_part"): add_unique(extracted["title_part"])

    # [4] Season & Episode
    if extracted.get("season_episode"): add_unique(extracted["season_episode"])

    # [5] Episode Title
    if extracted.get("season_episode") and extracted.get("episode_title"):
        add_unique(extracted["episode_title"])

    # [6] Series Status
    if extracted.get("series_status"): add_unique(extracted["series_status"])

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

    for acodec in ["Dolby TrueHD", "Dolby Atmos", "DTS-X", "DTS-HD", "DDP 7.1", "DDP 5.1",
                   "DD 5.1", "DD 2.0", "DTS 5.1", "AAC 5.1", "AAC"]:
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
    file_name = re.sub(r"\s+", " ", file_name)
    file_name = file_name + ext.lower()
    file_name = re.sub(r"\s+\.", ".", file_name)

    return (file_name if file_name != original_name else None), final_title, title_source


def build_new_name(doc, title_override: str | None = None) -> str | None:
    """Canonical (save_file-style) naam. None = koi change nahi / title detect nahi hui."""
    return _build(doc, title_override)[0]


def _fmt_title(t: str) -> str:
    words = []
    for word in (t or "").split():
        words.append(word[0].upper() + word[1:] if len(word) > 1 else word.upper())
    return " ".join(words)


def _fields_for(new_name: str, caption: str = "") -> dict:
    """DB me update hone wale fields (file_name + title + year + media_type)."""
    fields = {
        "file_name": new_name,
        "media_type": "series" if is_series_file(new_name) else "movie",
    }
    t, _src = resolve_title(new_name, caption)
    if t:
        fields["title"] = _fmt_title(t)
    year = extract_languages_quality(new_name).get("year")
    if year:
        fields["year"] = year
    return fields


def _canon(name: str, caption: str = "") -> str:
    """Canonical (save_file-style) name; falls back to the given name if unchanged."""
    return build_new_name({"file_name": name, "caption": caption}) or name


# ── Background Process ───────────────────────────────────────────
async def process_rename_db(client: Client, status_msg: Message, user_id: int,
                            dry_run: bool, keyword: str | None = None):
    mode_label = "🔍 DRY-RUN (preview only)" if dry_run else "✏️ LIVE UPDATE"
    scope_label = f"🔎 Keyword: <code>{_esc(keyword)}</code>\n" if keyword else ""
    logger.info(f"Command received: /rename_db | Mode: {mode_label} | User: {user_id} | Keyword: {keyword}")

    updated = 0
    skipped = 0
    reviewed = 0
    errors = 0
    total = 0
    cancelled = False
    samples: list[tuple[str, str]] = []

    query_filter: dict = {}
    if keyword:
        query_filter = {"file_name": {"$regex": re.escape(keyword), "$options": "i"}}

    _db_labels = ["Primary DB", "Secondary DB", "Tertiary DB", "Quaternary DB", "Quinary DB"]
    collections_to_process = [
        (_db_labels[i] if i < len(_db_labels) else f"DB {i + 1}", media_cls.collection)
        for i, media_cls in enumerate(MEDIA_DBS)
    ]

    logger.info("✅ Process start log: Starting batch processing.")
    last_edit_time = time.time()
    db_label = ""

    for db_label, collection in collections_to_process:
        if cancelled:
            break

        total_docs = await collection.count_documents(query_filter)
        if total_docs == 0:
            continue

        db_seen = 0
        cursor = collection.find(query_filter, {"_id": 1, "file_name": 1, "caption": 1})

        async for doc in cursor:
            if _cancel_flags.get(user_id):
                logger.warning(f"⚠️ Cancel requested log: Process stopped by user {user_id}")
                cancelled = True
                break

            total += 1
            db_seen += 1
            try:
                old_name = doc.get("file_name")
                new_name, _t, t_src = _build(doc)

                if t_src == "review":
                    # Title detect nahi hui -> galat naam mat do, review list me daalo
                    reviewed += 1
                    logger.warning(f"⚠️ NEEDS REVIEW | DB: {db_label} | {old_name}")
                    if not dry_run:
                        await collection.update_one({"_id": doc["_id"]}, {"$set": {"needs_review": True}})
                elif new_name is None:
                    skipped += 1
                else:
                    logger.info(f"🔄 OLD → NEW | DB: {db_label} | OLD: {old_name} → NEW: {new_name}")
                    if dry_run and len(samples) < 8:
                        samples.append((old_name or "Unknown", new_name))
                    if not dry_run:
                        await collection.update_one(
                            {"_id": doc["_id"]},
                            {
                                "$set": _fields_for(new_name, doc.get("caption") or ""),
                                "$unset": {"needs_review": ""},
                            }
                        )
                    updated += 1

            except Exception as e:
                errors += 1
                logger.error(f"❌ Error: DB: {db_label} | _id={doc.get('_id')} | {e}", exc_info=True)

            if total % 100 == 0:
                await asyncio.sleep(0.05)

            if total % 500 == 0:
                logger.info(f"📊 Progress: {total} files processed. (DB: {db_label} | Updated: {updated}, Skipped: {skipped}, Errors: {errors})")

            if time.time() - last_edit_time > 5:
                percentage = (db_seen / total_docs) * 100 if total_docs > 0 else 0
                progress_text = (
                    f"<b>{mode_label}</b>\n\n"
                    f"{scope_label}"
                    f"📁 <b>{db_label}</b>\n"
                    f"📊 Progress : <b>{percentage:.2f}%</b> ({db_seen}/{total_docs})\n"
                    f"✅ Renamed  : <b>{updated}</b>\n"
                    f"⏭️ Skipped  : <b>{skipped}</b>\n"
                    f"⚠️ Review   : <b>{reviewed}</b>\n"
                    f"❌ Errors   : <b>{errors}</b>"
                )
                try:
                    await status_msg.edit_text(F(progress_text), reply_markup=_cancel_button())
                    last_edit_time = time.time()
                except FloodWait as e:
                    logger.warning(f"FloodWait of {e.value} seconds encountered. Sleeping...")
                    await asyncio.sleep(e.value)
                except Exception:
                    pass

    # ── Final report ─────────────────────────────────────────
    _cancel_flags.pop(user_id, None)

    if not dry_run:
        try:
            clear_search_cache()
        except Exception:
            pass

    if cancelled:
        logger.info("✅ Cancel completed log: Process aborted successfully.")
        try:
            await status_msg.edit_text(
                F(
                    f"<b>🛑 Cancelled by admin</b>\n\n"
                    f"📊 Processed : <b>{total}</b>\n"
                    f"✏️ {'Would rename' if dry_run else 'Renamed'} : <b>{updated}</b>\n"
                    f"⏭️ No change  : <b>{skipped}</b>\n"
                    f"⚠️ Review     : <b>{reviewed}</b>\n"
                    f"❌ Errors     : <b>{errors}</b>"
                ),
                reply_markup=None
            )
        except Exception:
            pass
        return

    logger.info(f"✅ Final summary: Total: {total}, Updated: {updated}, Skipped: {skipped}, Errors: {errors}")
    action_word = "Would rename" if dry_run else "Renamed"

    sample_text = ""
    if dry_run and samples:
        lines = []
        for old, new in samples:
            lines.append(f"<code>{_esc(old[:70])}</code>\n➜ <code>{_esc(new[:90])}</code>")
        sample_text = "\n\n<b>📝 Sample Preview</b>\n" + "\n\n".join(lines)

    footer = (
        "\n\n⚠️ <i>Ye sirf preview tha. Actual rename ke liye neeche Confirm button dabao.</i>"
        if dry_run else
        "\n\n✅ <i>Sabhi files successfully rename ho gayi hain.</i>"
    )

    body = (
        f"<b>{mode_label} — Complete ✅</b>\n\n"
        f"{scope_label}"
        f"📊 Total scanned : <b>{total}</b>\n"
        f"✏️ {action_word}   : <b>{updated}</b>\n"
        f"⏭️ No change     : <b>{skipped}</b>\n"
        f"⚠️ Needs review  : <b>{reviewed}</b>\n"
        f"❌ Errors        : <b>{errors}</b>"
    )
    text = body + sample_text + footer
    if len(text) > 3900:  # Telegram 4096 limit safety
        text = body + footer

    try:
        await status_msg.edit_text(
            F(text),
            reply_markup=_confirm_buttons() if (dry_run and updated) else _done_button()
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
    # /rename = cancel any pending text input
    _single_states.pop(message.from_user.id, None)
    _kw_wait.pop(message.from_user.id, None)
    await message.reply_text(
        F(
            "<b>🛠️ FILE RENAME MANAGER</b>\n\n"
            "Yahan se single file ya configured <code>MEDIA_DBS</code> ki saari files rename kar sakte ho.\n\n"
            "📄 <b>Single File</b> → file par Reply karke <code>/renamefile</code> bhejo.\n"
            "🗄️ <b>All DB</b> → ia_filterdb ke current naming sequence ke according batch rename\n"
            "🔎 <b>Keyword Rename</b> → sirf kisi keyword wali files rename karo\n"
            "📊 <b>DB Stats</b> → kitni files rename hui / baaki hain\n\n"
            "<i>Single file ke liye pehle file ko bot ke kisi chat me send/forward karo.</i>"
        ),
        reply_markup=_rename_menu()
    )


@Client.on_callback_query(filters.regex(r"^rename_menu$") & filters.user(ADMINS))
async def rename_menu_cb(client: Client, query: CallbackQuery):
    _single_states.pop(query.from_user.id, None)
    _kw_wait.pop(query.from_user.id, None)
    await query.answer()
    await query.message.edit_text(
        F(
            "<b>🛠️ FILE RENAME MANAGER</b>\n\n"
            "📄 Single file ko custom rename karo ya 🗄️ saari configured DBs ko canonical format me rename karo."
        ),
        reply_markup=_rename_menu()
    )


@Client.on_callback_query(filters.regex(r"^rename_single$") & filters.user(ADMINS))
async def rename_single_cb(client: Client, query: CallbackQuery):
    _single_states.pop(query.from_user.id, None)
    await query.answer()
    await query.message.edit_text(
        F(
            "<b>📄 SINGLE FILE RENAME</b>\n\n"
            "Ab jis file ko rename karna hai us message par <b>Reply</b> karo aur\n"
            "<code>/renamefile</code> command bhejo.\n\n"
            "✅ Original file ho ya forwarded file — dono chalegi.\n"
            "❌ Is mode me normal search/message disturb nahi hoga.\n\n"
            "Example: file par Reply → <code>/renamefile</code>"
        ),
        reply_markup=_back_menu_button()
    )


@Client.on_callback_query(filters.regex(r"^rename_all_db$") & filters.user(ADMINS))
async def rename_all_db_cb(client: Client, query: CallbackQuery):
    user_id = query.from_user.id
    if _cancel_flags.get(user_id) is False:
        await query.answer(F("Ek rename process already chal raha hai!"), show_alert=True)
        return
    _cancel_flags[user_id] = False
    _batch_scope[user_id] = None
    await query.answer(F("DB scan start ho raha hai..."))
    status_msg = await query.message.edit_text(
        F("<b>🔍 DRY-RUN PREVIEW</b>\n\nConfigured MEDIA_DBS scan ho rahi hain... ⏳"),
        reply_markup=_cancel_button()
    )
    asyncio.create_task(process_rename_db(client, status_msg, user_id, True))


@Client.on_callback_query(filters.regex(r"^rename_keyword$") & filters.user(ADMINS))
async def rename_keyword_cb(client: Client, query: CallbackQuery):
    user_id = query.from_user.id
    _single_states.pop(user_id, None)
    _kw_wait[user_id] = time.time()
    await query.answer()
    await query.message.edit_text(
        F(
            "<b>🔎 KEYWORD RENAME</b>\n\n"
            "Wo keyword bhejo jiski files rename karni hain.\n"
            "Example: <code>Pushpa</code> ya <code>S01</code> ya <code>CAMRip</code>\n\n"
            "Pehle dry-run preview aayega, phir Confirm button.\n\n"
            "<i>Cancel ke liye /rename bhej sakte ho.</i>"
        ),
        reply_markup=_back_menu_button()
    )


@Client.on_callback_query(filters.regex(r"^rename_stats$") & filters.user(ADMINS))
async def rename_stats_cb(client: Client, query: CallbackQuery):
    await query.answer(F("Stats nikal raha hu..."))
    _db_labels = ["Primary DB", "Secondary DB", "Tertiary DB", "Quaternary DB", "Quinary DB"]
    tag_regex = {"$regex": re.escape(RELEASE_TAG) + r"\.[A-Za-z0-9]+$"}
    lines = []
    all_total = all_done = all_review = 0
    for i, media_cls in enumerate(MEDIA_DBS):
        label = _db_labels[i] if i < len(_db_labels) else f"DB {i + 1}"
        try:
            total = await media_cls.collection.count_documents({})
            done = await media_cls.collection.count_documents({"file_name": tag_regex})
            review = await media_cls.collection.count_documents({"needs_review": True})
        except Exception as e:
            lines.append(f"🗄️ <b>{label}</b> : ❌ <code>{_esc(e)}</code>")
            continue
        all_total += total
        all_done += done
        all_review += review
        lines.append(
            f"🗄️ <b>{label}</b>\n   📁 Total: <b>{total}</b> | ✅ Renamed: <b>{done}</b> | ⏳ Baaki: <b>{total - done}</b>"
            f" | ⚠️ Review: <b>{review}</b>"
        )
    pct = (all_done / all_total * 100) if all_total else 0
    await query.message.edit_text(
        F(
            "<b>📊 RENAME STATS</b>\n\n"
            + "\n\n".join(lines)
            + f"\n\n📈 Overall: <b>{all_done}/{all_total}</b> (<b>{pct:.1f}%</b>) | ⚠️ Review: <b>{all_review}</b>"
            + "\n\n<i>Renamed = filename release tag se end hota hai.</i>"
        ),
        reply_markup=InlineKeyboardMarkup([
            [B("🔄 Refresh", callback_data="rename_stats")],
            [B("🔙 Back", callback_data="rename_menu")],
        ])
    )


@Client.on_callback_query(filters.regex(r"^rename_review$") & filters.user(ADMINS))
async def rename_review_cb(client: Client, query: CallbackQuery):
    """Wo files jinki title auto detect nahi hui (needs_review)."""
    user_id = query.from_user.id
    await query.answer()
    items: list = []
    total_review = 0
    limit = 10
    for media_cls in MEDIA_DBS:
        coll = media_cls.collection
        try:
            total_review += await coll.count_documents({"needs_review": True})
            if len(items) < limit:
                async for doc in coll.find({"needs_review": True}).limit(limit - len(items)):
                    items.append((media_cls, doc))
        except Exception as e:
            logger.error(f"Review list failed: {e}", exc_info=True)

    _review_lists[user_id] = items
    if not items:
        await query.message.edit_text(
            F("<b>📋 NEEDS REVIEW</b>\n\n✅ Koi file review ke liye pending nahi hai."),
            reply_markup=_back_menu_button()
        )
        return

    lines = [f"<b>{i}.</b> <code>{_esc((d.get('file_name') or 'Unknown')[:60])}</code>" for i, (_m, d) in enumerate(items, 1)]
    rows = []
    row = []
    for i in range(1, len(items) + 1):
        row.append(InlineKeyboardButton(str(i), callback_data=f"rename_pick:{i - 1}"))
        if len(row) == 5:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    rows.append([B("🔄 Refresh", callback_data="rename_review")])
    rows.append([B("🔙 Back", callback_data="rename_menu")])
    await query.message.edit_text(
        F(
            f"<b>📋 NEEDS REVIEW</b> ({total_review} total)\n\n"
            + "\n".join(lines)
            + "\n\nNumber dabao, phir <b>Title From Caption</b> ya <b>Change Title</b> use karo."
        ),
        reply_markup=InlineKeyboardMarkup(rows)
    )


@Client.on_callback_query(filters.regex(r"^rename_pick:(\d+)$") & filters.user(ADMINS))
async def rename_pick_cb(client: Client, query: CallbackQuery):
    user_id = query.from_user.id
    idx = int(query.data.split(":", 1)[1])
    items = _review_lists.get(user_id) or []
    if idx >= len(items):
        await query.answer(F("List purani ho gayi, refresh karo."), show_alert=True)
        return
    media_cls, doc = items[idx]
    fresh = await media_cls.collection.find_one({"_id": doc["_id"]})
    if not fresh:
        await query.answer(F("File ab DB me nahi hai."), show_alert=True)
        return
    _kw_wait.pop(user_id, None)
    _single_states[user_id] = {
        "action": "selected",
        "file_id": fresh["_id"],
        "media_cls": media_cls,
        "doc": fresh,
        "ts": time.time(),
    }
    await query.answer()
    await query.message.edit_text(_selected_text(_single_states[user_id]), reply_markup=_single_actions())


@Client.on_callback_query(filters.regex(r"^rename_help$") & filters.user(ADMINS))
async def rename_help_cb(client: Client, query: CallbackQuery):
    await query.answer()
    await query.message.edit_text(
        F(
            "<b>ℹ️ RENAME HELP</b>\n\n"
            "📄 <b>Single File</b>\n"
            "• File par Reply → <code>/renamefile</code>\n"
            "• ✨ Auto Clean → ia_filterdb naming rules\n"
            "• 👁 Preview Auto → bina save kiye naya naam dekho\n"
            "• ✏️ Full Name → poora filename manually set\n"
            "• 🎬 Change Title → sirf title replace\n"
            "• 📝 Title From Caption → caption se title lo\n"
            "• 📅 Set Year → year change karo\n"
            "• 🎞 Set Quality → 480P/720P/1080P... button se\n"
            "• 🧹 Remove Word → filename se word/phrase remove\n"
            "• 🔁 Replace Text → <code>old | new</code> format me bhejo\n"
            "• ➕/➖ Language → language add / remove\n"
            "• ↩️ Undo Last → last rename wapas\n\n"
            "📋 <b>Needs Review</b>\n"
            "Jin files ki title filename aur caption dono se nahi mili, wo yaha aati hain.\n\n"
            "🗄️ <b>All DB / Keyword</b>\n"
            "Pehle dry-run preview chalega, phir Confirm button se live rename.\n\n"
            "<i>Extension automatically preserve hoti hai.</i>"
        ),
        reply_markup=_back_menu_button()
    )


@Client.on_callback_query(filters.regex(r"^rename_back_actions$") & filters.user(ADMINS))
async def rename_back_actions_cb(client: Client, query: CallbackQuery):
    state = _single_states.get(query.from_user.id)
    if not state:
        await query.answer(F("Pehle file select karo."), show_alert=True)
        return
    state["action"] = "selected"
    await query.answer()
    await query.message.edit_text(_selected_text(state), reply_markup=_single_actions())


@Client.on_callback_query(filters.regex(r"^rename_action:(auto|preview|captitle|full|title|year|replace|remove|addlang|remlang)$") & filters.user(ADMINS))
async def rename_action_cb(client: Client, query: CallbackQuery):
    action = query.data.split(":", 1)[1]
    user_id = query.from_user.id
    state = _single_states.get(user_id)
    if not state or not state.get("file_id"):
        await query.answer(F("Pehle file select karo."), show_alert=True)
        return

    if action == "auto":
        await query.answer(F("Auto clean ho raha hai..."))
        result = await _apply_single_rename(state, action, None, user_id)
        await query.message.edit_text(F(result), reply_markup=_single_actions())
        return

    if action == "captitle":
        await query.answer(F("Caption se title nikal raha hu..."))
        result = await _apply_single_rename(state, action, None, user_id)
        await query.message.edit_text(F(result), reply_markup=_single_actions())
        return

    if action == "preview":
        await query.answer()
        old_name = state["doc"].get("file_name") or "Unnamed File"
        new_name, _t, t_src = _build(state["doc"])
        if t_src == "review":
            text = (
                "<b>👁 PREVIEW</b>\n\n⚠️ Title auto detect nahi hui (filename aur caption dono me).\n"
                "<b>Change Title</b> ya <b>Title From Caption</b> use karo.\n\n"
                f"<code>{_esc(old_name)}</code>"
            )
        elif new_name is None:
            text = f"<b>👁 PREVIEW</b>\n\nℹ️ Koi change nahi hoga.\n\n<code>{_esc(old_name)}</code>"
        else:
            text = (
                "<b>👁 PREVIEW (save nahi hua)</b>\n\n"
                f"OLD:\n<code>{_esc(old_name)}</code>\n\nNEW:\n<code>{_esc(new_name)}</code>"
            )
        await query.message.edit_text(F(text), reply_markup=_single_actions())
        return

    prompts = {
        "full": "✏️ <b>Full Name</b>\n\nNaya poora filename bhejo, extension optional hai.",
        "title": "🎬 <b>Change Title</b>\n\nNaya title bhejo. Baaki quality/language/OTT etc. preserve rahenge.",
        "year": "📅 <b>Set Year</b>\n\nNaya year bhejo, jaise <code>2024</code>.",
        "replace": "🔁 <b>Replace Text</b>\n\nIs format me bhejo:\n<code>old text | new text</code>",
        "remove": "🧹 <b>Remove Word</b>\n\nJo word/phrase hatana hai woh bhejo.",
        "addlang": "➕ <b>Add Language</b>\n\nLanguage bhejo, jaise <code>Hindi</code> / <code>Tamil</code>.",
        "remlang": "➖ <b>Remove Language</b>\n\nLanguage bhejo, jaise <code>Hindi</code> / <code>Tamil</code>.",
    }
    state["action"] = action
    state["ts"] = time.time()
    await query.answer()
    await query.message.edit_text(
        F(prompts[action] + "\n\n<i>Cancel ke liye /rename bhej sakte ho.</i>"),
        reply_markup=InlineKeyboardMarkup([[B("🔙 Rename Menu", callback_data="rename_menu")]])
    )


@Client.on_callback_query(filters.regex(r"^rename_quality$") & filters.user(ADMINS))
async def rename_quality_menu_cb(client: Client, query: CallbackQuery):
    state = _single_states.get(query.from_user.id)
    if not state or not state.get("file_id"):
        await query.answer(F("Pehle file select karo."), show_alert=True)
        return
    state["action"] = "selected"
    await query.answer()
    await query.message.edit_text(
        F("<b>🎞 SET QUALITY</b>\n\nNeeche se resolution choose karo:"),
        reply_markup=_quality_buttons()
    )


@Client.on_callback_query(filters.regex(r"^rename_q:(480P|720P|1080P|1440P|2160P)$") & filters.user(ADMINS))
async def rename_quality_set_cb(client: Client, query: CallbackQuery):
    user_id = query.from_user.id
    state = _single_states.get(user_id)
    if not state or not state.get("file_id"):
        await query.answer(F("Pehle file select karo."), show_alert=True)
        return
    quality = query.data.split(":", 1)[1]
    await query.answer(F(f"{quality} set ho raha hai..."))
    result = await _apply_single_rename(state, "quality", quality, user_id)
    await query.message.edit_text(F(result), reply_markup=_single_actions())


@Client.on_callback_query(filters.regex(r"^rename_info$") & filters.user(ADMINS))
async def rename_info_cb(client: Client, query: CallbackQuery):
    state = _single_states.get(query.from_user.id)
    if not state or not state.get("file_id"):
        await query.answer(F("Pehle file select karo."), show_alert=True)
        return
    doc = state["doc"]
    size = doc.get("file_size")
    size_txt = f"{size / (1024 * 1024):.1f} MB" if isinstance(size, (int, float)) else "Unknown"
    await query.answer()
    await query.message.edit_text(
        F(
            "<b>ℹ️ FILE INFO</b>\n\n"
            f"🗄️ DB: <code>{state['media_cls'].__name__}</code>\n"
            f"📄 Name:\n<code>{_esc(doc.get('file_name', 'Unknown'))}</code>\n\n"
            f"🎬 Title: <code>{_esc(doc.get('title') or 'N/A')}</code>\n"
            f"📅 Year: <code>{_esc(doc.get('year') or 'N/A')}</code>\n"
            f"📺 Type: <code>{_esc(doc.get('media_type') or 'N/A')}</code>\n"
            f"📦 Size: <code>{size_txt}</code>\n"
            f"🆔 ID: <code>{_esc(doc.get('_id'))}</code>"
        ),
        reply_markup=_single_actions()
    )


@Client.on_callback_query(filters.regex(r"^rename_undo$") & filters.user(ADMINS))
async def rename_undo_cb(client: Client, query: CallbackQuery):
    user_id = query.from_user.id
    snap = _undo.get(user_id)
    if not snap:
        await query.answer(F("Undo ke liye kuch nahi hai."), show_alert=True)
        return
    try:
        await snap["media_cls"].collection.update_one({"_id": snap["_id"]}, {"$set": snap["fields"]})
        clear_search_cache()
    except Exception as e:
        logger.error(f"Undo failed: {e}", exc_info=True)
        await query.answer(F("Undo fail ho gaya."), show_alert=True)
        return

    state = _single_states.get(user_id)
    if state and state.get("doc") and state["doc"].get("_id") == snap["_id"]:
        state["doc"].update(snap["fields"])
    restored = snap["fields"].get("file_name")
    _undo.pop(user_id, None)
    await query.answer(F("Undo ho gaya!"))
    await query.message.edit_text(
        F(f"<b>↩️ UNDO DONE</b>\n\nRestored:\n<code>{_esc(restored)}</code>"),
        reply_markup=_single_actions() if state else _back_menu_button()
    )


@Client.on_callback_query(filters.regex(r"^rename_db_confirm$") & filters.user(ADMINS))
async def rename_db_confirm_cb(client: Client, query: CallbackQuery):
    user_id = query.from_user.id
    if _cancel_flags.get(user_id) is False:
        await query.answer(F("Ek rename process already chal raha hai!"), show_alert=True)
        return
    _cancel_flags[user_id] = False
    keyword = _batch_scope.get(user_id)
    await query.answer(F("Live rename start ho raha hai..."))
    status_msg = await query.message.edit_text(
        F("<b>✏️ LIVE UPDATE</b>\n\nConfigured MEDIA_DBS me actual rename start ho raha hai... ⏳"),
        reply_markup=_cancel_button()
    )
    asyncio.create_task(process_rename_db(client, status_msg, user_id, False, keyword))


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


_RES_RE = re.compile(r"(?i)(?<![A-Za-z0-9])(?:4320|2160|1440|1080|720|576|480|360|240|144)[pi](?![A-Za-z0-9])|(?<![A-Za-z0-9])(?:8K|4K|2K|UHD)(?![A-Za-z0-9])")


async def _apply_single_rename(state: dict, action: str, value: str | None, user_id: int | None = None):
    media_cls = state["media_cls"]
    doc = state["doc"]
    old_name = doc.get("file_name") or "Unnamed File"
    base, ext = os.path.splitext(old_name)
    caption = doc.get("caption") or ""

    if action == "full":
        new_name = (value or "").strip()
        if not new_name:
            return "❌ Filename empty nahi ho sakta."
        if not os.path.splitext(new_name)[1]:
            new_name += ext
        new_name = re.sub(r"[\\/:*?\"<>|]", "", new_name).strip()

    elif action == "title":
        if not value or not value.strip():
            return "❌ Title empty nahi ho sakta."
        # Naya title de ke poora naam canonical order me dobara banao
        new_name = build_new_name({"file_name": old_name, "caption": caption}, title_override=value.strip())
        if new_name is None:
            new_name = old_name

    elif action == "captitle":
        cap_title = resolve_title_from_caption(caption)
        if not cap_title:
            return (
                "❌ <b>Caption me bhi sahi title nahi mila.</b>\n\n"
                "<b>Change Title</b> se manually title bhejo."
            )
        new_name = build_new_name({"file_name": old_name, "caption": caption}, title_override=cap_title)
        if new_name is None:
            new_name = old_name

    elif action == "year":
        year = (value or "").strip().strip("()[]")
        if not re.fullmatch(r"(?:19|20)\d{2}", year):
            return "❌ Valid year bhejo (1900-2099), jaise <code>2024</code>."
        extracted = extract_languages_quality(f"{old_name} {caption}")
        old_year = extracted.get("year")
        working = base
        if old_year:
            working = re.sub(rf"[\(\[]?{re.escape(str(old_year))}[\)\]]?", " ", working, count=1)
        working = re.sub(r"\s+", " ", working).strip()
        new_name = _canon(f"{working} ({year}){ext}", caption)

    elif action == "quality":
        working = _RES_RE.sub(" ", base)
        working = re.sub(r"\s+", " ", working).strip()
        new_name = _canon(f"{working} {value}{ext}", caption)

    elif action == "replace":
        if not value or "|" not in value:
            return "❌ Format galat hai. Aise bhejo: <code>old text | new text</code>"
        old_txt, new_txt = (p.strip() for p in value.split("|", 1))
        if not old_txt:
            return "❌ Old text empty nahi ho sakta."
        if not re.search(re.escape(old_txt), old_name, flags=re.I):
            return f"❌ Filename me <code>{_esc(old_txt)}</code> nahi mila."
        working = re.sub(re.escape(old_txt), lambda _m: new_txt, old_name, flags=re.I)
        working = re.sub(r"\s+", " ", working).strip()
        new_name = _canon(working, caption)

    elif action == "remove":
        if not value or not value.strip():
            return "❌ Word empty nahi ho sakta."
        new_name = re.sub(re.escape(value.strip()), "", old_name, flags=re.I)
        new_name = re.sub(r"\s+", " ", new_name).strip()
        new_name = _canon(new_name, caption)

    elif action in ("addlang", "remlang"):
        if not value or not value.strip():
            return "❌ Language empty nahi ho sakti."
        lang = value.strip()
        if action == "addlang":
            working = f"{os.path.splitext(old_name)[0]} {lang}{ext}"
        else:
            working = re.sub(rf"(?i)(?<![A-Za-z]){re.escape(lang)}(?![A-Za-z])", "", old_name)
            working = re.sub(r"\s+", " ", working).strip()
        new_name = _canon(working, caption)

    else:  # auto
        new_name = build_new_name(doc) or old_name

    if new_name == old_name:
        return f"<b>ℹ️ No change</b>\n\n<code>{_esc(old_name)}</code>"

    # Prevent duplicate filename for the same size when another record already has it.
    size = doc.get("file_size")
    duplicate_query = {"file_name": new_name}
    if size is not None:
        duplicate_query["file_size"] = size
    duplicate = await media_cls.collection.find_one({**duplicate_query, "_id": {"$ne": doc["_id"]}})
    if duplicate:
        return f"❌ <b>Duplicate file name already exists.</b>\n\n<code>{_esc(new_name)}</code>"

    new_fields = _fields_for(new_name, caption)

    # Undo snapshot
    if user_id is not None:
        _undo[user_id] = {
            "media_cls": media_cls,
            "_id": doc["_id"],
            "fields": {k: doc.get(k) for k in new_fields},
        }

    await media_cls.collection.update_one(
        {"_id": doc["_id"]},
        {"$set": new_fields, "$unset": {"needs_review": ""}},
    )
    doc.update(new_fields)
    doc.pop("needs_review", None)
    try:
        clear_search_cache()
    except Exception:
        pass
    return (
        f"<b>✅ Rename Successful</b>\n\n"
        f"OLD:\n<code>{_esc(old_name)}</code>\n\n"
        f"NEW:\n<code>{_esc(new_name)}</code>"
    )


def _selected_text(state: dict) -> str:
    doc = state["doc"]
    review = ""
    if doc.get("needs_review"):
        cap = re.sub(r"<[^>]+>", "", doc.get("caption") or "").strip()
        cap_line = cap.splitlines()[0][:80] if cap else "N/A"
        review = (
            "⚠️ <b>Title auto detect nahi hui.</b>\n"
            f"📝 Caption: <code>{_esc(cap_line)}</code>\n\n"
        )
    return F(
        f"<b>✅ FILE SELECTED</b>\n\n"
        f"🗄️ DB: <code>{state['media_cls'].__name__}</code>\n"
        f"📄 Current Name:\n<code>{_esc(doc.get('file_name', 'Unknown'))}</code>\n\n"
        f"{review}"
        "Ab neeche se rename operation choose karo:"
    )


@Client.on_message(filters.command("renamefile") & filters.user(ADMINS))
async def rename_single_select_command(client: Client, message: Message):
    """Select the file from a replied message. This never intercepts normal searches."""
    reply = message.reply_to_message
    if not reply:
        await message.reply_text(F(
            "❌ <b>File select nahi hui.</b>\n\n"
            "Jis document/video/audio ko rename karna hai, us message par <b>Reply</b> karo "
            "aur phir <code>/renamefile</code> bhejo.\n\n"
            "Example: <i>File Message → Reply → /renamefile</i>"
        ))
        return

    media = reply.document or reply.video or reply.audio
    if not media:
        await message.reply_text(F("❌ Reply kiya hua message document/video/audio nahi hai."))
        return

    user_id = message.from_user.id
    media_name = getattr(media, "file_name", None)
    media_size = getattr(media, "file_size", None)
    media_id = getattr(media, "file_id", None)

    await message.reply_text(F("🔎 <b>Database me exact file search ho rahi hai...</b> ⏳"))
    media_cls, doc = await _find_single_doc(media_id, media_name, media_size)

    if not doc:
        await message.reply_text(F(
            "❌ <b>Ye file configured MEDIA_DBS me nahi mili.</b>\n\n"
            f"📄 File: <code>{_esc(media_name or 'Unknown')}</code>\n"
            f"📦 Size: <code>{media_size or 'Unknown'}</code>\n\n"
            "Agar file bot ke DB me saved hai, usi saved file ko reply karke "
            "<code>/renamefile</code> dobara bhejo."
        ))
        return

    _kw_wait.pop(user_id, None)
    _single_states[user_id] = {
        "action": "selected",
        "file_id": media_id,
        "media_cls": media_cls,
        "doc": doc,
        "ts": time.time(),
    }

    await message.reply_text(_selected_text(_single_states[user_id]), reply_markup=_single_actions())


# group=-7 : sabse pehle chalta hai (group 0..N regroup se collide nahi karta)
# aur message.stop_propagation() se PM search / baaki handlers tak text nahi jata.
@Client.on_message(filters.text & filters.private & filters.incoming & filters.user(ADMINS), group=-7)
async def rename_single_text_input(client: Client, message: Message):
    """Consume text only when a rename button explicitly requested input."""
    user_id = message.from_user.id
    value = message.text or ""

    # ── Keyword batch rename input ──
    kw_ts = _kw_wait.get(user_id)
    if kw_ts:
        if time.time() - kw_ts > INPUT_TIMEOUT:
            _kw_wait.pop(user_id, None)
        elif not value.startswith("/"):
            _kw_wait.pop(user_id, None)
            keyword = value.strip()
            if not keyword:
                return
            if _cancel_flags.get(user_id) is False:
                await message.reply_text(F("⏳ Ek rename process already chal raha hai!"))
                message.stop_propagation()
            _cancel_flags[user_id] = False
            _batch_scope[user_id] = keyword
            status_msg = await message.reply_text(
                F(f"<b>🔍 DRY-RUN PREVIEW</b>\n\n🔎 Keyword: <code>{_esc(keyword)}</code>\nScan ho raha hai... ⏳"),
                reply_markup=_cancel_button()
            )
            asyncio.create_task(process_rename_db(client, status_msg, user_id, True, keyword))
            message.stop_propagation()
        else:
            return

    # ── Single file rename input ──
    state = _single_states.get(user_id)  # expiry handled inside _StateDict.get
    if not state or state.get("action") not in INPUT_ACTIONS:
        return
    if value.startswith("/"):
        return

    action = state["action"]
    try:
        result = await _apply_single_rename(state, action, value, user_id)
    except Exception as e:
        logger.error(f"Single rename failed: {e}", exc_info=True)
        result = f"❌ <b>Rename fail hua.</b>\n\n<code>{_esc(e)}</code>"
    state["action"] = "selected"
    await message.reply_text(F(result), reply_markup=_single_actions())
    message.stop_propagation()   # ← FIX: ab ye text PM search me nahi jayega


# ── /rename confirm remains available for actual batch update ─────────
@Client.on_message(filters.command("rename") & filters.user(ADMINS))
async def rename_confirm_command(client: Client, message: Message):
    args = message.command
    if len(args) < 2 or args[1].lower() != "confirm":
        return
    user_id = message.from_user.id
    if _cancel_flags.get(user_id) is False:
        await message.reply_text(F("⏳ Ek DB rename process already background mein chal rahi hai!"))
        return
    _cancel_flags[user_id] = False
    keyword = _batch_scope.get(user_id)
    status_msg = await message.reply_text(
        F("<b>✏️ LIVE UPDATE</b>\n\nConfigured MEDIA_DBS me actual rename start ho raha hai... ⏳"),
        reply_markup=_cancel_button()
    )
    asyncio.create_task(process_rename_db(client, status_msg, user_id, False, keyword))


# ── Cancel callback ───────────────────────────────────────────
@Client.on_callback_query(filters.regex("^rename_db_cancel$") & filters.user(ADMINS))
async def rename_db_cancel_cb(client: Client, query: CallbackQuery):
    _cancel_flags[query.from_user.id] = True
    await query.answer(F("🛑 Cancel signal bheja gaya! Task ruk raha hai..."), show_alert=True)
    try:
        await query.message.edit_reply_markup(reply_markup=None)
    except Exception:
        pass


@Client.on_callback_query(filters.regex("^rename_db_done$") & filters.user(ADMINS))
async def rename_db_done_cb(client: Client, query: CallbackQuery):
    await query.answer(F("👍 Done!"))
    try:
        await query.message.delete()
    except Exception:
        pass
