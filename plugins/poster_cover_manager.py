"""
Admin commands:

  /setposter <naam>   (photo pe reply / photo+caption / ya image URL)
      Movie update channel post ka poster badalta hai. Aage us naam ki har nayi file ke
      update post me yahi poster aayega. Jis naam ka poster pehle aaya hi nahi tha
      (blank), uske liye bhi set ho jata hai.

  Update channel me hi: kisi update post par REPLY karke photo bhejo jiske caption me /setposter
      likha ho (ya reply me text "/setposter <image-url>"). Bot post ka naam khud pehchan leta hai,
      poster set karke naya post bhejta hai aur purana delete kar deta hai.

  /setcover <naam> [year]   (photo pe reply / photo+caption / ya image URL)
      File ka cover set karta hai (info.py me COVERX=False rahe tab bhi manual cover lagta hai).
        - "Is title ki saari files" : strict match (title + year + movie/series), aage aane
          wali isi title ki files par bhi apne aap lagta hai.
        - "Sirf ek specific file"   : bot file maangega, usko forward kar do.

  /delcover <naam> [year]
      Manual cover hata deta hai (title wala + us title ki files ke file-wale bhi).
"""
import io
import os
import re
import time
import uuid
import asyncio
import logging
from html import escape

import aiohttp
from PIL import Image
from pyrogram import Client, filters, enums, StopPropagation
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton

from info import ADMINS, MOVIE_UPDATE_CHANNEL
from database.users_chats_db import db
from database.ia_filterdb import MEDIA_DBS, unpack_new_file_id
from database.manual_media_db import (
    title_regex, norm_title,
    set_poster_override,
    set_title_cover, set_file_cover, clear_file_covers, clear_title_cover,
)

logger = logging.getLogger(__name__)

# plugins/Dreamxfutures/telegraph.py me jo key hai wahi default; chaho to env me IMGBB_API_KEY daal do
IMGBB_API_KEY = os.environ.get("IMGBB_API_KEY", "d4cc3d793cb68b2c6cdc2197588e895c")

PENDING = {}        # token -> state dict
AWAIT_FILE = {}     # admin_id -> token  (specific-file cover ke liye file ka intezaar)
PENDING_TTL = 600   # 10 min
YEAR_RE = re.compile(r"^(?:19|20)\d{2}$")
HTML = enums.ParseMode.HTML

USAGE_POSTER = (
    "<b>🖼️ Poster set karne ka tareeka:</b>\n\n"
    "Photo pe reply karke:\n<code>/setposter Pushpa 2 2024</code>\n"
    "ya photo bhejte time caption me wahi command, ya image URL:\n"
    "<code>/setposter Pushpa 2 2024 https://site/img.jpg</code>\n\n"
    "Naam wahi likho jaisa update post me aata hai."
)
USAGE_COVER = (
    "<b>🎞️ Cover set karne ka tareeka:</b>\n\n"
    "Photo pe reply karke:\n<code>/setcover Pushpa 2 2024</code>\n"
    "ya photo bhejte time caption me wahi command, ya image URL:\n"
    "<code>/setcover Pushpa 2 2024 https://site/img.jpg</code>\n\n"
    "Year optional hai (same title ke multiple year ho to bot poochega).\n"
    "Cover hatane ke liye: <code>/delcover Pushpa 2 2024</code>"
)


# ============================================================ small helpers
def _new_token(data):
    now = time.time()
    for t in [t for t, d in PENDING.items() if now - d["ts"] > PENDING_TTL]:
        PENDING.pop(t, None)
    for a in [a for a, t in AWAIT_FILE.items() if t not in PENDING]:
        AWAIT_FILE.pop(a, None)
    token = uuid.uuid4().hex[:8]
    data["ts"] = now
    PENDING[token] = data
    return token


def _get_pending(token, admin_id):
    d = PENDING.get(token)
    if not d or time.time() - d["ts"] > PENDING_TTL or d.get("admin") != admin_id:
        return None
    return d


