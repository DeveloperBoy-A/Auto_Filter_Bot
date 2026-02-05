import asyncio
import logging
import re
from pyrogram import Client, filters
from pyrogram.enums import ParseMode
from pyrogram.types import (
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    CallbackQuery,
    Message,
)
from pyrogram.errors import MessageNotModified, MessageTooLong
from plugins.Dreamxfutures.Imdbposter import get_movie_detailsx
from info import ADMINS, MOVIE_UPDATE_CHANNEL, ABOVE_PREVIEW
from utils import temp

logger = logging.getLogger(__name__)
post_sessions = {}


USE_GETFILE_BUTTON_BY_DEFAULT = True
DEFAULT_WATERMARK = "ᴊᴏɪɴ『 𝑻𝒐𝒌𝒚𝒐 𝑼𝒑𝒅𝒂𝒕𝒆𝒔 』𓆩ꨄ︎𓆪(https://t.me/Tokyo_Updates)"
LANGUAGES_FORMAT = "➥ <b>𝑳𝒂𝒏𝒈𝒖𝒂𝒈𝒆𝒔 :</b> <code>{langs}</code>"
RESOLUTIONS_FORMAT = "\n➥ <b>𝑸𝒖𝒂𝒍𝒊𝒕𝒊𝒆𝒔 :</b> <code>{resolutions}</code>"
OTT_FORMAT = "\n➥ <b>ᴀᴠᴀɪʟᴀʙʟᴇ ᴏɴ :</b> <code>{otts}</code>"

