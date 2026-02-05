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

# logger setup
logger = logging.getLogger(__name__)
post_sessions = {}

BOT_NAME = temp.U_NAME
USE_GETFILE_BUTTON_BY_DEFAULT = True

# --- NEW CUSTOM FORMATTING (FOR POST) ---
CUSTOM_TEMPLATE = """<b><a href="{poster_url}">🖼️</a><a href="{imdb_url}">🆕</a></b> <code>【{title} ({year})】</code> ✅

🎭 𝑮𝒆𝒏𝒓𝒆𝒔 : <b>{genres}</b>
📺 𝑶𝑻𝑻 : <b>{otts}</b>
🖼️ 𝑷𝒊𝒙𝒆𝒍𝒔 : <b>{quality}</b>
🎧 𝑨𝒖𝒅𝒊𝒐 : <b>{language}</b>
🔥 𝑹𝒂𝒕𝒊𝒏𝒈 : <b>{rating}</b>

<blockquote>🌿 𝑷𝒐𝒘𝒆𝒓𝒆𝒅 𝒃𝒚 : [🔰𝑵𝒆𝒘 𝒎𝒐𝒗𝒊𝒆 & 𝒘𝒆𝒃 𝒔𝒆𝒓𝒊𝒆𝒔🔰]</blockquote>"""

LANGUAGES = ["Hindi", "English", "Tamil", "Telugu", "Malayalam", "Kannada", "Bengali", "Punjabi", "Marathi", "Gujarati"]
RESOLUTIONS = ["480p", "720p", "1080p", "2160p", "4K", "HEVC", "BluRay", "WEB-DL"]
OTT_PLATFORMS = ["Netflix", "Amazon Prime", "JioCinema", "Hotstar", "SonyLIV", "Zee5", "Aha"]

@Client.on_message(filters.command("post") & filters.user(ADMINS), group=-4)
async def post_command(client: Client, message: Message):
    if len(message.command) == 1:
        return await message.reply_text("𝐏𝐥𝐞𝐚𝐬𝐞 𝐩𝐫𝐨𝐯𝐢𝐝𝐞 𝐚 𝐦𝐨𝐯𝐢𝐞 𝐧𝐚𝐦𝐞. 𝐔𝐬𝐚𝐠𝐞: `/post Movie Name`")

    movie_name = " ".join(message.command[1:])
    user_id = message.from_user.id
    
    status = await message.reply_text("🔍 𝑭𝒆𝒕𝒄𝒉𝒊𝒏𝒈 𝑫𝒆𝒕𝒂𝒊𝒍𝒔...")
    movie_details = await get_movie_detailsx(movie_name)
    await status.delete()

    if not movie_details:
        return await message.reply_text("❌ 𝐂𝐨𝐮𝐥𝐝 𝐧𝐨𝐭 𝐟𝐞𝐭𝐜𝐡 𝐝𝐞𝐭𝐚𝐢𝐥𝐬!")

    post_sessions[user_id] = {
        "movie_name": movie_name,
        "movie_details": movie_details,
        "buttons": [],
        "photo_mode": True,
        "use_landscape": True if movie_details.get("backdrop_url") else False,
        "custom_languages": [],
        "custom_resolutions": [],
        "custom_otts": [],
        "original_message_id": message.id,
        "last_preview_message_id": None,
        "custom_poster": None
    }

    if USE_GETFILE_BUTTON_BY_DEFAULT:
        title = movie_details.get("title", "movie")
        year = movie_details.get("year", "")
        movie_year = re.sub(r"[ *:\.]", "-", f"{title} {year}").strip()
        url = f"https://t.me/{BOT_NAME}?start=getfile-{movie_year}"
        
        post_sessions[user_id]["buttons"] = [
            [InlineKeyboardButton('🗃️ ✦ 𝗚𝗘𝗧 𝗙𝗜𝗟𝗘 ✦ 🗃️', url=url)],
            [InlineKeyboardButton('♻️ Hᴏᴡ Tᴏ Dᴏᴡɴʟᴏᴀᴅ ♻️', url="https://t.me/newmovieswebseries_group/56553?single")],
            [InlineKeyboardButton('🔰 ᴍᴏᴠɪᴇ ꜱᴇᴀʀᴄʜ ɢʀᴏᴜᴘ 🔰', url="https://t.me/newmovieswebseries_group")]
        ]

    await update_post_preview(client, user_id, message.chat.id, force_resend=True)

