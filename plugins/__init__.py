from aiohttp import web
from .route import routes
from asyncio import sleep
from datetime import datetime
from database.users_chats_db import db
from info import LOG_CHANNEL, URL, PREMIUM_LOGS
import aiohttp
import asyncio
import logging

logging.basicConfig(level=logging.INFO)
logging.getLogger("pyrogram").setLevel(logging.ERROR)


async def web_server():
    web_app = web.Application(client_max_size=30000000)
    web_app.add_routes(routes)
    return web_app


async def check_expired_premium(client):
    while True:
        try:
            data = await db.get_expired(datetime.now())

            for user in data:
                user_id = user["id"]
                await db.remove_premium_access(user_id)

                try:
                    user_obj = await client.get_users(user_id)

                    await client.send_message(
                        chat_id=user_id,
                        text=(
                            f"<b>ʜᴇʏ {user_obj.mention},\n\n"
                            "𝑌𝑜𝑢𝑟 𝑃𝑟𝑒𝑚𝑖𝑢𝑚 𝐴𝑐𝑐𝑒𝑠𝑠 𝐻𝑎𝑠 𝐸𝑥𝑝𝑖𝑟𝑒𝑑.\n"
                            "Thank you for using our service 😊</b>"
                        )
                    )

                    await client.send_message(
                        PREMIUM_LOGS,
                        text=(
                            "<b>#Premium_Expire\n\n"
                            f"User: {user_obj.mention}\n"
                            f"User ID: <code>{user_id}</code></b>"
                        )
                    )

                except Exception as e:
                    logging.error(f"Error sending expiry message to {user_id}: {e}")

                await sleep(100)

        except Exception as e:
            logging.error(f"Error in premium check loop: {e}")

        await sleep(7200)


async def keep_alive():
    if not URL:
        logging.warning("URL variable not found, keep_alive disabled!")
        return

    async with aiohttp.ClientSession() as session:
        while True:
            await asyncio.sleep(500)

            try:
                async with session.get(URL) as resp:
                    if resp.status != 200:
                        logging.warning(f"⚠️ Ping failed! Status: {resp.status}")

            except Exception as e:
                logging.error(f"❌ Keep-alive error: {e}")

            await asyncio.sleep(10)