def _parse_args(message):
    """'/cmd Pushpa 2 2024 https://x/y.jpg' -> ('Pushpa 2 2024', 'https://x/y.jpg')"""
    raw = message.text or message.caption or ""
    parts = raw.split(None, 1)
    arg = parts[1].strip() if len(parts) > 1 else ""
    url = None
    m = re.search(r"(https?://\S+)", arg)
    if m:
        url = m.group(1)
        arg = (arg[:m.start()] + " " + arg[m.end():]).strip()
    return arg, url


async def _download_url(url):
    try:
        async with aiohttp.ClientSession() as s:
            async with s.get(url, timeout=aiohttp.ClientTimeout(total=25)) as r:
                if r.status != 200:
                    return None
                return await r.read()
    except Exception as e:
        logger.warning(f"[PCM] url download failed: {e}")
        return None


def _normalize_image(data: bytes):
    img = Image.open(io.BytesIO(data))
    w, h = img.size
    img = img.convert("RGB")
    out = io.BytesIO()
    img.save(out, "JPEG", quality=92)
    return out.getvalue(), (w, h)


async def _upload_imgbb(data: bytes):
    try:
        form = aiohttp.FormData()
        form.add_field("key", IMGBB_API_KEY)
        form.add_field("image", data, filename="image.jpg", content_type="image/jpeg")
        async with aiohttp.ClientSession() as s:
            async with s.post("https://api.imgbb.com/1/upload", data=form,
                              timeout=aiohttp.ClientTimeout(total=60)) as r:
                j = await r.json(content_type=None)
                if r.status == 200 and j.get("success"):
                    return j["data"]["url"]
                logger.warning(f"[PCM] imgbb upload failed: {r.status} {j}")
    except Exception as e:
        logger.warning(f"[PCM] imgbb upload error: {e}")
    return None


async def _resolve_image(client, message, url_arg, allow_reply=True):
    """
    Return (url, is_landscape) ya (None, error_text) ya (None, None) agar image di hi nahi gayi.
    Image imgbb pe upload hoti hai (permanent URL) taaki baaki poora code URL se hi chale.
    """
    src = None
    for m in ((message, message.reply_to_message) if allow_reply else (message,)):
        if not m:
            continue
        if m.photo or (m.document and (m.document.mime_type or "").startswith("image/")):
            src = m
            break

    if url_arg:
        raw = await _download_url(url_arg)
        if not raw:
            return None, "❌ URL se image download nahi hui."
    elif src:
        bio = await client.download_media(src, in_memory=True)
        raw = bytes(bio.getbuffer()) if bio else None
        if not raw:
            return None, "❌ Photo download nahi hui."
    else:
        return None, None

    try:
        jpg, (w, h) = await asyncio.to_thread(_normalize_image, raw)
    except Exception:
        return None, "❌ Ye valid image nahi hai."

    url = await _upload_imgbb(jpg)
    if not url:
        if url_arg:
            url = url_arg          # upload fail ho gaya to direct URL hi use kar lo
        else:
            return None, "❌ Image upload (imgbb) fail hua, thodi der baad try karo."
    return url, (w > h)


def _cancel_btn(token):
    return [InlineKeyboardButton("❌ Cancel", callback_data=f"pcm_x:{token}")]


# ============================================================ /setposter
@Client.on_message(filters.private & filters.command("setposter") & filters.user(ADMINS), group=-4)
async def setposter_cmd(client, message):
    try:
        await _setposter(client, message)
    except StopPropagation:
        raise
    except Exception as e:
        logger.exception("setposter failed")
        await message.reply_text(f"<b>❗ Error:</b> <code>{escape(str(e))}</code>", parse_mode=HTML)
    raise StopPropagation