async def _build_final_post_content(session: dict, session_id: int):
    movie_details = session["movie_details"]
    title = movie_details.get("title", "N/A")
    year = movie_details.get("year", "N/A")
    rating = movie_details.get("rating", "N/A")
    genres = ", ".join(movie_details.get("genres", [])) if movie_details.get("genres") else "N/A"
    poster_url = movie_details.get("poster_url", "")
    imdb_id = movie_details.get("imdb_id", "tt0000000")
    imdb_url = f"https://www.imdb.com/title/{imdb_id}/"

    langs = ', '.join(session['custom_languages']) if session['custom_languages'] else "𝑵𝒐𝒕 𝑺𝒆𝒍𝒆𝒄𝒕𝒆𝒅"
    res = ', '.join(session['custom_resolutions']) if session['custom_resolutions'] else "𝑵𝒐𝒕 𝑺𝒆𝒍𝒆𝒄𝒕𝒆𝒅"
    otts = ', '.join(session['custom_otts']) if session['custom_otts'] else "𝑵𝒐𝒕 𝑺𝒆𝒍𝒆𝒄𝒕𝒆𝒅"

    final_caption = CUSTOM_TEMPLATE.format(
        poster_url=poster_url,
        imdb_url=imdb_url,
        title=title,
        year=year,
        genres=genres,
        otts=otts,
        quality=res,
        language=langs,
        rating=rating
    )
    
    # Admin ke liye extra info add karna (Preview mode mein)
    admin_caption = f"⚙️ 𝐀𝐝𝐦𝐢𝐧 𝐂𝐨𝐧𝐭𝐫𝐨𝐥 𝐏𝐚𝐧𝐞𝐥\n\n{final_caption}"

    keyboard = build_keyboard(session, session_id)
    poster_to_use = session.get("custom_poster") or \
        (movie_details.get("backdrop_url") if session.get("use_landscape") else poster_url)

    return admin_caption, keyboard, poster_to_use

def build_keyboard(session: dict, session_id: int):
    # Admin Buttons with Custom Font
    rows = [
        [InlineKeyboardButton("🎧 𝑨𝒖𝒅𝒊𝒐", callback_data=f"post:languages:{session_id}"),
         InlineKeyboardButton("🖼️ 𝑷𝒊𝒙𝒆𝒍𝒔", callback_data=f"post:resolutions:{session_id}")],
        [InlineKeyboardButton("📺 𝑶𝑻𝑻 𝑷𝒍𝒂𝒕𝒇𝒐𝒓𝒎𝒔", callback_data=f"post:otts:{session_id}")],
        [InlineKeyboardButton(f"𝑴𝒐𝒅𝒆: {'𝑷𝒉𝒐𝒕𝒐' if session['photo_mode'] else '𝑻𝒆𝒙𝒕'}", callback_data=f"post:toggle_preview:{session_id}"),
         InlineKeyboardButton(f"𝑷𝒐𝒔𝒕𝒆𝒓: {'𝑳𝒂𝒏𝒅' if session['use_landscape'] else '𝑷𝒐𝒓𝒕'}", callback_data=f"post:toggle_poster:{session_id}")],
        [InlineKeyboardButton("✅ 𝑷𝒐𝒔𝒕 𝒕𝒐 𝑪𝒉𝒂𝒏𝒏𝒆𝒍", callback_data=f"post:finalize:{session_id}"),
         InlineKeyboardButton("❌ 𝑪𝒂𝒏𝒄𝒆𝒍", callback_data=f"post:cancel:{session_id}")]
    ]
    return InlineKeyboardMarkup(rows)

async def update_post_preview(client: Client, session_id: int, chat_id: int, force_resend: bool = False):
    session = post_sessions.get(session_id)
    if not session: return

    admin_caption, keyboard, poster_to_use = await _build_final_post_content(session, session_id)

    if force_resend:
        if session.get("last_preview_message_id"):
            try: await client.delete_messages(chat_id, session["last_preview_message_id"])
            except: pass
        
        sent = await client.send_photo(
            chat_id, photo=poster_to_use, caption=admin_caption, 
            reply_markup=keyboard, reply_to_message_id=session["original_message_id"]
        )
        session["last_preview_message_id"] = sent.id
    else:
        try:
            await client.edit_message_caption(chat_id, session["last_preview_message_id"], caption=admin_caption, reply_markup=keyboard)
        except MessageNotModified: pass

