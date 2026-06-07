from pyrogram.errors import MessageNotModified
from utils import get_size, is_subscribed, is_req_subscribed, group_setting_buttons, get_poster, temp, get_settings, get_time, save_group_settings, get_cap, imdb, is_check_admin, extract_request_content, log_error, clean_filename, generate_season_variations, clean_search_text
import tracemalloc
from datetime import datetime
#from fuzzywuzzy import process
from rapidfuzz import fuzz
from rapidfuzz import process
from dreamxbotz.util.file_properties import get_name, get_hash
from urllib.parse import quote_plus
import logging
from database.ia_filterdb import Media, Media2, get_file_details, get_search_results, get_bad_files
from database.config_db import mdb
from pyrogram.errors import FloodWait, UserIsBlocked, MessageNotModified, PeerIdInvalid, ChatAdminRequired, UserNotParticipant
from pyrogram import Client, filters, enums
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery, InputMediaPhoto, WebAppInfo
from info import *
from Script import script
from pyrogram.errors.exceptions.bad_request_400 import MediaEmpty, PhotoInvalidDimensions, WebpageMediaEmpty
from database.refer import referdb
from database.users_chats_db import db
import unicodedata
import asyncio
import re
import math
import random
import pytz
from datetime import datetime, timedelta
lock = asyncio.Lock()

logger = logging.getLogger(__name__)
logger.setLevel(logging.ERROR)

tracemalloc.start()


TIMEZONE = "Asia/Kolkata"
BUTTON = {}
BUTTONS = {}
FRESH = {}
BUTTONS0 = {}
BUTTONS1 = {}
BUTTONS2 = {}
SPELL_CHECK = {}


@Client.on_message(filters.group & filters.text & filters.incoming)
async def give_filter(client, message):
    if EMOJI_MODE:
        try:
            await message.react(emoji=random.choice(REACTIONS), big=True)
        except Exception:
            await message.react(emoji="⚡️", big=True)
    await mdb.update_top_messages(message.from_user.id, message.text)
    if message.chat.id != SUPPORT_CHAT_ID:
        settings = await get_settings(message.chat.id)
        try:
            if settings['auto_ffilter']:
                if re.search(r'https?://\S+|www\.\S+|t\.me/\S+', message.text):
                    if await is_check_admin(client, message.chat.id, message.from_user.id):
                        return
                    return await message.delete()
                await auto_filter(client, message)
        except KeyError:
            pass
    else:
        search = message.text
        _, _, total_results = await get_search_results(chat_id=message.chat.id, query=search.lower(), offset=0, filter=True)
        if total_results == 0:
            return
        await message.reply_text(
            f"<b>Hᴇʏ {message.from_user.mention},\n\n"
            f"ʏᴏᴜʀ ʀᴇǫᴜᴇꜱᴛ ɪꜱ ᴀʟʀᴇᴀᴅʏ ᴀᴠᴀɪʟᴀʙʟᴇ ✅\n\n"
            f"📂 ꜰɪʟᴇꜱ ꜰᴏᴜɴᴅ : {str(total_results)}\n"
            f"🔍 ꜱᴇᴀʀᴄʜ :</b> <code>{search}</code>\n\n"
            f"<b>‼️ ᴛʜɪs ɪs ᴀ <u>sᴜᴘᴘᴏʀᴛ ɢʀᴏᴜᴘ</u> sᴏ ᴛʜᴀᴛ ʏᴏᴜ ᴄᴀɴ'ᴛ ɢᴇᴛ ғɪʟᴇs ғʀᴏᴍ ʜᴇʀᴇ...\n\n"
            f"📝 ꜱᴇᴀʀᴄʜ ʜᴇʀᴇ : 👇</b>",
            reply_markup=InlineKeyboardMarkup(
                [[InlineKeyboardButton("🔍 ᴊᴏɪɴ ᴀɴᴅ ꜱᴇᴀʀᴄʜ ʜᴇʀᴇ 🔎", url=GRP_LNK)]])
        )


@Client.on_message(filters.private & filters.text & filters.incoming & ~filters.regex(r"^/"))
async def pm_text(bot, message):
    bot_id = bot.me.id
    content = message.text
    user = message.from_user.first_name
    user_id = message.from_user.id
    if EMOJI_MODE:
        try:
            await message.react(emoji=random.choice(REACTIONS), big=True)
        except Exception:
            await message.react(emoji="⚡️", big=True)
    if content.startswith(("#")):
        return
    try:
        await mdb.update_top_messages(user_id, content)
        pm_search = await db.pm_search_status(bot_id)
        if pm_search:
            await auto_filter(bot, message)
        else:
            await message.reply_text(
                text=(
                    f"<b>🙋 ʜᴇʏ {user} 😍 ,\n\n"
                    "𝒀𝒐𝒖 𝒄𝒂𝒏 𝒔𝒆𝒂𝒓𝒄𝒉 𝒇𝒐𝒓 𝒎𝒐𝒗𝒊𝒆𝒔 𝒐𝒏𝒍𝒚 𝒐𝒏 𝒐𝒖𝒓 𝑴𝒐𝒗𝒊𝒆 𝑮𝒓𝒐𝒖𝒑. 𝒀𝒐𝒖 𝒂𝒓𝒆 𝒏𝒐𝒕 𝒂𝒍𝒍𝒐𝒘𝒆𝒅 𝒕𝒐 𝒔𝒆𝒂𝒓𝒄𝒉 𝒇𝒐𝒓 𝒎𝒐𝒗𝒊𝒆𝒔 𝒐𝒏 𝑫𝒊𝒓𝒆𝒄𝒕 𝑩𝒐𝒕. 𝑷𝒍𝒆𝒂𝒔𝒆 𝒋𝒐𝒊𝒏 𝒐𝒖𝒓 𝒎𝒐𝒗𝒊𝒆 𝒈𝒓𝒐𝒖𝒑 𝒃𝒚 𝒄𝒍𝒊𝒄𝒌𝒊𝒏𝒈 𝒐𝒏 𝒕𝒉𝒆  𝑹𝑬𝑸𝑼𝑬𝑺𝑻 𝑯𝑬𝑹𝑬 𝒃𝒖𝒕𝒕𝒐𝒏 𝒈𝒊𝒗𝒆𝒏 𝒃𝒆𝒍𝒐𝒘 𝒂𝒏𝒅 𝒔𝒆𝒂𝒓𝒄𝒉 𝒚𝒐𝒖𝒓 𝒇𝒂𝒗𝒐𝒓𝒊𝒕𝒆 𝒎𝒐𝒗𝒊𝒆 𝒕𝒉𝒆𝒓𝒆 👇\n\n"
                    "<blockquote>"
                    "आप केवल हमारे 𝑴𝒐𝒗𝒊𝒆 𝑮𝒓𝒐𝒖𝒑 पर ही 𝑴𝒐𝒗𝒊𝒆 𝑺𝒆𝒂𝒓𝒄𝒉 कर सकते हो । "
                    "आपको 𝑫𝒊𝒓𝒆𝒄𝒕 𝑩𝒐𝒕 पर 𝑴𝒐𝒗𝒊𝒆 𝑺𝒆𝒂𝒓𝒄𝒉 करने की 𝑷𝒆𝒓𝒎𝒊𝒔𝒔𝒊𝒐𝒏 नहीं है कृपया नीचे दिए गए 𝑹𝑬𝑸𝑼𝑬𝑺𝑻 𝑯𝑬𝑹𝑬 वाले 𝑩𝒖𝒕𝒕𝒐𝒏 पर क्लिक करके हमारे 𝑴𝒐𝒗𝒊𝒆 𝑮𝒓𝒐𝒖𝒑 को 𝑱𝒐𝒊𝒏 करें और वहां पर अपनी मनपसंद 𝑴𝒐𝒗𝒊𝒆 𝑺𝒆𝒂𝒓𝒄𝒉 सर्च करें ।"
                    "</blockquote></b>"
                ), reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("📝 ʀᴇǫᴜᴇsᴛ ʜᴇʀᴇ ", url=GRP_LNK)]]))
            await bot.send_message(chat_id=LOG_CHANNEL,
                                   text=(
                                       f"<b>#𝐏𝐌_𝐌𝐒𝐆\n\n"
                                       f"👤 Nᴀᴍᴇ : {user}\n"
                                       f"🆔 ID : {user_id}\n"
                                       f"💬 Mᴇssᴀɢᴇ : {content}</b>"
                                   )
                                   )
    except Exception:
        pass


@Client.on_callback_query(filters.regex(r"^reffff"))
async def refercall(bot, query):
    btn = [[
        InlineKeyboardButton(
            'invite link', url=f'https://telegram.me/share/url?url=https://t.me/{bot.me.username}?start=reff_{query.from_user.id}&text=Hello%21%20Experience%20a%20bot%20that%20offers%20a%20vast%20library%20of%20unlimited%20movies%20and%20series.%20%F0%9F%98%83'),
        InlineKeyboardButton(
            f'⏳ {referdb.get_refer_points(query.from_user.id)}', callback_data='ref_point'),
        InlineKeyboardButton('Back', callback_data='premium_info')
    ]]
    reply_markup = InlineKeyboardMarkup(btn)
    try:
        await bot.edit_message_media(
            query.message.chat.id,
            query.message.id,
            InputMediaPhoto("https://graph.org/file/1a2e64aee3d4d10edd930.jpg")
        )
    except Exception as e:    
        pass
    await query.message.edit_text(
        text=f'Hay Your refer link:\n\nhttps://t.me/{bot.me.username}?start=reff_{query.from_user.id}\n\nShare this link with your friends, Each time they join,  you will get 10 refferal points and after 100 points you will get 1 month premium subscription.',
        reply_markup=reply_markup,
        parse_mode=enums.ParseMode.HTML
    )
    await query.answer()


@Client.on_callback_query(filters.regex(r"^next"))
async def next_page(bot, query):
    ident, req, key, offset = query.data.split("_")
    curr_time = datetime.now(pytz.timezone('Asia/Kolkata')).time()
    if int(req) not in [query.from_user.id, 0]:
        return await query.answer(script.ALRT_TXT.format(query.from_user.first_name), show_alert=True)
    try:
        offset = int(offset)
    except:
        offset = 0
    if BUTTONS.get(key) != None:
        search = BUTTONS.get(key)
    else:
        search = FRESH.get(key)
    if not search:
        await query.answer(script.OLD_ALRT_TXT.format(query.from_user.first_name), show_alert=True)
        return
    files, n_offset, total = await get_search_results(query.message.chat.id, search, offset=offset, filter=True)
    try:
        n_offset = int(n_offset)
    except:
        n_offset = 0

    if not files:
        return
    temp.GETALL[key] = files
    temp.SHORT[query.from_user.id] = query.message.chat.id
    settings = await get_settings(query.message.chat.id)
    if settings.get('button'):
        btn = [
            [
                InlineKeyboardButton(text=f"🔗 {get_size(file.file_size)} ≽ " + clean_filename(
                    file.file_name), callback_data=f'file#{file.file_id}'),
            ]
            for file in files
        ]
        btn.insert(0,
                   [
                       InlineKeyboardButton(
                           f'Qᴜᴀʟɪᴛʏ', callback_data=f"qualities#{key}"),
                       InlineKeyboardButton(
                           "Lᴀɴɢᴜᴀɢᴇ", callback_data=f"languages#{key}"),
                       InlineKeyboardButton(
                           "Sᴇᴀsᴏɴ",  callback_data=f"seasons#{key}")
                   ]
                   )
        btn.insert(0,
                   [
                       InlineKeyboardButton(
                           "⚜️ 𝐑𝐞𝐦𝐨𝐯𝐞 𝐚𝐝𝐬 ⚜️", url=f"https://t.me/{temp.U_NAME}?start=premium"),
                       InlineKeyboardButton(
                           "Sᴇɴᴅ Aʟʟ", callback_data=f"sendfiles#{key}")

                   ]
                   )

    else:
        btn = []
        btn.insert(0,
                   [
                       InlineKeyboardButton(
                           f'Qᴜᴀʟɪᴛʏ', callback_data=f"qualities#{key}"),
                       InlineKeyboardButton(
                           "Lᴀɴɢᴜᴀɢᴇ", callback_data=f"languages#{key}"),
                       InlineKeyboardButton(
                           "Sᴇᴀsᴏɴ",  callback_data=f"seasons#{key}")
                   ]
                   )
        btn.insert(0, [
            InlineKeyboardButton(
                "⚜️ 𝐑𝐞𝐦𝐨𝐯𝐞 𝐚𝐝𝐬 ⚜️", url=f"https://t.me/{temp.U_NAME}?start=premium"),
            InlineKeyboardButton("Sᴇɴᴅ Aʟʟ", callback_data=f"sendfiles#{key}")
        ])
    try:
        if settings['max_btn']:
            if 0 < offset <= 10:
                off_set = 0
            elif offset == 0:
                off_set = None
            else:
                off_set = offset - 10
            if n_offset == 0:
                btn.append([InlineKeyboardButton("⋞ ʙᴀᴄᴋ", callback_data=f"next_{req}_{key}_{off_set}"), InlineKeyboardButton(
                    f"{math.ceil(int(offset)/10)+1} / {math.ceil(total/10)}", callback_data="pages")])
            elif off_set is None:
                btn.append([InlineKeyboardButton("ᴘᴀɢᴇ", callback_data="pages"), InlineKeyboardButton(
                    f"{math.ceil(int(offset)/10)+1} / {math.ceil(total/10)}", callback_data="pages"), InlineKeyboardButton("ɴᴇxᴛ ⋟", callback_data=f"next_{req}_{key}_{n_offset}")])
            else:
                btn.append(
                    [
                        InlineKeyboardButton(
                            "⋞ ʙᴀᴄᴋ", callback_data=f"next_{req}_{key}_{off_set}"),
                        InlineKeyboardButton(
                            f"{math.ceil(int(offset)/10)+1} / {math.ceil(total/10)}", callback_data="pages"),
                        InlineKeyboardButton(
                            "ɴᴇxᴛ ⋟", callback_data=f"next_{req}_{key}_{n_offset}")
                    ],
                )
        else:
            if 0 < offset <= int(MAX_B_TN):
                off_set = 0
            elif offset == 0:
                off_set = None
            else:
                off_set = offset - int(MAX_B_TN)
            if n_offset == 0:
                btn.append([InlineKeyboardButton("⋞ ʙᴀᴄᴋ", callback_data=f"next_{req}_{key}_{off_set}"), InlineKeyboardButton(
                    f"{math.ceil(int(offset)/int(MAX_B_TN))+1} / {math.ceil(total/int(MAX_B_TN))}", callback_data="pages")])
            elif off_set is None:
                btn.append([InlineKeyboardButton("ᴘᴀɢᴇ", callback_data="pages"), InlineKeyboardButton(
                    f"{math.ceil(int(offset)/int(MAX_B_TN))+1} / {math.ceil(total/int(MAX_B_TN))}", callback_data="pages"), InlineKeyboardButton("ɴᴇxᴛ ⋟", callback_data=f"next_{req}_{key}_{n_offset}")])
            else:
                btn.append(
                    [
                        InlineKeyboardButton(
                            "⋞ ʙᴀᴄᴋ", callback_data=f"next_{req}_{key}_{off_set}"),
                        InlineKeyboardButton(
                            f"{math.ceil(int(offset)/int(MAX_B_TN))+1} / {math.ceil(total/int(MAX_B_TN))}", callback_data="pages"),
                        InlineKeyboardButton(
                            "ɴᴇxᴛ ⋟", callback_data=f"next_{req}_{key}_{n_offset}")
                    ],
                )
    except KeyError:
        await save_group_settings(query.message.chat.id, 'max_btn', True)
        if 0 < offset <= 10:
            off_set = 0
        elif offset == 0:
            off_set = None
        else:
            off_set = offset - 10
        if n_offset == 0:
            btn.append(
                [InlineKeyboardButton("⋞ ʙᴀᴄᴋ", callback_data=f"next_{req}_{key}_{off_set}"), InlineKeyboardButton(
                    f"{math.ceil(int(offset)/10)+1} / {math.ceil(total/10)}", callback_data="pages")]
            )
        elif off_set is None:
            btn.append([InlineKeyboardButton("ᴘᴀɢᴇ", callback_data="pages"), InlineKeyboardButton(
                f"{math.ceil(int(offset)/10)+1} / {math.ceil(total/10)}", callback_data="pages"), InlineKeyboardButton("ɴᴇxᴛ ⋟", callback_data=f"next_{req}_{key}_{n_offset}")])
        else:
            btn.append(
                [
                    InlineKeyboardButton(
                        "⋞ ʙᴀᴄᴋ", callback_data=f"next_{req}_{key}_{off_set}"),
                    InlineKeyboardButton(
                        f"{math.ceil(int(offset)/10)+1} / {math.ceil(total/10)}", callback_data="pages"),
                    InlineKeyboardButton(
                        "ɴᴇxᴛ ⋟", callback_data=f"next_{req}_{key}_{n_offset}")
                ],
            )
    if not settings["button"]:
        cur_time = datetime.now(pytz.timezone('Asia/Kolkata')).time()
        time_difference = timedelta(hours=cur_time.hour, minutes=cur_time.minute, seconds=(cur_time.second+(cur_time.microsecond/1000000))) - \
            timedelta(hours=curr_time.hour, minutes=curr_time.minute, seconds=(
                curr_time.second+(curr_time.microsecond/1000000)))
        remaining_seconds = "{:.2f}".format(time_difference.total_seconds())
        dreamx_title = clean_search_text(search)
        cap = await get_cap(settings, remaining_seconds, files, query, total, dreamx_title, offset+1)
        try:
            await query.message.edit_text(text=cap, reply_markup=InlineKeyboardMarkup(btn), disable_web_page_preview=True)
        except MessageNotModified:
            pass
    else:
        try:
            await query.edit_message_reply_markup(reply_markup=InlineKeyboardMarkup(btn))
        except MessageNotModified:
            pass
    await query.answer()


