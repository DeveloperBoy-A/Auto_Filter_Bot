#Join Telegram Channel - @Tokyo_Updates

from pyrogram import Client, filters, enums
from pyrogram.types import ChatJoinRequest, ChatMemberUpdated
from database.users_chats_db import db
from info import ADMINS, AUTH_REQ_CHANNELS
from pyrogram.filters import create


def is_auth_req_channel(_, __, update):
    return update.chat.id in AUTH_REQ_CHANNELS


@Client.on_chat_join_request(create(is_auth_req_channel))
async def join_reqs(client, message: ChatJoinRequest):
    # User ki request DB mein save karo
    await db.add_join_req(
        message.from_user.id,
        message.chat.id
    )

    # 💯 Turant Approve karo taaki user Member ban jaye
    try:
        await client.approve_chat_join_request(
            message.chat.id,
            message.from_user.id
        )
    except Exception as e:
        if "USER_ALREADY_PARTICIPANT" not in str(e):
            print(f"Auto-Approve Error: {e}")


# 💯 Jab user channel leave karega, toh usko DB se hata dega
@Client.on_chat_member_updated(create(is_auth_req_channel))
async def handle_leave_member(client, member: ChatMemberUpdated):
    if member.new_chat_member.status in [
        enums.ChatMemberStatus.LEFT,
        enums.ChatMemberStatus.BANNED
    ]:
        await db.remove_join_req(
            member.from_user.id,
            member.chat.id
        )


@Client.on_message(
    filters.command("delreq") &
    filters.private &
    filters.user(ADMINS)
)
async def del_requests(client, message):
    await db.del_join_req()
    await message.reply(
        "<b>⚙ ꜱᴜᴄᴄᴇꜱꜱғᴜʟʟʏ ᴄʜᴀɴɴᴇʟ ʟᴇғᴛ ᴜꜱᴇʀꜱ ᴅᴇʟᴇᴛᴇᴅ</b>"
    )