@Client.on_callback_query(filters.regex(r"^post:"), group=-4)
async def post_callbacks(client: Client, query: CallbackQuery):
    data = query.data.split(":")
    action, session_id = data[1], int(data[2])
    session = post_sessions.get(session_id)

    if not session or query.from_user.id != session_id:
        return await query.answer("🚨 𝑨𝒄𝒄𝒆𝒔𝒔 𝑫𝒆𝒏𝒊𝒆𝒅!", show_alert=True)

    if action == "languages":
        await show_selection_menu(query, session_id, "languages", LANGUAGES, session["custom_languages"])
    elif action == "resolutions":
        await show_selection_menu(query, session_id, "resolutions", RESOLUTIONS, session["custom_resolutions"])
    elif action == "otts":
        await show_selection_menu(query, session_id, "otts", OTT_PLATFORMS, session["custom_otts"])
    
    elif action == "select":
        category, item = data[3], data[4]
        target_list = f"custom_{category}"
        if item in session[target_list]: session[target_list].remove(item)
        else: session[target_list].append(item)
        
        items_list = LANGUAGES if category == "languages" else RESOLUTIONS if category == "resolutions" else OTT_PLATFORMS
        await show_selection_menu(query, session_id, category, items_list, session[target_list])

    elif action == "toggle_preview":
        session["photo_mode"] = not session["photo_mode"]
        await update_post_preview(client, session_id, query.message.chat.id)

    elif action == "toggle_poster":
        session["use_landscape"] = not session["use_landscape"]
        await update_post_preview(client, session_id, query.message.chat.id, force_resend=True)

    elif action == "back":
        await update_post_preview(client, session_id, query.message.chat.id)

    elif action == "finalize":
        await finalize_and_post(client, query, session_id)
    
    elif action == "cancel":
        post_sessions.pop(session_id, None)
        await query.message.delete()
        await query.answer("🗑️ 𝑷𝒐𝒔𝒕 𝑪𝒂𝒏𝒄𝒆𝒍𝒍𝒆𝒅")

async def show_selection_menu(query, session_id, category, all_items, selected_items):
    buttons = []
    for i in range(0, len(all_items), 2):
        row = []
        for item in all_items[i:i+2]:
            text = f"✅ {item}" if item in selected_items else f"🔘 {item}"
            row.append(InlineKeyboardButton(text, callback_data=f"post:select:{session_id}:{category}:{item}"))
        buttons.append(row)
    buttons.append([InlineKeyboardButton("🔙 𝑩𝒂𝒄𝒌 𝒕𝒐 𝑷𝒓𝒆𝒗𝒊𝒆𝒘", callback_data=f"post:back:{session_id}")])
    await query.edit_message_reply_markup(InlineKeyboardMarkup(buttons))

async def finalize_and_post(client: Client, query: CallbackQuery, session_id: int):
    session = post_sessions.pop(session_id, None)
    if not session: return

    # Post ke liye caption bina "Admin Panel" text ke
    movie_details = session["movie_details"]
    langs = ', '.join(session['custom_languages']) if session['custom_languages'] else "𝑵/𝑨"
    res = ', '.join(session['custom_resolutions']) if session['custom_resolutions'] else "𝑵/𝑨"
    otts = ', '.join(session['custom_otts']) if session['custom_otts'] else "𝑵/𝑨"

    final_caption = CUSTOM_TEMPLATE.format(
        poster_url=movie_details.get("poster_url", ""),
        imdb_url=f"https://www.imdb.com/title/{movie_details.get('imdb_id', 'tt0000000')}/",
        title=movie_details.get("title", "N/A"),
        year=movie_details.get("year", "N/A"),
        genres=", ".join(movie_details.get("genres", [])) if movie_details.get("genres") else "N/A",
        otts=otts,
        quality=res,
        language=langs,
        rating=movie_details.get("rating", "N/A")
    )
    
    final_keyboard = InlineKeyboardMarkup(session["buttons"])

    try:
        await client.send_photo(
            chat_id=MOVIE_UPDATE_CHANNEL,
            photo=session.get("custom_poster") or (movie_details.get("backdrop_url") if session.get("use_landscape") else movie_details.get("poster_url")),
            caption=final_caption,
            reply_markup=final_keyboard
        )
        await query.message.edit_caption("✅ **𝑷𝒐𝒔𝒕𝒆𝒅 𝑺𝒖𝒄𝒄𝒆𝒔𝒔𝒇𝒖𝒍𝒍𝒚!**")
    except Exception as e:
        await query.message.reply_text(f"❌ 𝑬𝒓𝒓𝒐𝒓: {e}")