@Client.on_callback_query(filters.regex(r"^spol"))
async def advantage_spoll_choker(bot, query):
    _, id, user = query.data.split('#')

    # Check if only the intended user can use this
    if int(user) != 0 and query.from_user.id != int(user):
        return await query.answer(script.ALRT_TXT.format(query.from_user.first_name), show_alert=True)

    # Get movie details
    movies = await get_poster(id, id=True)
    movie = movies.get('title') or "Unknown Movie"
    movie = re.sub(r"[:-]", " ", movie)
    movie = re.sub(r"\s+", " ", movie).strip()
    year = movies.get('year')  # Optional: get year if available

    await query.answer(script.TOP_ALRT_MSG)

    # Search results
    files, offset, total_results = await get_search_results(query.message.chat.id, movie, offset=0, filter=True)

    if files:
        k = (movie, files, offset, total_results)
        await auto_filter(bot, query, k)
    else:
        # Log to BIN_CHANNEL
        reqstr1 = query.from_user.id if query.from_user else 0
        reqstr = await bot.get_users(reqstr1)
        if NO_RESULTS_MSG:
            try:
                await bot.send_message(
                    chat_id=BIN_CHANNEL,
                    text=script.NORSLTS.format(reqstr.id, reqstr.mention, movie)
                )
            except Exception as e:
                print(f"Error In Spol - {e}   Make Sure Bot Admin BIN CHANNEL")

    # Prepare auto-fill request button (always show to user)
    auto_fill_text = f"/request {movie}"
    if year:
        auto_fill_text += f" {year}"

    btn = InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                "📝Sᴇɴᴅ Rᴇǫᴜᴇsᴛ Tᴏ Aᴅᴍɪɴ📝",
            switch_inline_query_current_chat=auto_fill_text
            )
        ],
        [
            InlineKeyboardButton(
                "💬 Jᴏɪɴ Sᴜᴘᴘᴏʀᴛ Gʀᴏᴜᴘ 💬",
                url=SUPPORT_CHAT
            )
        ]
    ])

    # Edit message to show guide + buttons
    k = await query.message.edit(script.MVE_NT_FND, reply_markup=btn)

    # Auto-delete after 30 sec
    await asyncio.sleep(30)
    await k.delete()

# Qualities
@Client.on_callback_query(filters.regex(r"^qualities#"))
async def qualities_cb_handler(client: Client, query: CallbackQuery):
    try:
        if int(query.from_user.id) not in [query.message.reply_to_message.from_user.id, 0]:
            return await query.answer(
                f"⚠️ ʜᴇʟʟᴏ {query.from_user.first_name},\n"
                f"ᴛʜɪꜱ ɪꜱ ɴᴏᴛ ʏᴏᴜʀ ᴍᴏᴠɪᴇ ʀᴇǫᴜᴇꜱᴛ,\nʀᴇǫᴜᴇꜱᴛ ʏᴏᴜʀ'ꜱ...",
                show_alert=True,
            )
    except:
        pass

    _, key = query.data.split("#")
    search = FRESH.get(key)
    search = search.replace(' ', '_')

    btn = []
    for i in range(0, len(QUALITIES), 2):
        q1 = QUALITIES[i]
        row = [InlineKeyboardButton(
            text=q1, callback_data=f"fq#{q1.lower()}#{key}")]
        if i + 1 < len(QUALITIES):
            q2 = QUALITIES[i + 1]
            row.append(InlineKeyboardButton(
                text=q2, callback_data=f"fq#{q2.lower()}#{key}"))
        btn.append(row)

    btn.insert(0, [
        InlineKeyboardButton(text="⇊ ꜱᴇʟᴇᴄᴛ ǫᴜᴀʟɪᴛʏ ⇊", callback_data="ident")
    ])
    btn.append([
        InlineKeyboardButton(text="↭ ʙᴀᴄᴋ ᴛᴏ ꜰɪʟᴇs ↭",
                             callback_data=f"fq#homepage#{key}")
    ])

    await query.edit_message_reply_markup(InlineKeyboardMarkup(btn))


@Client.on_callback_query(filters.regex(r"^fq#"))
async def filter_qualities_cb_handler(client: Client, query: CallbackQuery):
    _, qual, key = query.data.split("#")
    curr_time = datetime.now(pytz.timezone('Asia/Kolkata')).time()
    search = FRESH.get(key)
    search = search.replace("_", " ")
    baal = qual in search
    if baal:
        search = search.replace(qual, "")
    else:
        search = search
    req = query.from_user.id
    chat_id = query.message.chat.id
    message = query.message
    try:
        if int(query.from_user.id) not in [query.message.reply_to_message.from_user.id, 0]:
            return await query.answer(f"⚠️ ʜᴇʟʟᴏ {query.from_user.first_name},\nᴛʜɪꜱ ɪꜱ ɴᴏᴛ ʏᴏᴜʀ ᴍᴏᴠɪᴇ ʀᴇǫᴜᴇꜱᴛ,\nʀᴇǫᴜᴇꜱᴛ ʏᴏᴜʀ'ꜱ...", show_alert=True,)
    except:
        pass
    if qual != "homepage":
        search = f"{search} {qual}"
    BUTTONS[key] = search
    files, offset, total_results = await get_search_results(chat_id, search, offset=0, filter=True)
    if not files:
        await query.answer("🚫 ɴᴏ ꜰɪʟᴇꜱ ᴡᴇʀᴇ ꜰᴏᴜɴᴅ 🚫", show_alert=1)
        return
    temp.GETALL[key] = files
    settings = await get_settings(message.chat.id)
    if settings.get('button'):
        btn = [
            [
                InlineKeyboardButton(text=f"🔗 {get_size(file.file_size)} ≽ " + clean_filename(
                    file.file_name), callback_data=f'file#{file.file_id}'),
            ]
            for file in files
        ]
        btn.insert(0,
                   [
                       InlineKeyboardButton(
                           f'Qᴜᴀʟɪᴛʏ', callback_data=f"qualities#{key}"),
                       InlineKeyboardButton(
                           "Lᴀɴɢᴜᴀɢᴇ", callback_data=f"languages#{key}"),
                       InlineKeyboardButton(
                           "Sᴇᴀsᴏɴ",  callback_data=f"seasons#{key}")
                   ]
                   )
        btn.insert(0,
                   [
                       InlineKeyboardButton(
                           "⚜️ 𝐑𝐞𝐦𝐨𝐯𝐞 𝐚𝐝𝐬 ⚜️", url=f"https://t.me/{temp.U_NAME}?start=premium"),
                       InlineKeyboardButton(
                           "Sᴇɴᴅ Aʟʟ", callback_data=f"sendfiles#{key}")
                   ])
    else:
        btn = []
        btn.insert(0,
                   [
                       InlineKeyboardButton(
                           f'Qᴜᴀʟɪᴛʏ', callback_data=f"qualities#{key}"),
                       InlineKeyboardButton(
                           "Lᴀɴɢᴜᴀɢᴇ", callback_data=f"languages#{key}"),
                       InlineKeyboardButton(
                           "Sᴇᴀsᴏɴ",  callback_data=f"seasons#{key}")
                   ]
                   )
        btn.insert(0,
                   [
                       InlineKeyboardButton(
                           "⚜️ 𝐑𝐞𝐦𝐨𝐯𝐞 𝐚𝐝𝐬 ⚜️", url=f"https://t.me/{temp.U_NAME}?start=premium"),
                       InlineKeyboardButton(
                           "Sᴇɴᴅ Aʟʟ", callback_data=f"sendfiles#{key}")

                   ])
    if offset != "":
        try:
            if settings['max_btn']:
                btn.append(

                    [InlineKeyboardButton("ᴘᴀɢᴇ", callback_data="pages"), InlineKeyboardButton(
                        text=f"1/{math.ceil(int(total_results)/10)}", callback_data="pages"), InlineKeyboardButton(text="ɴᴇxᴛ ⋟", callback_data=f"next_{req}_{key}_{offset}")]
                )
            else:
                btn.append(

                    [InlineKeyboardButton("ᴘᴀɢᴇ", callback_data="pages"), InlineKeyboardButton(
                        text=f"1/{math.ceil(int(total_results)/int(MAX_B_TN))}", callback_data="pages"), InlineKeyboardButton(text="ɴᴇxᴛ ⋟", callback_data=f"next_{req}_{key}_{offset}")]
                )
        except KeyError:
            await save_group_settings(query.message.chat.id, 'max_btn', True)
            btn.append(

                [InlineKeyboardButton("ᴘᴀɢᴇ", callback_data="pages"), InlineKeyboardButton(
                    text=f"1/{math.ceil(int(total_results)/10)}", callback_data="pages"), InlineKeyboardButton(text="ɴᴇxᴛ ⋟", callback_data=f"next_{req}_{key}_{offset}")]
            )
    else:
        btn.append(

            [InlineKeyboardButton(
                text="↭ ɴᴏ ᴍᴏʀᴇ ᴘᴀɢᴇꜱ ᴀᴠᴀɪʟᴀʙʟᴇ ↭", callback_data="pages")]
        )
    if not settings["button"]:
        cur_time = datetime.now(pytz.timezone('Asia/Kolkata')).time()
        time_difference = timedelta(hours=cur_time.hour, minutes=cur_time.minute, seconds=(cur_time.second+(cur_time.microsecond/1000000))) - \
            timedelta(hours=curr_time.hour, minutes=curr_time.minute, seconds=(
                curr_time.second+(curr_time.microsecond/1000000)))
        remaining_seconds = "{:.2f}".format(time_difference.total_seconds())
        dreamx_title = clean_search_text(search)
        cap = await get_cap(settings, remaining_seconds, files, query, total_results, dreamx_title, offset=1)
        try:
            await query.message.edit_text(text=cap, reply_markup=InlineKeyboardMarkup(btn), disable_web_page_preview=True)
        except MessageNotModified:
            pass
    else:
        try:
            await query.edit_message_reply_markup(reply_markup=InlineKeyboardMarkup(btn))
        except MessageNotModified:
            pass
    await query.answer()

# languages


@Client.on_callback_query(filters.regex(r"^languages#"))
async def languages_cb_handler(client: Client, query: CallbackQuery):
    try:
        if int(query.from_user.id) not in [query.message.reply_to_message.from_user.id, 0]:
            return await query.answer(
                f"⚠️ ʜᴇʟʟᴏ {query.from_user.first_name},\n"
                f"ᴛʜɪꜱ ɪꜱ ɴᴏᴛ ʏᴏᴜʀ ᴍᴏᴠɪᴇ ʀᴇǫᴜᴇꜱᴛ,\nʀᴇǫᴜᴇꜱᴛ ʏᴏᴜʀ'ꜱ...",
                show_alert=True,
            )
    except:
        pass

    _, key = query.data.split("#")
    search = FRESH.get(key)
    search = search.replace(' ', '_')

    items = list(LANGUAGES.items())
    btn = []

    for i in range(0, len(items), 2):
        name1, code1 = items[i]
        row = [InlineKeyboardButton(
            text=name1, callback_data=f"fl#{code1}#{key}")]
        if i + 1 < len(items):
            name2, code2 = items[i + 1]
            row.append(InlineKeyboardButton(
                text=name2, callback_data=f"fl#{code2}#{key}"))
        btn.append(row)

    btn.insert(0, [InlineKeyboardButton(
        text="⇊ ꜱᴇʟᴇᴄᴛ ʟᴀɴɢᴜᴀɢᴇ ⇊", callback_data="ident")])
    btn.append([InlineKeyboardButton(text="↭ ʙᴀᴄᴋ ᴛᴏ ꜰɪʟᴇs ↭",
               callback_data=f"fl#homepage#{key}")])

    await query.edit_message_reply_markup(InlineKeyboardMarkup(btn))


