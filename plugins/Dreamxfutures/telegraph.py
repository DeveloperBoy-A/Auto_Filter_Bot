import os
import requests
from pyrogram import Client, filters
from pyrogram.types import Message

IMGBB_API_KEY = "d4cc3d793cb68b2c6cdc2197588e895c"


@Client.on_message(filters.command(["img", "cup", "telegraph"], prefixes="/") & filters.reply)
async def c_upload(client: Client, message: Message):
    reply = message.reply_to_message

    if not reply.media:
        return await message.reply_text("Reply to a media to upload it to Cloud.")

    if reply.document and reply.document.file_size and reply.document.file_size > 5 * 1024 * 1024:
        return await message.reply_text("File size limit is 5 MB.")

    msg = await message.reply_text("Processing...")
    downloaded_media = None

    try:
        downloaded_media = await reply.download()

        if not downloaded_media:
            return await msg.edit_text("Something went wrong during download.")

        with open(downloaded_media, "rb") as f:
            resp = requests.post(
                "https://api.imgbb.com/1/upload",
                data={"key": IMGBB_API_KEY},
                files={"image": f},
                timeout=60,
            )

        try:
            result = resp.json()
        except ValueError:
            result = {}

        if resp.status_code == 200 and result.get("success"):
            await msg.edit_text(result["data"]["url"])
        else:
            error = result.get("error", {})
            if isinstance(error, dict):
                reason = error.get("message", "Unknown API error")
                code = error.get("code", "N/A")
            else:
                reason = str(error)
                code = "N/A"

            await msg.edit_text(
                "❌ ImgBB Upload Failed\n"
                f"HTTP Status: {resp.status_code}\n"
                f"API Error Code: {code}\n"
                f"Reason: {reason}"
            )

    except Exception as e:
        await msg.edit_text(f"Error: {type(e).__name__}: {e}")

    finally:
        if downloaded_media and os.path.exists(downloaded_media):
            try:
                os.remove(downloaded_media)
            except OSError:
                pass
