#this code is developed by 【𝐃𝐞𝐯𝐞𝐥𝐨𝐩𝐞𝐫_𝐁𝐨𝐲™(𝓐𝓷𝓴𝓲𝓽_𝓜𝓮𝓮𝓷𝓪😝)】
# Don't remove the my credit please 🙏
import time
import asyncio
from pyrogram import Client, filters
from pyrogram.types import InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup
from database.users_chats_db import db
from info import ADMINS
from utils import temp, get_readable_time

lock = asyncio.Lock()

# ----------------- Helper: Auto-delete -----------------
#This feature is developed by 【𝐃𝐞𝐯𝐞𝐥𝐨𝐩𝐞𝐫_𝐁𝐨𝐲™(𝓐𝓷𝓴𝓲𝓽_𝓜𝓮𝓮𝓷𝓪😝)】
# Don't remove the my credit please 🙏
async def auto_delete(msg, delay):
    await asyncio.sleep(delay)
    try:
        await msg.delete()
    except:
        pass

# ----------------- Track last broadcast messages -----------------
#this code is developed by 【𝐃𝐞𝐯𝐞𝐥𝐨𝐩𝐞𝐫_𝐁𝐨𝐲™(𝓐𝓷𝓴𝓲𝓽_𝓜𝓮𝓮𝓷𝓪😝)】
# Don't remove the my credit please 🙏
temp.LAST_USER_BROADCAST = []
temp.LAST_GROUP_BROADCAST = []

async def track_message(sent_msg, target="user"):
    if sent_msg:
        if target == "user":
            temp.LAST_USER_BROADCAST.append(sent_msg)
        elif target == "group":
            temp.LAST_GROUP_BROADCAST.append(sent_msg)

# ----------------- Cancel Callback -----------------
#this code is developed by 【𝐃𝐞𝐯𝐞𝐥𝐨𝐩𝐞𝐫_𝐁𝐨𝐲™(𝓐𝓷𝓴𝓲𝓽_𝓜𝓮𝓮𝓷𝓪😝)】
# Don't remove the my credit please 🙏
@Client.on_callback_query(filters.regex(r'^broadcast_cancel'))
async def broadcast_cancel(bot, query):
    _, target = query.data.split("#", 1)
    if target == 'users':
        temp.B_USERS_CANCEL = True
        await query.message.edit("🛑 Trying to cancel users broadcasting...")
    elif target == 'groups':
        temp.B_GROUPS_CANCEL = True
        await query.message.edit("🛑 Trying to cancel groups broadcasting...")

# ----------------- Helper: Send message -----------------
#this code is developed by 【𝐃𝐞𝐯𝐞𝐥𝐨𝐩𝐞𝐫_𝐁𝐨𝐲™(𝓐𝓷𝓴𝓲𝓽_𝓜𝓮𝓮𝓷𝓪😝)】
# Don't remove the my credit please 🙏
async def send_message(bot, chat_id, reply_msg, pin=False):
    try:
        reply_markup = reply_msg.reply_markup  # keep original buttons
        sent = None

        if reply_msg.text:
            sent = await bot.send_message(chat_id=chat_id, text=reply_msg.text, reply_markup=reply_markup)
        elif reply_msg.photo:
            sent = await bot.send_photo(chat_id=chat_id, photo=reply_msg.photo.file_id, caption=reply_msg.caption, reply_markup=reply_markup)
        elif reply_msg.video:
            sent = await bot.send_video(chat_id=chat_id, video=reply_msg.video.file_id, caption=reply_msg.caption, reply_markup=reply_markup)
        elif reply_msg.document:
            sent = await bot.send_document(chat_id=chat_id, document=reply_msg.document.file_id, caption=reply_msg.caption, reply_markup=reply_markup)
        else:
            return None, "Error"

        if pin:
            try:
                await sent.pin(disable_notification=True)
            except:
                pass
        return sent, "Success"

    except Exception as e:
        err = str(e).lower()
        if "blocked" in err:
            return None, "Blocked"
        elif "chat not found" in err or "deleted" in err:
            return None, "Deleted"
        else:
            return None, "Error"

