#Thanks [@Tokyo_Updates] for helping in this journey 

import jinja2
from info import *
from dreamxbotz.Bot import dreamxbotz
from dreamxbotz.util.human_readable import humanbytes
from dreamxbotz.util.file_properties import get_file_ids
from dreamxbotz.server.exceptions import InvalidHash
from utils import temp
import urllib.parse
import re
import json
import html
import logging
import aiohttp


async def render_page(id, secure_hash, src=None):
    file = await dreamxbotz.get_messages(int(BIN_CHANNEL), int(id))
    file_data = await get_file_ids(dreamxbotz, int(BIN_CHANNEL), int(id), prefer_db_name=True)
    if file_data.unique_id[:6] != secure_hash:
        logging.debug(f"link hash: {secure_hash} - {file_data.unique_id[:6]}")
        logging.debug(f"Invalid hash for message with - ID {id}")
        raise InvalidHash

    src = urllib.parse.urljoin(
        URL,
        f"{id}/{urllib.parse.quote_plus(file_data.file_name)}?hash={secure_hash}",
    )

    tag = file_data.mime_type.split("/")[0].strip()
    file_size = humanbytes(file_data.file_size)
    if tag in ["video", "audio"]:
        template_file = "dreamxbotz/template/req.html"
    else:
        template_file = "dreamxbotz/template/dl.html"
        # file_data.file_size is already available. Do not make an extra HTTP
        # request to our own streaming endpoint just to calculate file size.
        # That extra request could create another Telegram media-session load.
        file_size = humanbytes(file_data.file_size)

    with open(template_file) as f:
        template = jinja2.Template(f.read())

    file_name = file_data.file_name.replace("_", " ")

    # Create a Telegram bot search/deep-link for the player page.
    # IMPORTANT: Copy/Share must never expose the real /watch/... stream URL.
    search_title = re.sub(r"\.[A-Za-z0-9]{2,5}$", "", file_name).strip()
    search_title = re.sub(
        r"(?i)\b(?:480p|576p|720p|1080p|2160p|4k|8k|"
        r"web[- .]?dl|web[- .]?rip|webrip|bluray|blu[- .]?ray|"
        r"bdrip|hdrip|hdtc|hdts|cam|dvdrip|x264|x265|hevc|av1|"
        r"aac|ddp(?:2\.0|5\.1)?|dd5\.1|eac3|ac3|multi[ -]?audio|"
        r"dual[ -]?audio|line|proper|repack|remux|v2|v3|"
        r"hindi|english|eng|tamil|telugu|malayalam|kannada|bengali|marathi|"
        r"punjabi|gujarati|urdu|korean|japanese|chinese)\b",
        " ",
        search_title,
    )
    # Remove the bot/channel branding that is commonly appended to filenames.
    search_title = re.sub(r"(?i)\bTokyo[_ -]?Updates\b", " ", search_title)
    search_title = re.sub(r"[~\[\]{}()]+", " ", search_title)
    search_title = re.sub(r"[_+.-]+", " ", search_title)
    search_title = re.sub(r"\s{2,}", " ", search_title).strip(" -")

    # Telegram bot start parameters are limited to 64 characters and only
    # allow A-Z/a-z/0-9/_/-. Keep the title readable while staying valid.
    bot_username = (temp.U_NAME or "").lstrip("@").strip()
    safe_query = re.sub(r"[^a-zA-Z0-9]+", "-", search_title).strip("-")
    start_payload = f"getfile-{safe_query}"
    if len(start_payload) > 64:
        start_payload = start_payload[:64].rstrip("-")
    bot_search_link = f"https://t.me/{bot_username}?start={start_payload}"

    # JSON literals are injected into JavaScript instead of raw strings, so
    # quotes/backslashes/newlines in unusual Telegram filenames cannot break
    # the page script.
    file_name_json = json.dumps(file_name, ensure_ascii=False)
    file_url_json = json.dumps(src, ensure_ascii=False)
    bot_search_link_json = json.dumps(bot_search_link, ensure_ascii=False)
    bot_search_title_json = json.dumps(search_title, ensure_ascii=False)

    return template.render(
        file_name=file_name,
        file_url=src,
        file_size=file_size,
        file_unique_id=file_data.unique_id,
        bot_search_link=bot_search_link,
        bot_search_title=search_title,
        file_name_json=file_name_json,
        file_url_json=file_url_json,
        bot_search_link_json=bot_search_link_json,
        bot_search_title_json=bot_search_title_json,
        file_name_html=html.escape(file_name, quote=True),
    )

