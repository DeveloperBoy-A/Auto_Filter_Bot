# ---------------- Optimized Persistent Broadcast Bot (FULL FIXED) ----------------
# Developed by 【𝐃𝐞𝐯𝐞𝐥𝐨𝐩𝐞𝐫_𝐁𝐨𝐲™】

import time
import asyncio
import logging
import pymongo
from datetime import datetime
from pyrogram import Client, filters
from pyrogram.types import InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup
from pyrogram.errors import FloodWait, UserIsBlocked, InputUserDeactivated, PeerIdInvalid

from database.users_chats_db import db  # Ensure this path is correct
from info import ADMINS                 # Ensure ADMINS list is in info.py
from utils import temp, get_readable_time

# Logging setup
logging.basicConfig(level=logging.ERROR)

lock = asyncio.Lock()

# ---------------- MongoDB setup ----------------
# MongoDB client (use your URI)
client = pymongo.MongoClient("mongodb://localhost:27017/") 
broadcast_db = client["broadcast_db"]
user_broadcast_collection = broadcast_db["user_broadcasts"]
group_broadcast_collection = broadcast_db["group_broadcasts"]

# --- Runtime Cancel Flags ---
if not hasattr(temp, 'B_USERS_CANCEL'): temp.B_USERS_CANCEL = False
if not hasattr(temp, 'B_GROUPS_CANCEL'): temp.B_GROUPS_CANCEL = False

# ----------------- Helpers -----------------

async def auto_delete(msg, delay):
    """दिए गए समय के बाद मैसेज को डिलीट करता है"""
    try:
        await asyncio.sleep(delay)
        await msg.delete()
    except:
        pass

async def send_message(bot, chat_id, reply_msg, pin=False):
    """यूनिवर्सल सेंडर जो FloodWait और Errors को हैंडल करता है"""
    try:
        # copy_message सबसे सुरक्षित तरीका है (Text/Media/Polls सब चलता है)
        sent = await bot.copy_message(
            chat_id=int(chat_id),
            from_chat_id=reply_msg.chat.id,
            message_id=reply_msg.id,
            reply_markup=reply_msg.reply_markup
        )
        if pin:
            try:
                await sent.pin(disable_notification=True)
            except:
                pass
        return sent, "Success"
    except FloodWait as e:
        await asyncio.sleep(e.value)
        return await send_message(bot, chat_id, reply_msg, pin)
    except (UserIsBlocked, InputUserDeactivated):
        return None, "Blocked"
    except (PeerIdInvalid, Exception):
        return None, "Error"

# ----------------- Cancel Callback -----------------

@Client.on_callback_query(filters.regex(r'^broadcast_cancel'))
async def broadcast_cancel(bot, query):
    _, target = query.data.split("#", 1)
    if target == 'users':
        temp.B_USERS_CANCEL = True
        await query.answer("🛑 Users broadcast cancel ho raha hai...", show_alert=True)
    elif target == 'groups':
        temp.B_GROUPS_CANCEL = True
        await query.answer("🛑 Groups broadcast cancel ho raha hai...", show_alert=True)

# ----------------- User Broadcast -----------------

@Client.on_message(filters.command("broadcast") & filters.user(ADMINS) & filters.reply)
async def broadcast_users(bot, message):
    if lock.locked():
        return await message.reply("⚠️ एक ब्रॉडकास्ट पहले से ही चल रहा है।")

    # Options (Pin & Auto-Delete)
    try:
        ask_pin = await message.reply("<b>क्या आप मैसेज पिन करना चाहते हैं?</b>", 
                                     reply_markup=ReplyKeyboardMarkup([["Yes", "No"]], one_time_keyboard=True, resize_keyboard=True))
        res_pin = await bot.listen(chat_id=message.chat.id, user_id=message.from_user.id, timeout=60)
        is_pin = res_pin.text == "Yes"
        await ask_pin.delete()

        ask_del = await message.reply("<b>Auto-delete समय (सेकंड में, 0 यानी नो डिलीट):</b>")
        res_del = await bot.listen(chat_id=message.chat.id, user_id=message.from_user.id, timeout=60)
        auto_del_time = int(res_del.text) if res_del.text.isdigit() else 0
        await ask_del.delete()
    except Exception:
        return await message.reply("❌ प्रक्रिया रद्द कर दी गई (Timeout)।")

    # Start fetching users
    all_users = [user async for user in await db.get_all_users()]
    total_users = len(all_users)
    status_msg = await message.reply_text("📤 **ब्रॉडकास्ट शुरू हो रहा है...**")
    
    success = blocked = failed = done = 0
    start_time = time.time()

    async with lock:
        # Batch processing (20 users per batch)
        for i in range(0, total_users, 20):
            if temp.B_USERS_CANCEL:
                temp.B_USERS_CANCEL = False
                break
            
            batch = all_users[i:i+20]
            for user in batch:
                sent, result = await send_message(bot, user["id"], message.reply_to_message, is_pin)
                if result == "Success":
                    success += 1
                    # MongoDB में रिकॉर्ड सेव करें
                    user_broadcast_collection.insert_one({
                        "user_id": int(user["id"]), 
                        "message_id": sent.message_id, 
                        "time": datetime.now()
                    })
                    if auto_del_time > 0:
                        asyncio.create_task(auto_delete(sent, auto_del_time))
                elif result == "Blocked":
                    blocked += 1
                else:
                    failed += 1
                done += 1

            # UI Update (यह अब लाइव प्रोग्रेस दिखाएगा)
            elapsed = get_readable_time(time.time() - start_time)
            try:
                await status_msg.edit(
                    f"📣 **ब्रॉडकास्ट प्रगति:**\n\n"
                    f"👥 कुल: `{total_users}` | पूर्ण: `{done}`\n"
                    f"✅ सफल: `{success}`\n"
                    f"🚫 ब्लॉक: `{blocked}`\n"
                    f"❌ फेल: `{failed}`\n"
                    f"⏱️ समय: {elapsed}",
                    reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ CANCEL", callback_data="broadcast_cancel#users")]])
                )
            except:
                pass
            await asyncio.sleep(1) # Telegram API को शांत रखने के लिए

    final_time = get_readable_time(time.time() - start_time)
    await status_msg.edit(f"✅ **ब्रॉडकास्ट पूरा हुआ!**\n\n⏱️ समय: {final_time}\n📬 सफल: {success}\n🚫 ब्लॉक: {blocked}")