# ----------------- User Broadcast -----------------
##This feature is developed by 【𝐃𝐞𝐯𝐞𝐥𝐨𝐩𝐞𝐫_𝐁𝐨𝐲™(𝓐𝓷𝓴𝓲𝓽_𝓜𝓮𝓮𝓷𝓪😝)】
# Don't remove the my credit please 🙏
@Client.on_message(filters.command("broadcast") & filters.user(ADMINS) & filters.reply)
async def broadcast_users(bot, message):
    if lock.locked():
        return await message.reply("⚠️ Another broadcast is in progress. Please wait...")
    ask = await message.reply(
        "<b>Do you want to pin this message in users?</b>",
        reply_markup=ReplyKeyboardMarkup([["Yes", "No"]], one_time_keyboard=True, resize_keyboard=True)
    )
    try:
        dreamxbotz_user_response = await bot.listen(chat_id=message.chat.id, user_id=message.from_user.id, timeout=60)
    except asyncio.TimeoutError:
        await ask.delete()
        return await message.reply("❌ Timed out. Broadcast cancelled.")
    await ask.delete()
    if dreamxbotz_user_response.text not in ("Yes", "No"):
        return await message.reply("❌ Invalid input. Broadcast cancelled.")

    is_pin = dreamxbotz_user_response.text == "Yes"

    # Auto-delete feature is developed by 【𝐃𝐞𝐯𝐞𝐥𝐨𝐩𝐞𝐫_𝐁𝐨𝐲™(𝓐𝓷𝓴𝓲𝓽_𝓜𝓮𝓮𝓷𝓪😝)】
# Don't remove the my credit please 🙏
    ask_time = await message.reply("<b>Enter auto-delete time in seconds (0 to disable auto-delete):</b>")
    try:
        time_response = await bot.listen(chat_id=message.chat.id, user_id=message.from_user.id, timeout=60)
        auto_delete_time = int(time_response.text)
    except:
        await ask_time.delete()
        return await message.reply("❌ Invalid or no response. Broadcast cancelled.")
    await ask_time.delete()

    b_msg = message.reply_to_message
    users = [user async for user in await db.get_all_users()]
    total_users = len(users)
    dreamxbotz_status_msg = await message.reply_text("📤 <b>Broadcasting your message...</b>")
    success = blocked = deleted = failed = 0
    start_time = time.time()
    cancelled = False

    async def send(user):
        try:
            _, result = await users_broadcast(int(user["id"]), b_msg, is_pin)
            return result
        except Exception as e:
            logging.exception(f"Error sending broadcast to {user['id']}")
            return "Error"
        if sent_msg and auto_delete_time > 0:
            asyncio.create_task(auto_delete(sent_msg, auto_delete_time))
        return result

    async with lock:
        for i in range(0, total_users, 50):
            if temp.B_USERS_CANCEL:
                temp.B_USERS_CANCEL = False
                cancelled = True
                break
            batch = users[i:i + 50]
            results = await asyncio.gather(*[send(user) for user in batch])
            for res in results:
                if res == "Success": success += 1
                elif res == "Blocked": blocked += 1
                elif res == "Deleted": deleted += 1
                elif res == "Error": failed += 1
            done = i + len(batch)
            elapsed = get_readable_time(time.time() - start_time)
            await status_msg.edit(
                f"📣 <b>Broadcast Progress:</b>\n\n"
                f"👥 Total: <code>{total_users}</code>\n"
                f"✅ Done: <code>{done}</code>\n"
                f"📬 Success: <code>{success}</code>\n"
                f"⛔ Blocked: <code>{blocked}</code>\n"
                f"🗑️ Deleted: <code>{deleted}</code>\n"
                f"❌ Failed: <code>{failed}</code>\n"
                f"⏱️ Time: {elapsed}",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ CANCEL", callback_data="broadcast_cancel#users")]])
            )
            await asyncio.sleep(0.2)

    elapsed = get_readable_time(time.time() - start_time)
    final_status = (
        f"{'❌ <b>Broadcast Cancelled.</b>' if cancelled else '✅ <b>Broadcast Completed.</b>'}\n\n"
        f"🕒 Time: {elapsed}\n"
        f"👥 Total: <code>{total_users}</code>\n"
        f"📬 Success: <code>{success}</code>\n"
        f"⛔ Blocked: <code>{blocked}</code>\n"
        f"🗑️ Deleted: <code>{deleted}</code>\n"
        f"❌ Failed: <code>{failed}</code>\n\n"
        f"🌿<blockquote> Maintained by :【𝐃𝐞𝐯𝐞𝐥𝐨𝐩𝐞𝐫_𝐁𝐨𝐲™(𝓐𝓷𝓴𝓲𝓽_𝓜𝓮𝓮𝓷𝓪😝)】</blockquote>"
    )
    await status_msg.edit(final_status)

# ----------------- Group Broadcast -----------------
          #This feature is developed by 【𝐃𝐞𝐯𝐞𝐥𝐨𝐩𝐞𝐫_𝐁𝐨𝐲™(𝓐𝓷𝓴𝓲𝓽_𝓜𝓮𝓮𝓷𝓪😝)】