TEMPLATES = {
    "classic_emoji": """<b>{title} ({year})</b>
⭐️ <b>𝑹𝒂𝒕𝒊𝒏𝒈:</b> {rating}/10
🎭 <b>𝑮𝒆𝒏𝒓𝒆:</b> {genres}
💬 <b>𝑷𝒍𝒐𝒕:</b> {plot}
🌿 ᴘᴏᴡᴇʀᴇᴅ ʙʏ : [🔰ɴᴇᴡ ᴍᴏᴠɪᴇ & ᴡᴇʙ sᴇʀɪᴇs🔰]""",

    "minimalist": """🎬 <b>{title}</b>
🗓 <b>𝒀𝒆𝒂𝒓:</b> {year}
🌟 <b>𝑹𝒂𝒕𝒊𝒏𝒈:</b> {rating}
🌿 ᴘᴏᴡᴇʀᴇᴅ ʙʏ : [🔰ɴᴇᴡ ᴍᴏᴠɪᴇ & ᴡᴇʙ sᴇʀɪᴇs🔰]""",

    "sparkle_header": """✨ <b>{title}</b> ✨

<b>🗓 𝒀𝒆𝒂𝒓:</b> {year} | <b>⭐️ 𝑹𝒂𝒕𝒊𝒏𝒈:</b> {rating}/10
<b>🎭 𝑮𝒆𝒏𝒓𝒆𝒔:</b> {genres}

<i>{plot}</i>
🌿 ᴘᴏᴡᴇʀᴇᴅ ʙʏ : [🔰ɴᴇᴡ ᴍᴏᴠɪᴇ & ᴡᴇʙ sᴇʀɪᴇs🔰]""",

    "markdown_style": """🎥 **{title}** ({year})

- **𝑹𝒂𝒕𝒊𝒏𝒈**: {rating} / 10 🌟
- **𝑮𝒆𝒏𝒓𝒆𝒔**: {genres}

**𝑷𝒍𝒐𝒕 𝑺𝒖𝒎𝒎𝒂𝒓𝒚**:
{plot}
🌿 ᴘᴏᴡᴇʀᴇᴅ ʙʏ : [🔰ɴᴇᴡ ᴍᴏᴠɪᴇ & ᴡᴇʙ sᴇʀɪᴇs🔰]""",

    "divider_list": """🎬 <b>{title} {year}</b>
━━━━━━━━━━━━━━━━━━
➥ <b>𝑹𝒂𝒕𝒊𝒏𝒈 :</b> <code>★ {rating}/10</code>
➥ <b>𝑮𝒆𝒏𝒓ᴇ𝒔 :</b> <code>{genres}</code>
🌿 ᴘᴏᴡᴇʀᴇᴅ ʙʏ : [🔰ɴᴇᴡ ᴍᴏᴠɪᴇ & ᴡᴇʙ sᴇʀɪᴇs🔰]""",

    "dashed_box": """- - - - - - - - - - - - - - - - - -
🎥 <b>{title}</b>
- - - - - - - - - - - - - - - - - -

➛ <b>𝒀𝒆𝒂𝒓 ∥</b> {year}
➛ <b>𝑹𝒂𝒕𝒊𝒏𝒈 ∥</b> {rating}/10
➛ <b>𝑮𝒆𝒏𝒓𝒆𝒔 ∥</b> {genres}

<b><u>𝑺𝒚𝒏𝒐𝒑𝒔𝒊𝒔</u></b>
<i>{plot}</i>
🌿 ᴘᴏᴡᴇʀᴇᴅ ʙʏ : [🔰ɴᴇᴡ ᴍᴏᴠɪᴇ & ᴡᴇʙ sᴇʀɪᴇs🔰]""",

    "chevron_details": """<b>{title}</b>

» <b>𝒀𝒆𝒂𝒓 ➣</b> {year}
» <b>𝑹𝒂𝒕𝒊𝒏𝒈 ➣</b> ★ {rating}/10
» <b>𝑮𝒆𝒏𝒓𝒆𝒔 ➣</b> {genres}

<b>∥ 𝑷𝑳𝑶𝑻 ∥</b>
└─ <i>{plot}</i>
🌿 ᴘᴏᴡᴇʀᴇᴅ ʙʏ : [🔰ɴᴇᴡ ᴍᴏᴠɪᴇ & ᴡᴇʙ sᴇʀɪᴇs🔰]""",

    "bullet_points": """✨ <b><u>{title} ({year})</u></b> ✨

● <b>𝑹𝒂𝒕𝒊𝒏𝒈 :</b> {rating}/10
● <b>𝑮𝒆𝒏𝒓𝒆𝒔 :</b> {genres}

<b>💬 𝑷𝒍𝒐𝒕 𝑺𝒖𝒎𝒎𝒂𝒓𝒚 ➥</b>
<i>{plot}</i>
🌿 ᴘᴏᴡᴇʀᴇᴅ ʙʏ : [🔰ɴᴇᴡ ᴍᴏᴠɪᴇ & ᴡᴇʙ sᴇʀɪᴇs🔰]""",

    "clean_grid": """🎬 {title} ({year})

🗓️ <b>𝒀𝒆𝒂𝒓 ∥</b> {year}
⭐️ <b>𝑹𝒂𝒕𝒊𝒏𝒈 ∥</b> {rating}/10
🎭 <b>𝑮𝒆𝒏𝒓𝒆𝒔 ∥</b> {genres}

➣ <i>{plot}</i>
🌿 ᴘᴏᴡᴇʀᴇᴅ ʙʏ : [🔰ɴᴇᴡ ᴍᴏᴠɪᴇ & ᴡᴇʙ sᴇʀɪᴇs🔰]"""
}

    "divider_list": """🎬 <b>{title} {year}</b>
━━━━━━━━━━━━━━━━━━
➥ <b>𝑹𝒂𝒕𝒊𝒏𝒈 :</b> <code>★ {rating}/10</code>
➥ <b>𝑮𝒆𝒏𝒓𝒆𝒔 :</b> <code>{genres}</code>
""",

    "dashed_box": """- - - - - - - - - - - - - - - - - -
🎥 <b>{title}</b>
- - - - - - - - - - - - - - - - - -

➛ <b>𝒀𝒆𝒂𝒓 ∥</b> {year}
➛ <b>𝑹𝒂𝒕𝒊𝒏𝒈 ∥</b> {rating}/10
➛ <b>𝑮𝒆𝒏𝒓𝒆𝒔 ∥</b> {genres}

<b><u>𝑺𝒚𝒏𝒐𝒑𝒔𝒊𝒔</u></b>
<i>{plot}</i>""",

    "chevron_details": """<b>{title}</b>

» <b>𝒀𝒆𝒂𝒓 ➣</b> {year}
» <b>𝑹𝒂𝒕𝒊𝒏𝒈 ➣</b> ★ {rating}/10
» <b>𝑮𝒆𝒏𝒓𝒆𝒔 ➣</b> {genres}

<b>∥ 𝑷𝑳𝑶𝑻 ∥</b>
└─ <i>{plot}</i>""",

    "bullet_points": """✨ <b><u>{title} ({year})</u></b> ✨

● <b>𝑹𝒂𝒕𝒊𝒏𝒈 :</b> {rating}/10
● <b>𝑮𝒆𝒏𝒓𝒆𝒔 :</b> {genres}

<b>💬 𝑷𝒍𝒐𝒕 𝑺𝒖𝒎𝒎𝒂𝒓𝒚 ➥</b>
<i>{plot}</i>""",

    "clean_grid": """🎬 <b>{title} ({year})</b>

🗓️ <b>𝒀𝒆𝒂𝒓 ∥</b> {year}
⭐️ <b>𝑹𝒂𝒕𝒊𝒏𝒈 ∥</b> {rating}/10
🎭 <b>𝑮𝒆𝒏𝒓𝒆𝒔 ∥</b> {genres}

➣ <i>{plot}</i>"""
}


LANGUAGES = [

    "Bengali", "English", "Gujarati", "Hindi", "Kannada", "Malayalam",
    "Marathi", "Punjabi", "Tamil", "Telugu", "Urdu",

    "Arabic", "French", "German", "Italian", "Japanese", "Korean",
    "Mandarin", "Portuguese", "Russian", "Spanish"
]
RESOLUTIONS = [

    "144p", "240p", "480p", "720p", "1080p", "1440p", "2160p", "4320p",

    "BluRay", "BDRip", "WEB-DL", "VOD", "WEBRip", "HDTV",
    "DVDRip", "DVDScr", "TS", "CAM",

    "AV1", "HEVC", "x264"
]

OTT_PLATFORMS = [

    "Aha",
    "ALTBalaji",
    "JioHotstar",
    "Eros Now",
    "Hoichoi",
    "JioCinema",
    "MX Player",
    "SonyLIV",
    "Sun NXT",
    "Voot",
    "Zee5",


    "Amazon Prime Video",  
    "Apple TV+",
    "Crunchyroll",
    "Discovery+",
    "HBO Max",
    "Hulu",
    "Netflix",
    "Paramount+",
    "Peacock",
    "YouTube Premium"
]