# ----------------- Delete User Broadcast -----------------

@Client.on_message(filters.command("del_broadcast") & filters.user(ADMINS))
async def del_all_user_broadcast(bot, message):
    total = user_broadcast_collection.count_documents({})
    if total == 0:
        return await message.reply("⚠️ डेटाबेस में कोई ब्रॉडकास्ट रिकॉर्ड नहीं मिला।")
    
    status = await message.reply(f"🗑️ `{total}` मैसेज डिलीट करना शुरू कर रहा हूँ...")
    count = 0
    
    # Cursor का उपयोग (Bot Freeze नहीं होगा)
    cursor = user_broadcast_collection.find({})
    for record in cursor:
        try:
            await bot.delete_message(record["user_id"], record["message_id"])
            count += 1
            if count % 20 == 0:
                await status.edit(f"🗑️ प्रगति: `{count}/{total}` डिलीट हुए...")
        except FloodWait as e:
            await asyncio.sleep(e.value)
        except:
            pass
            
    user_broadcast_collection.delete_many({})
    await status.edit(f"✅ सफलतापूर्वक {count} मैसेज डिलीट कर दिए गए।")

# ----------------- Group Broadcast -----------------

@Client.on_message(filters.command("grp_broadcast") & filters.user(ADMINS) & filters.reply)
async def broadcast_group(bot, message):
    if lock.locked(): return await message.reply("⚠️ व्यस्त है...")
    
    all_chats = [chat async for chat in await db.get_all_chats()]
    total_chats = len(all_chats)
    status_msg = await message.reply_text("📤 **ग्रुप ब्रॉडकास्ट शुरू हो रहा है...**")
    
    success = failed = done = 0
    start_time = time.time()

    async with lock:
        for i in range(0, total_chats, 15):
            if temp.B_GROUPS_CANCEL:
                temp.B_GROUPS_CANCEL = False
                break
            
            batch = all_chats[i:i+15]
            for chat in batch:
                sent, result = await send_message(bot, chat["id"], message.reply_to_message)
                if result == "Success":
                    success += 1
                    group_broadcast_collection.insert_one({
                        "group_id": int(chat["id"]), 
                        "message_id": sent.message_id, 
                        "time": datetime.now()
                    })
                else:
                    failed += 1
                done += 1

            await status_msg.edit(
                f"📣 **ग्रुप प्रगति:**\n\n👥 कुल: `{total_chats}`\n✅ पूर्ण: `{done}`\n📬 सफल: `{success}`", 
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ CANCEL", callback_data="broadcast_cancel#groups")]])
            )
            await asyncio.sleep(1.5)

    await status_msg.edit(f"✅ ग्रुप ब्रॉडकास्ट खत्म!\nसफल: {success}")

# ----------------- Delete Group Broadcast -----------------

@Client.on_message(filters.command("del_grp_broadcast") & filters.user(ADMINS))
async def del_all_group_broadcast(bot, message):
    total = group_broadcast_collection.count_documents({})
    if total == 0: return await message.reply("⚠️ कोई रिकॉर्ड नहीं!")
    
    status = await message.reply(f"🗑️ `{total}` ग्रुप मैसेज डिलीट हो रहे हैं...")
    count = 0
    cursor = group_broadcast_collection.find({})
    for record in cursor:
        try:
            await bot.delete_message(record["group_id"], record["message_id"])
            count += 1
            if count % 20 == 0:
                await status.edit(f"🗑️ प्रगति: `{count}/{total}`")
        except FloodWait as e:
            await asyncio.sleep(e.value)
        except:
            pass
    
    group_broadcast_collection.delete_many({})
    await status.edit("✅ सभी ग्रुप मैसेज डिलीट हो गए!")
