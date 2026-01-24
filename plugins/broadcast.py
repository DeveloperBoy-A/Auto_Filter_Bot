# ---------------- Optimized Persistent Broadcast Bot ----------------
# Developed by 【𝐃𝐞𝐯𝐞𝐥𝐨𝐩𝐞𝐫_𝐁𝐨𝐲™(𝓐𝓷𝓴𝓲𝓽_𝓜𝐞𝐞𝐧𝐚😝)】
# Don't remove credit

import time
import asyncio
from pyrogram import Client, filters
from pyrogram.types import InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup
from database.users_chats_db import db
from info import ADMINS
from utils import temp, get_readable_time
import pymongo
from datetime import datetime

lock = asyncio.Lock()

# ---------------- MongoDB setup for persistent broadcast ----------------
client = pymongo.MongoClient("mongodb://localhost:27017/")  # Change URI as needed
broadcast_db = client["broadcast_db"]
user_broadcast_collection = broadcast_db["user_broadcasts"]
group_broadcast_collection = broadcast_db["group_broadcasts"]

# ----------------- Runtime temp lists -----------------
temp.LAST_USER_BROADCAST = []
temp.LAST_GROUP_BROADCAST = []

# ----------------- Auto-delete -----------------
async def auto_delete(msg, delay):
    try:
        await asyncio.sleep(delay)
        await msg.delete()
        if msg in temp.LAST_USER_BROADCAST:
            temp.LAST_USER_BROADCAST.remove(msg)
        if msg in temp.LAST_GROUP_BROADCAST:
            temp.LAST_GROUP_BROADCAST.remove(msg)
    except:
        pass

# ----------------- Track messages in runtime -----------------
async def track_message(sent_msg, target="user"):
    if sent_msg:
        if target == "user":
            temp.LAST_USER_BROADCAST.append(sent_msg)
        elif target == "group":
            temp.LAST_GROUP_BROADCAST.append(sent_msg)

# ----------------- Cancel Callback -----------------
@Client.on_callback_query(filters.regex(r'^broadcast_cancel'))
async def broadcast_cancel(bot, query):
    _, target = query.data.split("#", 1)
    if target == 'users':
        temp.B_USERS_CANCEL = True
        await query.message.edit("🛑 Trying to cancel users broadcasting...")
    elif target == 'groups':
        temp.B_GROUPS_CANCEL = True
        await query.message.edit("🛑 Trying to cancel groups broadcasting...")

# ----------------- Send message helper -----------------
async def send_message(bot, chat_id, reply_msg, pin=False):
    try:
        reply_markup = reply_msg.reply_markup
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

# ----------------- User Broadcast (Batch + Async + Persistent) -----------------
@Client.on_message(filters.command("broadcast") & filters.user(ADMINS) & filters.reply)
async def broadcast_users(bot, message):
    if lock.locked():
        return await message.reply("⚠️ Another broadcast is in progress. Please wait...")

    # Pin option
    ask = await message.reply(
        "<b>Do you want to pin this message in users?</b>",
        reply_markup=ReplyKeyboardMarkup([["Yes", "No"]], one_time_keyboard=True, resize_keyboard=True)
    )
    try:
        user_response = await bot.listen(chat_id=message.chat.id, user_id=message.from_user.id, timeout=60)
    except asyncio.TimeoutError:
        await ask.delete()
        return await message.reply("❌ Timed out. Broadcast cancelled.")
    await ask.delete()
    if user_response.text not in ("Yes", "No"):
        return await message.reply("❌ Invalid input. Broadcast cancelled.")
    is_pin = user_response.text == "Yes"

    # Auto-delete time
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
    status_msg = await message.reply_text("📤 <b>Broadcasting your message...</b>")

    success = blocked = deleted = failed = 0
    start_time = time.time()
    cancelled = False

    async def send(user):
        sent_msg, result = await send_message(bot, int(user["id"]), b_msg, is_pin)
        await track_message(sent_msg, "user")
        # MongoDB storage
        if sent_msg:
            user_broadcast_collection.insert_one({
                "user_id": int(user["id"]),
                "message_id": sent_msg.message_id,
                "timestamp": datetime.now()
            })
        if sent_msg and auto_delete_time > 0:
            asyncio.create_task(auto_delete(sent_msg, auto_delete_time))
        return result

    async with lock:
        batch_size = 50
        for i in range(0, total_users, batch_size):
            if temp.B_USERS_CANCEL:
                temp.B_USERS_CANCEL = False
                cancelled = True
                break

            batch = users[i:i + batch_size]
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
            await asyncio.sleep(0.1)  # small delay to avoid flood

    elapsed = get_readable_time(time.time() - start_time)
    final_status = (
        f"{'❌ <b>Broadcast Cancelled.</b>' if cancelled else '✅ <b>Broadcast Completed.</b>'}\n\n"
        f"🕒 Time: {elapsed}\n"
        f"👥 Total: <code>{total_users}</code>\n"
        f"📬 Success: <code>{success}</code>\n"
        f"⛔ Blocked: <code>{blocked}</code>\n"
        f"🗑️ Deleted: <code>{deleted}</code>\n"
        f"❌ Failed: <code>{failed}</code>\n\n"
        f"🌿<blockquote> Maintained by :【𝐃𝐞𝐯𝐞𝐥𝐨𝐩𝐞𝐫_𝐁𝐨𝐲™】</blockquote>"
    )
    await status_msg.edit(final_status)