@Client.on_message(filters.command("post") & filters.user(ADMINS), group=-4)
async def post_command(client: Client, message: Message):
    if len(message.command) == 1:
        return await message.reply_text("ᴘʟᴇᴀsᴇ ᴘʀᴏᴠɪᴅᴇ ᴀ ᴍᴏᴠɪᴇ ɴᴀᴍᴇ. ᴜsᴀɢᴇ: `/post The Dark Knight`")

    movie_name = " ".join(message.command[1:])
    user_id = message.from_user.id
    logger.info(f"User {user_id} initiated post for '{movie_name}'")

    await start_post_session(client, message, user_id, movie_name)

#code is created by @bharath_boy for public use so atleast don't remove credits
async def start_post_session(client: Client, message: Message, user_id: int, movie_name: str):
    movie_details = await get_movie_detailsx(movie_name)
    if not movie_details:
        return await message.reply_text("Could not fetch details for the movie.")

    logger.info(f"User {user_id} is starting post session for '{movie_name}'.")

    if user_id in post_sessions and post_sessions[user_id].get("last_preview_message_id"):
        try:
            await client.delete_messages(message.chat.id, post_sessions[user_id]["last_preview_message_id"])
        except Exception:
            pass

    post_sessions[user_id] = {
        "movie_name": movie_name, "caption": None, "buttons": [],
        "photo_mode": False,
        "use_landscape": True if movie_details.get("backdrop_url") else False,
        "custom_languages": [], "custom_resolutions": [], "custom_otts": [],
        "last_preview_message_id": None, "original_message_id": message.id,
        "custom_poster": None,
        "watermark": DEFAULT_WATERMARK,
        "lang_format": LANGUAGES_FORMAT,
        "ott_format": OTT_FORMAT,
        "res_format": RESOLUTIONS_FORMAT, "active_template": "divider_list",
        "movie_details": movie_details
    }

        if USE_GETFILE_BUTTON_BY_DEFAULT:
        title = movie_details.get("title", "movie")
        year = movie_details.get("year", "")
        movie_year = f"{title} {year}".strip()
        movie_year = re.sub(r"[ *:\.]", "-", movie_year)
        url = f"https://t.me/{temp.U_NAME}?start=getfile-{movie_year}"
        post_sessions[user_id]["buttons"] = [  
            [InlineKeyboardButton('🗃️ ✦ 𝗚𝗘𝗧 𝗙𝗜𝗟𝗘 ✦ 🗃️', url=url)],  
            [InlineKeyboardButton('♻️ ʜᴏᴡ ᴛᴏ ᴅᴏᴡɴʟᴏᴀᴅ ♻️', url="https://t.me/newmovieswebseries_group/56553?single")],  
            [InlineKeyboardButton('🔰 ᴍᴏᴠɪᴇ ꜱᴇᴀʀᴄʜ ɢʀᴏᴜᴘ 🔰', url="https://t.me/newmovieswebseries_group")]  
        ]
        logger.info(f"ᴅᴇꜰᴀᴜʟᴛ ʙᴜᴛᴛᴏɴs ᴀᴅᴅᴇᴅ ꜰᴏʀ sᴇssɪᴏɴ {user_id}")

    await update_post_preview(client, user_id, message.chat.id, force_resend=True)



async def _build_final_post_content(session: dict, session_id: int):
    movie_details = session["movie_details"]
    if not movie_details:
        return None, None, None

    if not session.get("caption"):
        session["caption"] = TEMPLATES[session["active_template"]].format(
            title=movie_details.get("title", "N/A"), year=movie_details.get("year", "N/A"),
            rating=movie_details.get("rating", "N/A"),
            genres=", ".join(movie_details.get("genres", [])
                             if movie_details.get("genres") else []),
            plot=movie_details.get("plot", "N/A"),
        )

    final_caption = session["caption"]
    if session.get("custom_languages"):
        final_caption += session["lang_format"].format(
            langs=', '.join(session['custom_languages']))
    if session.get("custom_resolutions"):
        final_caption += session["res_format"].format(
            resolutions=', '.join(session['custom_resolutions']))
    if session.get("custom_otts"):
        final_caption += session["ott_format"].format(
            otts=', '.join(session['custom_otts']))
    if session.get("watermark"):
        final_caption += f"\n\n{session['watermark']}"

    keyboard = build_keyboard(session, session_id)
    poster_to_use = session.get("custom_poster") or \
        (movie_details.get("backdrop_url") if session.get(
            "use_landscape") else movie_details.get("poster_url"))

    return final_caption, keyboard, poster_to_use

