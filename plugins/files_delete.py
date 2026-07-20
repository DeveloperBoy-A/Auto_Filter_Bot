import re
import logging
from pyrogram import Client, filters
from info import DELETE_CHANNELS
from database.ia_filterdb import Media, Media2, delete_file_by_id, delete_files_by_query, unpack_new_file_id
logger = logging.getLogger(__name__)

media_filter = filters.document | filters.video | filters.audio


@Client.on_message(filters.chat(DELETE_CHANNELS) & media_filter)
async def deletemultiplemedia(bot, message):
    """Delete Multiple files from database"""

    for file_type in ("document", "video", "audio"):
        media = getattr(message, file_type, None)
        if media is not None:
            break
    else:
        return

    file_id, file_ref = unpack_new_file_id(media.file_id)

    # 1. Sabse pehle exact file_id se try karo (sabhi configured DBs mein)
    deleted_count = await delete_file_by_id(file_id)

    if not deleted_count:
        # 2. Fallback: cleaned file_name + size + mime se try karo
        file_name = re.sub(r"(_|\-|\.|\+)", " ", str(media.file_name))
        deleted_count = await delete_files_by_query({
            'file_name': file_name,
            'file_size': media.file_size,
            'mime_type': media.mime_type
        })

    if not deleted_count:
        # 3. Fallback: raw file_name + size + mime se try karo
        deleted_count = await delete_files_by_query({
            'file_name': media.file_name,
            'file_size': media.file_size,
            'mime_type': media.mime_type
        })

    if deleted_count:
        logger.info('File is successfully deleted from database.')
    else:
        logger.info('File not found in database.')
