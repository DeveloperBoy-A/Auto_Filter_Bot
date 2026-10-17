import os
import logging
from datetime import datetime

from pyrogram import Client, filters, enums
from pyrogram.enums import ParseMode
from pyrogram.errors.exceptions.bad_request_400 import (
    UserNotParticipant, MediaEmpty, PhotoInvalidDimensions, WebpageMediaEmpty
)
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery

from utils import extract_user, get_file_id, get_poster
from database.ia_filterdb import dreamxbotz_get_movies, dreamxbotz_get_series

logger = logging.getLogger(__name__)
logger.setLevel(logging.ERROR)


# ───────────── /id ─────────────
@Client.on_message(filters.command('id'))
async def showid(client, message):
    chat = message.chat
    user = message.from_user
    target = message.reply_to_message or message
    text = ""

    if chat.type == enums.ChatType.PRIVATE:
        username = f"@{user.username}" if user.username else "None"
        text += (
            f"<b>➲ 𝐅ɪʀꜱᴛ Nᴀᴍᴇ:</b> {user.first_name}\n"
            f"<b>➲ 𝐋ᴀꜱᴛ Nᴀᴍᴇ:</b> {user.last_name or 'None'}\n"
            f"<b>➲ 𝐔ꜱᴇʀɴᴀᴍᴇ:</b> {username}\n"
            f"<b>➲ 𝐓ᴇʟᴇɢʀᴀᴍ ID:</b> <code>{user.id}</code>\n"
            f"<b>➲ 𝐃ᴀᴛᴀ Cᴇɴᴛʀᴇ:</b> <code>{user.dc_id or 'N/A'}</code>\n"
        )
    else:
        text += f"<b>➲ 𝐂ʜᴀᴛ ID:</b> <code>{chat.id}</code>\n"
        text += f"<b>➲ 𝐔ꜱᴇʀ ID:</b> <code>{user.id if user else 'Anonymous'}</code>\n"
        if message.reply_to_message:
            r = message.reply_to_message.from_user
            text += f"<b>➲ 𝐑ᴇᴘʟɪᴇᴅ Uꜱᴇʀ ID:</b> <code>{r.id if r else 'Anonymous'}</code>\n"

    file_info = get_file_id(target)
    if file_info:
        text += f"<b>➲ {file_info.message_type}:</b> <code>{file_info.file_id}</code>\n"

    await message.reply_text(text, quote=True, parse_mode=ParseMode.HTML)


# ───────────── /info ─────────────
@Client.on_message(filters.command(["info"]))
async def who_is(client, message):
    status_message = await message.reply_text("`Fetching user info...`")
    await status_message.edit("`Processing user info...`")

    from_user_id, _ = extract_user(message)
    try:
        from_user = await client.get_users(from_user_id)
    except Exception as error:
        return await status_message.edit(str(error))
    if from_user is None:
        return await status_message.edit("no valid user_id / message specified")

    last_name = from_user.last_name or "None"
    username = f"@{from_user.username}" if from_user.username else "None"
    dc_id = from_user.dc_id or "[User Doesn't Have A Valid DP]"

    out = (
        f"<b>➲ 𝐅ɪʀꜱᴛ Nᴀᴍᴇ:</b> {from_user.first_name}\n"
        f"<b>➲ 𝐋ᴀꜱᴛ Nᴀᴍᴇ:</b> {last_name}\n"
        f"<b>➲ 𝐓ᴇʟᴇɢʀᴀᴍ ID:</b> <code>{from_user.id}</code>\n"
        f"<b>➲ 𝐃ᴀᴛᴀ Cᴇɴᴛʀᴇ:</b> <code>{dc_id}</code>\n"
        f"<b>➲ 𝐔ꜱᴇʀ Nᴀᴍᴇ:</b> {username}\n"
        f"<b>➲ 𝐔ꜱᴇʀ Lɪɴᴋ:</b> <a href='tg://user?id={from_user.id}'><b>Cʟɪᴄᴋ Hᴇʀᴇ</b></a>\n"
    )

    if message.chat.type in (enums.ChatType.SUPERGROUP, enums.ChatType.CHANNEL):
        try:
            member = await message.chat.get_member(from_user.id)
            joined = (member.joined_date or datetime.now()).strftime("%Y.%m.%d %H:%M:%S")
            out += f"<b>➲ 𝐉ᴏɪɴᴇᴅ Tʜɪꜱ Cʜᴀᴛ Oɴ:</b> <code>{joined}</code>\n"
        except UserNotParticipant:
            pass

    reply_markup = InlineKeyboardMarkup(
        [[InlineKeyboardButton('🔐 Cʟᴏꜱᴇ', callback_data='close_data')]]
    )

    if from_user.photo:
        local_photo = await client.download_media(message=from_user.photo.big_file_id)
        try:
            await message.reply_photo(
                photo=local_photo, quote=True, reply_markup=reply_markup,
                caption=out, parse_mode=enums.ParseMode.HTML, disable_notification=True
            )
        finally:
            if local_photo and os.path.exists(local_photo):
                os.remove(local_photo)
    else:
        await message.reply_text(
            text=out, reply_markup=reply_markup, quote=True,
            parse_mode=enums.ParseMode.HTML, disable_notification=True
        )
    await status_message.delete()