#code is created by @bharath_boy for public use so atleast don't remove credits
async def update_post_preview(client: Client, session_id: int, chat_id: int, force_resend: bool = False):
    session = post_sessions.get(session_id)
    if not session:
        return

    is_new = not session.get("last_preview_message_id")

    if is_new or force_resend:
        if not is_new:
            try:
                await client.delete_messages(chat_id, session["last_preview_message_id"])
            except Exception:
                pass
        status_msg = await client.send_message(
            chat_id, "<i>Fetching details...</i>",
            reply_to_message_id=session["original_message_id"]
        )
        session["last_preview_message_id"] = status_msg.id

    final_caption, keyboard, poster_to_use = await _build_final_post_content(session, session_id)

    if not final_caption:
        return await client.edit_message_text(chat_id, session["last_preview_message_id"], "Could not find details for this movie.")

    try:
        if session["photo_mode"] and poster_to_use:
            if force_resend:
                await client.delete_messages(chat_id, session["last_preview_message_id"])
                sent_message = await client.send_photo(chat_id, photo=poster_to_use, caption=final_caption, reply_markup=keyboard, reply_to_message_id=session["original_message_id"])
                session["last_preview_message_id"] = sent_message.id
            else:
                await client.edit_message_caption(chat_id, session["last_preview_message_id"], caption=final_caption, reply_markup=keyboard)
        else:
            text_content = f"<a href='{poster_to_use}'>&#8205;</a>{final_caption}" if poster_to_use else final_caption
            await client.edit_message_text(chat_id, session["last_preview_message_id"], text_content, reply_markup=keyboard, disable_web_page_preview=False, invert_media=ABOVE_PREVIEW)
    except MessageNotModified:
        pass
    except Exception as e:
        logger.error(f"Error updating preview: {e}", exc_info=True)


def build_keyboard(session: dict, session_id: int):
    rows = []
    if session.get("buttons"):
        rows.extend(session["buttons"])

    rows.extend([
        [InlineKeyboardButton(f"✏️ 𝑩𝒖𝒕𝒕𝒐𝒏𝒔", callback_data=f"post:buttons_menu:{session_id}"),
         InlineKeyboardButton(f"📝 𝑪𝒂𝒑𝒕𝒊𝒐𝒏", callback_data=f"post:edit_caption:{session_id}")],
        [InlineKeyboardButton(f"🖼️ 𝑷𝒐𝒔𝒕𝒆𝒓", callback_data=f"post:set_poster:{session_id}"),
         InlineKeyboardButton(f"✨ 𝑻𝒆𝒎𝒑𝒍𝒂𝒕𝒆𝒔", callback_data=f"post:templates:{session_id}"),
         InlineKeyboardButton(f"💧 𝑾𝒂𝒕𝒆𝒓𝒎𝒂𝒓𝒌", callback_data=f"post:set_watermark:{session_id}")],
        [InlineKeyboardButton(f"🗣️ 𝑳𝒂𝒏𝒈𝒖𝒂𝒈𝒆𝒔", callback_data=f"post:languages:{session_id}"),
         InlineKeyboardButton(f"📺 𝑸𝒖𝒂𝒍𝒊𝒕𝒊𝒆𝒔", callback_data=f"post:resolutions:{session_id}"),
         InlineKeyboardButton(f"🌐 𝑶𝑻𝑻", callback_data=f"post:otts:{session_id}")],  
        [InlineKeyboardButton(f"⚙️ 𝑴𝒐𝒅𝒆: {'𝑷𝒉𝒐𝒕𝒐' if session['photo_mode'] else '𝑻𝒆𝒙𝒕'}", callback_data=f"post:toggle_preview:{session_id}"),
         InlineKeyboardButton(f"📐 𝑷𝒐𝒔𝒕𝒆𝒓: {'𝑳𝒂𝒏𝒅𝒔𝒄𝒂𝒑𝒆' if session['use_landscape'] else '𝑷𝒐𝒓𝒕𝒓𝒂𝒊𝒕'}", callback_data=f"post:toggle_poster:{session_id}")],
        [InlineKeyboardButton(f"✅ 𝑷𝒐𝒔𝒕", callback_data=f"post:finalize:{session_id}"),
         InlineKeyboardButton(f"❌ 𝑪𝒂𝒏𝒄𝒆𝒍", callback_data=f"post:cancel:{session_id}")]
    ])
    return InlineKeyboardMarkup(rows)