@Client.on_callback_query(filters.regex(r"^fl#"))
async def filter_languages_cb_handler(client: Client, query: CallbackQuery):
    _, lang, key = query.data.split("#")
    curr_time = datetime.now(pytz.timezone('Asia/Kolkata')).time()
    search = FRESH.get(key)
    search = search.replace("_", " ")
    baal = lang in search
    if baal:
        search = search.replace(lang, "")
    else:
        search = search
    req = query.from_user.id
    chat_id = query.message.chat.id
    message = query.message
    try:
        if int(query.from_user.id) not in [query.message.reply_to_message.from_user.id, 0]:
            return await query.answer(f"⚠️ ʜᴇʟʟᴏ {query.from_user.first_name},\nᴛʜɪꜱ ɪꜱ ɴᴏᴛ ʏᴏᴜʀ ᴍᴏᴠɪᴇ ʀᴇǫᴜᴇꜱᴛ,\nʀᴇǫᴜᴇꜱᴛ ʏᴏᴜʀ'ꜱ...", show_alert=True,)
    except:
        pass
    if lang != "homepage":
        search = f"{search} {lang}"
    BUTTONS[key] = search
    files, offset, total_results = await get_search_results(chat_id, search, offset=0, filter=True)
    if not files:
        await query.answer("🚫 ɴᴏ ꜰɪʟᴇꜱ ᴡᴇʀᴇ ꜰᴏᴜɴᴅ 🚫", show_alert=1)
        return
    temp.GETALL[key] = files
    settings = await get_settings(message.chat.id)
    if settings.get('button'):
        btn = [
            [
                InlineKeyboardButton(text=f"🔗 {get_size(file.file_size)} ≽ " + clean_filename(
                    file.file_name), callback_data=f'file#{file.file_id}'),
            ]
            for file in files
        ]
        btn.insert(0,
                   [
                       InlineKeyboardButton(
                           f'Qᴜᴀʟɪᴛʏ', callback_data=f"qualities#{key}"),
                       InlineKeyboardButton(
                           "Lᴀɴɢᴜᴀɢᴇ", callback_data=f"languages#{key}"),
                       InlineKeyboardButton(
                           "Sᴇᴀsᴏɴ",  callback_data=f"seasons#{key}")
                   ]
                   )
        btn.insert(0,
                   [
                       InlineKeyboardButton(
                           "⚜️ 𝐑𝐞𝐦𝐨𝐯𝐞 𝐚𝐝𝐬 ⚜️", url=f"https://t.me/{temp.U_NAME}?start=premium"),
                       InlineKeyboardButton(
                           "Sᴇɴᴅ Aʟʟ", callback_data=f"sendfiles#{key}")
                   ]
                   )
    else:
        btn = []
        btn.insert(0,
                   [
                       InlineKeyboardButton(
                           f'Qᴜᴀʟɪᴛʏ', callback_data=f"qualities#{key}"),
                       InlineKeyboardButton(
                           "Lᴀɴɢᴜᴀɢᴇ", callback_data=f"languages#{key}"),
                       InlineKeyboardButton(
                           "Sᴇᴀsᴏɴ",  callback_data=f"seasons#{key}")
                   ])
        btn.insert(0,
                   [
                       InlineKeyboardButton(
                           "⚜️ 𝐑𝐞𝐦𝐨𝐯𝐞 𝐚𝐝𝐬 ⚜️", url=f"https://t.me/{temp.U_NAME}?start=premium"),
                       InlineKeyboardButton(
                           "Sᴇɴᴅ Aʟʟ", callback_data=f"sendfiles#{key}")
                   ])
    if offset != "":
        try:
            if settings['max_btn']:
                btn.append(
                    [
                        InlineKeyboardButton("ᴘᴀɢᴇ", callback_data="pages"), InlineKeyboardButton(
                            text=f"1/{math.ceil(int(total_results)/10)}", callback_data="pages"), InlineKeyboardButton(text="ɴᴇxᴛ ⋟", callback_data=f"next_{req}_{key}_{offset}")
                    ])
            else:
                btn.append(
                    [
                        InlineKeyboardButton("ᴘᴀɢᴇ", callback_data="pages"), InlineKeyboardButton(
                            text=f"1/{math.ceil(int(total_results)/int(MAX_B_TN))}", callback_data="pages"), InlineKeyboardButton(text="ɴᴇxᴛ ⋟", callback_data=f"next_{req}_{key}_{offset}")
                    ])
        except KeyError:
            await save_group_settings(query.message.chat.id, 'max_btn', True)
            btn.append(
                [
                    InlineKeyboardButton("ᴘᴀɢᴇ", callback_data="pages"), InlineKeyboardButton(
                        text=f"1/{math.ceil(int(total_results)/10)}", callback_data="pages"), InlineKeyboardButton(text="ɴᴇxᴛ ⋟", callback_data=f"next_{req}_{key}_{offset}")
                ])
    else:
        btn.append([InlineKeyboardButton(
            text="↭ ɴᴏ ᴍᴏʀᴇ ᴘᴀɢᴇꜱ ᴀᴠᴀɪʟᴀʙʟᴇ ↭", callback_data="pages")])
    if not settings["button"]:
        cur_time = datetime.now(pytz.timezone('Asia/Kolkata')).time()
        time_difference = timedelta(hours=cur_time.hour, minutes=cur_time.minute, seconds=(cur_time.second+(cur_time.microsecond/1000000))) - \
            timedelta(hours=curr_time.hour, minutes=curr_time.minute, seconds=(
                curr_time.second+(curr_time.microsecond/1000000)))
        remaining_seconds = "{:.2f}".format(time_difference.total_seconds())
        dreamx_title = clean_search_text(search)
        cap = await get_cap(settings, remaining_seconds, files, query, total_results, dreamx_title, offset=1)
        try:
            await query.message.edit_text(text=cap, reply_markup=InlineKeyboardMarkup(btn), disable_web_page_preview=True)
        except MessageNotModified:
            pass
    else:
        try:
            await query.edit_message_reply_markup(reply_markup=InlineKeyboardMarkup(btn))
        except MessageNotModified:
            pass
    await query.answer()


@Client.on_callback_query(filters.regex(r"^seasons#"))
async def seasons_cb_handler(client: Client, query: CallbackQuery):
    try:
        if int(query.from_user.id) not in [query.message.reply_to_message.from_user.id, 0]:
            return await query.answer(
                f"⚠️ ʜᴇʟʟᴏ {query.from_user.first_name},\nᴛʜɪꜱ ɪꜱ ɴᴏᴛ ʏᴏᴜʀ ᴍᴏᴠɪᴇ ʀᴇǫᴜᴇꜱᴛ,\nʀᴇǫᴜᴇꜱᴛ ʏᴏᴜʀ'ꜱ…",
                show_alert=True,
            )
    except Exception:
        pass
    _, key = query.data.split("#")
    search = FRESH.get(key).replace(" ", "_")
    req = query.from_user.id
    offset = 0
    btn: list[list[InlineKeyboardButton]] = []
    for i in range(0, len(SEASONS) - 1, 2):
        btn.append([
            InlineKeyboardButton(
                f"Sᴇᴀꜱᴏɴ {SEASONS[i][1:]}", callback_data=f"fs#{SEASONS[i].lower()}#{key}"),
            InlineKeyboardButton(
                f"Sᴇᴀꜱᴏɴ {SEASONS[i+1][1:]}", callback_data=f"fs#{SEASONS[i+1].lower()}#{key}")
        ])

    btn.insert(
        0,
        [InlineKeyboardButton("⇊ ꜱᴇʟᴇᴄᴛ ꜱᴇᴀꜱᴏɴ ⇊", callback_data="ident")],
    )
    btn.append([InlineKeyboardButton(text="↭ ʙᴀᴄᴋ ᴛᴏ ꜰɪʟᴇs ↭",
               callback_data=f"next_{req}_{key}_{offset}")])
    await query.edit_message_reply_markup(InlineKeyboardMarkup(btn))
    await query.answer()


@Client.on_callback_query(filters.regex(r"^fs#"))
async def filter_seasons_cb_handler(client: Client, query: CallbackQuery):
    _, season_tag, key = query.data.split("#")
    search = FRESH.get(key).replace("_", " ")
    season_tag = season_tag.lower()
    if season_tag == "homepage":
        search_final = search
        query_input = search_final
    else:
        season_number = int(season_tag[1:])
        query_input = generate_season_variations(search, season_number)
        search_final = query_input[0] if query_input else search

    BUTTONS[key] = search_final
    try:
        if int(query.from_user.id) not in [query.message.reply_to_message.from_user.id, 0]:
            return await query.answer("⚠️ Not your request", show_alert=True)
    except Exception:
        pass

    chat_id = query.message.chat.id
    req = query.from_user.id
    files, n_offset, total_results = await get_search_results(chat_id, query_input, offset=0, filter=True)
    if not files:
        return await query.answer("🚫 ɴᴏ ꜰɪʟᴇꜱ ꜰᴏᴜɴᴅ 🚫", show_alert=True)

    temp.GETALL[key] = files
    settings = await get_settings(chat_id)
    btn: list[list[InlineKeyboardButton]] = []
    if settings.get("button"):
        btn.extend(
            [
                [
                    InlineKeyboardButton(
                        f"🔗 {get_size(f.file_size)} ≽ " +
                        clean_filename(f.file_name),
                        callback_data=f"file#{f.file_id}",
                    )
                ]
                for f in files
            ]
        )
    btn.insert(
        0,
        [
            InlineKeyboardButton("Qᴜᴀʟɪᴛʏ", callback_data=f"qualities#{key}"),
            InlineKeyboardButton("Lᴀɴɢᴜᴀɢᴇ", callback_data=f"languages#{key}"),
            InlineKeyboardButton("Sᴇᴀꜱᴏɴ", callback_data=f"seasons#{key}"),
        ],
    )
    btn.insert(
        0,
        [
            InlineKeyboardButton(
                "⚜️ 𝐑𝐞𝐦𝐨𝐯𝐞 Aᴅꜱ ⚜️", url=f"https://t.me/{temp.U_NAME}?start=premium"),
            InlineKeyboardButton("Sᴇɴᴅ Aʟʟ", callback_data=f"sendfiles#{key}"),
        ],
    )
    if n_offset != "":
        try:
            if settings['max_btn']:
                btn.append(
                    [InlineKeyboardButton("ᴘᴀɢᴇ", callback_data="pages"), InlineKeyboardButton(
                        text=f"1/{math.ceil(int(total_results)/10)}", callback_data="pages"), InlineKeyboardButton(text="ɴᴇxᴛ ⋟", callback_data=f"next_{req}_{key}_{n_offset}")]
                )

            else:
                btn.append(
                    [InlineKeyboardButton("ᴘᴀɢᴇ", callback_data="pages"), InlineKeyboardButton(
                        text=f"1/{math.ceil(int(total_results)/int(MAX_B_TN))}", callback_data="pages"), InlineKeyboardButton(text="ɴᴇxᴛ ⋟", callback_data=f"next_{req}_{key}_{n_offset}")]
                )
        except KeyError:
            await save_group_settings(query.message.chat.id, 'max_btn', True)
            btn.append(
                [InlineKeyboardButton("ᴘᴀɢᴇ", callback_data="pages"), InlineKeyboardButton(
                    text=f"1/{math.ceil(int(total_results)/10)}", callback_data="pages"), InlineKeyboardButton(text="ɴᴇxᴛ ⋟", callback_data=f"next_{req}_{key}_{n_offset}")]
            )
    else:
        n_offset = 0
        btn.append(
            [InlineKeyboardButton(
                "↭  ɴᴏ ᴍᴏʀᴇ ᴘᴀɢᴇꜱ ᴀᴠᴀɪʟᴀʙʟᴇ ↭", callback_data="pages")]
        )
    if not settings.get("button"):
        curr_time = datetime.now(pytz.timezone("Asia/Kolkata")).time()
        time_difference = timedelta(
            hours=curr_time.hour,
            minutes=curr_time.minute,
            seconds=curr_time.second + curr_time.microsecond / 1_000_000,
        )
        remaining_seconds = f"{time_difference.total_seconds():.2f}"
        dreamx_title = clean_search_text(search_final)
        cap = await get_cap(settings, remaining_seconds, files, query, total_results, dreamx_title, offset=1)
        try:
            await query.message.edit_text(
                text=cap,
                reply_markup=InlineKeyboardMarkup(btn),
                disable_web_page_preview=True,
            )
        except MessageNotModified:
            pass
    else:
        try:
            await query.edit_message_reply_markup(InlineKeyboardMarkup(btn))
        except MessageNotModified:
            pass
    await query.answer()



# -------- AUTO DELETE -------- #
async def send_auto_delete(client, chat_id, text, reply_markup, seconds=60):
    try:
        msg = await client.send_message(chat_id, text, reply_markup=reply_markup)
        await asyncio.sleep(seconds)
        await msg.delete()
    except Exception as e:
        print(e)