# ───────────── /imdb ─────────────
@Client.on_message(filters.command(["imdb", 'search']))
async def imdb_search(client, message):
    if ' ' not in message.text:
        return await message.reply('𝐆ɪᴠᴇ Mᴇ A Mᴏᴠɪᴇ / Sᴇʀɪᴇꜱ Nᴀᴍᴇ')

    k = await message.reply('𝐒ᴇᴀʀᴄʜɪɴɢ Iᴍᴅʙ...')
    r, title = message.text.split(None, 1)
    movies = await get_poster(title, bulk=True)
    if not movies:
        return await k.edit("𝐍ᴏ Rᴇꜱᴜʟᴛꜱ Fᴏᴜɴᴅ")

    btn = [
        [InlineKeyboardButton(
            text=f"{movie.get('title')} - {movie.get('year')}",
            callback_data=f"imdb#{movie.movieID}",
        )]
        for movie in movies
    ]
    await k.edit('𝐇ᴇʀᴇ Iꜱ Wʜᴀᴛ I Fᴏᴜɴᴅ Oɴ Iᴍᴅʙ', reply_markup=InlineKeyboardMarkup(btn))


@Client.on_callback_query(filters.regex('^imdb'))
async def imdb_callback(bot: Client, quer_y: CallbackQuery):
    i, movie = quer_y.data.split('#')
    imdb = await get_poster(query=movie, id=True)

    if not imdb:
        await quer_y.message.edit("𝐍ᴏ Rᴇꜱᴜʟᴛꜱ")
        return await quer_y.answer()

    btn = [[InlineKeyboardButton(text=f"{imdb.get('title')}", url=imdb['url'])]]
    markup = InlineKeyboardMarkup(btn)

    caption = f"""
<b>𝐐ᴜᴇʀʏ: {imdb['title']}</b>

🏷 𝐓ɪᴛʟᴇ: <a href="{imdb['url']}">{imdb['title']}</a>
🎭 𝐆ᴇɴʀᴇꜱ: {imdb.get('genres', 'N/A')}
📆 𝐘ᴇᴀʀ: <a href="{imdb['url']}/releaseinfo">{imdb['year']}</a>
🌟 𝐑ᴀᴛɪɴɢ: <a href="{imdb['url']}/ratings">{imdb['rating']}</a>/10
"""

    if imdb.get('poster'):
        try:
            await quer_y.message.reply_photo(photo=imdb['poster'], caption=caption, reply_markup=markup)
        except (MediaEmpty, PhotoInvalidDimensions, WebpageMediaEmpty):
            poster = imdb['poster'].replace('.jpg', "._V1_UX360.jpg")
            await quer_y.message.reply_photo(photo=poster, caption=caption, reply_markup=markup)
        except Exception as e:
            logger.exception(e)
            await quer_y.message.reply(caption, reply_markup=markup, disable_web_page_preview=False)
        await quer_y.message.delete()
    else:
        await quer_y.message.edit(caption, reply_markup=markup, disable_web_page_preview=False)
    await quer_y.answer()


# ───────────── /movies ─────────────
@Client.on_message(filters.private & filters.command("movies"))
async def dreamxbotz_list_movies(client, message):
    try:
        movies = await dreamxbotz_get_movies()
        if not movies:
            return await message.reply("❌ 𝐍ᴏ Rᴇᴄᴇɴᴛ Mᴏᴠɪᴇꜱ Fᴏᴜɴᴅ", parse_mode=ParseMode.HTML)
        msg = "<b>𝐋ᴀᴛᴇꜱᴛ Uᴘʟᴏᴀᴅꜱ Lɪꜱᴛ ✅</b>\n\n<b>🎬 𝐌ᴏᴠɪᴇꜱ:</b>\n\n"
        msg += "\n".join(f"<b>{i+1}. {m}</b>" for i, m in enumerate(movies))
        await message.reply(msg[:4096], parse_mode=ParseMode.HTML)
    except Exception as e:
        logger.error(f"Error in dreamxbotz_list_movies: {e}")
        await message.reply("𝐀ɴ Eʀʀᴏʀ Oᴄᴄᴜʀʀᴇᴅ ☹️", parse_mode=ParseMode.HTML)


# ───────────── /series ─────────────
@Client.on_message(filters.private & filters.command("series"))
async def dreamxbotz_list_series(client, message):
    try:
        series_data = await dreamxbotz_get_series()
        if not series_data:
            return await message.reply("❌ 𝐍ᴏ Rᴇᴄᴇɴᴛ Sᴇʀɪᴇꜱ Fᴏᴜɴᴅ", parse_mode=ParseMode.HTML)
        msg = "<b>𝐋ᴀᴛᴇꜱᴛ Uᴘʟᴏᴀᴅꜱ Lɪꜱᴛ ✅</b>\n\n<b>📺 𝐒ᴇʀɪᴇꜱ:</b>\n"
        for i, (title, seasons) in enumerate(series_data.items(), 1):
            season_list = ", ".join(f"{s}" for s in seasons)
            msg += f"<b>{i}. {title} - Season {season_list}</b>\n"
        await message.reply(msg[:4096], parse_mode=ParseMode.HTML)
    except Exception as e:
        logger.error(f"Error in dreamxbotz_list_series: {e}")
        await message.reply("𝐀ɴ Eʀʀᴏʀ Oᴄᴄᴜʀʀᴇᴅ ☹️", parse_mode=ParseMode.HTML)