#code is created by @bharath_boy for public use so atleast don't remove credits
@Client.on_callback_query(filters.regex(r"^post:"), group=-4)
async def post_callbacks(client: Client, query: CallbackQuery):
    data_parts = query.data.split(":")
    action = data_parts[1]
    session_id = int(data_parts[2])
    extra_data = data_parts[3:]

    if query.from_user.id != session_id:
        return await query.answer("This is not for you!", show_alert=True)

    session = post_sessions.get(session_id)
    if not session:
        await query.answer("Session expired or was cancelled.", show_alert=True)
        return await query.message.delete()

    force_resend = False

    if action == "back":
        await query.answer()

    elif action in ["languages", "resolutions", "templates", "buttons_menu", "remove_buttons_menu", "otts"]:  
        await query.answer()
        if action == "languages":
            await show_selection_menu(query, session_id, "languages")
        elif action == "resolutions":
            await show_selection_menu(query, session_id, "resolutions")
        elif action == "otts":

            await show_selection_menu(query, session_id, "otts")
        elif action == "templates":
            await handle_templates_menu(query, session)
        elif action == "buttons_menu":
            await handle_buttons_menu(query, session_id)
        elif action == "remove_buttons_menu":
            await handle_remove_buttons_menu(query, session)
        return

    elif action in ["select_lang", "select_res", "select_ott"]:  
        await query.answer()
        item = extra_data[0]
        if action == "select_lang":
            if item not in session["custom_languages"]:
                session["custom_languages"].append(item)
            else:
                session["custom_languages"].remove(item)
            await show_selection_menu(query, session_id, "languages")
        elif action == "select_res":
            if item not in session["custom_resolutions"]:
                session["custom_resolutions"].append(item)
            else:
                session["custom_resolutions"].remove(item)
            await show_selection_menu(query, session_id, "resolutions")
        elif action == "select_ott":  
            if item not in session["custom_otts"]:
                session["custom_otts"].append(item)
            else:
                session["custom_otts"].remove(item)
            await show_selection_menu(query, session_id, "otts")
        return

    else:

        if action == "edit_buttons":
            await handle_edit_buttons(client, query, session)
        elif action == "add_get_files":
            await handle_add_get_files(session)
            await query.answer("✅ 'Get Files' button added!")
        elif action == "edit_caption":
            await handle_edit_caption(client, query, session)
        elif action == "set_poster":
            force_resend = await handle_set_poster(client, query, session)
        elif action == "remove_button":
            await handle_remove_button(session, extra_data)
            await handle_remove_buttons_menu(query, session)
            return
        elif action == "select_template":
            await handle_select_template(session, extra_data[0])
        elif action == "toggle_preview":
            force_resend = await handle_toggle_preview(query, session)
        elif action == "toggle_poster":
            force_resend = await handle_toggle_poster(session)
        elif action == "set_watermark":
            await handle_set_watermark(client, query, session)
        elif action == "format_lang":
            await handle_format_lang(client, query, session)
        elif action == "format_res":
            await handle_format_res(client, query, session)
        elif action == "format_ott":

            await handle_format_ott(client, query, session)
        elif action == "finalize":
            return await finalize_and_post(client, query, session_id)
        elif action == "cancel":
            return await handle_cancel(client, query, session_id)

    await update_post_preview(client, session_id, query.message.chat.id, force_resend)

#code is created by @bharath_boy for public use so atleast don't remove credits
async def show_selection_menu(query: CallbackQuery, session_id: int, menu_type: str):
    session = post_sessions[session_id]


    if menu_type == "languages":
        items, selected, action_prefix, format_action = (
            LANGUAGES, session["custom_languages"], "select_lang", "format_lang")
    elif menu_type == "resolutions":
        items, selected, action_prefix, format_action = (
            RESOLUTIONS, session["custom_resolutions"], "select_res", "format_res")
    elif menu_type == "otts":
        items, selected, action_prefix, format_action = (
            OTT_PLATFORMS, session["custom_otts"], "select_ott", "format_ott")
    else:
        return

        buttons = [InlineKeyboardButton(
        f"✅ {i}" if i in selected else i, callback_data=f"post:{action_prefix}:{session_id}:{i}") for i in items]
    keyboard = [buttons[i:i+3]
                for i in range(0, len(buttons), 3)]  
    keyboard.append([InlineKeyboardButton("⚙️ ᴄʜᴀɴɢᴇ ꜰᴏʀᴍᴀᴛ",
                    callback_data=f"post:{format_action}:{session_id}")])
    keyboard.append([InlineKeyboardButton(
        "✅ ᴅᴏɴᴇ", callback_data=f"post:back:{session_id}")])
    await query.edit_message_reply_markup(InlineKeyboardMarkup(keyboard))



async def get_user_input(client, query, session, prompt_text):
    ask_msg = await query.message.reply_text(prompt_text, reply_to_message_id=session.get("original_message_id"))
    try:
        response = await client.listen(chat_id=query.message.chat.id, user_id=query.from_user.id, timeout=300)
        await ask_msg.delete()
        if response:
            await response.delete()
            return response
    except asyncio.TimeoutError:
        await ask_msg.edit("Timeout (5 minutes). The operation was cancelled.")
        await asyncio.sleep(5)
        await ask_msg.delete()
    return None


async def handle_buttons_menu(query, session_id):
    buttons = [
        [InlineKeyboardButton("➕ ᴀᴅᴅ/ᴇᴅɪᴛ ʟᴀʏᴏᴜᴛ", callback_data=f"post:edit_buttons:{session_id}")],
        [InlineKeyboardButton("🗃️ ✦ ɢᴇᴛ ꜰɪʟᴇ ✦ 🗃️", callback_data=f"post:add_get_files:{session_id}")],
        [InlineKeyboardButton("♻️ ʜᴏᴡ ᴛᴏ ᴅᴏᴡɴʟᴏᴀᴅ ♻️", url="https://t.me/newmovieswebseries_group/56553?single")],  
        [InlineKeyboardButton("🔰 ᴍᴏᴠɪᴇ ꜱᴇᴀʀᴄʜ ɢʀᴏᴜᴘ 🔰", url="https://t.me/newmovieswebseries_group")],
        [InlineKeyboardButton("🗑️ ʀᴇᴍᴏᴠᴇ ᴀ ʙᴜᴛᴛᴏɴ", callback_data=f"post:remove_buttons_menu:{session_id}")],
        [InlineKeyboardButton("🔙 ʙᴀᴄᴋ", callback_data=f"post:back:{session_id}")]
    ]
    await query.edit_message_reply_markup(InlineKeyboardMarkup(buttons))