# ----------------- Delete all user broadcasts (Persistent) -----------------
@Client.on_message(filters.command("del_broadcast") & filters.user(ADMINS))
async def del_all_user_broadcast(bot, message):
    records = list(user_broadcast_collection.find({}))
    if not records:
        return await message.reply("⚠️ No user broadcast messages to delete.")

    count = 0
    for record in records:
        try:
            await bot.delete_message(record["user_id"], record["message_id"])
            count += 1
        except:
            pass
    user_broadcast_collection.delete_many({})
    temp.LAST_USER_BROADCAST.clear()
    await message.reply(f"🗑️ Deleted {count} user broadcast messages successfully.")

# ----------------- Group Broadcast (Batch + Async + Persistent) -----------------
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

    async def send(chat):
        sent_msg, result = await send_message(bot, int(chat["id"]), b_msg, is_pin)
        await track_message(sent_msg, "group")
        if sent_msg:
            group_broadcast_collection.insert_one({
                "group_id": int(chat["id"]),
                "message_id": sent_msg.message_id,
                "timestamp": datetime.now()
            })
        if sent_msg and auto_delete_time > 0:
            asyncio.create_task(auto_delete(sent_msg, auto_delete_time))
        return result

    async with lock:
        batch_size = 50
        async for i in range(0, total_chats, batch_size):
            batch = []
            async for chat in chats:
                batch.append(chat)
                if len(batch) == batch_size:
                    break
            if temp.B_GROUPS_CANCEL:
                temp.B_GROUPS_CANCEL = False
                cancelled = True
                break

            results = await asyncio.gather(*[send(chat) for chat in batch])
            for res in results:
                if res == "Success": success += 1
                else: failed += 1
            done += len(batch)

            elapsed = get_readable_time(time.time() - start_time)
            await status_msg.edit(
                f"📣 <b>Group broadcast progress:</b>\n\n"
                f"👥 Total Groups: <code>{total_chats}</code>\n"
                f"✅ Completed: <code>{done}</code>\n"
                f"📬 Success: <code>{success}</code>\n"
                f"❌ Failed: <code>{failed}</code>\n"
                f"⏱️ Time: {elapsed}",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ CANCEL", callback_data="broadcast_cancel#groups")]])
            )
            await asyncio.sleep(0.1)

    elapsed = get_readable_time(time.time() - start_time)
    final_text = (
        f"{'❌ <b>Groups broadcast cancelled!</b>' if cancelled else '✅ <b>Group broadcast completed.</b>'}\n\n"
        f"⏱️ Completed in {elapsed}\n"
        f"👥 Total Groups: <code>{total_chats}</code>\n"
        f"📬 Success: <code>{success}</code>\n"
        f"❌ Failed: <code>{failed}</code>\n\n"
        f"🌿<blockquote> Maintained by :【𝐃𝐞𝐯𝐞𝐥𝐨𝐩𝐞𝐫_𝐁𝐨𝐲™】</blockquote>"
    )
    await status_msg.edit(final_text)

# ----------------- Delete all group broadcasts (Persistent) -----------------
@Client.on_message(filters.command("del_grp_broadcast") & filters.user(ADMINS))
async def del_all_group_broadcast(bot, message):
    records = list(group_broadcast_collection.find({}))
    if not records:
        return await message.reply("⚠️ No group broadcast messages to delete.")

    count = 0
    for record in records:
        try:
            await bot.delete_message(record["group_id"], record["message_id"])
            count += 1
        except:
            pass
    group_broadcast_collection.delete_many({})
    temp.LAST_GROUP_BROADCAST.clear()
    await message.reply(f"🗑️ Deleted {count} group broadcast messages successfully.")