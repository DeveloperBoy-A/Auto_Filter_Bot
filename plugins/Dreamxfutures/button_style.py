"""
Coloured inline buttons (Telegram Bot API 9.4+, Feb 2026).

Installed pyrofork 2.3.69 purana layer use karta hai aur button `style` bhej hi nahi
sakta. Isliye update-channel ke post Telegram Bot API (HTTP) se bheje jaate hain, jo
`style` support karta hai: "success" (hara), "primary" (neela), "danger" (laal).
Bot API fail ho to code apne aap purane pyrogram tareeke par aa jaata hai (bina colour),
post kabhi nahi rukta.

Colour band karne ke liye env: BUTTON_COLORS=False

Helpers: api_send / api_edit (low level), reply_styled / send_styled / edit_styled (aasaan),
html_mention(user). Button dict me ye keys chalti hain: text, style, aur inme se ek:
url | callback_data | switch_inline_query_current_chat | switch_inline_query

Note: colour sirf un users ko dikhega jinka Telegram app Feb 2026 ke baad ka hai.
"""
import os
import json
import logging
import aiohttp
from html import escape as _esc
from io import BytesIO
from pyrogram import enums
from pyrogram.types import InlineKeyboardButton

logger = logging.getLogger(__name__)

COLORS_ENABLED = os.environ.get("BUTTON_COLORS", "True").strip().lower() not in ("false", "0", "off", "no", "")
VALID_STYLES = {"primary", "success", "danger"}
_BTN_KEYS = ("url", "callback_data", "switch_inline_query_current_chat", "switch_inline_query")

_session = None
_warned = False


class BotApiError(Exception):
    def __init__(self, code, description, retry_after=None):
        super().__init__(f"[{code}] {description}")
        self.code = code
        self.description = description or ""
        self.retry_after = retry_after

    @property
    def not_modified(self):
        return "not modified" in self.description.lower()

    @property
    def not_found(self):
        d = self.description.lower()
        return "message to edit not found" in d or "message_id_invalid" in d or "message not found" in d


# ---------------------------------------------------------------- pyrogram side
def StyledButton(text, style=None, **kwargs):
    """Agar kabhi library `style` support wali ho (Kurigram/Electrogram), to ye colour
    lagata hai; warna plain button deta hai (crash nahi)."""
    global _warned
    if style:
        ButtonStyle = getattr(enums, "ButtonStyle", None)
        resolved = getattr(ButtonStyle, str(style).upper(), None) if ButtonStyle else None
        if resolved is not None:
            try:
                return InlineKeyboardButton(text, style=resolved, **kwargs)
            except TypeError:
                pass
        if not _warned:
            _warned = True
            logger.info("Library button style support nahi karti - colours Bot API se jaayenge.")
    return InlineKeyboardButton(text, **kwargs)


# ---------------------------------------------------------------- Bot API side
async def _get_session():
    global _session
    if _session is None or _session.closed:
        _session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=60))
    return _session


def markup_json(rows):
    """rows: [[{"text":..., "url":... / "callback_data":..., "style": "success"}, ...], ...]"""
    keyboard = []
    for row in rows:
        out = []
        for b in row:
            d = {"text": b["text"]}
            for key in _BTN_KEYS:
                if b.get(key) is not None:
                    d[key] = b[key]
            if b.get("style") in VALID_STYLES:
                d["style"] = b["style"]
            out.append(d)
        keyboard.append(out)
    return json.dumps({"inline_keyboard": keyboard})


def _field(v):
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (dict, list)):
        return json.dumps(v)
    return str(v)


async def _call(bot, method, fields, photo=None):
    token = getattr(bot, "bot_token", None)
    if not token:
        raise BotApiError(0, "no bot token on client")

    form = aiohttp.FormData()
    for k, v in fields.items():
        if v is not None:
            form.add_field(k, _field(v))
    if photo is not None:
        if isinstance(photo, BytesIO):
            photo.seek(0)
            form.add_field("photo", photo.read(), filename="poster.jpg", content_type="image/jpeg")
        else:
            form.add_field("photo", str(photo))  # URL / file_id

    session = await _get_session()
    async with session.post(f"https://api.telegram.org/bot{token}/{method}", data=form) as resp:
        js = await resp.json(content_type=None)

    if not js.get("ok"):
        raise BotApiError(js.get("error_code"), js.get("description", ""),
                          (js.get("parameters") or {}).get("retry_after"))
    return js["result"]


async def api_send(bot, chat_id, rows, *, text=None, photo=None, spoiler=False, link_preview=None, reply_to=None):
    """Post bhejta hai (photo+caption ya text). Return: message_id."""
    if photo is not None:
        res = await _call(bot, "sendPhoto", {
            "chat_id": chat_id, "caption": text, "parse_mode": "HTML",
            "reply_markup": markup_json(rows), "has_spoiler": spoiler or None,
            "reply_parameters": {"message_id": reply_to, "allow_sending_without_reply": True} if reply_to else None,
        }, photo=photo)
    else:
        res = await _call(bot, "sendMessage", {
            "chat_id": chat_id, "text": text, "parse_mode": "HTML",
            "reply_markup": markup_json(rows), "link_preview_options": link_preview,
            "reply_parameters": {"message_id": reply_to, "allow_sending_without_reply": True} if reply_to else None,
        })
    return res["message_id"]