@Client.on_callback_query()
async def cb_handler(client: Client, query: CallbackQuery):
    DreamxData = query.data
    try:
        link = await client.create_chat_invite_link(int(REQST_CHANNEL))
    except:
        pass
    if query.data == "close_data":
        try:
            user = query.message.reply_to_message.from_user.id
        except:
            user = query.from_user.id
        if int(user) != 0 and query.from_user.id != int(user):
            return await query.answer(script.NT_ALRT_TXT, show_alert=True)
        await query.answer("ᴛʜᴀɴᴋs ꜰᴏʀ ᴄʟᴏsᴇ 🙈")
        await query.message.delete()
        try:
            await query.message.reply_to_message.delete()
        except:
            pass

    elif query.data == "pages":
        await query.answer("ᴛʜɪs ɪs ᴘᴀɢᴇs ʙᴜᴛᴛᴏɴ 😅")

    elif query.data == "delallcancel":
        userid = query.from_user.id
        chat_type = query.message.chat.type
        if chat_type == enums.ChatType.PRIVATE:
            await query.message.reply_to_message.delete()
            await query.message.delete()
        elif chat_type in [enums.ChatType.GROUP, enums.ChatType.SUPERGROUP]:
            grp_id = query.message.chat.id
            st = await client.get_chat_member(grp_id, userid)
            if (st.status == enums.ChatMemberStatus.OWNER) or (str(userid) in ADMINS):
                await query.message.delete()
                try:
                    await query.message.reply_to_message.delete()
                except:
                    pass
            else:
                await query.answer("Tʜᴀᴛ's ɴᴏᴛ ғᴏʀ ʏᴏᴜ!!", show_alert=True)

    if query.data.startswith("file"):
        ident, file_id = query.data.split("#")
        user = query.message.reply_to_message.from_user.id if query.message.reply_to_message else query.from_user.id
        if int(user) != 0 and query.from_user.id != int(user):
            return await query.answer(script.ALRT_TXT.format(query.from_user.first_name), show_alert=True)
        await query.answer(url=f"https://t.me/{temp.U_NAME}?start=file_{query.message.chat.id}_{file_id}")

    elif query.data.startswith("sendfiles"):
        clicked = query.from_user.id
        ident, key = query.data.split("#")
        settings = await get_settings(query.message.chat.id)
        try:
            await query.answer(url=f"https://telegram.me/{temp.U_NAME}?start=allfiles_{query.message.chat.id}_{key}")
            return
        except UserIsBlocked:
            await query.answer('Uɴʙʟᴏᴄᴋ ᴛʜᴇ ʙᴏᴛ ᴍᴀʜɴ !', show_alert=True)
        except PeerIdInvalid:
            await query.answer(url=f"https://telegram.me/{temp.U_NAME}?start=sendfiles3_{key}")
        except Exception as e:
            logger.exception(e)
            await query.answer(url=f"https://telegram.me/{temp.U_NAME}?start=sendfiles4_{key}")

    elif query.data.startswith("del"):
        ident, file_id = query.data.split("#")
        files_ = await get_file_details(file_id)
        if not files_:
            return await query.answer('Nᴏ sᴜᴄʜ ғɪʟᴇ ᴇxɪsᴛ.')
        files = files_[0]
        title = files.file_name
        size = get_size(files.file_size)
        f_caption = files.caption
        settings = await get_settings(query.message.chat.id)
        if CUSTOM_FILE_CAPTION:
            try:
                f_caption = CUSTOM_FILE_CAPTION.format(file_name='' if title is None else title,
                                                       file_size='' if size is None else size,
                                                       file_caption='' if f_caption is None else f_caption)
            except Exception as e:
                logger.exception(e)
            f_caption = f_caption
        if f_caption is None:
            f_caption = f"{files.file_name}"
        await query.answer(url=f"href='https://telegram.me/{temp.U_NAME}?start=file_{query.message.chat.id}_{file.file_id}")

    elif query.data.startswith("autofilter_delete"):
        await Media.collection.drop()
        if MULTIPLE_DB:    
            await Media2.collection.drop()
        await query.answer("Eᴠᴇʀʏᴛʜɪɴɢ's Gᴏɴᴇ")
        await query.message.edit('ꜱᴜᴄᴄᴇꜱꜱꜰᴜʟʟʏ ᴅᴇʟᴇᴛᴇᴅ ᴀʟʟ ɪɴᴅᴇxᴇᴅ ꜰɪʟᴇꜱ ✅')

    elif query.data.startswith("checksub"):
        try:
            ident, kk, file_id = query.data.split("#")
            btn = []
            chat = file_id.split("_")[0]
            settings = await get_settings(chat)
            fsub_channels = list(dict.fromkeys((settings.get('fsub', []) if settings else [])+ AUTH_CHANNELS)) 
            btn += await is_subscribed(client, query.from_user.id, fsub_channels)
            btn += await is_req_subscribed(client, query.from_user.id, AUTH_REQ_CHANNELS)
            if btn:
                btn.append([InlineKeyboardButton("♻️ ᴛʀʏ ᴀɢᴀɪɴ ♻️", callback_data=f"checksub#{kk}#{file_id}")])
                try:
                    await query.edit_message_reply_markup(reply_markup=InlineKeyboardMarkup(btn))
                except MessageNotModified:
                    pass
                await query.answer(
                    f"👋 Hello {query.from_user.first_name},\n\n"
                    "🛑 Yᴏᴜ ʜᴀᴠᴇ ɴᴏᴛ ᴊᴏɪɴᴇᴅ ᴀʟʟ ʀᴇǫᴜɪʀᴇᴅ ᴜᴘᴅᴀᴛᴇ Cʜᴀɴɴᴇʟs.\n"
                    "👉 Pʟᴇᴀsᴇ ᴊᴏɪɴ ᴇᴀᴄʜ ᴏɴᴇ ᴀɴᴅ ᴛʀʏ ᴀɢᴀɪɴ.\n",
                    show_alert=True
                )
                return
            await query.answer(url=f"https://t.me/{temp.U_NAME}?start={kk}_{file_id}")
            await query.message.delete()
        except Exception as e:
            await log_error(client, f"❌ Error in checksub callback:\n\n{repr(e)}")
            logger.error(f"❌ Error in checksub callback:\n\n{repr(e)}")


    elif query.data.startswith("killfilesdq"):
        ident, keyword = query.data.split("#")
        await query.message.edit_text(f"<b>Fetching Files for your query {keyword} on DB... Please wait...</b>")
        files, total = await get_bad_files(keyword)
        await query.message.edit_text("<b>ꜰɪʟᴇ ᴅᴇʟᴇᴛɪᴏɴ ᴘʀᴏᴄᴇꜱꜱ ᴡɪʟʟ ꜱᴛᴀʀᴛ ɪɴ 5 ꜱᴇᴄᴏɴᴅꜱ !</b>")
        await asyncio.sleep(5)
        deleted = 0
        async with lock:
            try:
                for file in files:
                    file_ids = file.file_id
                    file_name = file.file_name
                    result = await Media.collection.delete_one({
                        '_id': file_ids,
                    })
                    if not result.deleted_count and MULTIPLE_DB:
                        result = await Media2.collection.delete_one({
                            '_id': file_ids,
                        })
                    if result.deleted_count:
                        logger.info(
                            f'ꜰɪʟᴇ ꜰᴏᴜɴᴅ ꜰᴏʀ ʏᴏᴜʀ ǫᴜᴇʀʏ {keyword}! ꜱᴜᴄᴄᴇꜱꜱꜰᴜʟʟʏ ᴅᴇʟᴇᴛᴇᴅ {file_name} ꜰʀᴏᴍ ᴅᴀᴛᴀʙᴀꜱᴇ.')
                    deleted += 1
                    if deleted % 20 == 0:
                        await query.message.edit_text(f"<b>ᴘʀᴏᴄᴇꜱꜱ ꜱᴛᴀʀᴛᴇᴅ ꜰᴏʀ ᴅᴇʟᴇᴛɪɴɢ ꜰɪʟᴇꜱ ꜰʀᴏᴍ ᴅʙ. ꜱᴜᴄᴄᴇꜱꜱꜰᴜʟʟʏ ᴅᴇʟᴇᴛᴇᴅ {str(deleted)} ꜰɪʟᴇꜱ ꜰʀᴏᴍ ᴅʙ ꜰᴏʀ ʏᴏᴜʀ ǫᴜᴇʀʏ {keyword} !\n\nᴘʟᴇᴀꜱᴇ ᴡᴀɪᴛ...</b>")
            except Exception as e:
                print(f"Error In killfiledq -{e}")
                await query.message.edit_text(f'Error: {e}')
            else:
                await query.message.edit_text(f"<b>ᴘʀᴏᴄᴇꜱꜱ ᴄᴏᴍᴘʟᴇᴛᴇᴅ ꜰᴏʀ ꜰɪʟᴇ ᴅᴇʟᴇᴛᴀᴛɪᴏɴ !\n\nꜱᴜᴄᴄᴇꜱꜱꜰᴜʟʟʏ ᴅᴇʟᴇᴛᴇᴅ {str(deleted)} ꜰɪʟᴇꜱ ꜰʀᴏᴍ ᴅʙ ꜰᴏʀ ʏᴏᴜʀ ǫᴜᴇʀʏ {keyword}.</b>")

    elif query.data.startswith("opnsetgrp"):
        ident, grp_id = query.data.split("#")
        userid = query.from_user.id if query.from_user else None
        st = await client.get_chat_member(grp_id, userid)
        if (
                st.status != enums.ChatMemberStatus.ADMINISTRATOR
                and st.status != enums.ChatMemberStatus.OWNER
                and str(userid) not in ADMINS
        ):
            await query.answer("ʏᴏᴜ ᴅᴏɴ'ᴛ ʜᴀᴠᴇ ʀɪɢʜᴛꜱ ᴛᴏ ᴅᴏ ᴛʜɪꜱ !", show_alert=True)
            return
        title = query.message.chat.title
        settings = await get_settings(grp_id)
        if settings is not None:
            btn = await group_setting_buttons(int(grp_id))
            reply_markup = InlineKeyboardMarkup(btn)
            await query.message.edit_text(
                text=f"<b>ᴄʜᴀɴɢᴇ ʏᴏᴜʀ ꜱᴇᴛᴛɪɴɢꜱ ꜰᴏʀ {title} ᴀꜱ ʏᴏᴜ ᴡɪꜱʜ ⚙</b>",
                disable_web_page_preview=True,
                parse_mode=enums.ParseMode.HTML
            )
            await query.message.edit_reply_markup(reply_markup)

    elif query.data.startswith("opnsetpm"):
        ident, grp_id = query.data.split("#")
        userid = query.from_user.id if query.from_user else None
        st = await client.get_chat_member(grp_id, userid)
        if (
                st.status != enums.ChatMemberStatus.ADMINISTRATOR
                and st.status != enums.ChatMemberStatus.OWNER
                and str(userid) not in ADMINS
        ):
            await query.answer("Yᴏᴜ ᴅᴏɴ'ᴛ ʜᴀᴠᴇ sᴜғғɪᴄɪᴀɴᴛ ʀɪɢʜᴛs ᴛᴏ ᴅᴏ ᴛʜɪs !", show_alert=True)
            return
        title = query.message.chat.title
        settings = await get_settings(grp_id)
        btn2 = [[
            InlineKeyboardButton(
                "ᴄʜᴇᴄᴋ ᴍʏ ᴅᴍ 🗳️", url=f"telegram.me/{temp.U_NAME}")
        ]]
        reply_markup = InlineKeyboardMarkup(btn2)
        await query.message.edit_text(f"<b>ʏᴏᴜʀ sᴇᴛᴛɪɴɢs ᴍᴇɴᴜ ғᴏʀ {title} ʜᴀs ʙᴇᴇɴ sᴇɴᴛ ᴛᴏ ʏᴏᴜ ʙʏ ᴅᴍ.</b>")
        await query.message.edit_reply_markup(reply_markup)
        if settings is not None:
            btn = await group_setting_buttons(int(grp_id))
            reply_markup = InlineKeyboardMarkup(btn)
            await client.send_message(
                chat_id=userid,
                text=f"<b>ᴄʜᴀɴɢᴇ ʏᴏᴜʀ ꜱᴇᴛᴛɪɴɢꜱ ꜰᴏʀ {title} ᴀꜱ ʏᴏᴜ ᴡɪꜱʜ ⚙</b>",
                reply_markup=reply_markup,
                disable_web_page_preview=True,
                parse_mode=enums.ParseMode.HTML,
                reply_to_message_id=query.message.id
            )

    elif query.data.startswith("show_option"):
        ident, from_user = query.data.split("#")

        btn = [[
            InlineKeyboardButton("⚠️ ᴜɴᴀᴠᴀɪʟᴀʙʟᴇ ⚠️", callback_data=f"unavailable#{from_user}"),
            InlineKeyboardButton("✅ ᴜᴘʟᴏᴀᴅᴇᴅ ✅", callback_data=f"uploaded#{from_user}")
        ], [
            InlineKeyboardButton("♻️ ᴀʟʀᴇᴀʟʏ ᴀᴠᴀɪʟᴀʙʟᴇ ♻️", callback_data=f"already_available#{from_user}")
        ], [
            InlineKeyboardButton("📌 Not Released 📌", callback_data=f"Not_Released#{from_user}"),
            InlineKeyboardButton("♨️ Type Correct Spelling ♨️", callback_data=f"Type_Correct_Spelling#{from_user}")
        ], [
            InlineKeyboardButton("⚜️ Not Available In The Hindi ⚜️", callback_data=f"Not_Available_In_The_Hindi#{from_user}")
        ]]

        if query.from_user.id in ADMINS:
            await query.message.edit_reply_markup(InlineKeyboardMarkup(btn))
            await query.answer("Here are the options!")
        else:
            await query.answer("No permission ❌", show_alert=True)

    # ---------------- UNAVAILABLE ---------------- #

    elif query.data.startswith("unavailable"):
        _, from_user = query.data.split("#")

        btn = [[InlineKeyboardButton("⚠️ ᴜɴᴀᴠᴀɪʟᴀʙʟᴇ ⚠️", callback_data=f"unalert#{from_user}")]]
        btn2 = [[
            InlineKeyboardButton("Movie Channel 📢", url=MOVIE_UPDATE_CHANNEL_LINK),
            InlineKeyboardButton("View Status", url=f"{query.message.link}")
        ]]

        if query.from_user.id in ADMINS:
            user = await client.get_users(from_user)
            content = extract_request_content(query.message.text)

            await query.message.edit_text(f"<b><strike>{query.message.text}</strike></b>")
            await query.message.edit_reply_markup(InlineKeyboardMarkup(btn))

            msg = f"<b>Hey {user.mention},</b>\n\n<u>{content}</u> marked unavailable 💔"

            asyncio.create_task(send_auto_delete(client, int(from_user), msg, InlineKeyboardMarkup(btn2), 24000))
            await query.answer("Set to Unavailable!")

    # ---------------- NOT RELEASED ---------------- #

    elif query.data.startswith("Not_Released"):
        _, from_user = query.data.split("#")

        btn = [[InlineKeyboardButton("📌 Not Released 📌", callback_data=f"nralert#{from_user}")]]
        btn2 = [[
            InlineKeyboardButton("Movie Channel 📢", url=MOVIE_UPDATE_CHANNEL_LINK),
            InlineKeyboardButton("View Status", url=f"{query.message.link}")
        ]]

        if query.from_user.id in ADMINS:
            user = await client.get_users(from_user)
            content = extract_request_content(query.message.text)

            await query.message.edit_text(f"<b><strike>{query.message.text}</strike></b>")
            await query.message.edit_reply_markup(InlineKeyboardMarkup(btn))

            msg = f"<b>Hey {user.mention}\n\n<code>{content}</code> not released yet 🕊️</b>"

            asyncio.create_task(send_auto_delete(client, int(from_user), msg, InlineKeyboardMarkup(btn2), 24000))
            await query.answer("Set to Not Released!")

    # ---------------- WRONG SPELLING ---------------- #

    elif query.data.startswith("Type_Correct_Spelling"):
        _, from_user = query.data.split("#")

        btn = [[InlineKeyboardButton("♨️ Correct Spelling ♨️", callback_data=f"wsalert#{from_user}")]]
        btn2 = [[
            InlineKeyboardButton("Movie Channel 📢", url=MOVIE_UPDATE_CHANNEL_LINK),
            InlineKeyboardButton("View Status", url=f"{query.message.link}")
        ]]

        if query.from_user.id in ADMINS:
            user = await client.get_users(from_user)
            content = extract_request_content(query.message.text)

            await query.message.edit_text(f"<b><strike>{query.message.text}</strike></b>")
            await query.message.edit_reply_markup(InlineKeyboardMarkup(btn))

            msg = f"<b>Hey {user.mention}\n\nWrong spelling: <code>{content}</code> ❗</b>"

            asyncio.create_task(send_auto_delete(client, int(from_user), msg, InlineKeyboardMarkup(btn2), 24000))
            await query.answer("Wrong Spelling Set!")

    # ---------------- HINDI NOT AVAILABLE ---------------- #

    elif query.data.startswith("Not_Available_In_The_Hindi"):
        _, from_user = query.data.split("#")

        btn = [[InlineKeyboardButton("⚜️ Hindi Not Available ⚜️", callback_data=f"hnalert#{from_user}")]]
        btn2 = [[
            InlineKeyboardButton("Movie Channel 📢", url=MOVIE_UPDATE_CHANNEL_LINK),
            InlineKeyboardButton("View Status", url=f"{query.message.link}")
        ]]

        if query.from_user.id in ADMINS:
            user = await client.get_users(from_user)
            content = extract_request_content(query.message.text)

            await query.message.edit_text(f"<b><strike>{query.message.text}</strike></b>")
            await query.message.edit_reply_markup(InlineKeyboardMarkup(btn))

            msg = f"<b>Hey {user.mention}\n\n<code>{content}</code> not available in Hindi ❌</b>"

            asyncio.create_task(send_auto_delete(client, int(from_user), msg, InlineKeyboardMarkup(btn2), 24000))
            await query.answer("Hindi Not Available!")

    # ---------------- UPLOADED ---------------- #

    elif query.data.startswith("uploaded"):
        _, from_user = query.data.split("#")

        btn = [[InlineKeyboardButton("✅ Uploaded ✅", callback_data=f"upalert#{from_user}")]]
        btn2 = [[
            InlineKeyboardButton("Movie Channel 📢", url=MOVIE_UPDATE_CHANNEL_LINK),
            InlineKeyboardButton("View Status", url=f"{query.message.link}")
        ], [
            InlineKeyboardButton("Search 🔍", url=GRP_LNK)
        ]]

        if query.from_user.id in ADMINS:
            user = await client.get_users(from_user)
            content = extract_request_content(query.message.text)

            await query.message.edit_text(f"<b><strike>{query.message.text}</strike></b>")
            await query.message.edit_reply_markup(InlineKeyboardMarkup(btn))

            msg = f"<b>Hey {user.mention},\n\n<u>{content}</u> uploaded ✅</b>"

            asyncio.create_task(send_auto_delete(client, int(from_user), msg, InlineKeyboardMarkup(btn2), 24000))
            await query.answer("Uploaded!")

    # ---------------- ALREADY AVAILABLE ---------------- #

    elif query.data.startswith("already_available"):
        _, from_user = query.data.split("#")

        btn = [[InlineKeyboardButton("♻️ Already Available ♻️", callback_data=f"alalert#{from_user}")]]
        btn2 = [[
            InlineKeyboardButton("Movie Channel 📢", url=MOVIE_UPDATE_CHANNEL_LINK),
            InlineKeyboardButton("View Status", url=f"{query.message.link}")
        ], [
            InlineKeyboardButton("Search 🔍", url=GRP_LNK)
        ]]

        if query.from_user.id in ADMINS:
            user = await client.get_users(from_user)
            content = extract_request_content(query.message.text)

            await query.message.edit_text(f"<b><strike>{query.message.text}</strike></b>")
            await query.message.edit_reply_markup(InlineKeyboardMarkup(btn))

            msg = f"<b>Hey {user.mention},\n\n<u>{content}</u> already available ✅</b>"

            asyncio.create_task(send_auto_delete(client, int(from_user), msg, InlineKeyboardMarkup(btn2), 24000))
            await query.answer("Already Available!")

    # ================= ALERT SECTION ================= #

    elif query.data.startswith("alalert"):
        _, from_user = query.data.split("#")
        if int(query.from_user.id) == int(from_user):
            await query.answer(f"Hey {query.from_user.first_name}, Already Available ✅", show_alert=True)
        else:
            await query.answer("No permission ❌", show_alert=True)

    elif query.data.startswith("upalert"):
        _, from_user = query.data.split("#")
        if int(query.from_user.id) == int(from_user):
            await query.answer(f"Hey {query.from_user.first_name}, Uploaded 🔼", show_alert=True)
        else:
            await query.answer("No permission ❌", show_alert=True)

    elif query.data.startswith("unalert"):
        _, from_user = query.data.split("#")
        if int(query.from_user.id) == int(from_user):
            await query.answer(f"Hey {query.from_user.first_name}, Unavailable ⚠️", show_alert=True)
        else:
            await query.answer("No permission ❌", show_alert=True)

    elif query.data.startswith("hnalert"):
        _, from_user = query.data.split("#")
        if int(query.from_user.id) == int(from_user):
            await query.answer(f"Hey {query.from_user.first_name}, Not Available in Hindi ❌", show_alert=True)
        else:
            await query.answer("Not allowed ❌", show_alert=True)

    elif query.data.startswith("nralert"):
        _, from_user = query.data.split("#")
        if int(query.from_user.id) == int(from_user):
            await query.answer(f"Hey {query.from_user.first_name}, Not Released Yet 🆕", show_alert=True)
        else:
            await query.answer("Not allowed ❌", show_alert=True)

    elif query.data.startswith("wsalert"):
        _, from_user = query.data.split("#")
        if int(query.from_user.id) == int(from_user):
            await query.answer(f"Hey {query.from_user.first_name}, Wrong Spelling ❗", show_alert=True)
        else:
            await query.answer("No permission ❌", show_alert=True)

    elif DreamxData.startswith("generate_stream_link"):
        _, file_id = DreamxData.split(":")
        try:
            user_id = query.from_user.id
            username = query.from_user.mention
            log_msg = await client.send_cached_media(chat_id=BIN_CHANNEL, file_id=file_id,)
            fileName = {quote_plus(get_name(log_msg))}
            dreamx_stream = f"{URL}watch/{str(log_msg.id)}/{quote_plus(get_name(log_msg))}?hash={get_hash(log_msg)}"
            dreamx_download = f"{URL}{str(log_msg.id)}/{quote_plus(get_name(log_msg))}?hash={get_hash(log_msg)}"
            xo = await query.message.reply_text(f'💘')
            await asyncio.sleep(1)
            await xo.delete()
            await log_msg.reply_text(
                text=f"•• ʟɪɴᴋ ɢᴇɴᴇʀᴀᴛᴇᴅ ꜰᴏʀ ɪᴅ #{user_id} \n•• ᴜꜱᴇʀɴᴀᴍᴇ : {username} \n\n•• ᖴᎥᒪᗴ Nᗩᗰᗴ : {fileName}",
                quote=True,
                disable_web_page_preview=True,
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🚀 Fast Download 🚀", url=dreamx_download),  # we download Link
                                                    InlineKeyboardButton('🖥️ Watch online 🖥️', url=dreamx_stream)]])  # web stream Link
            )
            dreamcinezone = await query.edit_message_reply_markup(
                reply_markup=InlineKeyboardMarkup([
                    [
                        InlineKeyboardButton("🚀 Download ", url=dreamx_download),
                        InlineKeyboardButton('🖥️ Watch ', url=dreamx_stream)
                    ],
                    [
                        InlineKeyboardButton('📌 ᴊᴏɪɴ ᴜᴘᴅᴀᴛᴇꜱ ᴄʜᴀɴɴᴇʟ 📌', url=UPDATE_CHNL_LNK)
                    ]
                ])
            )
            await asyncio.sleep(DELETE_TIME)
            await dreamcinezone.delete()
            return
        except Exception as e:
            print(e)
            await query.answer(f"⚠️ SOMETHING WENT WRONG STREAM LINK  \n\n{e}", show_alert=True)
            return


    elif query.data == "prestream":
        await query.answer(text=script.PRE_STREAM_ALERT, show_alert=True)
        dreamcinezone = await client.send_photo(
            chat_id=query.message.chat.id,
            photo="https://i.ibb.co/whf8xF7j/photo-2025-07-26-10-42-46-7531339305176793100.jpg", 
            caption=script.PRE_STREAM,
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🚀 Buy Premium 🚀", callback_data="premium_info")]
            ])
        )
        await asyncio.sleep(DELETE_TIME)
        await dreamcinezone.delete()


    elif query.data == "pagesn1":
        await query.answer(text=script.PAGE_TXT, show_alert=True)

    elif query.data == "sinfo":
        await query.answer(text=script.SINFO, show_alert=True)

    elif query.data == "start":
        buttons = [[
                    InlineKeyboardButton('🔰 ᴀᴅᴅ ᴍᴇ ᴛᴏ ʏᴏᴜʀ ɢʀᴏᴜᴘ 🔰', url=f'http://telegram.me/{temp.U_NAME}?startgroup=true')
                ],[
                    InlineKeyboardButton(' ʜᴇʟᴘ 📢', callback_data='help'),
                    InlineKeyboardButton(' ᴀʙᴏᴜᴛ 📖', callback_data='about')
                ],[
                    InlineKeyboardButton('ᴛᴏᴘ sᴇᴀʀᴄʜɪɴɢ ⭐', callback_data="topsearch"),
                     InlineKeyboardButton('ᴜᴘɢʀᴀᴅᴇ 🎟', callback_data="premium_info"),
                ]]
        reply_markup = InlineKeyboardMarkup(buttons)
        current_time = datetime.now(pytz.timezone(TIMEZONE))
        curr_time = current_time.hour        
        if curr_time < 12:
            gtxt = "ɢᴏᴏᴅ ᴍᴏʀɴɪɴɢ 🌞" 
        elif curr_time < 17:
            gtxt = "ɢᴏᴏᴅ ᴀғᴛᴇʀɴᴏᴏɴ 🌓" 
        elif curr_time < 21:
            gtxt = "ɢᴏᴏᴅ ᴇᴠᴇɴɪɴɢ 🌘"
        else:
            gtxt = "ɢᴏᴏᴅ ɴɪɢʜᴛ 🌑"
        try:
            await client.edit_message_media(
                query.message.chat.id, 
                query.message.id, 
                InputMediaPhoto(random.choice(PICS))
            )
        except Exception as e:    
            pass
        await query.message.edit_text(
            text=script.START_TXT.format(query.from_user.mention, gtxt, temp.U_NAME, temp.B_NAME),
            reply_markup=reply_markup,
            parse_mode=enums.ParseMode.HTML
        )
        await query.answer(MSG_ALRT)

    elif query.data == "donation":
        buttons = [[
                InlineKeyboardButton('📸 Sᴇɴᴅ Dᴏɴᴀᴛᴇ Sᴄʀᴇᴇɴsʜᴏᴛ Hᴇʀᴇ', url=OWNER_LNK)
            ],[
                InlineKeyboardButton('⇍ ʙᴀᴄᴋ ⇏', callback_data='about')
            ]]
        reply_markup = InlineKeyboardMarkup(buttons)
        await query.message.edit_text(text="● ◌ ◌")
        await query.message.edit_text(text="● ● ◌")
        await query.message.edit_text(text="● ● ●")
        reply_markup = InlineKeyboardMarkup(buttons)
        await client.edit_message_media(
            query.message.chat.id, 
            query.message.id, 
            InputMediaPhoto('https://graph.org/file/99eebf5dbe8a134f548e0.jpg')
        )
        await query.message.edit_text(
            text=script.DREAMXBOTZ_DONATION.format(query.from_user.mention, QR_CODE, OWNER_UPI_ID),
            reply_markup=reply_markup,
            parse_mode=enums.ParseMode.HTML
        )

    elif query.data == "help":
        buttons = [[
            InlineKeyboardButton('⇋ ʙᴀᴄᴋ ᴛᴏ ʜᴏᴍᴇ ⇋', callback_data='start')
        ]]
        reply_markup = InlineKeyboardMarkup(buttons)
        await query.message.edit_text(
            text=script.HELP_TXT, 
            reply_markup=reply_markup,
            parse_mode=enums.ParseMode.HTML
        )

    elif query.data == "about":
        buttons = [[
            InlineKeyboardButton('‼️ ᴅɪꜱᴄʟᴀɪᴍᴇʀ ‼️', callback_data='disclaimer'),
            InlineKeyboardButton ('🪔 sᴏᴜʀᴄᴇ', callback_data='source'),
        ],[
            InlineKeyboardButton('ᴅᴏɴᴀᴛɪᴏɴ 💰', callback_data='donation'), 
        ],[
            InlineKeyboardButton('⇋ ʙᴀᴄᴋ ᴛᴏ ʜᴏᴍᴇ ⇋', callback_data='start')
        ]]
        reply_markup = InlineKeyboardMarkup(buttons)
        await query.message.edit_text(
            text=script.ABOUT_TXT.format(temp.U_NAME, temp.B_NAME, OWNER_LNK),
            reply_markup=reply_markup,
            disable_web_page_preview=True,
            parse_mode=enums.ParseMode.HTML
        )

    elif query.data == "give_trial":
        try:
            user_id = query.from_user.id
            has_free_trial = await db.check_trial_status(user_id)
            if has_free_trial:
                await query.answer(
                    "🚸 ʏᴏᴜ'ᴠᴇ ᴀʟʀᴇᴀᴅʏ ᴄʟᴀɪᴍᴇᴅ ʏᴏᴜʀ ꜰʀᴇᴇ ᴛʀɪᴀʟ ᴏɴᴄᴇ !\n\n📌 ᴄʜᴇᴄᴋᴏᴜᴛ ᴏᴜʀ ᴘʟᴀɴꜱ ʙʏ : /plan",
                    show_alert=True
                )
                return
            else:            
                await db.give_free_trial(user_id)
                await query.answer("✅ Trial activated!", show_alert=True)

                msg = await client.send_photo(
                    chat_id=query.message.chat.id,
                    photo="https://i.ibb.co/0jC8MSDZ/photo-2025-07-26-10-42-36-7531339283701956616.jpg", 
                    caption=(
                        "<b>🥳 ᴄᴏɴɢʀᴀᴛᴜʟᴀᴛɪᴏɴꜱ\n\n"
                        "🎉 ʏᴏᴜ ᴄᴀɴ ᴜsᴇ ꜰʀᴇᴇ ᴛʀᴀɪʟ ꜰᴏʀ <u>5 ᴍɪɴᴜᴛᴇs</u> ꜰʀᴏᴍ ɴᴏᴡ !\n\n"
                        "ɴᴇᴇᴅ ᴘʀᴇᴍɪᴜᴍ 👉🏻 /plan</b>"
                    ),
                    parse_mode=enums.ParseMode.HTML,
                    reply_markup=InlineKeyboardMarkup([[
                        InlineKeyboardButton("🚀 Buy Premium 🚀", callback_data="premium_info")
                    ]])
                )
                await asyncio.sleep(DELETE_TIME)
                return await msg.delete()
        except Exception as e:
            logging.exception("Error in give_trial callback")



    elif query.data == "source":
        buttons = [[
            InlineKeyboardButton('𝐃𝐞𝐯𝐞𝐥𝐨𝐩𝐞𝐫_𝐁𝐨𝐲™(𝓐𝓷𝓴𝓲𝓽_𝓜𝓮𝓮𝓷𝓪😝)📜', url='https://t.me/its_ankit_meena907'),
            InlineKeyboardButton('⇋ ʙᴀᴄᴋ ⇋', callback_data='about')
        ]]
        reply_markup = InlineKeyboardMarkup(buttons)
        await query.message.edit_text(
            text=script.SOURCE_TXT,
            reply_markup=reply_markup,
            parse_mode=enums.ParseMode.HTML
        )

    elif query.data == "ref_point":
        await query.answer(f'You Have: {referdb.get_refer_points(query.from_user.id)} Refferal points.', show_alert=True)

    elif query.data == "disclaimer":
            btn = [[
                    InlineKeyboardButton("⇋ ʙᴀᴄᴋ ⇋", callback_data="about")
                  ]]
            reply_markup = InlineKeyboardMarkup(btn)
            await query.message.edit_text(
                text=(script.DISCLAIMER_TXT),
                reply_markup=reply_markup,
                parse_mode=enums.ParseMode.HTML 
            )

    elif query.data == "premium_info":
        try:
            btn = [[
                InlineKeyboardButton('• ʙᴜʏ ᴘʀᴇᴍɪᴜᴍ •', callback_data='buy_info'),
            ],[
                InlineKeyboardButton('• ʀᴇꜰᴇʀ ꜰʀɪᴇɴᴅꜱ', callback_data='reffff'),
                InlineKeyboardButton('ꜰʀᴇᴇ ᴛʀɪᴀʟ •', callback_data='give_trial')
            ],[            
                InlineKeyboardButton('⇋ ʙᴀᴄᴋ ᴛᴏ ʜᴏᴍᴇ ⇋', callback_data='start')
            ]]
            reply_markup = InlineKeyboardMarkup(btn)                        
            await client.edit_message_media(
                chat_id=query.message.chat.id,
                message_id=query.message.id,
                media=InputMediaPhoto(media=SUBSCRIPTION, caption=script.BPREMIUM_TXT, parse_mode=enums.ParseMode.HTML),
                reply_markup=reply_markup
            )
        except MessageNotModified:
            pass
        except Exception as e:
            logging.exception("Exception in 'premium_info' callback")


    elif query.data == "buy_info":
        try:
            btn = [[ 
                InlineKeyboardButton('ꜱᴛᴀʀ', callback_data='star_info'),
                InlineKeyboardButton('ᴜᴘɪ & Qʀ ᴄᴏᴅᴇ', callback_data='upi_info')
            ],[
                InlineKeyboardButton('⇋ ʙᴀᴄᴋ ᴛᴏ ᴘʀᴇᴍɪᴜᴍ ⇋', callback_data='premium_info')
            ]]
            reply_markup = InlineKeyboardMarkup(btn)
            await client.edit_message_media(
                chat_id=query.message.chat.id,
                message_id=query.message.id,
                media=InputMediaPhoto(media=SUBSCRIPTION, caption=script.PREMIUM_TEXT, parse_mode=enums.ParseMode.HTML),
                reply_markup=reply_markup
            )
        except MessageNotModified:
            pass
        except Exception as e:
            logging.exception("Exception in 'buy_info' callback")

    elif query.data == "upi_info":
        try:
            btn = [[ 
                InlineKeyboardButton('📸• ꜱᴇɴᴅ  ᴘᴀʏᴍᴇɴᴛ ꜱᴄʀᴇᴇɴꜱʜᴏᴛ •📸', url=OWNER_LNK),
            ],[
                InlineKeyboardButton('⇋ ʙᴀᴄᴋ ⇋', callback_data='buy_info')
            ]]
            reply_markup = InlineKeyboardMarkup(btn)
            await client.edit_message_media(
                chat_id=query.message.chat.id,
                message_id=query.message.id,
                media=InputMediaPhoto(media=QR_CODE, caption=script.PREMIUM_UPI_TEXT.format(OWNER_UPI_ID,QR_CODE), parse_mode=enums.ParseMode.HTML),
                reply_markup=reply_markup
            )
        except MessageNotModified:
            pass
        except Exception as e:
            logging.exception("Exception in 'upi_info' callback")

    elif query.data == "star_info":
        try:
            btn = [
                InlineKeyboardButton(f"{stars}⭐", callback_data=f"buy_{stars}")
                for stars, days in STAR_PREMIUM_PLANS.items()
            ]
            buttons = [btn[i:i + 2] for i in range(0, len(btn), 2)]
            buttons.append([InlineKeyboardButton("⋞ ʙᴀᴄᴋ", callback_data="buy_info")])
            reply_markup = InlineKeyboardMarkup(buttons)
            await client.edit_message_media(
                chat_id=query.message.chat.id,
                message_id=query.message.id,
                media=InputMediaPhoto(media=SUBSCRIPTION, caption=script.PREMIUM_STAR_TEXT, parse_mode=enums.ParseMode.HTML),
                reply_markup=reply_markup
            )
        except Exception as e:
            logging.exception("Exception in 'star' callback")


    elif query.data.startswith("grp_pm"):
        _, grp_id = query.data.split("#")
        user_id = query.from_user.id if query.from_user else None
        if not await is_check_admin(client, int(grp_id), user_id):
            return await query.answer(script.NT_ADMIN_ALRT_TXT, show_alert=True)

        btn = await group_setting_buttons(int(grp_id))
        dreamx = await client.get_chat(int(grp_id))
        await query.message.edit(text=f"ᴄʜᴀɴɢᴇ ʏᴏᴜʀ ɢʀᴏᴜᴘ ꜱᴇᴛᴛɪɴɢꜱ ✅\nɢʀᴏᴜᴘ ɴᴀᴍᴇ - '{dreamx.title}'</b>⚙", reply_markup=InlineKeyboardMarkup(btn))

    elif query.data.startswith("removegrp"):
        user_id = query.from_user.id
        data = query.data
        grp_id = int(data.split("#")[1])
        if not await is_check_admin(client, grp_id, query.from_user.id):
            return await query.answer(script.NT_ADMIN_ALRT_TXT, show_alert=True)
        await db.remove_group_connection(grp_id, user_id)
        await query.answer("Group removed from your connections.", show_alert=True)
        connected_groups = await db.get_connected_grps(user_id)
        if not connected_groups:
            await query.edit_message_text("Nᴏ Cᴏɴɴᴇᴄᴛᴇᴅ Gʀᴏᴜᴘs Fᴏᴜɴᴅ .")
            return
        group_list = []
        for group in connected_groups:
            try:
                Chat = await client.get_chat(group)
                group_list.append([
                    InlineKeyboardButton(
                        text=Chat.title, callback_data=f"grp_pm#{Chat.id}")
                ])
            except Exception as e:
                print(f"Error In PM Settings Button - {e}")
                pass
        await query.edit_message_text(
            "⚠️ ꜱᴇʟᴇᴄᴛ ᴛʜᴇ ɢʀᴏᴜᴘ ᴡʜᴏꜱᴇ ꜱᴇᴛᴛɪɴɢꜱ ʏᴏᴜ ᴡᴀɴᴛ ᴛᴏ ᴄʜᴀɴɢᴇ.\n\n"
            "ɪꜰ ʏᴏᴜʀ ɢʀᴏᴜᴘ ɪꜱ ɴᴏᴛ ꜱʜᴏᴡɪɴɢ ʜᴇʀᴇ,\n"
            "ᴜꜱᴇ /reload ɪɴ ᴛʜᴀᴛ ɢʀᴏᴜᴘ ᴀɴᴅ ɪᴛ ᴡɪʟʟ ᴀᴘᴘᴇᴀʀ ʜᴇʀᴇ.",
            reply_markup=InlineKeyboardMarkup(group_list)
        )

    elif query.data.startswith("setgs"):
        ident, set_type, status, grp_id = query.data.split("#")
        userid = query.from_user.id if query.from_user else None
        if not await is_check_admin(client, int(grp_id), userid):
            await query.answer(script.NT_ADMIN_ALRT_TXT, show_alert=True)
            return
        if status == "True":
            await save_group_settings(int(grp_id), set_type, False)
            await query.answer("ᴏꜰꜰ ✗")
        else:
            await save_group_settings(int(grp_id), set_type, True)
            await query.answer("ᴏɴ ✓")
        settings = await get_settings(int(grp_id))
        if settings is not None:
            btn = await group_setting_buttons(int(grp_id))
            reply_markup = InlineKeyboardMarkup(btn)
            await query.message.edit_reply_markup(reply_markup)
    await query.answer(MSG_ALRT)