# Don't remove the my credit please 🙏
@Client.on_message(filters.command("grp_broadcast") & filters.user(ADMINS) & filters.reply)
async def broadcast_group(bot, message):
    ask = await message.reply(
        "<b>Do you want to pin this message in groups?</b>",
        reply_markup=ReplyKeyboardMarkup([["Yes", "No"]], one_time_keyboard=True, resize_keyboard=True)
    )
    try:
        user_response = await bot.listen(chat_id=message.chat.id, user_id=message.from_user.id, timeout=60)
    except asyncio.TimeoutError:
        await ask.delete()
        return await message.reply("❌ Timed out. Broadcast cancelled.")
    await ask.delete()
    is_pin = user_response.text == "Yes"

    ask_time = await message.reply("<b>Enter auto-delete time in seconds (0 to disable auto-delete):</b>")
    try:
        time_response = await bot.listen(chat_id=message.chat.id, user_id=message.from_user.id, timeout=60)
        auto_delete_time = int(time_response.text)
    except:
        await ask_time.delete()
        return await message.reply("❌ Invalid or no response. Broadcast cancelled.")
    await ask_time.delete()

    b_msg = message.reply_to_message
    chats = await db.get_all_chats()
    total_chats = await db.total_chat_count()
    status_msg = await message.reply_text("📤 <b>Broadcasting your message to groups...</b>")

    start_time = time.time()
    done = success = failed = 0
    cancelled = False

    async with lock:
        async for chat in chats:
            if temp.B_GROUPS_CANCEL:
                temp.B_GROUPS_CANCEL = False
                cancelled = True
                break
            sent_msg, result = await send_message(bot, int(chat["id"]), b_msg, is_pin)
            await track_message(sent_msg, "group")
            if sent_msg and auto_delete_time > 0:
                asyncio.create_task(auto_delete(sent_msg, auto_delete_time))
            if result == "Success": success += 1
            else: failed += 1
            done += 1

            if done % 10 == 0:
                await status_msg.edit(
    f"📣 <b>Group broadcast progress:</b>\n\n"
    f"👥 Total Groups: <code>{total_chats}</code>\n"
    f"✅ Completed: <code>{done} / {total_chats}</code>\n"
    f"📬 Success: <code>{success}</code>\n"
    f"❌ Failed: <code>{failed}</code>\n\n"
    f"🌿<blockquote> Maintained by :【𝐃𝐞𝐯𝐞𝐥𝐨𝐩𝐞𝐫_𝐁𝐨𝐲™(𝓐𝓷𝓴𝓲𝓽_𝓜𝓮𝓮𝓷𝓪😝)】</blockquote>",
    reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ CANCEL", callback_data="broadcast_cancel#groups")]])
)

    elapsed = get_readable_time(time.time() - start_time)
    final_text = (
        f"{'❌ <b>Groups broadcast cancelled!</b>' if cancelled else '✅ <b>Group broadcast completed.</b>'}\n\n"
        f"⏱️ Completed in {elapsed}\n"
        f"👥 Total Groups: <code>{total_chats}</code>\n"
        f"📬 Success: <code>{success}</code>\n"
        f"❌ Failed: <code>{failed}</code>\n\n"
        f"🌿<blockquote> Maintained by :【𝐃𝐞𝐯𝐞𝐥𝐨𝐩𝐞𝐫_𝐁𝐨𝐲™(𝓐𝓷𝓴𝓲𝓽_𝓜𝓮𝓮𝓷𝓪😝)】</blockquote>"
    )
    await status_msg.edit(final_text)

# ----------------- Delete last broadcast -----------------
#This feature is developed by 【𝐃𝐞𝐯𝐞𝐥𝐨𝐩𝐞𝐫_𝐁𝐨𝐲™(𝓐𝓷𝓴𝓲𝓽_𝓜𝓮𝓮𝓷𝓪😝)】
# Don't remove the my credit please 🙏
@Client.on_message(filters.command("del_broadcast") & filters.user(ADMINS))
async def del_last_user_broadcast(bot, message):
    if not temp.LAST_USER_BROADCAST:
        return await message.reply("⚠️ No user broadcast messages to delete.")
    count = 0
    for msg in temp.LAST_USER_BROADCAST:
        try:
            await msg.delete()
            count += 1
        except:
            pass
    temp.LAST_USER_BROADCAST.clear()
    await message.reply(f"🗑️ Deleted {count} user broadcast messages successfully.")

@Client.on_message(filters.command("del_grp_broadcast") & filters.user(ADMINS))
async def del_last_group_broadcast(bot, message):
    if not temp.LAST_GROUP_BROADCAST:
        return await message.reply("⚠️ No group broadcast messages to delete.")
    count = 0
    for msg in temp.LAST_GROUP_BROADCAST:
        try:
            await msg.delete()
            count += 1
        except:
            pass
    temp.LAST_GROUP_BROADCAST.clear()
    await message.reply(f"🗑️ Deleted {count} group broadcast messages successfully.")
#_________________________End of the broadcast code____________________#
##this code is developed by 【𝐃𝐞𝐯𝐞𝐥𝐨𝐩𝐞𝐫_𝐁𝐨𝐲™(𝓐𝓷𝓴𝓲𝓽_𝓜𝓮𝓮𝓷𝓪😝)】
# Don't remove the my credit please 🙏 Thanks 👍 
