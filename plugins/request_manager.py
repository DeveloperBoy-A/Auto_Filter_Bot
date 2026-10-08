"""
plugins/request_manager.py
══════════════════════════════════════════════════════════════════════
Movie / series request system (replaces the old /request in commands.py)

✦ /request <name> [year] [lang]     – in any group (also #request, or reply to a message)
    • same user asking again          → NOT counted (just told "already requested")
    • other user, same movie          → merged into ONE request, user count +1
    • recently uploaded already       → user is told it's available
✦ Request channel post               – one post per request, live user count
✦ Admin options (💢 Show Options 💢) – Uploaded / Unavailable / Already / Not Released /
    Wrong Spelling / Hindi N/A / Dub N/A / Processing / CAM Only / Need Details /
    Close Silently / Delete  – every option notifies ALL users of that request
✦ AUTO notification on upload        – channel.py calls notify_on_upload(); matching
    requests are closed + every requester gets a DM (fallback: tagged in their group)
✦ Dashboard page in the request channel – pending requests with user counts,
    "Completed" tab with "N users notified". Admin command: /reqpage
══════════════════════════════════════════════════════════════════════
"""
import os
import time
import asyncio
import logging
import math
from datetime import datetime
from collections import defaultdict

import pytz
from pymongo.errors import DuplicateKeyError
from pyrogram import Client, filters, enums, StopPropagation
from pyrogram.types import InlineKeyboardButton as Btn, InlineKeyboardMarkup as Markup
from pyrogram.errors import FloodWait, MessageNotModified, MessageIdInvalid

from info import ADMINS, REQST_CHANNEL, MOVIE_UPDATE_CHANNEL_LINK, GRP_LNK
from utils import temp
from database.requests_db import rq
from request_helpers import (
    ADMIN_ACTIONS, status_meta, display_name, esc, user_mention,
    parse_request, parse_file_info, request_matches_file,
    render_post, render_dashboard, user_message, sc, bs, top, BOX_END, TABS,
)

logger = logging.getLogger(__name__)
HTML = enums.ParseMode.HTML
IST = pytz.timezone("Asia/Kolkata")