async def handle_edit_buttons(client: Client, query: CallbackQuery, session: dict):
    response = await get_user_input(client, query, session, "Send the button layout. Format:\n`Button 1 - URL1 | Button 2 - URL2` (for same row)\n`Button 3 - URL3` (for new row)")
    if response and response.text:
        new_layout = []
        for row_str in response.text.strip().split('\n'):
            row_btns = [InlineKeyboardButton(text.strip(), url=url.strip()) for btn_str in row_str.split(
                '|') if ' - ' in btn_str for text, url in [btn_str.split(' - ', 1)]]
            if row_btns:
                new_layout.append(row_btns)
        session["buttons"] = new_layout

#code is created by @bharath_boy for public use so atleast don't remove credits
async def handle_add_get_files(session):
    movie_details = session["movie_details"]
    if movie_details:
        title = movie_details.get("title", "movie")
        year = movie_details.get("year", "")
        movie_year = f"{title} {year}".strip()
        url = f"https://t.me/{BOT_NAME}?start=getfile-{movie_year.replace(' ', '-')}"
        session["buttons"].append(
            [InlineKeyboardButton("🗃️✦𝑮𝒆𝒕 𝑭𝒊𝒍𝒆𝒔✦🗃️", url=url)])



async def handle_edit_caption(client: Client, query: CallbackQuery, session: dict):
    response = await get_user_input(client, query, session, "Send the new caption text.")
    if response and response.text:
        session["caption"] = response.text


async def handle_set_poster(client: Client, query: CallbackQuery, session: dict):
    response = await get_user_input(client, query, session, "sᴇɴᴅ ᴀ ᴘʜᴏᴛᴏ ᴏʀ ᴀɴ ɪᴍᴀɢᴇ ᴜʀʟ. sᴇɴᴅ `/reset` ᴛᴏ ᴜsᴇ ᴛʜᴇ ᴅᴇꜰᴀᴜʟᴛ ᴘᴏsᴛᴇʀ.")
    if response:
        if response.photo:
            session["custom_poster"] = response.photo.file_id
            if not session["photo_mode"]:
                session["photo_mode"] = True
                await query.answer("sᴡɪᴛᴄʜᴇᴅ ᴛᴏ ᴘʜᴏᴛᴏ ᴍᴏᴅᴇ ᴀs ʏᴏᴜ ᴜᴘʟᴏᴀᴅᴇᴅ ᴀɴ ɪᴍᴀɢᴇ.", show_alert=True)
        elif response.text and response.text.startswith("http"):
            session["custom_poster"] = response.text
        elif response.text and response.text == "/reset":
            session["custom_poster"] = None
    return True



async def handle_set_watermark(client, query, session):
    prompt_text = (
        "sᴇɴᴅ ᴛʜᴇ ᴡᴀᴛᴇʀᴍᴀʀᴋ ᴛᴇxᴛ. ᴍᴀʀᴋᴅᴏᴡɴ ɪs sᴜᴘᴘᴏʀᴛᴇᴅ.\n\n"
        "• sᴇɴᴅ `/reset` ᴛᴏ ʀᴇᴍᴏᴠᴇ ᴛʜᴇ ᴡᴀᴛᴇʀᴍᴀʀᴋ.\n"
        "• sᴇɴᴅ `/default` ᴛᴏ ᴜsᴇ ᴛʜᴇ ᴅᴇꜰᴀᴜʟᴛ ᴡᴀᴛᴇʀᴍᴀʀᴋ."
    )
    response = await get_user_input(client, query, session, prompt_text)
    if response and response.text:
        if response.text == "/reset":
            session["watermark"] = ""
        elif response.text == "/default":
            session["watermark"] = DEFAULT_WATERMARK
        else:
            session["watermark"] = response.text

# ᴄᴏᴅᴇ ɪs ᴄʀᴇᴀᴛᴇᴅ ʙʏ @ʙʜᴀʀᴀᴛʜ_ʙᴏʏ ꜰᴏʀ ᴘᴜʙʟɪᴄ ᴜsᴇ sᴏ ᴀᴛʟᴇᴀsᴛ ᴅᴏɴ'ᴛ ʀᴇᴍᴏᴠᴇ ᴄʀᴇᴅɪᴛs
async def handle_format_lang(client, query, session):
    response = await get_user_input(client, query, session, "sᴇɴᴅ ᴛʜᴇ ꜰᴏʀᴍᴀᴛ ꜰᴏʀ ʟᴀɴɢᴜᴀɢᴇs. ᴜsᴇ `{langs}` ᴀs ᴀ ᴘʟᴀᴄᴇʜᴏʟᴅᴇʀ. sᴇɴᴅ `/reset` ꜰᴏʀ ᴅᴇꜰᴀᴜʟᴛ.\n\n ᴄᴜʀʀᴇɴᴛ: " + session["lang_format"])
    if response and response.text:
        session["lang_format"] = LANGUAGES_FORMAT if response.text == "/reset" else response.text