async def _setposter(client, message):
    title_text, url_arg = _parse_args(message)
    if not title_text or not title_regex(title_text):
        return await message.reply_text(USAGE_POSTER, parse_mode=HTML)

    wait = await message.reply_text("⏳ Image process ho rahi hai...")
    url, extra = await _resolve_image(client, message, url_arg)
    if not url:
        return await wait.edit_text(extra or USAGE_POSTER, parse_mode=HTML)
    is_bd = bool(extra)
    admin = message.from_user.id

    exact_rx = title_regex(title_text, True)
    exact = [d["_id"] async for d in db.movie_updates.find(
        {"_id": {"$regex": exact_rx, "$options": "i"}}, {"_id": 1}).limit(10)]
    if len(exact) == 1:
        return await _apply_poster(wait, exact[0], url, is_bd, admin)

    cands = exact
    if not cands:
        cands = [d["_id"] async for d in db.movie_updates.find(
            {"_id": {"$regex": title_regex(title_text, False), "$options": "i"}}, {"_id": 1}).limit(8)]

    if not cands:
        # abhi is naam ka update post/doc hai hi nahi -> aage ke liye save kar do
        await set_poster_override(title_text, url, is_bd, admin)
        return await wait.edit_text(
            f"✅ <b>Poster save ho gaya:</b> <code>{escape(title_text)}</code>\n\n"
            "Abhi is naam ka update post nahi mila. Jab is naam ki file aayegi tab ye poster lagega.\n"
            "<i>(Naam update post wale naam se same hona chahiye, year ke saath agar aata ho.)</i>",
            parse_mode=HTML)

    token = _new_token({"kind": "poster", "admin": admin, "url": url, "bd": is_bd, "cands": cands})
    rows = [[InlineKeyboardButton(c[:60], callback_data=f"pcm_pp:{token}:{i}")] for i, c in enumerate(cands)]
    rows.append(_cancel_btn(token))
    await wait.edit_text("<b>Kaunsa update post?</b> Neeche se chuno 👇",
                         reply_markup=InlineKeyboardMarkup(rows), parse_mode=HTML)


async def _apply_poster(msg, base_name, url, is_bd, admin_id):
    await set_poster_override(base_name, url, is_bd, admin_id)
    await db.movie_updates.update_one(
        {"_id": base_name}, {"$set": {"poster_url": url, "is_backdrop": bool(is_bd)}})
    token = _new_token({"kind": "refresh", "admin": admin_id, "base": base_name})
    await msg.edit_text(
        f"✅ <b>Poster set:</b> <code>{escape(base_name)}</code>\n\n"
        "Ab is naam ki har nayi file ke update post me yahi poster aayega.\n"
        "Channel ka purana post abhi badalna ho to neeche button dabao "
        "(naya post jayega, purana delete ho jayega).",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton(
            "🔄 Abhi update post refresh karo", callback_data=f"pcm_rf:{token}")]]),
        parse_mode=HTML)



# ============================================================ update channel me reply se /setposter
async def _del_later(msg, sec=6):
    await asyncio.sleep(sec)
    try:
        await msg.delete()
    except Exception:
        pass


@Client.on_message(filters.chat(MOVIE_UPDATE_CHANNEL) & filters.reply & filters.command("setposter"),
                   group=-4)
async def channel_setposter(client, message):
    """Update post par reply + (photo caption me /setposter) ya (/setposter <url>)."""
    try:
        replied = message.reply_to_message
        doc = await db.movie_updates.find_one({"message_id": replied.id}, {"_id": 1})
        if not doc:
            note = await message.reply_text(
                "❌ Ye update post DB me nahi mila (sirf latest update post par kaam karta hai).")
            asyncio.create_task(_del_later(note))
            asyncio.create_task(_del_later(message))
            raise StopPropagation

        _, url_arg = _parse_args(message)
        # allow_reply=False: reply wale post ki photo (purana galat poster) galti se na uth jaye
        url, extra = await _resolve_image(client, message, url_arg, allow_reply=False)
        if not url:
            note = await message.reply_text(
                extra or "📸 Post par reply karke photo bhejo jiske caption me <code>/setposter</code> ho, "
                         "ya <code>/setposter &lt;image-url&gt;</code> likho.", parse_mode=HTML)
            asyncio.create_task(_del_later(note, 10))
            asyncio.create_task(_del_later(message, 10))
            raise StopPropagation

        base_name = doc["_id"]
        by = getattr(message, "author_signature", None) or (message.from_user.id if message.from_user else None)
        await set_poster_override(base_name, url, bool(extra), by)
        await db.movie_updates.update_one(
            {"_id": base_name}, {"$set": {"poster_url": url, "is_backdrop": bool(extra)}})

        from plugins.channel import refresh_update_post          # lazy import
        ok = await refresh_update_post(client, base_name)        # naya post + purana delete
        try:
            await message.delete()                               # admin ka command/photo hata do
        except Exception:
            pass
        if not ok:
            note = await client.send_message(
                MOVIE_UPDATE_CHANNEL, "❌ Poster save ho gaya par post refresh nahi hua (logs check karo).")
            asyncio.create_task(_del_later(note, 10))
    except StopPropagation:
        raise
    except Exception:
        logger.exception("channel setposter failed")
    raise StopPropagation

