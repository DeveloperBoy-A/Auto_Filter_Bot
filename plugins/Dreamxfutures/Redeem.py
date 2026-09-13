 
from datetime import timedelta, datetime
import pytz
import string
import random
from pyrogram import Client, filters
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from database.users_chats_db import db
from info import ADMINS, PREMIUM_LOGS
from utils import get_seconds, temp

# Bug fix: redeem codes used to live only in this in-memory dict, so every
# bot restart (crash / redeploy / update) wiped all pending codes and
# /redeem would always say "Invalid Redeem Code or Expired" even for
# codes generated minutes earlier. They are now stored persistently in
# MongoDB via db.add_redeem_code() / db.use_redeem_code(), so codes
# survive restarts and work correctly even across multiple bot workers.

def generate_code(length=10):
    letters_and_digits = string.ascii_letters + string.digits
    return ''.join(random.choice(letters_and_digits) for _ in range(length))

@Client.on_message(filters.command("add_redeem") & filters.user(ADMINS))
async def add_redeem_code(client, message):
    user_id = message.from_user.id
    if len(message.command) == 3:
        try:
            time = message.command[1]
            num_codes = int(message.command[2])
        except ValueError:
            await message.reply_text("Please provide a valid number of codes to generate.")
            return

        codes = []
        for _ in range(num_codes):
            code = generate_code()
            await db.add_redeem_code(code, time)
            codes.append(code)

        codes_text = '\n'.join(f"🎟 <code>{code}</code>" for code in codes)
        text = f"""🎉 <b><u>Gɪꜰᴛ Cᴏᴅᴇ Gᴇɴᴇʀᴀᴛᴇᴅ!</u></b> ✨

┌─────────────────────
│ 📦 <b>Tᴏᴛᴀʟ Cᴏᴅᴇꜱ</b> : <code>{num_codes}</code>
│ ⏳ <b>Vᴀʟɪᴅɪᴛʏ</b>    : <code>{time}</code>
└─────────────────────

{codes_text}

━━━━━━━━━━━━━━━━━━━━━━
📝 <b><u>Hᴏᴡ Tᴏ Rᴇᴅᴇᴇᴍ:</u></b>

1️⃣ Tap a code above to copy it instantly
2️⃣ Send it to me like this: <code>/redeem CODE</code>
3️⃣ Boom 💎 — Premium unlocked right away!
━━━━━━━━━━━━━━━━━━━━━━

⚠️ <i>Eᴀᴄʜ ᴄᴏᴅᴇ ᴡᴏʀᴋꜱ ᴏɴʟʏ ᴏɴᴄᴇ — sʜᴀʀᴇ ʀᴇꜱᴘᴏɴꜱɪʙʟʏ!</i> 🔥"""
        keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("🔑 Redeem Now 🔥", url=f"https://t.me/{temp.U_NAME}")]])
        await message.reply_text(text, reply_markup=keyboard)
    else:
        await message.reply_text("<b>♻ Usage:\n\n➩ <code>/add_redeem 1min 1</code>,\n➩ <code>/add_redeem 1hour 10</code>,\n➩ <code>/add_redeem 1day 5</code></b>")