async def handle_format_res(client, query, session):
    response = await get_user_input(client, query, session, "sᴇɴᴅ ᴛʜᴇ ꜰᴏʀᴍᴀᴛ ꜰᴏʀ ǫᴜᴀʟɪᴛɪᴇs. ᴜsᴇ `{resolutions}` ᴀs ᴀ ᴘʟᴀᴄᴇʜᴏʟᴅᴇʀ. sᴇɴᴅ `/reset` ꜰᴏʀ ᴅᴇꜰᴀᴜʟᴛ.\n\n ᴄᴜʀʀᴇɴᴛ: " + session["res_format"])
    if response and response.text:
        session["res_format"] = RESOLUTIONS_FORMAT if response.text == "/reset" else response.text




async def handle_format_ott(client, query, session):
    response = await get_user_input(client, query, session, "sᴇɴᴅ ᴛʜᴇ ꜰᴏʀᴍᴀᴛ ꜰᴏʀ ᴏᴛᴛ. ᴜsᴇ `{otts}` ᴀs ᴀ ᴘʟᴀᴄᴇʜᴏʟᴅᴇʀ. sᴇɴᴅ `/reset` ꜰᴏʀ ᴅᴇꜰᴀᴜʟᴛ.\n\n ᴄᴜʀʀᴇɴᴛ: " + session["ott_format"])
    if response and response.text:
        session["ott_format"] = OTT_FORMAT if response.text == "/reset" else response.text


async def handle_templates_menu(query, session):
    buttons = []
    for name in TEMPLATES:
        text = f"✅ {name}" if session.get("active_template") == name else name
        buttons.append([InlineKeyboardButton(
            text, callback_data=f"post:select_template:{query.from_user.id}:{name}")])
    buttons.append([InlineKeyboardButton(
        "ʙᴀᴄᴋ", callback_data=f"post:back:{query.from_user.id}")])
    await query.edit_message_reply_markup(InlineKeyboardMarkup(buttons))


async def handle_select_template(session, template_name):
    session["active_template"] = template_name
    session["caption"] = None

# ᴄᴏᴅᴇ ɪs ᴄʀᴇᴀᴛᴇᴅ ʙʏ @ʙʜᴀʀᴀᴛʜ_ʙᴏʏ ꜰᴏʀ ᴘᴜʙʟɪᴄ ᴜsᴇ sᴏ ᴀᴛʟᴇᴀsᴛ ᴅᴏɴ'ᴛ ʀᴇᴍᴏᴠᴇ ᴄʀᴇᴅɪᴛs
async def handle_remove_buttons_menu(query, session):
    buttons = []
    for i, row in enumerate(session["buttons"]):
        for j, btn in enumerate(row):
            buttons.append([InlineKeyboardButton(
                f"❌ {btn.text}", callback_data=f"post:remove_button:{query.from_user.id}:{i}:{j}")])
    if not buttons:
        buttons.append([InlineKeyboardButton(
            "ɴᴏ ʙᴜᴛᴛᴏɴs ᴛᴏ ʀᴇᴍᴏᴠᴇ", callback_data="noop")])
    buttons.append([InlineKeyboardButton(
        "ʙᴀᴄᴋ", callback_data=f"post:back:{query.from_user.id}")])
    await query.edit_message_reply_markup(InlineKeyboardMarkup(buttons))



async def handle_remove_button(session, extra_data):
    try:
        row_i, col_i = int(extra_data[0]), int(extra_data[1])
        session["buttons"][row_i].pop(col_i)
        if not session["buttons"][row_i]:
            session["buttons"].pop(row_i)
    except (IndexError, ValueError):
        logger.warning("⚠️ ᴛʀɪᴇᴅ ᴛᴏ ʀᴇᴍᴏᴠᴇ ᴀ ʙᴜᴛᴛᴏɴ ᴛʜᴀᴛ ᴅᴏᴇs ɴᴏᴛ ᴇxɪsᴛ.")


async def handle_toggle_preview(query: CallbackQuery, session: dict):
    if session.get("custom_poster") and not session["custom_poster"].startswith("http"):
        await query.answer("🚫 ᴄᴀɴɴᴏᴛ sᴡɪᴛᴄʜ ᴛᴏ ᴛᴇxᴛ ᴍᴏᴅᴇ ᴡɪᴛʜ ᴀɴ ᴜᴘʟᴏᴀᴅᴇᴅ ᴘʜᴏᴛᴏ.", show_alert=True)
        return False
    session["photo_mode"] = not session["photo_mode"]
    return True


async def handle_toggle_poster(session):
    session["use_landscape"] = not session["use_landscape"]
    return True


async def handle_cancel(client: Client, query: CallbackQuery, session_id: int, _=None):
    if session := post_sessions.pop(session_id, None):
        if session.get("last_preview_message_id"):
            await client.delete_messages(query.message.chat.id, session["last_preview_message_id"])
    await query.message.reply_to_message.reply_text("❌ ᴘᴏsᴛ ᴄʀᴇᴀᴛɪᴏɴ ᴄᴀɴᴄᴇʟʟᴇᴅ.")