#__________________________________

def normalize_season(search):
    import re

    # season → s
    search = re.sub(r'season[\s\-]*(\d+)', r's \1', search, flags=re.IGNORECASE)

    # s1 / s 1 / S-1 → s 1
    search = re.sub(r'\bs[\s\-]*(\d+)', r's \1', search, flags=re.IGNORECASE)

    # convert to s01, s02
    def pad(match):
        num = int(match.group(1))
        return f"s{num:02d}"

    return re.sub(r'\bs\s*(\d+)', pad, search, flags=re.IGNORECASE)


def normalize_episode(search):
    import re

    # episode → e
    search = re.sub(r'episode[\s\-]*(\d+)', r'e \1', search, flags=re.IGNORECASE)

    # ep1 / ep 1 / Ep-1 → e 1
    search = re.sub(r'\bep[\s\-]*(\d+)', r'e \1', search, flags=re.IGNORECASE)

    # e1 / e 1 → e 1 (normalize)
    search = re.sub(r'\be[\s\-]*(\d+)', r'e \1', search, flags=re.IGNORECASE)

    # convert to e01, e02
    def pad(match):
        num = int(match.group(1))
        return f"e{num:02d}"

    return re.sub(r'\be\s*(\d+)', pad, search, flags=re.IGNORECASE)