def _env_int(name, default):
    try:
        return int(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


def _env_flag(name, default=True):
    return os.environ.get(name, str(default)).strip().lower() not in ("false", "0", "off", "no", "")


# ─────────── tunables (all optional env vars) ─────────── #
AUTO_NOTIFY = _env_flag("REQUEST_AUTO_NOTIFY", True)                 # notify on upload automatically
GROUP_FALLBACK = _env_flag("REQUEST_GROUP_FALLBACK", True)           # tag users in group if DM fails
MAX_OPEN_PER_USER = _env_int("REQUEST_MAX_OPEN_PER_USER", 10)        # 0 = unlimited
UPLOADED_BLOCK_DAYS = _env_int("REQUEST_UPLOADED_BLOCK_DAYS", 7)     # 0 = never block re-requests
REPLY_DELETE_AFTER = _env_int("REQUEST_REPLY_DELETE_AFTER", 60)      # group reply auto-delete (sec)
DM_DELETE_AFTER = _env_int("REQUEST_DM_DELETE_AFTER", 0)             # 0 = keep DM forever
GROUP_NOTE_DELETE_AFTER = _env_int("REQUEST_GROUP_NOTE_DELETE_AFTER", 600)
DASH_PAGE_SIZE = _env_int("REQUEST_DASH_PAGE_SIZE", 8)

# shared state lives on `temp` (plugin modules can be executed twice by the custom loader)
for _name, _val in (("RQ_TASKS", set()), ("RQ_POST_TASKS", {}), ("RQ_DASH_TASK", None),
                    ("RQ_DASH_LOCK", None), ("RQ_MATCH_CACHE", (0.0, []))):
    if not hasattr(temp, _name):
        setattr(temp, _name, _val)


def _spawn(coro):
    """create_task + keep a strong reference so it isn't garbage collected mid-flight."""
    task = asyncio.create_task(coro)
    temp.RQ_TASKS.add(task)
    task.add_done_callback(temp.RQ_TASKS.discard)
    return task


async def _delete_later(*messages, delay=60):
    await asyncio.sleep(delay)
    for m in messages:
        try:
            if m:
                await m.delete()
        except Exception:
            pass


# ═════════════════════════════ markups ═════════════════════════════ #

def post_markup(req):
    rid = str(req["_id"])
    if req.get("open", True):
        row = []
        users = req.get("users") or []
        if users and users[0].get("link"):
            row.append(Btn("👁️ ᴠɪᴇᴡ ʀᴇǫᴜᴇꜱᴛ 👁️", url=users[0]["link"]))
        row.append(Btn("💢 ꜱʜᴏᴡ ᴏᴘᴛɪᴏɴꜱ 💢", callback_data=f"rq#opt#{rid}"))
        return Markup([row])
    icon, label = status_meta(req.get("status"))
    nt = req.get("notified") or {}
    sent = nt.get("dm", 0) + nt.get("group", 0)
    first = f"{icon} {label}" + (f" • 📨 {sent}/{nt.get('total', req.get('user_count', 0))}" if req.get("notified") else "")
    return Markup([[Btn(first, callback_data=f"rq#info#{rid}"), Btn("🔄 Reopen", callback_data=f"rq#reo#{rid}")]])


def options_markup(req):
    rid = str(req["_id"])

    def b(code):
        return Btn(ADMIN_ACTIONS[code]["btn"], callback_data=f"rq#{code}#{rid}")

    return Markup([
        [b("up"), b("un")],
        [b("aa")],
        [b("nr"), b("ws")],
        [b("hn"), b("dd")],
        [b("pr"), b("cm")],
        [b("dt"), b("nu")],
        [Btn("🗑 Delete", callback_data=f"rq#del#{rid}"), Btn("⬅️ Back", callback_data=f"rq#back#{rid}")],
    ])


def user_markup(req):
    rows = []
    first = []
    if MOVIE_UPDATE_CHANNEL_LINK:
        first.append(Btn("📢 ᴜᴘᴅᴀᴛᴇ ᴄʜᴀɴɴᴇʟ", url=MOVIE_UPDATE_CHANNEL_LINK))
    if GRP_LNK:
        first.append(Btn("🔍 Search", url=GRP_LNK))
    if first:
        rows.append(first)
    if req.get("post_link"):
        rows.append([Btn("👁 View Request", url=req["post_link"])])
    return Markup(rows) if rows else None


# ═════════════════════════════ safe telegram helpers ═════════════════════════════ #

async def _safe_edit_post(bot, req):
    """Edit the request-channel post to match the DB doc."""
    if not (REQST_CHANNEL and req.get("post_id")):
        return
    for _ in range(2):
        try:
            await bot.edit_message_text(
                REQST_CHANNEL, req["post_id"], render_post(req),
                reply_markup=post_markup(req), parse_mode=HTML, disable_web_page_preview=True,
            )
            return
        except MessageNotModified:
            return
        except FloodWait as e:
            await asyncio.sleep(e.value + 1)
        except Exception as e:
            logger.warning(f"[REQ] post edit failed for {req.get('_id')}: {e}")
            return


async def refresh_post(bot, rid):
    req = await rq.get(rid)
    if req:
        await _safe_edit_post(bot, req)


def schedule_post_refresh(bot, rid, delay=2.0):
    """Debounced post refresh (many users joining quickly = 1 edit, not 50)."""
    key = str(rid)
    task = temp.RQ_POST_TASKS.get(key)
    if task and not task.done():
        return

    async def _run():
        await asyncio.sleep(delay)
        temp.RQ_POST_TASKS.pop(key, None)
        await refresh_post(bot, rid)

    temp.RQ_POST_TASKS[key] = _spawn(_run())


# ═════════════════════════════ notifications ═════════════════════════════ #

async def _send(bot, chat_id, text, markup=None):
    for _ in range(2):
        try:
            return await bot.send_message(
                chat_id, text, reply_markup=markup, parse_mode=HTML, disable_web_page_preview=True
            )
        except FloodWait as e:
            await asyncio.sleep(e.value + 1)
        except Exception:
            return None
    return None


async def notify_users(bot, req, status, finfo=None):
    """
    DM every requester. Whoever can't be DM'd (never started the bot / blocked it)
    gets tagged in the group where they requested.
    Returns {"total", "dm", "group", "failed", "at"}.
    """
    users = req.get("users") or []
    title = display_name(req)
    n = len(users)
    markup = user_markup(req)
    dm = 0
    undelivered = []

    for u in users:
        text = user_message(status, user_mention(u), title, n, finfo)
        msg = await _send(bot, int(u["id"]), text, markup)
        if msg:
            dm += 1
            if DM_DELETE_AFTER > 0:
                _spawn(_delete_later(msg, delay=DM_DELETE_AFTER))
        else:
            undelivered.append(u)
        await asyncio.sleep(0.06)           # stay well below Telegram's flood limit

    grp = 0
    if undelivered and GROUP_FALLBACK:
        by_group = defaultdict(list)
        for u in undelivered:
            if u.get("group_id"):
                by_group[int(u["group_id"])].append(u)
        for gid, ulist in by_group.items():
            for i in range(0, len(ulist), 10):
                chunk = ulist[i:i + 10]
                tags = " ".join(user_mention(u) for u in chunk)
                body = user_message(status, tags, title, n, finfo)
                body += "\n\n<i>💡 Bot ko PM me /start kar do — agli baar seedha DM milega.</i>"
                msg = await _send(bot, gid, body, markup)
                if msg:
                    grp += len(chunk)
                    _spawn(_delete_later(msg, delay=GROUP_NOTE_DELETE_AFTER))
                await asyncio.sleep(0.3)

    return {"total": n, "dm": dm, "group": grp, "failed": max(n - dm - grp, 0), "at": datetime.utcnow()}


async def deliver(bot, req, status, finfo=None, notify=True):
    """Show new status on the post, notify everybody, store + show the stats, refresh dashboard."""
    try:
        await _safe_edit_post(bot, req)             # status visible immediately
        if notify:
            stats = await notify_users(bot, req, status, finfo)
            await rq.save_notified(req["_id"], stats)
            await refresh_post(bot, req["_id"])      # now with "📨 5/5 sent"
        schedule_dashboard_refresh(bot)
    except Exception:
        logger.exception("[REQ] deliver failed")


# ═════════════════════════════ AUTO notify on upload ═════════════════════════════ #

async def _open_candidates():
    ts, cached = temp.RQ_MATCH_CACHE
    if time.time() - ts < 8:
        return cached
    docs = await rq.open_for_match()
    temp.RQ_MATCH_CACHE = (time.time(), docs)
    return docs


async def notify_on_upload(bot, file_name, caption=""):
    """
    Called by plugins/channel.py after a NEW file was saved.
    Every open request that matches this file is closed (or set to CAM-only for cam prints)
    and ALL its users are notified.
    """
    if not AUTO_NOTIFY or REQST_CHANNEL is None:
        return
    try:
        finfo = parse_file_info(file_name, caption)
        for cand in await _open_candidates():
            if not request_matches_file(cand, finfo):
                continue
            if finfo["is_cam"] and cand.get("status") == "cam_only":
                continue                                    # already told them about CAM
            status = "cam_only" if finfo["is_cam"] else "uploaded"
            matched = {"name": file_name, "quality": finfo["quality"], "auto": True}
            req = await rq.transition(
                cand["_id"], status, keep_open=(status == "cam_only"), by="auto",
                extra={"matched_file": matched},
            )
            if not req:
                continue                                    # someone else (admin / another file) got it first
            temp.RQ_MATCH_CACHE = (0.0, [])                 # invalidate cache
            logger.info(f"[REQ] auto-matched '{display_name(req)}' <- {file_name[:70]} ({status})")
            _spawn(deliver(bot, req, status, finfo=finfo, notify=True))
    except Exception:
        logger.exception("[REQ] notify_on_upload failed")


# ═════════════════════════════ /request command ═════════════════════════════ #

USAGE = (
    f"{top('REQUEST FORMAT', '❌')}\n"
    "┃ अपनी मूवी या सीरीज का नाम तो लिखिये!\n"
    f"{BOX_END}\n\n"
    f"📝 <b>{sc('format')}</b>\n"
    "<code>/request [Name] [Year] [Lang]</code>\n\n"
    f"🎬 <b>{sc('movie example')}</b>\n"
    "<blockquote><code>/request Stree 2 2024 Hindi</code></blockquote>\n"
    f"📺 <b>{sc('series example')}</b>\n"
    "<blockquote><code>/request Mirzapur S03 Hindi</code></blockquote>\n"
    "✨ <i>Tip: Msg par Reply karke bhi request kar sakte ho.</i>"
)

request_filter = filters.regex(r"(?i)(?<!\w)[/#]request(?:@\w+)?(?!\w)")


def _safe_link(message):
    try:
        return message.link
    except Exception:
        return None


def _content_of(message):
    """Text we should parse: own text (minus the command) or the replied message."""
    raw = message.text or message.caption or ""
    own = parse_request(raw)
    if own:
        return own, _safe_link(message)
    r = message.reply_to_message
    if r:
        txt = r.text or r.caption or ""
        for media in ("document", "video", "audio"):
            m = getattr(r, media, None)
            if not txt and m and getattr(m, "file_name", None):
                txt = m.file_name
        parsed = parse_request(txt)
        if parsed:
            return parsed, _safe_link(r)
    return None, None


async def _join_or_create(bot, parsed, entry):
    """
    Returns (state, doc)
      state: new | joined | duplicate | uploaded | limit | error
    """
    uid = entry["id"]
    for _ in range(3):                                      # tiny retry loop for races
        existing = await rq.find_same_open(parsed)
        if existing:
            if any(int(u["id"]) == uid for u in existing.get("users", [])):
                return "duplicate", existing
            if MAX_OPEN_PER_USER and await rq.count_open_for_user(uid) >= MAX_OPEN_PER_USER:
                return "limit", existing
            if await rq.add_user(existing["_id"], entry):
                doc = await rq.get(existing["_id"])
                return "joined", doc
            continue                                        # closed in the meantime -> retry

        done = await rq.find_recent_uploaded(parsed, UPLOADED_BLOCK_DAYS)
        if done:
            return "uploaded", done

        if MAX_OPEN_PER_USER and await rq.count_open_for_user(uid) >= MAX_OPEN_PER_USER:
            return "limit", None

        try:
            doc = await rq.create(parsed, entry)
            return "new", doc
        except DuplicateKeyError:
            continue                                        # someone created it a split second ago
    return "error", None


@Client.on_message(request_filter & filters.group)
async def request_command(bot, message):
    if REQST_CHANNEL is None or not message.from_user:
        return
    user = message.from_user
    if user.is_bot or user.id in temp.BANNED_USERS:
        return

    await rq.ensure_indexes()

    parsed, source_link = _content_of(message)
    if not parsed:
        err = await message.reply_text(USAGE, parse_mode=HTML)
        _spawn(_delete_later(err, message, delay=REPLY_DELETE_AFTER))
        return

    entry = {
        "id": user.id,
        "name": user.first_name or "User",
        "group_id": message.chat.id,
        "group_title": message.chat.title,
        "link": source_link,
        "at": datetime.utcnow(),
    }

    try:
        state, doc = await _join_or_create(bot, parsed, entry)
    except Exception as e:
        logger.exception("[REQ] register failed")
        err = await message.reply_text(f"<b>ᴇʀʀᴏʀ:</b> <code>{esc(e)}</code>", parse_mode=HTML)
        _spawn(_delete_later(err, delay=30))
        return

    name = esc(display_name(parsed))
    bot_btn = Btn("🤖 Start Bot (DM alerts)", url=f"https://t.me/{temp.U_NAME}") if temp.U_NAME else None

    # ───── reply text per outcome ─────
    if state == "new":
        temp.RQ_MATCH_CACHE = (0.0, [])                     # new open request -> upload matcher must see it
        # create the post in the request channel
        try:
            post = await bot.send_message(
                REQST_CHANNEL, render_post(doc), reply_markup=post_markup(doc),
                parse_mode=HTML, disable_web_page_preview=True,
            )
            await rq.set_post(doc["_id"], post.id, _safe_link(post))
            doc["post_id"], doc["post_link"] = post.id, _safe_link(post)
        except Exception as e:
            logger.error(f"[REQ] cannot post in request channel: {e}")
            await rq.delete(doc["_id"])
            err = await message.reply_text(f"<b>ᴇʀʀᴏʀ:</b> <code>{esc(e)}</code>", parse_mode=HTML)
            _spawn(_delete_later(err, delay=30))
            return
        text = (
            f"{top('RECEIVED', '✅')}\n"
            f"┃ 🎬 <b>{name}</b>\n"
            f"┃ 📊 {sc('status')} : ⏳ Pending\n"
            f"┃ 👥 {sc('users')}  : <b>1</b>\n"
            f"{BOX_END}\n\n"
            "🔔 Upload hote hi aapko <b>auto notification</b> mil jayegi.\n"
            "💡 Bot ko PM me start kar lo taaki seedha DM aaye."
        )
    elif state == "joined":
        n = doc.get("user_count", 1)
        text = (
            f"{top('ADDED', '✅')}\n"
            f"┃ 🎬 <b>{name}</b>\n"
            f"┃ 👥 {sc('waiting')} : <b>{n}</b> users\n"
            f"{BOX_END}\n\n"
            "📌 Ye request pehle se maangi ja rahi thi.\n"
            "🔔 Upload hote hi aapko bhi notification milegi."
        )
        schedule_post_refresh(bot, doc["_id"])
    elif state == "duplicate":
        icon, label = status_meta(doc.get("status"))
        text = (
            f"{top('ALREADY REQUESTED', '⚠️')}\n"
            f"┃ 🎬 <b>{esc(display_name(doc))}</b>\n"
            f"┃ 📊 {sc('status')} : {icon} {label}\n"
            f"┃ 👥 {sc('users')}  : <b>{doc.get('user_count', 1)}</b>\n"
            f"{BOX_END}\n\n"
            "✅ Dobara bhejne ki zaroorat nahi — duplicate count nahi hoti."
        )
    elif state == "uploaded":
        text = (
            f"{top('ALREADY UPLOADED', '✅')}\n"
            f"┃ 🎬 <b>{esc(display_name(doc))}</b>\n"
            f"{BOX_END}\n\n"
            "🔍 Ye haal hi me upload ho chuki hai — group me search karke download kar lo."
        )
    elif state == "limit":
        text = (
            f"{top('REQUEST LIMIT', '🚫')}\n"
            f"┃ 📌 {sc('pending')} : <b>{MAX_OPEN_PER_USER}</b> / {MAX_OPEN_PER_USER}\n"
            f"{BOX_END}\n\n"
            "⏳ Kuch requests upload hone ke baad nayi request karo."
        )
    else:
        text = "<b>⚠️ Request abhi process nahi ho payi, thodi der baad try karo.</b>"

    rows = []
    r1 = []
    if MOVIE_UPDATE_CHANNEL_LINK:
        r1.append(Btn("ᴍᴏᴠɪᴇ ᴜᴘᴅᴀᴛᴇ ᴄʜᴀɴɴᴇʟ📢", url=MOVIE_UPDATE_CHANNEL_LINK))
    if doc and doc.get("post_link"):
        r1.append(Btn("ᴠɪᴇᴡ ʀᴇǫᴜᴇꜱᴛ👁‍🗨", url=doc["post_link"]))
    if r1:
        rows.append(r1)
    if state in ("new", "joined") and bot_btn:
        rows.append([bot_btn])
    if state == "uploaded" and GRP_LNK:
        rows.append([Btn("🔍 Search", url=GRP_LNK)])

    reply = await message.reply_text(
        text, reply_markup=Markup(rows) if rows else None, parse_mode=HTML, disable_web_page_preview=True
    )
    if state in ("new", "joined"):
        schedule_dashboard_refresh(bot)
    _spawn(_delete_later(reply, message, delay=REPLY_DELETE_AFTER))


# ═════════════════════════════ admin callbacks ═════════════════════════════ #

def _is_admin(query):
    return bool(query.from_user) and query.from_user.id in ADMINS


async def _handle_rq(bot, query):
    try:
        _, act, rid = query.data.split("#", 2)
    except ValueError:
        return await query.answer()

    if act == "noop":
        return await query.answer()

    req = await rq.get(rid)
    if not req:
        return await query.answer("❌ Request DB me nahi mili (delete ho chuki hai).", show_alert=True)

    # ── read-only info: everybody ──
    if act == "info":
        icon, label = status_meta(req.get("status"))
        nt = req.get("notified")
        if nt:
            txt = (
                f"{icon} {label}\n"
                f"📨 Notified {nt.get('dm', 0) + nt.get('group', 0)}/{nt.get('total', 0)}\n"
                f"💬 DM {nt.get('dm', 0)} • 👥 Group {nt.get('group', 0)} • ❌ Failed {nt.get('failed', 0)}"
            )
        else:
            txt = f"{icon} {label}\n👥 {req.get('user_count', 0)} users"
        return await query.answer(txt[:200], show_alert=True)

    # ── everything below: admins only ──
    if not _is_admin(query):
        return await query.answer("No permission ❌", show_alert=True)

    if act == "opt":
        if not req.get("open", True):
            return await query.answer("Ye request already close hai. Pehle Reopen karo.", show_alert=True)
        await query.message.edit_reply_markup(options_markup(req))
        return await query.answer("Here are the options!")

    if act == "back":
        await query.message.edit_reply_markup(post_markup(req))
        return await query.answer()

    if act == "reo":
        try:
            doc = await rq.reopen(rid)
        except DuplicateKeyError:
            return await query.answer("Same request already open hai, reopen nahi ho sakti.", show_alert=True)
        if not doc:
            return await query.answer("Request pehle se open hai.", show_alert=True)
        temp.RQ_MATCH_CACHE = (0.0, [])
        await _safe_edit_post(bot, doc)
        schedule_dashboard_refresh(bot)
        return await query.answer("🔄 Reopened!")

    if act == "del":
        kb = Markup([[
            Btn("✅ Haan, Delete", callback_data=f"rq#delok#{rid}"),
            Btn("❌ Cancel", callback_data=f"rq#back#{rid}"),
        ]])
        await query.message.edit_reply_markup(kb)
        return await query.answer("Pakka delete karna hai? (users ko kuch nahi bheja jayega)", show_alert=True)

    if act == "delok":
        await rq.delete(rid)
        temp.RQ_MATCH_CACHE = (0.0, [])
        try:
            await query.message.delete()
        except Exception:
            pass
        schedule_dashboard_refresh(bot)
        return await query.answer("🗑 Deleted")

    cfg = ADMIN_ACTIONS.get(act)
    if not cfg:
        return await query.answer()

    doc = await rq.transition(
        rid, cfg["status"], keep_open=cfg["open"], by=query.from_user.id,
        extra=None,
    )
    if not doc:
        return await query.answer("⚠️ Ye request already handle ho chuki hai.", show_alert=True)

    temp.RQ_MATCH_CACHE = (0.0, [])
    icon, label = status_meta(cfg["status"])
    n = doc.get("user_count", 0)
    if cfg["notify"]:
        await query.answer(f"{icon} {label} — {n} users ko notification bhej raha hu…")
    else:
        await query.answer(f"{icon} {label}")
    _spawn(deliver(bot, doc, cfg["status"], notify=cfg["notify"]))


@Client.on_callback_query(filters.regex(r"^rq#"), group=-4)
async def rq_callbacks(bot, query):
    try:
        await _handle_rq(bot, query)
    except StopPropagation:
        raise
    except Exception:
        logger.exception("[REQ] callback failed")
        try:
            await query.answer("⚠️ Error, logs check karo.", show_alert=True)
        except Exception:
            pass
    raise StopPropagation


# ═════════════════════════════ dashboard ═════════════════════════════ #

def _dash_markup(tab, page, pages, stats):
    labels = {
        "p": f"⏳ Pending ({stats['pending']})",
        "u": f"✅ Uploaded ({stats['uploaded']})",
        "r": f"❌ Rejected ({stats['rejected']})",
    }
    labels[tab] = "• " + labels[tab]
    prev_p = page - 1 if page > 1 else pages
    next_p = page + 1 if page < pages else 1
    return Markup([
        [Btn(labels["p"], callback_data="rqpg#p#1")],
        [Btn(labels["u"], callback_data="rqpg#u#1"), Btn(labels["r"], callback_data="rqpg#r#1")],
        [
            Btn("◀️ Prev", callback_data=f"rqpg#{tab}#{prev_p}"),
            Btn(f"📄 {page}/{pages}", callback_data="rq#noop#x"),
            Btn("Next ▶️", callback_data=f"rqpg#{tab}#{next_p}"),
        ],
        [Btn("🔄 Refresh", callback_data=f"rqpg#{tab}#{page}")],
    ])


async def build_dashboard(tab="p", page=1):
    stats = await rq.stats()
    total = await rq.count_tab(tab)
    pages = max(1, math.ceil(total / DASH_PAGE_SIZE))
    page = min(max(1, page), pages)
    skip = (page - 1) * DASH_PAGE_SIZE
    items = await rq.list_tab(tab, skip, DASH_PAGE_SIZE)
    now_text = datetime.now(IST).strftime("%d %b, %I:%M %p")
    text = render_dashboard(tab, page, pages, items, stats, now_text, skip=skip)
    return text, _dash_markup(tab, page, pages, stats), page


async def refresh_dashboard(bot, force_new=False):
    """Edit (or create + pin) the dashboard message in the request channel."""
    if REQST_CHANNEL is None:
        return None
    if temp.RQ_DASH_LOCK is None:
        temp.RQ_DASH_LOCK = asyncio.Lock()
    async with temp.RQ_DASH_LOCK:
        meta = await rq.get_meta("dashboard") or {}
        tab, page = "p", 1
        viewed = meta.get("viewed_at")
        if viewed and (datetime.utcnow() - viewed).total_seconds() < 300:
            tab, page = meta.get("tab", "p"), meta.get("page", 1)      # somebody is browsing: keep their view
        text, markup, page = await build_dashboard(tab, page)

        mid = meta.get("message_id")
        if mid and not force_new:
            try:
                await bot.edit_message_text(
                    REQST_CHANNEL, mid, text, reply_markup=markup, parse_mode=HTML, disable_web_page_preview=True
                )
                return mid
            except MessageNotModified:
                return mid
            except MessageIdInvalid:
                pass                                                    # deleted -> create a new one
            except FloodWait as e:
                await asyncio.sleep(e.value + 1)
                return mid
            except Exception as e:
                logger.warning(f"[REQ] dashboard edit failed: {e}")
                return mid

        text, markup, _ = await build_dashboard("p", 1)
        msg = await bot.send_message(
            REQST_CHANNEL, text, reply_markup=markup, parse_mode=HTML, disable_web_page_preview=True
        )
        try:
            await bot.pin_chat_message(REQST_CHANNEL, msg.id, disable_notification=True)
        except Exception:
            pass
        await rq.set_meta("dashboard", {"message_id": msg.id, "tab": "p", "page": 1, "viewed_at": None})
        return msg.id


def schedule_dashboard_refresh(bot, delay=3.0):
    """Debounced: any number of changes inside `delay` seconds = one edit."""
    if REQST_CHANNEL is None:
        return
    task = temp.RQ_DASH_TASK
    if task and not task.done():
        return

    async def _run():
        await asyncio.sleep(delay)
        try:
            await refresh_dashboard(bot)
        except Exception:
            logger.exception("[REQ] dashboard refresh failed")

    temp.RQ_DASH_TASK = _spawn(_run())


@Client.on_callback_query(filters.regex(r"^rqpg#"), group=-4)
async def dashboard_nav(bot, query):
    try:
        _, tab, page = query.data.split("#")
        tab = tab if tab in TABS else "p"
        text, markup, page = await build_dashboard(tab, int(page))
        try:
            await query.message.edit_text(text, reply_markup=markup, parse_mode=HTML, disable_web_page_preview=True)
            await query.answer("🔄 Updated")
        except MessageNotModified:
            await query.answer("Already up to date ✅")

        meta = await rq.get_meta("dashboard") or {}
        if meta.get("message_id") == query.message.id and query.message.chat.id == REQST_CHANNEL:
            await rq.set_meta("dashboard", {"tab": tab, "page": page, "viewed_at": datetime.utcnow()})
    except StopPropagation:
        raise
    except Exception:
        logger.exception("[REQ] dashboard nav failed")
        try:
            await query.answer("⚠️ Error", show_alert=True)
        except Exception:
            pass
    raise StopPropagation


_chan_filter = filters.chat(REQST_CHANNEL) if REQST_CHANNEL else filters.create(lambda *_: False)


@Client.on_message(filters.command("reqpage") & (filters.user(ADMINS) | _chan_filter), group=-4)
async def reqpage_cmd(bot, message):
    """/reqpage = refresh dashboard   |   /reqpage new = create a fresh dashboard message"""
    if REQST_CHANNEL is None:
        await message.reply_text("⚠️ REQST_CHANNEL set nahi hai.")
        raise StopPropagation
    force = len(message.command) > 1 and message.command[1].lower() in ("new", "reset")
    await rq.ensure_indexes()
    try:
        mid = await refresh_dashboard(bot, force_new=force)
    except Exception as e:
        await message.reply_text(f"❌ Dashboard error: <code>{esc(e)}</code>", parse_mode=HTML)
        raise StopPropagation
    if message.chat.id == REQST_CHANNEL:
        try:
            await message.delete()
        except Exception:
            pass
    else:
        await message.reply_text(f"✅ Dashboard ready (message id <code>{mid}</code>) — request channel check karo.", parse_mode=HTML)
    raise StopPropagation


@Client.on_message(filters.command("pendingreq") & filters.user(ADMINS) & filters.private, group=-4)
async def pending_here(bot, message):
    """/pendingreq – the same pending/completed pages, right here in your PM."""
    await rq.ensure_indexes()
    text, markup, _ = await build_dashboard("p", 1)
    await message.reply_text(text, reply_markup=markup, parse_mode=HTML, disable_web_page_preview=True)
    raise StopPropagation
