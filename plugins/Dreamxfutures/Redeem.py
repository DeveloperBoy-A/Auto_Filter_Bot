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


def display_name(user_id, name=None):
    return name if name else f"<code>{user_id}</code>"


async def build_codes_list_text(num_codes, time, codes):
    """Same exact message as before — just each code line now shows
    ✅ Redeemed by <name> once used, and the info box shows live
    Redeemed / Available counts."""
    redeemed = sum(1 for c in codes if c.get("used"))
    available = num_codes - redeemed

    lines = []
    for i, c in enumerate(codes, 1):
        if c.get("used"):
            who = display_name(c.get("used_by"), c.get("used_by_name"))
            line = f"{i}. 🎁 <b>Pʀᴇᴍɪᴜᴍ Cᴏᴅᴇ :</b>\n ╰ 👉 <s><code>/redeem {c['code']}</code></s> ✅ <i>Redeemed by</i> {who}"
        else:
            line = f"{i}. 🎁 <b>Pʀᴇᴍɪᴜᴍ Cᴏᴅᴇ :</b>\n ╰ 👉 <code>/redeem {c['code']}</code>"
        lines.append(line)
    codes_text = '\n\n'.join(lines)

    return f"""🎉 <b><u>Pʀᴇᴍɪᴜᴍ Gɪꜰᴛ Cᴏᴅᴇꜱ Gᴇɴᴇʀᴀᴛᴇᴅ!</u></b> ✨

Ye codes users ko <b>Premium Benefits</b> dene ke liye hain, jaise:
🚀 <i>Unlimited Downloads, Online Streaming & No Restrictions!</i>
┌──────────────────────
│ 📦 <b>Tᴏᴛᴀʟ Cᴏᴅᴇꜱ</b> : <code>{num_codes}</code>
│ ⏳ <b>Vᴀʟɪᴅɪᴛʏ</b>    : <code>{time}</code>
│ ✅ <b>Rᴇᴅᴇᴇᴍᴇᴅ</b>    : <code>{redeemed}</code>
│ 🟢 <b>Aᴠᴀɪʟᴀʙʟᴇ</b>   : <code>{available}</code>
└──────────────────────
👇 <b>Tᴀᴘ ᴀɴʏ ᴄᴏᴍᴍᴀɴᴅ ʙᴇʟᴏᴡ ᴛᴏ ᴄᴏᴘʏ ɪᴛ!</b> 👇

{codes_text}
━━━━━━━━━━━━━━━━━━━━━━
📝 <b><u>Hᴏᴡ Tᴏ Rᴇᴅᴇᴇᴍ (Kaise Use Karein):</u></b>

1️⃣ Upar diye gaye kisi bhi code par click karein (wo turant copy ho jayega).
2️⃣ Sidhe bot me aakar paste karein aur send kar dein.
3️⃣ Boom 💎 — Aapke Premium benefits instantly unlock ho jayenge!
━━━━━━━━━━━━━━━━━━━━━━
 🏃‍♂️ <b>Fɪʀꜱᴛ Cᴏᴍᴇ, Fɪʀꜱᴛ Sᴇʀᴠᴇᴅ (Pᴇʜʟᴇ Aᴀᴏ, Pᴇʜʟᴇ Pᴀᴏ!)</b>
<i>Ye limited codes hain, jo jaldi claim karega, Premium usika hoga!</i>
⚠️ <i>Eᴀᴄʜ ᴄᴏᴅᴇ ᴡᴏʀᴋꜱ ᴏɴʟʏ ᴏɴᴄᴇ — sʜᴀʀᴇ ʀᴇꜱᴘᴏɴꜱɪʙʟʏ!</i> 🔥"""


async def refresh_batch_message(client, batch_id):
    """Called right after a code gets redeemed. Rebuilds the exact same
    /add_redeem message from fresh DB data and edits it in place, so the
    admin sees ✅ + redeemer name and updated counts automatically."""
    if not batch_id:
        return
    batch = await db.get_redeem_batch(batch_id)
    if not batch or not batch.get("chat_id") or not batch.get("message_id"):
        return
    codes = await db.get_codes_by_batch(batch_id)
    if not codes:
        return
    text = await build_codes_list_text(len(codes), batch.get("duration"), codes)
    keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("🔑 Redeem Now 🔥", url=f"https://t.me/{temp.U_NAME}")]])
    try:
        await client.edit_message_text(batch["chat_id"], batch["message_id"], text, reply_markup=keyboard)
    except Exception:
        # message deleted / not modified / too old to edit — ignore silently
        pass