# ============================================================ cover: strict matching
def _year_filter(year):
    return year if year else {"$in": [None, ""]}


def _type_filter(mtype):
    return {"$in": ["movie", None]} if mtype == "movie" else mtype


async def _group_query(match):
    """Strict match karne wali files ko (year, movie/series) ke hisaab se group karo."""
    groups = {}
    for media_cls in MEDIA_DBS:
        pipeline = [
            {"$match": match},
            {"$group": {"_id": {"y": "$year", "m": "$media_type"},
                        "n": {"$sum": 1}, "t": {"$first": "$title"}}},
        ]
        async for g in media_cls.collection.aggregate(pipeline):
            year = (g["_id"].get("y") or "").strip()
            mtype = str(g["_id"].get("m") or "movie").lower()
            cur = groups.setdefault((year, mtype), {"year": year, "mtype": mtype, "n": 0, "title": g.get("t")})
            cur["n"] += g["n"]
    return sorted(groups.values(), key=lambda x: (x["year"], x["mtype"]))


async def _resolve_groups(title_text):
    """
    'Pushpa 2 2024' -> pehle (title='Pushpa 2', year=2024) try, na mile to poora text title maano
    (taaki 'Blade Runner 2049' jaise naam bhi chalein).
    """
    tokens = title_text.split()
    attempts = []
    if len(tokens) > 1 and YEAR_RE.match(tokens[-1]):
        attempts.append((" ".join(tokens[:-1]), tokens[-1]))
    attempts.append((title_text, None))
    for t, y in attempts:
        rx = title_regex(t, True)
        if not rx:
            continue
        match = {"title": {"$regex": rx, "$options": "i"}}
        if y:
            match["year"] = y
        groups = await _group_query(match)
        if groups:
            return groups, t
    return [], title_text


def _group_match(title, g):
    return {
        "title": {"$regex": title_regex(title, True), "$options": "i"},
        "year": _year_filter(g["year"]),
        "media_type": _type_filter(g["mtype"]),
    }


async def _group_file_ids(title, g):
    ids = []
    match = _group_match(title, g)
    for media_cls in MEDIA_DBS:
        async for d in media_cls.collection.find(match, {"_id": 1}):
            ids.append(d["_id"])
    return ids


async def _similar_titles(text):
    rx = title_regex(text, False)
    if not rx:
        return []
    seen = {}
    for media_cls in MEDIA_DBS:
        pipeline = [
            {"$match": {"title": {"$regex": rx, "$options": "i"}}},
            {"$group": {"_id": {"$toLower": "$title"}, "t": {"$first": "$title"}, "n": {"$sum": 1}}},
            {"$limit": 8},
        ]
        async for g in media_cls.collection.aggregate(pipeline):
            cur = seen.setdefault(g["_id"], [g["t"], 0])
            cur[1] += g["n"]
    return sorted(seen.values(), key=lambda x: -x[1])[:8]


def _g_label(title, g):
    kind = "Series" if g["mtype"] == "series" else "Movie"
    return f"{title} ({g['year'] or 'no year'}) • {kind} • {g['n']} files"


# ============================================================ /setcover
@Client.on_message(filters.private & filters.command("setcover") & filters.user(ADMINS), group=-4)
async def setcover_cmd(client, message):
    try:
        await _setcover(client, message)
    except StopPropagation:
        raise
    except Exception as e:
        logger.exception("setcover failed")
        await message.reply_text(f"<b>❗ Error:</b> <code>{escape(str(e))}</code>", parse_mode=HTML)
    raise StopPropagation