def normalize_for_search(text):
    text = text.lower()

    # 1x02 → s01 e02
    text = re.sub(r'(\d+)[xX](\d+)',
                  lambda m: f"s{int(m.group(1)):02d} e{int(m.group(2)):02d}",
                  text)

    # season → s01
    text = re.sub(r'season[\s\-]*(\d+)',
                  lambda m: f"s{int(m.group(1)):02d}", text)

    # s1 / s 1
    text = re.sub(r'\bs[\s\-]*(\d+)',
                  lambda m: f"s{int(m.group(1)):02d}", text)

    # episode → e01
    text = re.sub(r'episode[\s\-]*(\d+)',
                  lambda m: f"e{int(m.group(1)):02d}", text)

    # ep → e01
    text = re.sub(r'\bep[\s\-]*(\d+)',
                  lambda m: f"e{int(m.group(1)):02d}", text)

    # e1 → e01
    text = re.sub(r'\be[\s\-]*(\d+)',
                  lambda m: f"e{int(m.group(1)):02d}", text)

    # S1E2 → s01 e02
    text = re.sub(r's(\d+)e(\d+)',
                  lambda m: f"s{int(m.group(1)):02d} e{int(m.group(2)):02d}",
                  text)

    text = re.sub(r"\s+", " ", text).strip()
    return text
   


async def auto_filter(client, msg, spoll=False):
    # -------------------------------------------------------------------------
    # PART 1: MESSAGE VALIDATION (Message ko check karna ki chalana hai ya nahi)
    # -------------------------------------------------------------------------
    if not spoll:
        message = msg
        
        # 🛑 STOP SEARCH IF IT'S A REQUEST (Fix for @botusername /request)
        if message.text:
            msg_text_lower = message.text.lower()
            if "/request" in msg_text_lower or "#request" in msg_text_lower:
                return  # रिक्वेस्ट मैसेज है, यहीं से सर्च बंद और बॉट शांत रहेगा
            
            if message.text.startswith("/"):
                return  # Agar koi aur command hai toh return ho jaye
                
            # Check for specific command prefixes (, . !) or emojis at start
            if re.findall(r"((^\/|^,|^!|^\.|^[\U0001F600-\U000E007F]).*)", message.text):
                return
        else:
            return  # Agar message me text nahi hai (jaise sirf photo/video) toh return

        # -------------------------------------------------------------------------
        # PART 2: TEXT CLEANING & FILTERING (Movie ka naam saaf karna)
        # -------------------------------------------------------------------------
        if len(message.text) < 100:
            search = message.text.lower()
            
            # ✅ Unicode normalize (Very Important: Special fonts ko normal text me badalna)
            search = unicodedata.normalize('NFKD', search)
            search = search.encode('ascii', 'ignore').decode('ascii')
            
            # Remove emojis and pictographs (Saare emojis ko saaf karna)
            search = re.sub(
                r"[\U0001F600-\U0001F64F\U0001F300-\U0001F5FF\U0001F680-\U0001F6FF"
                r"\U0001F1E0-\U0001F1FF\U00002700-\U000027BF\U000024C2-\U0001F251]+",
                "",
                search
            )
            
            # Faltu words ki list jo movie ke naam ke sath log bhejte hain
            find = search.split(" ")
            removes = ["in", "upload", "series", "full", "horror", "thriller", "mystery", 
                       "print", "file", "pls", "please", "send", "give", "movie", "movies", 
                       "new", "latest", "download in", "bruh", "link", "dubbed", "download", 
                       "subtitle", "subtitles", ":", "any", "()", "iruka", 
                       "pannunga", "anuppunga", "film", "undo", "kitti", "kitty", "tharu", "&"]
            
            # List comprehension se faltu words ko remove karna
            search = " ".join([x for x in find if x not in removes])
            
            # Regex cleanup (Bache-kuche text variations aur please/bro ko saaf karna)
            search = re.sub(r"\b(pl(i|e)*?(s|z+|ease|se|ese|(e+)s(e)?)|((send|snd|giv(e)?|gib)(\sme)?)|movie(s)?|new|latest|,|bruh|broh|helo|that|find|dubbed|link|'|iruka|pannunga|pannungga|anuppunga|anupunga|anuppungga|anupungga|film|undo|kitti|kitty|tharu|kittumo|kittum|movie|any(one)|download\ssubtitle(s)?)", "", search, flags=re.IGNORECASE)
            search = re.sub(r"\s+", " ", search).strip()
            search = normalize_for_search(search)
            
            # Formatting and punctuation stripping (Yahan ab ',' comma bhi replace ho jayega)
            search = search.replace("-", " ").replace(":", "").replace(".", " ").replace("'", " ").replace("&", " ").replace(",", " ")
            
            # 🔥 REMOVE EXTRA SYMBOLS
            search = re.sub(r"[!@#$%^*()_+=\[\]{};\"<>?/\\|]", "", search)
            search = re.sub(r"\s+", " ", search).strip()
            
            # Security Check: Agar sirf emoji ya signs thhe aur ab search text khali ho gaya toh return
            if not search:
                return

            # -------------------------------------------------------------------------
            # PART 3: DATABASE SEARCH & SPELL CHECK (Files dhoondna aur spelling janchalna)
            # -------------------------------------------------------------------------
            m = await message.reply_text(f'**•『 🔍 ɪ ᴀᴍ ꜱᴇᴀʀᴄʜɪɴɢ 』•** `{search}`', reply_to_message_id=message.id)
            files, offset, total_results = await get_search_results(message.chat.id, search, offset=0, filter=True)
            settings = await get_settings(message.chat.id)
            
            if not files:
                if settings.get("spell_check"):
                    ai_sts = await m.edit('🤖 ᴘʟᴇᴀꜱᴇ ᴡᴀɪᴛ, ᴀɪ ɪꜱ ᴄʜᴇᴄᴋɪɴɢ ʏᴏᴜʀ ꜱᴘᴇʟʟɪɴɢ...')
                    is_misspelled = await ai_spell_check(chat_id=message.chat.id, wrong_name=search)
                    if is_misspelled:
                        await ai_sts.edit(f'✅ Aɪ Sᴜɢɢᴇsᴛᴇᴅ: <code>{is_misspelled}</code>\n🔍 Searching for it...')
                        message.text = is_misspelled
                        await ai_sts.delete()
                        return await auto_filter(client, message) # Sahi spelling ke sath dobara filter chalu
                    await ai_sts.delete()
                else:
                    await m.delete()
                return await advantage_spell_chok(client, message)
        else:
            return
    else:
        # Agar user ne Spell Check ke button par click kiya thha (spoll=True)
        message = msg.message.reply_to_message
        search, files, offset, total_results = spoll
        m = await message.reply_text(f'**•『 🔍 ɪ ᴀᴍ ꜱᴇᴀʀᴄʜɪɴɢ 』•** `{search}`', reply_to_message_id=message.id)
        settings = await get_settings(message.chat.id)
        await msg.message.delete()

    # Callback data handle karne ke liye keys banana
    key = f"{message.chat.id}-{message.id}"
    FRESH[key] = search
    temp.GETALL[key] = files
    temp.SHORT[message.from_user.id] = message.chat.id
    
    # -------------------------------------------------------------------------
    # PART 4: BUTTON GENERATION (Inline buttons taiyar karna)
    # -------------------------------------------------------------------------
    btn = []
    if settings.get('button'):
        btn = [
            [InlineKeyboardButton(text=f"🔗 {get_size(file.file_size)} ≽ " + clean_filename(file.file_name), callback_data=f'file#{file.file_id}')]
            for file in files
        ]
        
    # Common Headers Jo Dono Modes Me Dikhenge (Code short kiya gaya)
    btn.insert(0, [
        InlineKeyboardButton('Qᴜᴀʟɪᴛʏ', callback_data=f"qualities#{key}"),
        InlineKeyboardButton("Lᴀɴɢᴜᴀɢะ", callback_data=f"languages#{key}"),
        InlineKeyboardButton("Sᴇᴀsᴏɴ",  callback_data=f"seasons#{key}")
    ])
    btn.insert(0, [
        InlineKeyboardButton("⚜️ 𝐑𝐞𝐦𝐨𝐯𝐞 𝐚𝐝𝐬 ⚜️", url=f"https://t.me/{temp.U_NAME}?start=premium"),
        InlineKeyboardButton("Sᴇɴᴅ Aʟʟ", callback_data=f"sendfiles#{key}")
    ])

    # Pagination logic (Next/Page counter lagana)
    if offset != "":
        req = message.from_user.id if message.from_user else 0
        try:
            max_btn_val = 10 if settings.get('max_btn') else int(MAX_B_TN)
        except (KeyError, NameError, ValueError):
            await save_group_settings(message.chat.id, 'max_btn', True)
            max_btn_val = 10
            
        btn.append([
            InlineKeyboardButton("ᴘᴀɢᴇ", callback_data="pages"), 
            InlineKeyboardButton(text=f"1/{math.ceil(int(total_results)/max_btn_val)}", callback_data="pages"), 
            InlineKeyboardButton(text="ɴᴇxᴛ ⋟", callback_data=f"next_{req}_{key}_{offset}")
        ])
    else:
        btn.append([InlineKeyboardButton(text="↭ ɴᴏ ᴍᴏʀᴇ ᴘᴀɢᴇꜱ ᴀᴠᴀɪʟᴀʙʟे ↭", callback_data="pages")])

    # IMDb Poster check karna
    imdb = await get_poster(search, file=(files[0]).file_name) if settings.get("imdb") else None

    # -------------------------------------------------------------------------
    # PART 5: TIME DIFFERENCE & CAPTION (Time aur text message banana)
    # -------------------------------------------------------------------------
    # ⏰ FIX: Purana timedelta math hata kar simple accurate time system lagaya
    cur_time = datetime.now(pytz.timezone('Asia/Kolkata'))
    curr_datetime = cur_time.replace(hour=msg.date.hour, minute=msg.date.minute, second=msg.date.second) if hasattr(msg, 'date') else cur_time
    remaining_seconds = "{:.2f}".format((cur_time - curr_datetime).total_seconds())
    if float(remaining_seconds) < 0: 
        remaining_seconds = "0.10"  # Midnight crossover error handler

    TEMPLATE = settings.get('template', script.IMDB_TEMPLATE_TXT)

    if imdb:
        cap = TEMPLATE.format(
            query=search, title=imdb['title'], votes=imdb['votes'], aka=imdb["aka"],
            seasons=imdb["seasons"], box_office=imdb['box_office'], localized_title=imdb['localized_title'],
            kind=imdb['kind'], imdb_id=imdb["imdb_id"], cast=imdb["cast"], runtime=imdb["runtime"],
            countries=imdb["countries"], certificates=imdb["certificates"], languages=imdb["languages"],
            director=imdb["director"], writer=imdb["writer"], producer=imdb["producer"],
            composer=imdb["composer"], cinematographer=imdb["cinematographer"], music_team=imdb["music_team"],
            distributors=imdb["distributors"], release_date=imdb['release_date'], year=imdb['year'],
            genres=imdb['genres'], poster=imdb['poster'], plot=imdb['plot'], rating=imdb['rating'],
            url=imdb['url'], **locals()
        )
        Temp.IMDB_CAP[message.from_user.id] = cap
        
        if not settings.get('button'):
            cap += "\n📂 <b><u>𝒀𝒐𝒖𝒓 𝑭𝒊𝒍𝒆𝒔 𝑨𝒓𝒆 𝑹𝒆𝒂𝒅𝒚</u></b> 👇\n\n"
            for idx, file in enumerate(files, start=1):
                cap += f"<b>{idx}. <a href='https://telegram.me/{temp.U_NAME}?start=file_{message.chat.id}_{file.file_id}'>[{get_size(file.file_size)}] {clean_filename(file.file_name)}</a></b>\n\n"
            cap = cap.strip() + f"\n\n───────────────────\n\n<b>{script.DEL_MSG_2.format(get_time(DELETE_TIME)).lstrip()}</b>"    
    else:
        # NoneType handle karne ke liye variable safety check
        mention_user = message.from_user.mention if message.from_user else "User"
        chat_title = message.chat.title or getattr(temp, 'B_LINK', None) or 'ᴅʀᴇᴀᴍxʙᴏᴛᴢ'
        
        cap = f"<b>🏷 ᴛɪᴛʟᴇ : <code>{search}</code>\n🧱 ᴛᴏᴛᴀʟ ꜰɪʟᴇꜱ : <code>{total_results}</code>\n⏰ ʀᴇsᴜʟᴛ ɪɴ : <code>{remaining_seconds} sᴇᴄᴏɴᴅs</code>\n<blockquote>🌿 ᴍᴀɪɴᴛᴀɪɴᴇᴅ ʙʏ : ᴅᴇᴠᴇʟᴏᴘᴇʀ_ʙᴏʏ™(𝓐𝓷𝓴𝓲𝓽_𝓜𝓮𝓮𝓷𝓪😝)</blockquote>\n📝 ʀᴇǫᴜᴇsᴛᴇᴅ ʙʏ : {mention_user}\n⚜️ ᴘᴏᴡᴇʀᴇᴅ ʙʏ : ⚡ {chat_title} \n\n📂 <b><u>𝒀𝒐𝒖𝒓 𝑭𝒊𝒍𝒆𝒔 𝑨𝒓𝒆 𝑹𝒆𝒂𝒅𝒚</u></b> 👇 \n\n</b>"

        if not settings.get('button'):
            for idx, file in enumerate(files, start=1):
                cap += f"<b>{idx}. <a href='https://telegram.me/{temp.U_NAME}?start=file_{message.chat.id}_{file.file_id}'>[{get_size(file.file_size)}] {clean_filename(file.file_name)}</a></b>\n\n"
            cap = cap.strip() + f"\n\n───────────────────\n<b>{script.DEL_MSG_2.format(get_time(DELETE_TIME)).lstrip()}</b>"

    # -------------------------------------------------------------------------
    # PART 6: AUTO DELETE & SENDING (Message bhejna aur automatic delete karna)
    # -------------------------------------------------------------------------
    # 🛠 REUSABLE FUNCTION: Faltu line-copy paste ko hatakar single block banaya
    async def handle_auto_delete(sent_msg):
        try:
            if settings.get('auto_delete', True):
                await asyncio.sleep(DELETE_TIME)
                await sent_msg.delete()
                await message.delete()
        except Exception:
            await save_group_settings(message.chat.id, 'auto_delete', True)
            await asyncio.sleep(DELETE_TIME)
            await sent_msg.delete()
            await message.delete()

    # Final Execution (Photo ya Text deliver karna group me)
    if imdb and imdb.get('poster'):
        try:
            hehe = await message.reply_photo(photo=imdb.get('poster'), caption=cap, reply_markup=InlineKeyboardMarkup(btn), parse_mode=enums.ParseMode.HTML)
            await m.delete()
            await handle_auto_delete(hehe)
        except (MediaEmpty, PhotoInvalidDimensions, WebpageMediaEmpty):
            poster = imdb.get('poster').replace('.jpg', "._V1_UX360.jpg")
            try:
                hmm = await message.reply_photo(photo=poster, caption=cap, reply_markup=InlineKeyboardMarkup(btn), parse_mode=enums.ParseMode.HTML)
                await m.delete()
                await handle_auto_delete(hmm)
            except Exception:
                dxb = await message.reply_text(text=cap, reply_markup=InlineKeyboardMarkup(btn), parse_mode=enums.ParseMode.HTML)
                await m.delete()
                await handle_auto_delete(dxb)
        except Exception as e:
            logger.exception(e)
            dxb = await message.reply_text(text=cap, reply_markup=InlineKeyboardMarkup(btn), parse_mode=enums.ParseMode.HTML)
            await m.delete()
            await handle_auto_delete(dxb)
    else:
        dxb = await message.reply_text(text=cap, reply_markup=InlineKeyboardMarkup(btn), disable_web_page_preview=True, parse_mode=enums.ParseMode.HTML)
        await m.delete()
        await handle_auto_delete(dxb)
 #______________________________________________________AUTO_FILTER____________________________________________________________