@Client.on_message(filters.command("add_redeem") & filters.user(ADMINS))
async def add_redeem_code(client, message):
    user_id = message.from_user.id
    if len(message.command) == 3:
        try:
            time = message.command[1]
            num_codes = int(message.command[2])
        except ValueError:
            await message.reply_text("⚠️ <b>Bhai, sahi quantity daalo! (Please provide a valid number of codes to generate.)</b>")
            return

        batch_id = f"{user_id}_{int(datetime.now(pytz.utc).timestamp())}"
        await db.create_redeem_batch(batch_id, time)

        codes = []
        for _ in range(num_codes):
            code = generate_code()
            await db.add_redeem_code(code, time, batch_id=batch_id)
            codes.append({"code": code, "used": False, "used_by": None, "used_by_name": None})

        text = await build_codes_list_text(num_codes, time, codes)
        keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("🔑 Redeem Now 🔥", url=f"https://t.me/{temp.U_NAME}")]])
        sent = await message.reply_text(text, reply_markup=keyboard)

        # Save this message's location so it can be auto-edited (✅ + name,
        # live Redeemed/Available) every time one of its codes is redeemed.
        await db.set_redeem_batch_message(batch_id, sent.chat.id, sent.id)
    else:
        await message.reply_text(
            "⚠️ <b>Iɴᴠᴀʟɪᴅ Fᴏʀᴍᴀᴛ!</b>\n\n"
            "📝 <b><u>Cᴏʀʀᴇᴄᴛ Uꜱᴀɢᴇ:</u></b>\n"
            "➩ <code>/add_redeem [Time] [Quantity]</code>\n\n"
            "💡 <b>Exᴀᴍᴘʟᴇꜱ:</b>\n"
            "╰ 👉 <code>/add_redeem 1min 1</code>\n"
            "╰ 👉 <code>/add_redeem 1hour 10</code>\n"
            "╰ 👉 <code>/add_redeem 1day 5</code>\n\n"
            "<i>(Is command se aap Premium unlock karne wale special Gift Codes bana sakte ho)</i>"
        )