async def _setcover(client, message):
    title_text, url_arg = _parse_args(message)
    if not title_text or not title_regex(title_text):
        return await message.reply_text(USAGE_COVER, parse_mode=HTML)

    wait = await message.reply_text("⏳ Image process ho rahi hai...")
    url, extra = await _resolve_image(client, message, url_arg)
    if not url:
        return await wait.edit_text(extra or USAGE_COVER, parse_mode=HTML)

    groups, title = await _resolve_groups(title_text)
    if not groups:
        sim = await _similar_titles(title_text)
        txt = f"❌ <b>Strict match me koi file nahi mili:</b> <code>{escape(title_text)}</code>"
        if sim:
            txt += "\n\n<b>Kuch milte-julte titles:</b>\n" + "\n".join(
                f"• <code>{escape(t)}</code> ({n} files)" for t, n in sim)
        else:
            txt += "\n\n<i>Note: sirf wahi files match hongi jinke DB record me title save hai.</i>"
        return await wait.edit_text(txt, parse_mode=HTML)

    token = _new_token({"kind": "cover", "admin": message.from_user.id, "url": url,
                        "title": title, "groups": groups})
    if len(groups) == 1:
        return await _show_mode_menu(wait, token, 0)

    rows = [[InlineKeyboardButton(_g_label(title, g)[:60], callback_data=f"pcm_cg:{token}:{i}")]
            for i, g in enumerate(groups)]
    rows.append(_cancel_btn(token))
    await wait.edit_text(
        f"<b>{escape(title)}</b> ke ek se zyada versions mile, kaunsa? 👇",
        reply_markup=InlineKeyboardMarkup(rows), parse_mode=HTML)


async def _show_mode_menu(msg, token, gi):
    d = PENDING[token]
    d["gi"] = gi
    g = d["groups"][gi]
    rows = [
        [InlineKeyboardButton(f"✅ Is title ki saari files ({g['n']})", callback_data=f"pcm_cm:{token}:all")],
        [InlineKeyboardButton("🎯 Sirf ek specific file", callback_data=f"pcm_cm:{token}:one")],
        _cancel_btn(token),
    ]
    await msg.edit_text(
        f"🎞️ <b>{escape(_g_label(d['title'], g))}</b>\n\nCover kis par lagana hai?",
        reply_markup=InlineKeyboardMarkup(rows), parse_mode=HTML)


# ============================================================ specific file: file ka intezaar
async def _awaiting_file(_, __, m):
    return bool(m.from_user and m.from_user.id in AWAIT_FILE)

awaiting_file = filters.create(_awaiting_file)


async def _find_media_doc(file_id, media):
    for media_cls in MEDIA_DBS:
        d = await media_cls.collection.find_one({"_id": file_id}, {"file_name": 1})
        if d:
            return d
    # fallback: size + mime se, sirf tab jab bilkul ek hi file mile
    hits = []
    for media_cls in MEDIA_DBS:
        async for d in media_cls.collection.find(
                {"file_size": media.file_size, "mime_type": media.mime_type}, {"file_name": 1}).limit(2):
            hits.append(d)
    return hits[0] if len(hits) == 1 else None


@Client.on_message(filters.private & awaiting_file & (filters.document | filters.video | filters.audio),
                   group=-5)
async def specific_file_cover(client, message):
    uid = message.from_user.id
    token = AWAIT_FILE.pop(uid, None)
    d = _get_pending(token, uid) if token else None
    if not d:
        return
    media = message.document or message.video or message.audio
    file_id, _ = unpack_new_file_id(media.file_id)
    doc = await _find_media_doc(file_id, media)
    if not doc:
        AWAIT_FILE[uid] = token      # dobara try karne do
        await message.reply_text(
            "❌ <b>Ye file database me nahi mili.</b> Channel se file forward karo (ya Cancel dabao).",
            parse_mode=HTML)
        raise StopPropagation
    await set_file_cover(doc["_id"], d["url"], doc.get("file_name"), uid)
    PENDING.pop(token, None)
    await message.reply_text(
        f"✅ <b>Cover set (sirf is file ke liye):</b>\n<code>{escape(str(doc.get('file_name')))}</code>",
        parse_mode=HTML)
    raise StopPropagation


