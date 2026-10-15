import os
import aiohttp
import asyncio
import logging

from pyrogram import Client, filters
from pyrogram.types import Message

IMGBB_API_KEY = os.getenv(
    "IMGBB_API_KEY",
    "e8c4cb6e8f0a8ba3c056feacafc98f41"
)

MAX_FILE_SIZE = 5 * 1024 * 1024

LOG = logging.getLogger(__name__)


@Client.on_message(
    filters.command(["img", "cup", "telegraph"], prefixes="/")
    & filters.reply
)
async def c_upload(client: Client, message: Message):
    reply = message.reply_to_message

    if not reply or not reply.media:
        return await message.reply_text(
            "❌ Reply to a photo or image file to upload it."
        )

    media = (
        reply.photo
        or reply.document
        or reply.animation
        or reply.video
    )

    if not media:
        return await message.reply_text(
            "❌ Please reply to an image or supported media file."
        )

    file_size = getattr(media, "file_size", 0) or 0

    if file_size > MAX_FILE_SIZE:
        return await message.reply_text(
            "❌ File size limit is 5 MB."
        )

    msg = await message.reply_text("⏳ Processing your upload...")

    downloaded_media = None

    try:
        downloaded_media = await reply.download()

        if not downloaded_media or not os.path.isfile(downloaded_media):
            return await msg.edit_text(
                "❌ Something went wrong while downloading the file."
            )

        actual_size = os.path.getsize(downloaded_media)

        if actual_size > MAX_FILE_SIZE:
            return await msg.edit_text(
                "❌ File size limit is 5 MB."
            )

        if actual_size == 0:
            return await msg.edit_text(
                "❌ The downloaded file is empty."
            )

        with open(downloaded_media, "rb") as file:
            img_bytes = file.read()

        filename = os.path.basename(downloaded_media)

        content_type = "image/jpeg"

        if reply.photo:
            filename = "image.jpg"
            content_type = "image/jpeg"
        elif reply.document:
            filename = reply.document.file_name or filename
            mime_type = reply.document.mime_type or ""

            if mime_type.startswith("image/"):
                content_type = mime_type
            else:
                return await msg.edit_text(
                    "❌ Please reply to a valid image file."
                )
        elif reply.animation:
            filename = "image.gif"
            content_type = "image/gif"
        elif reply.video:
            return await msg.edit_text(
                "❌ Please reply to an image, not a video."
            )

        form = aiohttp.FormData()
        form.add_field("key", IMGBB_API_KEY)
        form.add_field(
            "image",
            img_bytes,
            filename=filename,
            content_type=content_type
        )

        timeout = aiohttp.ClientTimeout(total=60)

        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(
                "https://api.imgbb.com/1/upload",
                data=form
            ) as response:

                response_text = await response.text()

                try:
                    result = await response.json(
                        content_type=None
                    )
                except Exception:
                    result = {}

                if response.status == 200 and result.get("success"):
                    image_url = result.get("data", {}).get("url")

                    if image_url:
                        return await msg.edit_text(
                            f"✅ **Upload Successful!**\n\n{image_url}",
                            disable_web_page_preview=True
                        )

                error = result.get("error", {})
                error_code = error.get("code", "Unknown")
                error_message = error.get(
                    "message",
                    response_text[:500] or "Unknown API error"
                )

                LOG.error(
                    "ImgBB upload failed | HTTP=%s | Code=%s | Error=%s",
                    response.status,
                    error_code,
                    error_message
                )

                if str(error_code) == "103":
                    return await msg.edit_text(
                        "❌ **ImgBB has forbidden this request.**\n\n"
                        "The server or API access may be restricted.\n"
                        "Check the bot logs for the exact API response."
                    )

                return await msg.edit_text(
                    f"❌ **Upload Failed**\n"
                    f"HTTP Status: `{response.status}`\n"
                    f"API Error Code: `{error_code}`\n"
                    f"Reason: `{str(error_message)[:300]}`"
                )

    except asyncio.TimeoutError:
        await msg.edit_text(
            "❌ Upload timed out. Please try again."
        )

    except aiohttp.ClientError as error:
        LOG.exception("ImgBB network error")
        await msg.edit_text(
            f"❌ Network error: `{str(error)[:250]}`"
        )

    except Exception as error:
        LOG.exception("Image upload failed")
        await msg.edit_text(
            f"❌ Upload error: `{str(error)[:250]}`"
        )

    finally:
        if downloaded_media and os.path.isfile(downloaded_media):
            try:
                os.remove(downloaded_media)
            except OSError:
                LOG.warning(
                    "Could not remove temporary file: %s",
                    downloaded_media
                )