async def old_auto_filter(client, msg, spoll=False):
    curr_time = datetime.now(pytz.timezone('Asia/Kolkata')).time()
    if not spoll:
        message = msg
        # 🛑 STOP SEARCH IF IT'S A REQUEST (Fix for @botusername /request)
        if message.text:
            if "/request" in message.text.lower() or "#request" in message.text.lower():
                return  # रिक्वेस्ट मैसेज है, सर्च बंद
        if message.text.startswith("/"):
            return
        if re.findall(r"((^\/|^,|^!|^\.|^[\U0001F600-\U000E007F]).*)", message.text):
            return
        if len(message.text) < 100:
            search = message.text
            search = search.lower()
            # ✅ Unicode normalize(VERY IMPORTANT)
            search = unicodedata.normalize('NFKD', search)
            search = search.encode('ascii', 'ignore').decode('ascii')
            search = re.sub(
                "["
                "\U0001F600-\U0001F64F"  # emoticons
                "\U0001F300-\U0001F5FF"  # symbols & pictographs
                "\U0001F680-\U0001F6FF"  # transport & map
                "\U0001F1E0-\U0001F1FF"  # flags
                "\U00002700-\U000027BF"  # dingbats
                "\U000024C2-\U0001F251"
                "]+",
                "",
                search
            )
            find = search.split(" ")
            search = ""
            removes = ["in", "upload", "series", "full",
                       "horror", "thriller", "mystery", "print", "file", "pls", "please", "send", "give", "movie", "movies", "new", "latest", "bro", "bruh",
                       "link", "dubbed", "download", "subtitle", "subtitles", "anyone", "any",
                       "venum", "iruka", "pannunga", "anuppunga", "film", "undo", "kitti", "kitty", "tharu", "&"]
            for x in find:
                if x in removes:
                    continue
                else:
                    search = search + x + " "
            search = re.sub(r"\b(pl(i|e)*?(s|z+|ease|se|ese|(e+)s(e)?)|((send|snd|giv(e)?|gib)(\sme)?)|movie(s)?|new|latest|bro|bruh|broh|helo|that|find|dubbed|link|venum|iruka|pannunga|pannungga|anuppunga|anupunga|anuppungga|anupungga|film|undo|kitti|kitty|tharu|kittumo|kittum|movie|any(one)|download\ssubtitle(s)?)", "", search, flags=re.IGNORECASE)
            search = re.sub(r"\s+", " ", search).strip()
            search = normalize_for_search(search)
            # ✅ season normalize
            #search = normalize_season(search)
            #search = normalize_episode(search)
            search = search.replace("-", " ")
            search = search.replace(":", "")
            search = search.replace(".", " ")
            search = search.replace("'", " ")
            search = search.replace("&", " ")
            # 🔥 REMOVE SYMBOLS
            search = re.sub(r"[!@#$%^*()_+=\[\]{};\"<>?/\\|]", "", search)
            search = re.sub(r"\s+", " ", search).strip()
            m = await message.reply_text(f'**•『 🔍 ɪ ᴀᴍ ꜱᴇᴀʀᴄʜɪɴɢ 』•** `{search}`', reply_to_message_id=message.id)
            files, offset, total_results = await get_search_results(message.chat.id, search, offset=0, filter=True)
            settings = await get_settings(message.chat.id)
            if not files:
                if settings["spell_check"]:
                    ai_sts = await m.edit('🤖 ᴘʟᴇᴀꜱᴇ ᴡᴀɪᴛ, ᴀɪ ɪꜱ ᴄʜᴇᴄᴋɪɴɢ ʏᴏᴜʀ ꜱᴘᴇʟʟɪɴɢ...')
                    is_misspelled = await ai_spell_check(chat_id=message.chat.id, wrong_name=search)
                    if is_misspelled:
                        await ai_sts.edit(f'✅ Aɪ Sᴜɢɢᴇsᴛᴇᴅ: <code>{is_misspelled}</code>\n🔍 Searching for it...')
                        message.text = is_misspelled
                        await ai_sts.delete()
                        return await auto_filter(client, message)
                    await ai_sts.delete()
                    return await advantage_spell_chok(client, message)
                else:
                    await m.delete()
                    return await advantage_spell_chok(client, message)
        else:
            return
    else:
        message = msg.message.reply_to_message
        search, files, offset, total_results = spoll
        m = await message.reply_text(f'**•『 🔍 ɪ ᴀᴍ ꜱᴇᴀʀᴄʜɪɴɢ 』•** `{search}`', reply_to_message_id=message.id)
        settings = await get_settings(message.chat.id)
        await msg.message.delete()

    key = f"{message.chat.id}-{message.id}"
    FRESH[key] = search
    temp.GETALL[key] = files
    temp.SHORT[message.from_user.id] = message.chat.id
    
    if settings.get('button'):
        btn = [
            [
                InlineKeyboardButton(text=f"🔗 {get_size(file.file_size)} ≽ " + clean_filename(
                    file.file_name), callback_data=f'file#{file.file_id}'),
            ]
            for file in files
        ]
        btn.insert(0,
                   [
                       InlineKeyboardButton(f'Qᴜᴀʟɪᴛʏ', callback_data=f"qualities#{key}"),
                       InlineKeyboardButton("Lᴀɴɢᴜᴀɢᴇ", callback_data=f"languages#{key}"),
                       InlineKeyboardButton("Sᴇᴀsᴏɴ",  callback_data=f"seasons#{key}")
                   ]
                   )
        btn.insert(0,
                   [
                       InlineKeyboardButton("⚜️ 𝐑𝐞𝐦𝐨𝐯𝐞 𝐚𝐝𝐬 ⚜️", url=f"https://t.me/{temp.U_NAME}?start=premium"),
                       InlineKeyboardButton("Sᴇɴᴅ Aʟʟ", callback_data=f"sendfiles#{key}")
                   ])
    else:
        btn = []
        btn.insert(0,
                   [
                       InlineKeyboardButton(f'Qᴜᴀʟɪᴛʏ', callback_data=f"qualities#{key}"),
                       InlineKeyboardButton("Lᴀɴɢᴜᴀɢᴇ", callback_data=f"languages#{key}"),
                       InlineKeyboardButton("Sᴇᴀsᴏɴ",  callback_data=f"seasons#{key}")
                   ]
                   )
        btn.insert(0,
                   [
                       InlineKeyboardButton("⚜️ 𝐑𝐞𝐦𝐨𝐯𝐞 𝐚𝐝𝐬 ⚜️", url=f"https://t.me/{temp.U_NAME}?start=premium"),
                       InlineKeyboardButton("Sᴇɴᴅ Aʟʟ", callback_data=f"sendfiles#{key}")
                   ])

    if offset != "":
        req = message.from_user.id if message.from_user else 0
        try:
            if settings['max_btn']:
                btn.append(
                    [InlineKeyboardButton("ᴘᴀɢᴇ", callback_data="pages"), InlineKeyboardButton(
                        text=f"1/{math.ceil(int(total_results)/10)}", callback_data="pages"), InlineKeyboardButton(text="ɴᴇxᴛ ⋟", callback_data=f"next_{req}_{key}_{offset}")]
                )
            else:
                btn.append(
                    [InlineKeyboardButton("ᴘᴀɢᴇ", callback_data="pages"), InlineKeyboardButton(
                        text=f"1/{math.ceil(int(total_results)/int(MAX_B_TN))}", callback_data="pages"), InlineKeyboardButton(text="ɴᴇxᴛ ⋟", callback_data=f"next_{req}_{key}_{offset}")]
                )
        except KeyError:
            await save_group_settings(message.chat.id, 'max_btn', True)
            btn.append(
                [InlineKeyboardButton("ᴘᴀɢᴇ", callback_data="pages"), InlineKeyboardButton(
                    text=f"1/{math.ceil(int(total_results)/10)}", callback_data="pages"), InlineKeyboardButton(text="ɴᴇxᴛ ⋟", callback_data=f"next_{req}_{key}_{offset}")]
            )
    else:
        btn.append([InlineKeyboardButton(text="↭ ɴᴏ ᴍᴏʀᴇ ᴘᴀɢᴇꜱ ᴀᴠᴀɪʟᴀʙʟᴇ ↭", callback_data="pages")])

    imdb = await get_poster(search, file=(files[0]).file_name) if settings["imdb"] else None

    cur_time = datetime.now(pytz.timezone('Asia/Kolkata')).time()
    time_difference = timedelta(hours=cur_time.hour, minutes=cur_time.minute, seconds=(cur_time.second+(cur_time.microsecond/1000000))) - \
        timedelta(hours=curr_time.hour, minutes=curr_time.minute,
                  seconds=(curr_time.second+(curr_time.microsecond/1000000)))
    remaining_seconds = "{:.2f}".format(time_difference.total_seconds())
    
    TEMPLATE = script.IMDB_TEMPLATE_TXT
    if settings['template']:
        TEMPLATE = settings['template']

    if imdb:
        cap = TEMPLATE.format(
            query=search,
            title=imdb['title'],
            votes=imdb['votes'],
            aka=imdb["aka"],
            seasons=imdb["seasons"],
            box_office=imdb['box_office'],
            localized_title=imdb['localized_title'],
            kind=imdb['kind'],
            imdb_id=imdb["imdb_id"],
            cast=imdb["cast"],
            runtime=imdb["runtime"],
            countries=imdb["countries"],
            certificates=imdb["certificates"],
            languages=imdb["languages"],
            director=imdb["director"],
            writer=imdb["writer"],
            producer=imdb["producer"],
            composer=imdb["composer"],
            cinematographer=imdb["cinematographer"],
            music_team=imdb["music_team"],
            distributors=imdb["distributors"],
            release_date=imdb['release_date'],
            year=imdb['year'],
            genres=imdb['genres'],
            poster=imdb['poster'],
            plot=imdb['plot'],
            rating=imdb['rating'],
            url=imdb['url'],
            **locals()
        )
        Temp.IMDB_CAP[message.from_user.id] = cap
        if not settings.get('button'):
            cap += "\n📂 <b><u>𝒀𝒐𝒖𝒓 𝑭𝒊𝒍𝒆𝒔 𝑨𝒓𝒆 𝑹𝒆𝒂𝒅𝒚</u></b> 👇\n\n"
            for idx, file in enumerate(files, start=1):
                cap += f"<b>{idx}. <a href='https://telegram.me/{temp.U_NAME}?start=file_{message.chat.id}_{file.file_id}'>[{get_size(file.file_size)}] {clean_filename(file.file_name)}</a></b>\n\n"
            
            # Yahan gap control kiya hai
            cap = cap.strip()
            cap += f"\n\n───────────────────\n\n<b>{script.DEL_MSG_2.format(get_time(DELETE_TIME)).lstrip()}</b>"    
    else:
        if settings.get('button'):
            cap = f"<b>🏷 ᴛɪᴛʟᴇ : <code>{search}</code>\n🧱 ᴛᴏᴛᴀʟ ꜰɪʟᴇꜱ : <code>{total_results}</code>\n⏰ ʀᴇsᴜʟᴛ ɪɴ : <code>{remaining_seconds} sᴇᴄᴏɴᴅs</code>\n<blockquote>🌿 ᴍᴀɪɴᴛᴀɪɴᴇᴅ ʙʏ : ᴅᴇᴠᴇʟᴏᴘᴇʀ_ʙᴏʏ™(𝓐𝓷𝓴𝓲𝓽_𝓜𝓮𝓮𝓷𝓪😝)</blockquote>\n📝 ʀᴇǫᴜᴇsᴛᴇᴅ ʙʏ : {message.from_user.mention}\n⚜️ ᴘᴏᴡᴇʀᴇᴅ ʙʏ : ⚡ {message.chat.title or temp.B_LINK or 'ᴅʀᴇᴀᴍxʙᴏᴛᴢ'} \n\n📂 <b><u>𝒀𝒐𝒖𝒓 𝑭𝒊𝒍𝒆𝒔 𝑨𝒓𝒆 𝑹ᴇ𝒂𝒅𝒚</u></b> 👇 \n\n</b>"
        else:
            cap = f"<b>🏷 ᴛɪᴛʟᴇ : <code>{search}</code>\n🧱 ᴛᴏᴛᴀʟ ꜰɪʟᴇꜱ : <code>{total_results}</code>\n⏰ ʀᴇsᴜʟᴛ ɪɴ : <code>{remaining_seconds} sᴇᴄᴏɴᴅs</code>\n<blockquote>🌿 ᴍᴀɪɴᴛᴀɪɴᴇᴅ ʙʏ : ᴅᴇᴠᴇʟᴏᴘᴇʀ_ʙᴏʏ™(𝓐𝓷𝓴𝓲𝓽_𝓜𝓮𝓮𝓷𝓪😝)</blockquote>\n📝 ʀᴇǫᴜᴇsᴛᴇᴅ ʙʏ : {message.from_user.mention}\n⚜️ ᴘᴏᴡᴇʀᴇᴅ ʙʏ : ⚡ {message.chat.title or temp.B_LINK or 'ᴅʀᴇᴀᴍxʙᴏᴛᴢ'} \n\n📂 <b><u>𝒀𝒐𝒖𝒓 𝑭𝒊𝒍𝒆𝒔 𝑨𝒓𝒆 𝑹ᴇ𝒂𝒅𝒚</u></b> 👇 \n\n</b>"

            for idx, file in enumerate(files, start=1):
                cap += f"<b>{idx}. <a href='https://telegram.me/{temp.U_NAME}?start=file_{message.chat.id}_{file.file_id}'>[{get_size(file.file_size)}] {clean_filename(file.file_name)}</a></b>\n\n"
            
            # Yahan gap control kiya hai
            cap = cap.strip()
            cap += f"\n\n───────────────────\n<b>{script.DEL_MSG_2.format(get_time(DELETE_TIME)).lstrip()}</b>"   

    if imdb and imdb.get('poster'):
        try:
            hehe = await message.reply_photo(photo=imdb.get('poster'), caption=cap, reply_markup=InlineKeyboardMarkup(btn), parse_mode=enums.ParseMode.HTML)
            await m.delete()
            try:
                if settings['auto_delete']:
                    await asyncio.sleep(DELETE_TIME)
                    await hehe.delete()
                    await message.delete()
            except KeyError:
                await save_group_settings(message.chat.id, 'auto_delete', True)
                await asyncio.sleep(DELETE_TIME)
                await hehe.delete()
                await message.delete()
        except (MediaEmpty, PhotoInvalidDimensions, WebpageMediaEmpty):
            pic = imdb.get('poster')
            poster = pic.replace('.jpg', "._V1_UX360.jpg")
            hmm = await message.reply_photo(photo=poster, caption=cap, reply_markup=InlineKeyboardMarkup(btn), parse_mode=enums.ParseMode.HTML)
            await m.delete()
            try:
                if settings['auto_delete']:
                    await asyncio.sleep(DELETE_TIME)
                    await hmm.delete()
                    await message.delete()
            except KeyError:
                await save_group_settings(message.chat.id, 'auto_delete', True)
                await asyncio.sleep(DELETE_TIME)
                await hmm.delete()
                await message.delete()
        except Exception as e:
            logger.exception(e)
            dxb = await message.reply_text(text=cap, reply_markup=InlineKeyboardMarkup(btn), parse_mode=enums.ParseMode.HTML)
            try:
                if settings['auto_delete']:
                    await asyncio.sleep(DELETE_TIME)
                    await dxb.delete()
                    await message.delete()
            except KeyError:
                await save_group_settings(message.chat.id, 'auto_delete', True)
                await asyncio.sleep(DELETE_TIME)
                await dxb.delete()
    else:
        dxb = await message.reply_text(text=cap, reply_markup=InlineKeyboardMarkup(btn), disable_web_page_preview=True, parse_mode=enums.ParseMode.HTML)
        await m.delete()
        try:
            if settings['auto_delete']:
                await asyncio.sleep(DELETE_TIME)
                await dxb.delete()
                await message.delete()
        except KeyError:
            await save_group_settings(message.chat.id, 'auto_delete', True)
            await asyncio.sleep(DELETE_TIME)
            await dxb.delete()
            await message.delete()