# ============================================================ /delcover
@Client.on_message(filters.private & filters.command("delcover") & filters.user(ADMINS), group=-4)
async def delcover_cmd(client, message):
    try:
        title_text, _ = _parse_args(message)
        if not title_text or not title_regex(title_text):
            await message.reply_text("<b>Use:</b> <code>/delcover Pushpa 2 2024</code>", parse_mode=HTML)
        else:
            groups, title = await _resolve_groups(title_text)
            if not groups:
                await message.reply_text("❌ Strict match me koi file nahi mili.", parse_mode=HTML)
            else:
                lines = []
                for g in groups:
                    t_del = await clear_title_cover(title, g["year"], g["mtype"])
                    f_del = await clear_file_covers(await _group_file_ids(title, g))
                    lines.append(f"• {escape(_g_label(title, g))} → title cover: {t_del}, file covers: {f_del} hataye")
                await message.reply_text("🗑️ <b>Manual cover hata diya:</b>\n" + "\n".join(lines), parse_mode=HTML)
    except Exception as e:
        logger.exception("delcover failed")
        await message.reply_text(f"<b>❗ Error:</b> <code>{escape(str(e))}</code>", parse_mode=HTML)
    raise StopPropagation


# ============================================================ callbacks
@Client.on_callback_query(filters.regex(r"^pcm_") & filters.user(ADMINS), group=-4)
async def pcm_callbacks(client, query):
    try:
        await _pcm_callbacks(client, query)
    except StopPropagation:
        raise
    except Exception as e:
        logger.exception("pcm callback failed")
        try:
            await query.message.edit_text(f"<b>❗ Error:</b> <code>{escape(str(e))}</code>", parse_mode=HTML)
        except Exception:
            pass
    raise StopPropagation


async def _pcm_callbacks(client, query):
    parts = query.data.split(":")
    action, token = parts[0], parts[1]
    uid = query.from_user.id

    if action == "pcm_x":
        PENDING.pop(token, None)
        AWAIT_FILE.pop(uid, None)
        await query.answer("Cancelled")
        return await query.message.edit_text("❌ Cancel kar diya.")

    d = _get_pending(token, uid)
    if not d:
        await query.answer("Ye request expire ho gayi, command dobara chalao.", show_alert=True)
        return

    if action == "pcm_pp":                                   # poster: candidate chuna
        base = d["cands"][int(parts[2])]
        await query.answer()
        PENDING.pop(token, None)
        return await _apply_poster(query.message, base, d["url"], d["bd"], uid)

    if action == "pcm_rf":                                   # poster: post refresh
        from plugins.channel import refresh_update_post      # lazy import
        await query.answer("Refresh ho raha hai...")
        ok = await refresh_update_post(client, d["base"])
        PENDING.pop(token, None)
        return await query.message.edit_text(
            f"✅ Update post refresh ho gaya: <code>{escape(d['base'])}</code>" if ok
            else "❌ Post refresh nahi ho paya (logs check karo).", parse_mode=HTML)

    if action == "pcm_cg":                                   # cover: version (year/type) chuna
        await query.answer()
        return await _show_mode_menu(query.message, token, int(parts[2]))

    if action == "pcm_cm":                                   # cover: all / one
        mode = parts[2]
        g = d["groups"][d["gi"]]
        if mode == "one":
            AWAIT_FILE[uid] = token
            d["ts"] = time.time()
            await query.answer()
            return await query.message.edit_text(
                "📤 <b>Ab jis file ka cover badalna hai wo file bot ko forward karo.</b>\n"
                "<i>(Channel/bot se forward kiya hua hona chahiye)</i>",
                reply_markup=InlineKeyboardMarkup([_cancel_btn(token)]), parse_mode=HTML)

        await query.answer("Lag raha hai...")
        await set_title_cover(d["title"], g["year"], g["mtype"], d["url"], uid)
        # purane file-level covers hata do, warna wo naye title cover ko override karte
        cleared = await clear_file_covers(await _group_file_ids(d["title"], g))
        PENDING.pop(token, None)
        return await query.message.edit_text(
            f"✅ <b>Cover set:</b> {escape(_g_label(d['title'], g))}\n\n"
            "Ye title ki abhi ki saari files par lag gaya, aur aage isi title "
            "(same year/type) ki nayi files par bhi apne aap lagega."
            + (f"\n<i>({cleared} purane file-wale cover hata diye)</i>" if cleared else ""),
            parse_mode=HTML)
