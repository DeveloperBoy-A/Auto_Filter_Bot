from aiohttp import web
from .route import routes
from datetime import datetime
from database.users_chats_db import db
from info import LOG_CHANNEL, URL, PREMIUM_LOGS
import aiohttp
import asyncio
import logging

logging.basicConfig(level=logging.INFO)
logging.getLogger("pyrogram").setLevel(logging.ERROR)

# ---------------- Web server ----------------
async def web_server():
    """
    Initialize aiohttp web server with routes.
    File uploads limited to 30 MB (original behavior).
    """
    web_app = web.Application(client_max_size=30_000_000)  # 30 MB limit
    web_app.add_routes(routes)
    return web_app

# ---------------- Premium check ----------------
async def check_expired_premium(client):
    """
    Check for expired premium users periodically.
    Async-safe, CPU-efficient, exception-handled.
    """
    while True:
        try:
            expired_users = await db.get_expired(datetime.now())
            for user_data in expired_users:
                user_id = user_data["id"]
                await db.remove_premium_access(user_id)
                try:
                    user = await client.get_users(user_id)
                    msg_text = (
                        f"<b>ʜᴇʏ {user.mention},\n\n"
                        "Your Premium Access Has Expired. Thank You For Using Our Service 😊. "
                        "If You Want To Take Premium Again, Click /plan For Details.\n\n"
                        "<blockquote>आपका 𝑷𝒓𝒆𝒎𝒊𝒖𝒎 𝑨𝒄𝒄𝒆𝒔𝒔 समाप्त हो गया है हमारी सेवा का उपयोग करने के लिए धन्यवाद 😊। "
                        "यदि आप फिर से 𝑷𝒓𝒆𝒎𝒊𝒖𝒎 लेना चाहते हैं, तो योजनाओं के विवरण के लिए /plan पर 𝑪𝒍𝒊𝒄𝒌 करें।</blockquote></b>"
                    )
                    await client.send_message(chat_id=user_id, text=msg_text)
                    await client.send_message(
                        PREMIUM_LOGS,
                        text=f"<b>#Premium_Expire\n\nUser name: {user.mention}\nUser id: <code>{user_id}</code>"
                    )
                except Exception as e:
                    logging.error(f"Premium message error for user {user_id}: {e}")
                await asyncio.sleep(0.5)  # short pause to reduce CPU spikes
        except Exception as e:
            logging.error(f"Premium check failed: {e}")
        await asyncio.sleep(1)  # loop pause

# ---------------- Keep-alive ping ----------------
async def keep_alive():
    """
    Keep bot alive by sending periodic pings to the URL.
    Async-safe and exception-handled.
    """
    async with aiohttp.ClientSession() as session:
        while True:
            await asyncio.sleep(298)  # ~5 minutes
            try:
                async with session.get(URL) as resp:
                    if resp.status != 200:
                        logging.warning(f"⚠️ Ping Error! Status: {resp.status}")
            except Exception as e:
                logging.error(f"❌ Ping Failed: {e}")