async def api_edit(bot, chat_id, message_id, rows, *, text, is_photo, link_preview=None):
    """Existing post edit karta hai. 'message is not modified' par BotApiError.not_modified True."""
    if is_photo:
        await _call(bot, "editMessageCaption", {
            "chat_id": chat_id, "message_id": message_id, "caption": text,
            "parse_mode": "HTML", "reply_markup": markup_json(rows),
        })
    else:
        await _call(bot, "editMessageText", {
            "chat_id": chat_id, "message_id": message_id, "text": text,
            "parse_mode": "HTML", "reply_markup": markup_json(rows),
            "link_preview_options": link_preview,
        })


# ---------------------------------------------------------------- reply helpers
class _SentHandle:
    """Bot API se bheje/edit kiye message ka chhota handle: .id aur .delete() (pyrogram Message jaisa)."""
    def __init__(self, client, chat_id, message_id):
        self._client = client
        self.chat_id = chat_id
        self.id = message_id

    async def delete(self):
        return await self._client.delete_messages(self.chat_id, self.id)


def html_mention(user):
    """Bot API (HTML) ke liye safe mention: <a href="tg://user?id=..">Name</a>."""
    if user is None:
        return "User"
    name = _esc(getattr(user, "first_name", None) or "User")
    return f'<a href="tg://user?id={user.id}">{name}</a>'


def _pyro_markup(rows):
    """Fallback: wahi buttons, bina colour, pyrogram markup me."""
    from pyrogram.types import InlineKeyboardMarkup
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                b["text"],
                **{k: b[k] for k in _BTN_KEYS if b.get(k) is not None}
            )
            for b in row
        ]
        for row in rows
    ])


def _has_media(message):
    return any(getattr(message, a, None) for a in ("photo", "video", "document", "animation", "audio"))


async def reply_styled(client, message, text, rows, *, plain_text=None, quote=True):
    """
    `message` ka reply coloured buttons ke saath bhejta hai.
    Bot API fail ho to normal (bina colour) reply_text se bhej deta hai.
    Return: object jisme .id aur .delete() hai (dono raste me).
    plain_text: fallback (pyrogram) wala text, agar API wale se alag chahiye.
    quote: False => user ke message ko quote/reply nahi karega (private chat me aksar yahi chahiye).
    """
    if COLORS_ENABLED and getattr(client, "bot_token", None):
        try:
            mid = await api_send(client, message.chat.id, rows, text=text,
                                 reply_to=message.id if quote else None)
            return _SentHandle(client, message.chat.id, mid)
        except Exception as e:
            logger.warning(f"Coloured reply failed ({e}), plain buttons se bhej raha hu")

    return await message.reply_text(
        text=plain_text if plain_text is not None else text,
        reply_markup=_pyro_markup(rows),
        reply_to_message_id=message.id if quote else None,
    )


async def send_styled(client, chat_id, text, rows, *, plain_text=None):
    """chat_id par coloured buttons wala naya message (reply nahi). Return: .id/.delete() wala object."""
    if COLORS_ENABLED and getattr(client, "bot_token", None):
        try:
            mid = await api_send(client, chat_id, rows, text=text)
            return _SentHandle(client, chat_id, mid)
        except Exception as e:
            logger.warning(f"Coloured send failed ({e}), plain buttons se bhej raha hu")
    return await client.send_message(
        chat_id, plain_text if plain_text is not None else text, reply_markup=_pyro_markup(rows)
    )


async def edit_styled(client, message, text, rows, *, plain_text=None):
    """
    Maujooda bot-message ko edit karke coloured buttons lagata hai (callback query ke
    `query.message.edit(...)` ki jagah). Photo/video message ho to caption edit hota hai.
    Return: .id/.delete() wala object.
    """
    media = _has_media(message)
    if COLORS_ENABLED and getattr(client, "bot_token", None):
        try:
            await api_edit(client, message.chat.id, message.id, rows, text=text, is_photo=media)
            return _SentHandle(client, message.chat.id, message.id)
        except BotApiError as e:
            if e.not_modified:
                return _SentHandle(client, message.chat.id, message.id)
            logger.warning(f"Coloured edit failed ({e}), plain buttons se edit kar raha hu")
        except Exception as e:
            logger.warning(f"Coloured edit error ({e}), plain buttons se edit kar raha hu")

    body = plain_text if plain_text is not None else text
    if media:
        return await message.edit_caption(body, reply_markup=_pyro_markup(rows))
    return await message.edit_text(body, reply_markup=_pyro_markup(rows))
