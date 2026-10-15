# -*- coding: utf-8 -*-

import os
import asyncio
import logging
import aiohttp

from pyrogram import Client, filters
from pyrogram.types import Message


IMGBB_API_KEY = os.getenv(
    "IMGBB_API_KEY",
    "d4cc3d793cb68b2c6cdc2197588e895c"
)

IMGBB_API_URL = "https://api.imgbb.com/1/upload"
MAX_FILE_SIZE = 5 * 1024 * 1024

LOG = logging.getLogger(__name__)


@Client.on_message(
    filters.command(
        ["img", "cup", "telegraph"],
        prefixes="/"
    )
    & filters.reply
)
async def c_upload(client: Client, message: Message):

    reply = message.reply_to_message

    if not reply or not reply.media:
        return await message.reply_text(
            "❌ Reply to an image or photo to upload it."
        )

    # Identify supported image media.
    if reply.photo:
        media = reply.photo
        filename = "image.jpg"
        content_type = "image/jpeg"

    elif reply.document:
        media = reply.document
        filename = media.file_name or "image"

        content_type = media.mime_type or (
            "application/octet-stream"
        )

        if not content_type.startswith("image/"):
            return await message.reply_text(
                "❌ Please reply to a valid image file."
            )

    elif reply.animation:
        media = reply.animation
        filename = "image.gif"
        content_type = "image/gif"

    else:
        return await message.reply_text(
            "❌ Supported formats: JPG, PNG, GIF and other images."
        )

    file_size = getattr(media, "file_size", 0) or 0

    if file_size > MAX_FILE_SIZE:
        return await message.reply_text(
            "❌ File size limit is 5 MB."
        )

    if not IMGBB_API_KEY:
        return await message.reply_text(
            "❌ ImgBB API key is missing."
        )

    msg = await message.reply_text(
        "⏳ Downloading image..."
    )

    downloaded_media = None

    try:
        downloaded_media = await reply.download()

        if (
            not downloaded_media
            or not os.path.isfile(downloaded_media)
        ):
            return await msg.edit_text(
                "❌ Failed to download the image."
            )

        actual_size = os.path.getsize(downloaded_media)

        if actual_size == 0:
            return await msg.edit_text(
                "❌ The downloaded file is empty."
            )

        if actual_size > MAX_FILE_SIZE:
            return await msg.edit_text(
                "❌ File size limit is 5 MB."
            )

        with open(downloaded_media, "rb") as file:
            image_bytes = file.read()

        await msg.edit_text(
            "⏳ Uploading image to ImgBB..."
        )

        # Official ImgBB API:
        # POST /1/upload?key=API_KEY
        # Multipart form field: image
        params = {
            "key": IMGBB_API_KEY
        }

        form = aiohttp.FormData()

        form.add_field(
            "image",
            image_bytes,
            filename=filename,
            content_type=content_type
        )

        timeout = aiohttp.ClientTimeout(
            total=60,
            connect=15
        )

        async with aiohttp.ClientSession(
            timeout=timeout
        ) as session:

            async with session.post(
                IMGBB_API_URL,
                params=params,
                data=form
            ) as response:

                http_status = response.status
                response_text = await response.text()

                try:
                    result = await response.json(
                        content_type=None
                    )
                except Exception:
                    result = {}

                if (
                    http_status == 200
                    and result.get("success")
                ):
                    image_data = result.get("data", {})
                    image_url = image_data.get("url")

                    if image_url:
                        return await msg.edit_text(
                            f"✅ **Image Uploaded Successfully!**\n\n"
                            f"{image_url}",
                            disable_web_page_preview=True
                        )

                error_data = result.get("error", {})

                error_code = error_data.get(
                    "code",
                    "Unknown"
                )

                error_message = error_data.get(
                    "message",
                    response_text[:400]
                    or "Unknown ImgBB API error."
                )

                LOG.error(
                    "ImgBB upload failed | HTTP=%s | "
                    "Code=%s | Error=%s",
                    http_status,
                    error_code,
                    error_message
                )

                if str(error_code) == "103":
                    return await msg.edit_text(
                        "❌ **ImgBB Upload Rejected**\n\n"
                        "HTTP Status: `400`\n"
                        "API Error Code: `103`\n"
                        "Reason: ImgBB has forbidden this request.\n\n"
                        "The API response indicates an access "
                        "restriction. Check ImgBB account/API "
                        "access or the request source."
                    )

                return await msg.edit_text(
                    "❌ **ImgBB Upload Failed**\n\n"
                    f"HTTP Status: `{http_status}`\n"
                    f"API Error Code: `{error_code}`\n"
                    f"Reason: `{str(error_message)[:300]}`"
                )

    except asyncio.TimeoutError:
        await msg.edit_text(
            "❌ ImgBB request timed out. Please try again."
        )

    except aiohttp.ClientError as error:
        LOG.exception("ImgBB network error")

        await msg.edit_text(
            "❌ Network error while uploading.\n"
            f"`{str(error)[:250]}`"
        )

    except Exception as error:
        LOG.exception("Image upload failed")

        await msg.edit_text(
            "❌ Upload failed.\n"
            f"`{str(error)[:250]}`"
        )

    finally:
        if (
            downloaded_media
            and os.path.isfile(downloaded_media)
        ):
            try:
                os.remove(downloaded_media)
            except OSError:
                LOG.warning(
                    "Failed to remove temporary file: %s",
                    downloaded_media
                )