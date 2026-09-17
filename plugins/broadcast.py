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
from info import DATABASE_URI

lock = asyncio.Lock()

# ---------------- MongoDB setup for persistent broadcast ----------------
_mongo_client = pymongo.MongoClient(DATABASE_URI)
broadcast_db = _mongo_client["broadcast_db"]
user_broadcast_collection = broadcast_db["user_broadcasts"]
group_broadcast_collection = broadcast_db["group_broadcasts"]

# ----------------- Runtime temp lists -----------------
temp.LAST_USER_BROADCAST = []
temp.LAST_GROUP_BROADCAST = []

# ----------------- Auto-delete -----------------
async def auto_delete(msg, delay, chat_id=None, message_id=None):
    """
    Broadcast message auto-delete.
    msg object ke alawa chat_id + message_id bhi pass karo —
    agar msg object stale ho jaye tab bhi delete kaam kare.
    """
    await asyncio.sleep(delay)
    try:
        await msg.delete()
    except Exception:
        if chat_id and message_id:
            try:
                await msg._client.delete_messages(chat_id, message_id)
            except Exception:
                pass
    for lst in (temp.LAST_USER_BROADCAST, temp.LAST_GROUP_BROADCAST):
        try:
            lst.remove(msg)
        except ValueError:
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

# ----------------- Send message helper (Optimized for Formatting, Pin & Spoiler) -----------------
async def send_message(bot, chat_id, reply_msg, pin=False):
    try:
        # Basic copy arguments
        copy_args = {
            "chat_id": chat_id,
            "reply_markup": reply_msg.reply_markup
        }
        
        # Check agar message me media hai aur uspar spoiler laga hai
        if reply_msg.media and getattr(reply_msg, "has_media_spoiler", False):
            copy_args["has_spoiler"] = True

        # Copy command with dynamic arguments
        sent = await reply_msg.copy(**copy_args)

        if pin:
            try:
                # Group aur User PM ke liye alag pin logic
                if str(chat_id).startswith("-"):
                    await bot.pin_chat_message(
                        chat_id=chat_id,
                        message_id=sent.id,
                        disable_notification=True
                    )
                else:
                    # User PM me both_sides=True required hota hai
                    await bot.pin_chat_message(
                        chat_id=chat_id,
                        message_id=sent.id,
                        disable_notification=True,
                        both_sides=True
                    )
            except Exception:
                pass
        return sent, "Success"

    except Exception as e:
        err = str(e).lower()
        if "blocked" in err or "user_is_blocked" in err:
            return None, "Blocked"
        elif "chat not found" in err or "deleted" in err:
            return None, "Deleted"
        else:
            return None, "Error"



# ----------------- User Broadcast -----------------
@Client.on_message(filters.command("broadcast") & filters.user(ADMINS) & filters.reply)
async def broadcast_users(bot, message):
    if lock.locked():
        return await message.reply("⚠️ Another broadcast is in progress. Please wait...")

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
        if sent_msg:
            user_broadcast_collection.insert_one({
                "user_id": int(user["id"]),
                "message_id": sent_msg.id,
                "timestamp": datetime.now()
            })
        if sent_msg and auto_delete_time > 0:
            asyncio.create_task(auto_delete(sent_msg, auto_delete_time, int(user["id"]), sent_msg.id))
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
            await asyncio.sleep(0.1)

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

# ----------------- Delete all user broadcasts (Optimized & Non-Freezing) -----------------
@Client.on_message(filters.command("del_broadcast") & filters.user(ADMINS))
async def del_all_user_broadcast(bot, message):
    records = list(user_broadcast_collection.find({}))
    total_records = len(records)
    
    if not records:
        return await message.reply("⚠️ No user broadcast messages to delete.")

    status_msg = await message.reply(f"🗑️ <b>Starting deletion of {total_records} messages...</b>")
    
    deleted_count = 0
    failed_count = 0
    batch_size = 50

    for i in range(0, total_records, batch_size):
        batch = records[i:i + batch_size]
        for record in batch:
            try:
                await bot.delete_messages(record["user_id"], record["message_id"])
                deleted_count += 1
            except Exception:
                failed_count += 1
        
        # Prevent Bot Freeze
        await asyncio.sleep(0.1)
        
        # Update progress UI
        done = i + len(batch)
        if done % 100 == 0 or done == total_records:
            await status_msg.edit(
                f"🗑️ <b>User Deletion Progress:</b>\n\n"
                f"🔢 Total: <code>{total_records}</code>\n"
                f"✅ Deleted: <code>{deleted_count}</code>\n"
                f"⛔ Failed/Blocked: <code>{failed_count}</code>"
            )

    # Free up DB storage
    user_broadcast_collection.delete_many({})
    temp.LAST_USER_BROADCAST.clear()
    
    await status_msg.edit(
        f"✅ <b>All user broadcasts deleted successfully!</b>\n\n"
        f"🗑️ Deleted from Chat: <code>{deleted_count}</code>\n"
        f"⛔ Blocked/Failed (Skipped): <code>{failed_count}</code>\n"
        f"💾 DB Storage: <b>Cleared 100%</b>"
    )

# ----------------- Group Broadcast -----------------
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
                "message_id": sent_msg.id,
                "timestamp": datetime.now()
            })
        if sent_msg and auto_delete_time > 0:
            asyncio.create_task(auto_delete(sent_msg, auto_delete_time, int(chat["id"]), sent_msg.id))
        return result

    all_chats = [chat async for chat in chats]
    total_chats = len(all_chats)

    async with lock:
        batch_size = 50
        for i in range(0, total_chats, batch_size):
            if temp.B_GROUPS_CANCEL:
                temp.B_GROUPS_CANCEL = False
                cancelled = True
                break

            batch = all_chats[i:i + batch_size]
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

# ----------------- Delete all group broadcasts (Optimized & Non-Freezing) -----------------
@Client.on_message(filters.command("del_grp_broadcast") & filters.user(ADMINS))
async def del_all_group_broadcast(bot, message):
    records = list(group_broadcast_collection.find({}))
    total_records = len(records)
    
    if not records:
        return await message.reply("⚠️ No group broadcast messages to delete.")

    status_msg = await message.reply(f"🗑️ <b>Starting deletion of {total_records} group messages...</b>")
    
    deleted_count = 0
    failed_count = 0
    batch_size = 50

    for i in range(0, total_records, batch_size):
        batch = records[i:i + batch_size]
        for record in batch:
            try:
                await bot.delete_messages(record["group_id"], record["message_id"])
                deleted_count += 1
            except Exception:
                failed_count += 1
        
        # Prevent Bot Freeze
        await asyncio.sleep(0.1)
        
        # Update progress UI
        done = i + len(batch)
        if done % 100 == 0 or done == total_records:
            await status_msg.edit(
                f"🗑️ <b>Group Deletion Progress:</b>\n\n"
                f"🔢 Total: <code>{total_records}</code>\n"
                f"✅ Deleted: <code>{deleted_count}</code>\n"
                f"⛔ Failed: <code>{failed_count}</code>"
            )

    # Free up DB storage
    group_broadcast_collection.delete_many({})
    temp.LAST_GROUP_BROADCAST.clear()
    
    await status_msg.edit(
        f"✅ <b>All group broadcasts deleted successfully!</b>\n\n"
        f"🗑️ Deleted from Groups: <code>{deleted_count}</code>\n"
        f"⛔ Failed/Missing (Skipped): <code>{failed_count}</code>\n"
        f"💾 DB Storage: <b>Cleared 100%</b>"
    )
