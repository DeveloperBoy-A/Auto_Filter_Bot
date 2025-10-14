import os
import sys
import glob
import importlib
from pathlib import Path
from pyrogram import idle, __version__
from pyrogram.raw.all import layer
import asyncio
from datetime import date, datetime
import pytz
from aiohttp import web
from database.ia_filterdb import Media, Media2
from database.users_chats_db import db
from info import *
from utils import temp
from Script import script
from plugins import web_server, check_expired_premium, keep_alive
from dreamxbotz.Bot import dreamxbotz
from dreamxbotz.util.keepalive import ping_server
from dreamxbotz.Bot.clients import initialize_clients
from PIL import Image
import logging
import logging.config
import traceback

# Use uvloop for faster async performance
try:
    import uvloop
    asyncio.set_event_loop_policy(uvloop.EventLoopPolicy())
except ImportError:
    pass  # uvloop optional

Image.MAX_IMAGE_PIXELS = 500_000_000

# ----------------- Logging -----------------
logging.config.fileConfig('logging.conf')
logging.getLogger().setLevel(logging.WARNING)
logging.getLogger("pyrogram").setLevel(logging.ERROR)
logging.getLogger("imdbpy").setLevel(logging.ERROR)
logging.getLogger("aiohttp").setLevel(logging.ERROR)
logging.getLogger("aiohttp.web").setLevel(logging.ERROR)
logging.getLogger("pymongo").setLevel(logging.WARNING)

# ----------------- Plugin loader -----------------
PLUGIN_FILES = tuple(glob.glob("plugins/*.py"))

# ----------------- Global loop -----------------
loop = asyncio.get_event_loop()

# ----------------- Exception handler -----------------
def handle_exception(loop, context):
    msg = context.get("exception", context.get("message", "Unknown"))
    print(f"[Caught Exception] {msg}")
    if isinstance(msg, BaseException):
        traceback.print_exc()

loop.set_exception_handler(handle_exception)

# ----------------- Async keep-alive -----------------
async def async_keep_alive_ping():
    url = os.environ.get("KOYEB_APP_URL")
    if not url:
        return
    import aiohttp
    timeout = aiohttp.ClientTimeout(total=10)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        while True:
            try:
                await session.get(url)
            except Exception:
                pass
            await asyncio.sleep(300)  # 5 min ping

# ----------------- Bot startup -----------------
async def dreamxbotz_start():
    print('\n\nInitializing DreamxBotz (optimized uvloop)')
    await dreamxbotz.start()

    me = await dreamxbotz.get_me()
    dreamxbotz.username = me.username

    await initialize_clients()

    # Load plugins (import once)
    for file_path in PLUGIN_FILES:
        patt = Path(file_path)
        plugin_name = patt.stem
        import_path = f"plugins.{plugin_name}"
        if import_path in sys.modules:
            continue
        spec = importlib.util.spec_from_file_location(import_path, patt)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        sys.modules[import_path] = module
        print("DreamxBotz Imported => " + plugin_name)

    if ON_HEROKU:
        asyncio.create_task(ping_server())

    b_users, b_chats = await db.get_banned()
    temp.BANNED_USERS = b_users
    temp.BANNED_CHATS = b_chats

    await Media.ensure_indexes()
    if MULTIPLE_DB:
        await Media2.ensure_indexes()
        print("Multiple DB mode ON")
    else:
        print("Single DB mode ON")

    temp.ME = me.id
    temp.U_NAME = me.username
    temp.B_NAME = me.first_name
    temp.B_LINK = me.mention
    dreamxbotz.username = '@' + me.username

    dreamxbotz.loop.create_task(check_expired_premium(dreamxbotz))

    tz = pytz.timezone('Asia/Kolkata')
    today = date.today()
    now = datetime.now(tz)
    time_str = now.strftime("%H:%M:%S %p")
    await dreamxbotz.send_message(chat_id=LOG_CHANNEL, text=script.RESTART_TXT.format(temp.B_LINK, today, time_str))

    # --- aiohttp web server ---
    runner = web.AppRunner(await web_server())
    await runner.setup()
    bind_address = "0.0.0.0"
    port = int(os.environ.get("PORT", 8080))
    site = web.TCPSite(runner, bind_address, port)
    await site.start()
    print(f"🌐 Web server started on port {port}")

    # --- Non-blocking keep-alive ping ---
    asyncio.create_task(async_keep_alive_ping())

    # --- Existing keep_alive task ---
    dreamxbotz.loop.create_task(keep_alive())

    await idle()
    await runner.cleanup()

# ----------------- Entry point -----------------
if __name__ == '__main__':
    try:
        loop.run_until_complete(dreamxbotz_start())
    except FloodWait as e:
        print(f"FloodWait! Sleeping for {e.value} seconds.")
        loop.run_until_complete(asyncio.sleep(e.value))
    except KeyboardInterrupt:
        logging.info('Service Stopped Bye 👋')
    except Exception as e:
        print(f"[Safeguard] Unexpected exception: {e}")
        traceback.print_exc()
