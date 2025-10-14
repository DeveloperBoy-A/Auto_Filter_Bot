import os
import sys
import glob
import importlib
from pathlib import Path
from pyrogram import Client, idle, __version__
from pyrogram.raw.all import layer
import time
from pyrogram.errors import FloodWait
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
import threading
import requests
import logging
import logging.config
import traceback

Image.MAX_IMAGE_PIXELS = 500_000_000

# ----------------- Logging -----------------
logging.config.fileConfig('logging.conf')
logging.getLogger().setLevel(logging.INFO)
logging.getLogger("pyrogram").setLevel(logging.ERROR)
logging.getLogger("imdbpy").setLevel(logging.ERROR)
logging.getLogger("aiohttp").setLevel(logging.ERROR)
logging.getLogger("aiohttp.web").setLevel(logging.ERROR)
logging.getLogger("pymongo").setLevel(logging.WARNING)

# ----------------- Bot start time -----------------
botStartTime = time.time()

# ----------------- Plugin loader -----------------
ppath = "plugins/*.py"
files = glob.glob(ppath)

# ----------------- Error Handler -----------------
def handle_exception(loop, context):
    msg = context.get("exception", context["message"])
    print(f"Caught exception: {msg}")
    traceback.print_exc()

loop = asyncio.get_event_loop()
loop.set_exception_handler(handle_exception)

# ----------------- Keep-alive ping -----------------
def keep_alive_ping():
    url = os.environ.get("KOYEB_APP_URL")
    if not url:
        return
    while True:
        try:
            requests.get(url)
        except Exception:
            pass
        time.sleep(300)  # 5 min ping

threading.Thread(target=keep_alive_ping, daemon=True).start()

# ----------------- Bot start -----------------
async def dreamxbotz_start():
    print('\n\nInitializing DreamxBotz')
    await dreamxbotz.start()
    bot_info = await dreamxbotz.get_me()
    dreamxbotz.username = bot_info.username
    await initialize_clients()

    # Load plugins
    for name in files:
        with open(name) as a:
            patt = Path(a.name)
            plugin_name = patt.stem.replace(".py", "")
            plugins_dir = Path(f"plugins/{plugin_name}.py")
            import_path = "plugins.{}".format(plugin_name)
            spec = importlib.util.spec_from_file_location(import_path, plugins_dir)
            load = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(load)
            sys.modules["plugins." + plugin_name] = load
            print("DreamxBotz Imported => " + plugin_name)

    if ON_HEROKU:
        asyncio.create_task(ping_server())

    # Banned users/chats
    b_users, b_chats = await db.get_banned()
    temp.BANNED_USERS = b_users
    temp.BANNED_CHATS = b_chats

    # Database indexes
    await Media.ensure_indexes()
    if MULTIPLE_DB:
        await Media2.ensure_indexes()
        print("Multiple DB mode ON")
    else:
        print("Single DB mode ON")

    me = await dreamxbotz.get_me()
    temp.ME = me.id
    temp.U_NAME = me.username
    temp.B_NAME = me.first_name
    temp.B_LINK = me.mention
    dreamxbotz.username = '@' + me.username

    dreamxbotz.loop.create_task(check_expired_premium(dreamxbotz))

    logging.info(f"{me.first_name} with Pyrogram v{__version__} (Layer {layer}) started on @{me.username}")
    logging.info(LOG_STR)
    logging.info(script.LOGO)

    # Send restart message
    tz = pytz.timezone('Asia/Kolkata')
    today = date.today()
    now = datetime.now(tz)
    time_str = now.strftime("%H:%M:%S %p")
    await dreamxbotz.send_message(chat_id=LOG_CHANNEL, text=script.RESTART_TXT.format(temp.B_LINK, today, time_str))

    # ----------------- aiohttp web server -----------------
    app = web.AppRunner(await web_server())
    await app.setup()
    bind_address = "0.0.0.0"
    port = int(os.environ.get("PORT", 8080))
    await web.TCPSite(app, bind_address, port).start()
    print(f"🌐 Web server started on port {port}")
    # ------------------------------------------------------

    dreamxbotz.loop.create_task(keep_alive())
    await idle()

# ----------------- Main loop with smarter FloodWait -----------------
if __name__ == '__main__':
    while True:
        try:
            loop.run_until_complete(dreamxbotz_start())
            break
        except FloodWait as e:
            print(f"FloodWait! Sleeping for {e.value} seconds but bot stays active in background.")
            time.sleep(e.value)
        except KeyboardInterrupt:
            logging.info('Service Stopped Bye 👋')
            break
        except Exception as e:
            print(f"Unexpected exception: {e}")
            traceback.print_exc()
            time.sleep(5)