@Client.on_message(filters.command("redeem"))
async def redeem_code(client, message):
    user_id = message.from_user.id
    if len(message.command) == 2:
        redeem_code = message.command[1]

        # Read-only lookup first — does NOT consume the code yet.
        code_data = await db.get_redeem_code(redeem_code)

        if not code_data:
            await message.reply_text(
                "❌ <b><u>Iɴᴠᴀʟɪᴅ Pʀᴇᴍɪᴜᴍ Cᴏᴅᴇ!</u></b> ❌\n\n"
                "⚠️ Ye code exist nahi karta ya expire ho chuka hai.\n\n"
                "🔎 <i>Kripya code ki spelling check karke dobaara try karein.</i>"
            )
            return

        if code_data.get("used"):
            if code_data.get("used_by") == user_id:
                await message.reply_text(
                    f"♻️ <b><u>Aʟʀᴇᴀᴅʏ Rᴇᴅᴇᴇᴍᴇᴅ!</u></b> ♻️\n\n"
                    f"👤 <b>Aap pehle hi ye code use kar chuke ho!</b>\n"
                    f"🎟 <b>Cᴏᴅᴇ :</b> <code>{redeem_code}</code>\n\n"
                    f"📌 <i>Har Premium code sirf 1 baar hi istemal ho sakta hai, aapka Premium pehle hi activate ho chuka hai.</i>\n\n"
                    f"💎 <b>Naya code chahiye? Bot owner se sampark karein.</b>"
                )
            else:
                await message.reply_text(
                    f"🚫 <b><u>Cᴏᴅᴇ Aʟʀᴇᴀᴅʏ Uꜱᴇᴅ!</u></b> 🚫\n\n"
                    f"🎟 <b>Cᴏᴅᴇ :</b> <code>{redeem_code}</code>\n\n"
                    f"⚠️ <i>Ye Premium gift code kisi aur user dwara pehle hi redeem kiya jaa chuka hai.</i>\n\n"
                    f"😔 <b>Better luck next time!</b>"
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
                await message.reply_text("⚠️ <b>Error:</b> Invalid time format in redeem code.")
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
                        f"🛑 <b><u>Aᴄᴛɪᴠᴇ Pʀᴇᴍɪᴜᴍ Exɪꜱᴛꜱ!</u></b> 🛑\n\n"
                        f"Aapke paas pehle se hi Active Premium plan hai!\n\n"
                        f"⏳ <b>Cᴜʀʀᴇɴᴛ Exᴘɪʀʏ :</b>\n"
                        f"╰ 👉 {expiry_str_in_ist}\n\n"
                        f"🎁 <i>Aapka naya code abhi bhi valid aur safe hai. Apna current plan expire hone ke baad ise wapas aakar redeem karein!</i>\n\n"
                        f"💖 <b>Thank you for using our premium service!</b> 🔥",
                        disable_web_page_preview=True
                    )
                    return

                redeemer_name = str(user.mention) if user else (message.from_user.first_name if message.from_user else None)
                marked = await db.mark_redeem_code_used(redeem_code, user_id, user_name=redeemer_name)
                if not marked:
                    await message.reply_text(
                        f"🚫 <b><u>Cᴏᴅᴇ Aʟʀᴇᴀᴅʏ Uꜱᴇᴅ!</u></b> 🚫\n\n"
                        f"🎟 <b>Cᴏᴅᴇ :</b> <code>{redeem_code}</code>\n\n"
                        f"⚠️ <i>Oh no! Kisi aur ne aapse thodi der pehle hi ye code redeem kar liya.</i>"
                    )
                    return

                expiry_time = now_aware + timedelta(seconds=seconds)
                user_data = {"id": user_id, "expiry_time": expiry_time}
                await db.update_user(user_data)

                expiry_str_in_ist = expiry_time.astimezone(pytz.timezone("Asia/Kolkata")).strftime("%d-%m-%Y\n⏱️ Expiry Time: %I:%M:%S %p")
                user_mention = user.mention if user else f"<code>{user_id}</code>"

                await message.reply_text(
                    f"🎉✨ <b><u>Pʀᴇᴍɪᴜᴍ Aᴄᴛɪᴠᴀᴛᴇᴅ Gɪꜰᴛ Cᴏᴅᴇ Sᴜᴄᴄᴇssғᴜʟʟʏ Rᴇᴅᴇᴇᴍᴇᴅ!</u></b> ✨🎉\n\n"
                    f"Badhaai ho! Aapke account mein <b>Premium Features</b> activate ho gaye hain! 🥳\n"
                    f"┌─────────────────────\n"
                    f"├ 👤 <b>Uꜱᴇʀ :</b> {user_mention}\n"
                    f"├ ⚡ <b>ID :</b> <code>{user_id}</code>\n"
                    f"├ 🎟 <b>Cᴏᴅᴇ :</b> <code>{redeem_code}</code>\n"
                    f"├ ⏳ <b>Dᴜʀᴀᴛɪᴏɴ :</b> <code>{time}</code>\n"
                    f"└─────────────────────\n"
                    f"⌛️ <b><u>Exᴘɪʀʏ Dᴀᴛᴇ & Tɪᴍᴇ:</u></b>\n"
                    f"╰ 👉 {expiry_str_in_ist}\n\n"
                    f"💎 <b><u>Pʀᴇᴍɪᴜᴍ Bᴇɴᴇғɪᴛꜱ Uɴʟᴏᴄᴋᴇᴅ:</u></b>\n"
                    f"✅ <i>Unlimited Downloading</i>\n"
                    f"✅ <i>Fast Online Streaming</i>\n"
                    f"✅ <i>No Timer Restrictions</i>\n\n"
                    f"🚀 <i>Enjoy all premium perks — happy downloading!</i>",
                    disable_web_page_preview=True
                )

                log_message = f"""
#Redeem_Premium 🔓

🎉 <b><u>Nᴇᴡ Pʀᴇᴍɪᴜᴍ Aᴄᴛɪᴠᴀᴛɪᴏɴ</u></b> 🎉

┌───────────────────────
├ 👤 <b>Uꜱᴇʀ :</b> {user_mention}
├ ⚡ <b>ID :</b> <code>{user_id}</code>
├ 🎟 <b>Cᴏᴅᴇ :</b> <code>{redeem_code}</code>
├ ⏳ <b>Dᴜʀᴀᴛɪᴏɴ :</b> <code>{time}</code>
└───────────────────────

⌛️ <b>Exᴘɪʀʏ :</b> {expiry_str_in_ist}

🚀 <b>Sᴛᴀᴛᴜꜱ :</b> Sᴜᴄᴄᴇꜱꜱғᴜʟʟʏ Rᴇᴅᴇᴇᴍᴇᴅ!
"""
                await client.send_message(
                    PREMIUM_LOGS,
                    text=log_message,
                    disable_web_page_preview=True
                )

                # Auto-update the original /add_redeem list message: mark
                # this code ✅ with the redeemer's name and refresh the
                # Redeemed/Available counts — no extra command needed.
                await refresh_batch_message(client, marked.get("batch_id"))
            else:
                await message.reply_text("⚠️ <b>Error:</b> Invalid time format in redeem code.")
        except Exception as e:
            await message.reply_text(f"⚠️ <b>An error occurred while redeeming the code:</b> {e}")
    else:
        await message.reply_text(
            "⚠️ <b>Iɴᴠᴀʟɪᴅ Fᴏʀᴍᴀᴛ!</b>\n\n"
            "📝 <b><u>Cᴏʀʀᴇᴄᴛ Uꜱᴀɢᴇ:</u></b>\n"
            "➩ <code>/redeem [Premium Code]</code>\n\n"
            "💡 <b>Exᴀᴍᴘʟᴇ:</b>\n"
            "╰ 👉 <code>/redeem abcdef1234</code>"
        )