@Client.on_message(filters.command("redeem"))
async def redeem_code(client, message):
    user_id = message.from_user.id
    if len(message.command) == 2:
        redeem_code = message.command[1]

        # Read-only lookup first — does NOT consume the code yet.
        code_data = await db.get_redeem_code(redeem_code)

        if not code_data:
            await message.reply_text(
                "❌ <b>Iɴᴠᴀʟɪᴅ Rᴇᴅᴇᴇᴍ Cᴏᴅᴇ!</b>\n\n"
                "Yᴇ ᴄᴏᴅᴇ ᴇxɪsᴛ ɴᴀʜɪ ᴋᴀʀᴛᴀ ʏᴀ ᴇxᴘɪʀᴇ ʜᴏ ᴄʜᴜᴋᴀ ʜᴀɪ.\n"
                "🔎 sᴘᴇʟʟɪɴɢ ᴄʜᴇᴄᴋ ᴋᴀʀᴋᴇ ᴅᴏʙᴀᴀʀᴀ ᴛʀʏ ᴋᴀʀᴇɪɴ."
            )
            return

        if code_data.get("used"):
            if code_data.get("used_by") == user_id:
                await message.reply_text(
                    "♻️ <b>Aᴀᴘ Pᴇʜʟᴇ Hɪ Yᴇ Cᴏᴅᴇ Rᴇᴅᴇᴇᴍ Kᴀʀ Cʜᴜᴋᴇ Hᴏ!</b>\n\n"
                    f"🎟 <b>Cᴏᴅᴇ:</b> <code>{redeem_code}</code>\n\n"
                    "<i>Hᴀʀ ᴄᴏᴅᴇ sɪʀғ 1 ʙᴀᴀʀ ʜɪ ɪsᴛᴇᴍᴀʟ ʜᴏ sᴀᴋᴛᴀ ʜᴀɪ, ᴀᴀᴘ ᴋᴀ ᴘʀᴇᴍɪᴜᴍ ᴘᴇʜʟᴇ ʜɪ ᴀᴄᴛɪᴠᴀᴛᴇ ʜᴏ ᴄʜᴜᴋᴀ ʜᴀɪ.</i>\n\n"
                    "💎 ɴᴀʏᴀ ᴄᴏᴅᴇ ᴄʜᴀʜɪʏᴇ? Oᴡɴᴇʀ sᴇ sᴀᴍᴘᴀʀᴋ ᴋᴀʀᴇɪɴ."
                )
            else:
                await message.reply_text(
                    "🚫 <b>Yᴇ Cᴏᴅᴇ Pᴇʜʟᴇ Hɪ Isᴛᴇᴍᴀʟ Hᴏ Cʜᴜᴋᴀ Hᴀɪ!</b>\n\n"
                    f"🎟 <b>Cᴏᴅᴇ:</b> <code>{redeem_code}</code>\n\n"
                    "Yᴇ ɢɪғᴛ ᴄᴏᴅᴇ ᴋɪsɪ ᴀᴜʀ ᴜsᴇʀ ᴅᴡᴀʀᴀ ᴘᴇʜʟᴇ ʜɪ ʀᴇᴅᴇᴇᴍ ᴋɪʏᴀ ᴊᴀᴀ ᴄʜᴜᴋᴀ ʜᴀɪ."
                )
            return

        try:
            time = code_data["duration"]
            try:
                user = await client.get_users(user_id)
            except Exception:
                user = None
            try:
                seconds = await get_seconds(time)
            except Exception:
                await message.reply_text("Invalid time format in redeem code.")
                return
            if seconds > 0:
                data = await db.get_user(user_id)
                current_expiry = data.get("expiry_time") if data else None
                now_aware = datetime.now(pytz.utc)

                if current_expiry:
                    current_expiry = current_expiry.replace(tzinfo=pytz.utc) if current_expiry.tzinfo is None else current_expiry
                if current_expiry and current_expiry > now_aware:
                    expiry_str_in_ist = current_expiry.astimezone(pytz.timezone("Asia/Kolkata")).strftime("%d-%m-%Y\n⏱️ Expiry Time: %I:%M:%S %p")
                    await message.reply_text(
                        f"🚫 <b>Yᴏᴜ ᴀʟʀᴇᴀᴅʏ ʜᴀᴠᴇ ᴀᴄᴛɪᴠᴇ ᴘʀᴇᴍɪᴜᴍ ᴀᴄᴄᴇss!</b>\n\n"
                        f"⏳ <b>Cᴜʀʀᴇɴᴛ Pʀᴇᴍɪᴜᴍ Exᴘɪʀʏ:</b> {expiry_str_in_ist}\n\n"
                        f"<i>Yᴏᴜʀ ᴄᴏᴅᴇ ɪs sᴛɪʟʟ ᴠᴀʟɪᴅ ᴀɴᴅ ᴜɴᴜsᴇᴅ — ᴄᴏᴍᴇ ʙᴀᴄᴋ ᴀɴᴅ ʀᴇᴅᴇᴇᴍ ɪᴛ ᴏɴᴄᴇ ʏᴏᴜʀ ᴄᴜʀʀᴇɴᴛ ᴘʀᴇᴍɪᴜᴍ ᴇxᴘɪʀᴇs.</i>\n\n"
                        f"<b>Tʜᴀɴᴋ ʏᴏᴜ ғᴏʀ ᴜsɪɴɢ ᴏᴜʀ sᴇʀᴠɪᴄᴇ! 🔥</b>",
                        disable_web_page_preview=True
                    )
                    return

                marked = await db.mark_redeem_code_used(redeem_code, user_id)
                if not marked:
                    await message.reply_text(
                        "🚫 <b>Yᴇ Cᴏᴅᴇ Pᴇʜʟᴇ Hɪ Isᴛᴇᴍᴀʟ Hᴏ Cʜᴜᴋᴀ Hᴀɪ!</b>\n\n"
                        f"🎟 <b>Cᴏᴅᴇ:</b> <code>{redeem_code}</code>\n\n"
                        "Kɪsɪ ᴀᴜʀ ɴᴇ ᴀᴀᴘsᴇ ᴘᴇʜʟᴇ ʏᴇ ᴄᴏᴅᴇ ʀᴇᴅᴇᴇᴍ ᴋᴀʀ ʟɪʏᴀ."
                    )
                    return

                expiry_time = now_aware + timedelta(seconds=seconds)
                user_data = {"id": user_id, "expiry_time": expiry_time}
                await db.update_user(user_data)

                expiry_str_in_ist = expiry_time.astimezone(pytz.timezone("Asia/Kolkata")).strftime("%d-%m-%Y\n⏱️ Expiry Time: %I:%M:%S %p")
                user_mention = user.mention if user else f"<code>{user_id}</code>"
                await message.reply_text(
                    f"🎉✨ <b>Pʀᴇᴍɪᴜᴍ Aᴄᴛɪᴠᴀᴛᴇᴅ Sᴜᴄᴄᴇssғᴜʟʟʏ!</b> ✨🎉\n\n"
                    f"👤 <b>User:</b> {user_mention}\n"
                    f"⚡ <b>User ID:</b> <code>{user_id}</code>\n"
                    f"🎟 <b>Code Used:</b> <code>{redeem_code}</code>\n"
                    f"⏳ <b>Premium Access Duration:</b> <code>{time}</code>\n"
                    f"⌛️ <b>Expiry Date:</b> {expiry_str_in_ist}\n\n"
                    f"💎 <i>Enjoy all premium perks — happy downloading!</i> 🚀",
                    disable_web_page_preview=True
                )
                log_message = f"""
                    #Redeem_Premium 🔓

                    👤 <b>User:</b> {user_mention}
                    ⚡ <b>User ID:</b> <code>{user_id}</code>
                    🎟 <b>Code Used:</b> <code>{redeem_code}</code>
                    ⏳ <b>Premium Access Duration:</b> <code>{time}</code>
                    ⌛️ <b>Expiry Date:</b> {expiry_str_in_ist}

                    🎉 Premium activated successfully! 🚀
                    """
                await client.send_message(
                    PREMIUM_LOGS,
                    text=log_message,
                    disable_web_page_preview=True
                )
            else:
                await message.reply_text("Invalid time format in redeem code.")
        except Exception as e:
            await message.reply_text(f"An error occurred while redeeming the code: {e}")
    else:
        await message.reply_text("Usage: /redeem <code>")