#_______________________________________________________ai_spell_check________________________________________________________

async def old_ai_spell_check(chat_id, wrong_name):
    async def search_movie(wrong_name):
        search_results = imdb.search_movie(wrong_name)
        movie_list = [movie['title'] for movie in search_results]
        return movie_list
    movie_list = await search_movie(wrong_name)
    if not movie_list:
        return
    for _ in range(4):
        closest_match = process.extractOne(wrong_name, movie_list)
        if not closest_match or closest_match[1] <= 70:
            return
        movie = closest_match[0]
        files, _, _ = await get_search_results(chat_id=chat_id, query=movie)
        if files:
            return movie
        movie_list.remove(movie)
#-----------------------------------#-----------------------------------

async def ai_spell_check(chat_id, wrong_name):
    async def search_movie(wrong_name):
        search_results = imdb.search_movie(wrong_name)
        if not search_results or not hasattr(search_results, "titles"):
            return []
        movie_list = [movie.title for movie in search_results.titles]
        return movie_list
    movie_list = await search_movie(wrong_name)
    if not movie_list:
        return
    for _ in range(3):
        closest_match = process.extractOne(wrong_name, movie_list)
        if not closest_match or closest_match[1] <= 85:
            return
        movie = closest_match[0]
        files, _, _ = await get_search_results(chat_id=chat_id, query=movie)
        if files:
            return movie
        movie_list.remove(movie)

async def advantage_spell_chok(client, message):
    mv_id = message.id
    search = message.text
    chat_id = message.chat.id
    settings = await get_settings(chat_id)
    query = re.sub(
        r"\b(pl(i|e)*?(s|z+|ease|se|ese|(e+)s(e)?)|((send|snd|giv(e)?|gib)(\sme)?)|movie(s)?|new|latest|br((o|u)h?)*|^h(e|a)?(l)*(o)*|mal(ayalam)?|t(h)?amil|file|that|find|und(o)*|kit(t(i|y)?)?o(w)?|thar(u)?(o)*w?|kittum(o)*|aya(k)*(um(o)*)?|full\smovie|any(one)|with\ssubtitle(s)?)",
        "", message.text, flags=re.IGNORECASE)
    query = query.strip() + " movie"
    try:
        movies = await get_poster(search, bulk=True)
    except Exception as e:
        logger.exception("get_poster failed for query=%s: %s", query, e)
        try:
            k = await message.reply(script.I_CUDNT.format(message.from_user.mention))
            await asyncio.sleep(60)
            try:
                await k.delete()
            except Exception:
                pass
        except Exception:
            pass
        try:
            await message.delete()
        except Exception:
            pass
        return
    if not movies:
        google = quote_plus(search)
        button = [[InlineKeyboardButton(
            "🔍 ᴄʜᴇᴄᴋ sᴘᴇʟʟɪɴɢ ᴏɴ ɢᴏᴏɢʟᴇ 🔍", url=f"https://www.google.com/search?q={google}")]]
        k = await message.reply_text(text=script.I_CUDNT.format(search), reply_markup=InlineKeyboardMarkup(button))
        await asyncio.sleep(60)
        await k.delete()
        try:
            await message.delete()
        except:
            pass
        return
    user = message.from_user.id if message.from_user else 0
    buttons = [
        [InlineKeyboardButton(text=movie.title, callback_data=f"spol#{movie.imdb_id}#{user}")
         ] for movie in movies]

    buttons.append([InlineKeyboardButton(
        text="🚫 ᴄʟᴏsᴇ 🚫", callback_data='close_data')])
    d = await message.reply_text(text=script.CUDNT_FND.format(message.from_user.mention), reply_markup=InlineKeyboardMarkup(buttons), reply_to_message_id=message.id)
    await asyncio.sleep(60)
    await d.delete()
    try:
        await message.delete()
    except:
        pass


async def old_advantage_spell_chok(client, message):
    mv_id = message.id
    search = message.text
    chat_id = message.chat.id
    settings = await get_settings(chat_id)
    query = re.sub(
        r"\b(pl(i|e)*?(s|z+|ease|se|ese|(e+)s(e)?)|((send|snd|giv(e)?|gib)(\sme)?)|movie(s)?|new|latest|br((o|u)h?)*|^h(e|a)?(l)*(o)*|mal(ayalam)?|t(h)?amil|file|that|find|und(o)*|kit(t(i|y)?)?o(w)?|thar(u)?(o)*w?|kittum(o)*|aya(k)*(um(o)*)?|full\smovie|any(one)|with\ssubtitle(s)?)",
        "", message.text, flags=re.IGNORECASE)
    query = query.strip() + " movie"
    try:
        movies = await get_poster(search, bulk=True)
    except:
        k = await message.reply(script.I_CUDNT.format(message.from_user.mention))
        await asyncio.sleep(60)
        await k.delete()
        try:
            await message.delete()
        except:
            pass
        return
    if not movies:
        google = search.replace(" ", "+")
        button = [[InlineKeyboardButton(
            "🔍 ᴄʜᴇᴄᴋ sᴘᴇʟʟɪɴɢ ᴏɴ ɢᴏᴏɢʟᴇ 🔍", url=f"https://www.google.com/search?q={google}")]]
        k = await message.reply_text(text=script.I_CUDNT.format(search), reply_markup=InlineKeyboardMarkup(button))
        await asyncio.sleep(60)
        await k.delete()
        try:
            await message.delete()
        except:
            pass
        return
    user = message.from_user.id if message.from_user else 0
    buttons = [
        [InlineKeyboardButton(text=movie.get('title'), callback_data=f"spol#{movie.movieID}#{user}")
         ] for movie in movies]

    buttons.append([InlineKeyboardButton(
        text="🚫 ᴄʟᴏsᴇ 🚫", callback_data='close_data')])
    d = await message.reply_text(text=script.CUDNT_FND.format(message.from_user.mention), reply_markup=InlineKeyboardMarkup(buttons), reply_to_message_id=message.id)
    await asyncio.sleep(60)
    await d.delete()
    try:
        await message.delete()
    except:
        pass



# --- ADVANTAGE SPELL CHECK FUNCTION ---

async def old_advantage_spell_chok(client, message):
    mv_id = message.id
    search = message.text
    chat_id = message.chat.id
    
    # Settings handle karne ke liye (agar get_settings fail ho jaye toh error na aaye)
    try:
        settings = await get_settings(chat_id)
    except:
        settings = {}

    # Cleaning the query (Faltu words nikalne ke liye)
    query = re.sub(
        r"\b(pl(i|e)*?(s|z+|ease|se|ese|(e+)s(e)?)|((send|snd|giv(e)?|gib)(\sme)?)|movie(s)?|new|latest|br((o|u)h?)*|^h(e|a)?(l)*(o)*|mal(ayalam)?|t(h)?amil|file|that|find|und(o)*|kit(t(i|y)?)?o(w)?|thar(u)?(o)*w?|kittum(o)*|aya(k)*(um(o)*)?|full\smovie|any(one)|with\ssubtitle(s)?)",
        "", message.text, flags=re.IGNORECASE)
    query = query.strip()

    try:
        # Search ke liye cleaned 'query' use karna behtar hai
        movies = await get_poster(query, bulk=True)
    except Exception as e:
        print(f"Error fetching posters: {e}")
        k = await message.reply(script.I_CUDNT.format(message.from_user.mention))
        await asyncio.sleep(60)
        await k.delete()
        return

    # Agar koi result na mile
    if not movies:
        google = search.replace(" ", "+")
        button = [[InlineKeyboardButton(
            "🔍 ᴄʜᴇᴄᴋ sᴘᴇʟʟɪɴɢ ᴏɴ ɢᴏᴏɢʟᴇ 🔍", url=f"https://www.google.com/search?q={google}")]]
        k = await message.reply_text(text=script.I_CUDNT.format(search), reply_markup=InlineKeyboardMarkup(button))
        await asyncio.sleep(60)
        await k.delete()
        return

    user = message.from_user.id if message.from_user else 0
    
    # SAHI LOGIC: datetime.now().year use karein kyunki aapka import 'from datetime import datetime' hai
    current_year = datetime.now().year 
    buttons = []

    for movie in movies:
        # Movie ka type check karein
        # IMDb aksar ye types bhejta hai: 'movie', 'tv series', 'tv mini series', 'video game', 'podcast'
        m_kind = getattr(movie, 'kind', movie.get('kind', 'movie')).lower()
        
        # --- FILTER LOGIC ---
        # Sirf movie aur series ko allow karein, baki sab skip (ignore) karein
        allowed_kinds = ['movie', 'tv series', 'tv mini series', 'series', 'web series']
        if m_kind not in allowed_kinds:
            continue  # Agar podcast ya news hai to loop agli movie par chala jayega

        m_title = movie.get('title')
        m_year_raw = str(movie.get('year', '')).strip()
        m_id = getattr(movie, 'movieID', None) or movie.get('id')

        # Year nikalne ka logic (Regex se)
        year_int = None
        clean_year = re.sub(r"\D", "", m_year_raw)[:4]
        if clean_year:
            year_int = int(clean_year)

        # 'Expected' aur 'Coming Soon' logic
        # 2026 current year hai, isliye >= current_year use kiya hai
        if "expected" in m_year_raw.lower() or (year_int and year_int >= current_year):
            btn_text = f"•✅ {m_title} 🕒 Coming Soon ({year_int if year_int else 'TBA'})"
        elif year_int:
            btn_text = f"•✅ {m_title} ({year_int})"
        else:
            btn_text = f"•✅ {m_title}"

        buttons.append([InlineKeyboardButton(text=btn_text, callback_data=f"spol#{m_id}#{user}")])

    # Close button
    buttons.append([InlineKeyboardButton(text="🚫 ᴄʟᴏsᴇ 🚫", callback_data='close_data')])

    try:
        d = await message.reply_text(
            text=script.CUDNT_FND.format(message.from_user.mention), 
            reply_markup=InlineKeyboardMarkup(buttons), 
            reply_to_message_id=message.id
        )
        
        # 60 seconds baad delete karne ka logic
        await asyncio.sleep(60)
        await d.delete()
        try:
            await message.delete()
        except:
            pass
    except Exception as e:
        print(f"Error sending buttons: {e}")