async def finalize_and_post(client: Client, query: CallbackQuery, session_id: int, _=None):
    session = post_sessions.pop(session_id, None)
    if not session:
        logger.warning(
            f"ꜰɪɴᴀʟɪᴢᴇ ᴄᴀʟʟᴇᴅ ꜰᴏʀ ᴀɴ ᴇxᴘɪʀᴇᴅ ᴏʀ ɪɴᴠᴀʟɪᴅ sᴇssɪᴏɴ_ɪᴅ: {session_id}")
        return

    await client.delete_messages(query.message.chat.id, session["last_preview_message_id"])
    status_msg = await query.message.reply_to_message.reply_text("⏳ <i>ꜰɪɴᴀʟɪᴢɪɴɢ ᴀɴᴅ ᴘᴏsᴛɪɴɢ...</i>")

    final_caption, _, poster_to_use = await _build_final_post_content(session, session_id)
    final_keyboard = InlineKeyboardMarkup(
        session["buttons"]) if session["buttons"] else None

    if not final_caption:
        logger.error(
            f"ꜰᴀɪʟᴇᴅ ᴛᴏ ꜰᴇᴛᴄʜ ᴍᴏᴠɪᴇ ᴅᴇᴛᴀɪʟs ꜰᴏʀ '{session['movie_name']}' ᴅᴜʀɪɴɢ ꜰɪɴᴀʟɪᴢᴀᴛɪᴏɴ.")
        return await status_msg.edit("⚠️ ᴄᴏᴜʟᴅ ɴᴏᴛ ꜰᴇᴛᴄʜ ᴍᴏᴠɪᴇ ᴅᴇᴛᴀɪʟs ᴛᴏ ᴘᴏsᴛ. ᴀʙᴏʀᴛɪɴɢ.")

    mode = "Photo" if session["photo_mode"] and poster_to_use else "Text"
    logger.info(f"ꜰɪɴᴀʟɪᴢɪɴɢ ᴘᴏsᴛ ꜰᴏʀ '{session['movie_name']}'. ᴍᴏᴅᴇ: {mode}")
    logger.info(f"ᴘᴏsᴛᴇʀ ᴛᴏ ᴜsᴇ: {poster_to_use}")
    logger.info(f"ꜰɪɴᴀʟ ᴄᴀᴘᴛɪᴏɴ ʟᴇɴɢᴛʜ: {len(final_caption)} ᴄʜᴀʀᴀᴄᴛᴇʀs.")

    try:
        if mode == "Photo":
            await client.send_photo(
                chat_id=MOVIE_UPDATE_CHANNEL, photo=poster_to_use,
                caption=final_caption, reply_markup=final_keyboard
            )
        else:
            text_content = f"<a href='{poster_to_use}'>&#8205;</a>{final_caption}" if poster_to_use else final_caption
            await client.send_message(
                chat_id=MOVIE_UPDATE_CHANNEL, text=text_content,
                reply_markup=final_keyboard, disable_web_page_preview=False,
                invert_media=ABOVE_PREVIEW
            )

        await status_msg.edit("✅ ᴘᴏsᴛ ʜᴀs ʙᴇᴇɴ sᴇɴᴛ ᴛᴏ ᴛʜᴇ ᴜᴘᴅᴀᴛᴇ ᴄʜᴀɴɴᴇʟ.")
        logger.info(
            f"sᴜᴄᴄᴇssꜰᴜʟʟʏ ᴘᴏsᴛᴇᴅ '{session['movie_name']}' ᴛᴏ ᴛʜᴇ ᴜᴘᴅᴀᴛᴇ ᴄʜᴀɴɴᴇʟ.")

    except MessageTooLong:
        error_text = "🚨 <b>ᴘᴏsᴛ ꜰᴀɪʟᴇᴅ</b>\n\nᴛʜᴇ ꜰɪɴᴀʟ ᴄᴀᴘᴛɪᴏɴ ɪs ᴛᴏᴏ ʟᴏɴɢ ꜰᴏʀ ᴀ ᴛᴇʟᴇɢʀᴀᴍ ᴍᴇssᴀɢᴇ (ʟɪᴍɪᴛ ɪs 4096 ᴄʜᴀʀᴀᴄᴛᴇʀs). ᴘʟᴇᴀsᴇ sʜᴏʀᴛᴇɴ ᴛʜᴇ ᴘʟᴏᴛ ᴏʀ ᴏᴛʜᴇʀ ᴛᴇxᴛ ᴀɴᴅ ᴛʀʏ ᴀɢᴀɪɴ."
        await status_msg.edit(error_text)
        logger.error(
            f"ꜰᴀɪʟᴇᴅ ᴛᴏ ᴘᴏsᴛ '{session['movie_name']}': ᴍᴇssᴀɢᴇᴛᴏᴏʟᴏɴɢ ᴇʀʀᴏʀ.", exc_info=True)
    except Exception as e:
        error_text = f"❌ ꜰᴀɪʟᴇᴅ ᴛᴏ ᴘᴏsᴛ ᴛᴏ ᴜᴘᴅᴀᴛᴇ ᴄʜᴀɴɴᴇʟ.\n<b>ᴇʀʀᴏʀ:</b> <code>{e}</code>"
        await status_msg.edit(error_text)
        logger.error(
            f"ᴀɴ ᴜɴᴇxᴘᴇᴄᴛᴇᴅ ᴇʀʀᴏʀ ᴏᴄᴄᴜʀʀᴇᴅ ᴡʜɪʟᴇ ᴘᴏsᴛɪɴɢ '{session['movie_name']}':", exc_info=True)



#code is created by @bharath_boy for public use so atleast don't remove credits