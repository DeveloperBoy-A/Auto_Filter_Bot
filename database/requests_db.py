"""
database/requests_db.py
MongoDB layer for the movie-request system.

One request = ONE document. Every requester lives inside `users`:
  - same user asking again  -> not added again (atomic `$ne` guard)  => duplicate not counted
  - other user, same movie  -> added to `users`, `user_count` +1     => one request, many users

Collections (same DB as the rest of the bot):
  movie_requests  – the requests
  request_meta    – small key/value docs (e.g. dashboard message id)
"""
import logging
from datetime import datetime, timedelta

from bson import ObjectId
from bson.errors import InvalidId
from pymongo import ReturnDocument, ASCENDING, DESCENDING
from pymongo.errors import DuplicateKeyError

from database.users_chats_db import db as _bot_db
from request_helpers import OPEN_STATUSES, same_request

logger = logging.getLogger(__name__)

_MATCH_FIELDS = {"title_key": 1, "season": 1, "year": 1, "langs": 1, "status": 1, "key": 1}


def to_oid(rid):
    if isinstance(rid, ObjectId):
        return rid
    try:
        return ObjectId(str(rid))
    except (InvalidId, TypeError):
        return None


class RequestDB:
    def __init__(self, database):
        self.col = database.db.movie_requests
        self.meta = database.db.request_meta
        self._ready = False

    # ───────────── setup ───────────── #

    async def ensure_indexes(self):
        if self._ready:
            return
        self._ready = True
        try:
            # only ONE open request per key -> race-proof duplicate protection
            await self.col.create_index(
                [("key", ASCENDING)],
                unique=True,
                partialFilterExpression={"open": True},
                name="uniq_open_key",
            )
        except Exception as e:
            logger.warning(f"[REQ] unique open-key index not created ({e}); falling back to app-level checks")
        try:
            await self.col.create_index([("open", ASCENDING), ("user_count", DESCENDING), ("created_at", ASCENDING)])
            await self.col.create_index([("open", ASCENDING), ("closed_at", DESCENDING)])
            await self.col.create_index([("users.id", ASCENDING)])
            await self.col.create_index([("status", ASCENDING), ("closed_at", DESCENDING)])
        except Exception as e:
            logger.warning(f"[REQ] index creation failed: {e}")

    # ───────────── reads ───────────── #

    async def get(self, rid):
        oid = to_oid(rid)
        if oid is None:
            return None
        return await self.col.find_one({"_id": oid})

    async def find_same_open(self, parsed):
        """Open request that is the same as `parsed` (exact key first, then fuzzy)."""
        doc = await self.col.find_one({"key": parsed["key"], "open": True})
        if doc:
            return doc
        cursor = self.col.find({"open": True}, _MATCH_FIELDS).limit(600)
        async for cand in cursor:
            if same_request(parsed, cand):
                return await self.col.find_one({"_id": cand["_id"]})
        return None

    async def find_recent_uploaded(self, parsed, days):
        if not days:
            return None
        since = datetime.utcnow() - timedelta(days=days)
        cursor = (
            self.col.find({"status": "uploaded", "closed_at": {"$gte": since}}, _MATCH_FIELDS)
            .sort("closed_at", DESCENDING)
            .limit(300)
        )
        async for cand in cursor:
            if same_request(parsed, cand):
                return await self.col.find_one({"_id": cand["_id"]})
        return None

    async def open_for_match(self):
        """Light-weight list of open requests (used on every upload)."""
        return await self.col.find({"open": True}, _MATCH_FIELDS).limit(2000).to_list(length=2000)

    async def count_open_for_user(self, user_id):
        return await self.col.count_documents({"open": True, "users.id": int(user_id)})

    # ───────────── writes ───────────── #

    async def create(self, parsed, user_entry):
        now = datetime.utcnow()
        doc = {
            "key": parsed["key"],
            "title_key": parsed["title_key"],
            "title": parsed["title"],
            "year": parsed["year"],
            "season": parsed["season"],
            "langs": parsed["langs"],
            "status": "pending",
            "open": True,
            "users": [user_entry],
            "user_count": 1,
            "created_at": now,
            "updated_at": now,
            "post_id": None,
            "post_link": None,
            "notified": None,
        }
        res = await self.col.insert_one(doc)   # may raise DuplicateKeyError
        doc["_id"] = res.inserted_id
        return doc

    async def add_user(self, rid, user_entry):
        """Atomically add a requester. False if already there (or request closed meanwhile)."""
        res = await self.col.update_one(
            {"_id": rid, "open": True, "users.id": {"$ne": int(user_entry["id"])}},
            {
                "$push": {"users": user_entry},
                "$inc": {"user_count": 1},
                "$set": {"updated_at": datetime.utcnow()},
            },
        )
        return res.modified_count == 1

    async def set_post(self, rid, post_id, post_link):
        await self.col.update_one({"_id": rid}, {"$set": {"post_id": post_id, "post_link": post_link}})

    async def transition(self, rid, status, keep_open, by=None, extra=None):
        """
        Atomic status change of an OPEN request. Returns the updated doc, or None if the
        request was already closed / already in that status (so nobody is notified twice).
        """
        oid = to_oid(rid)
        if oid is None:
            return None
        now = datetime.utcnow()
        fields = {"status": status, "open": bool(keep_open), "updated_at": now}
        if not keep_open:
            fields["closed_at"] = now
        if by is not None:
            fields["handled_by"] = by
        if extra:
            fields.update(extra)
        flt = {"_id": oid, "open": True}
        if keep_open:
            flt["status"] = {"$ne": status}
        return await self.col.find_one_and_update(flt, {"$set": fields}, return_document=ReturnDocument.AFTER)

    async def reopen(self, rid):
        """Closed -> pending again. Raises DuplicateKeyError if the same request is already open."""
        oid = to_oid(rid)
        if oid is None:
            return None
        return await self.col.find_one_and_update(
            {"_id": oid, "open": False},
            {
                "$set": {"status": "pending", "open": True, "updated_at": datetime.utcnow(), "notified": None},
                "$unset": {"closed_at": "", "matched_file": ""},
            },
            return_document=ReturnDocument.AFTER,
        )

    async def save_notified(self, rid, stats):
        await self.col.update_one({"_id": rid}, {"$set": {"notified": stats}})

    async def delete(self, rid):
        oid = to_oid(rid)
        if oid is not None:
            await self.col.delete_one({"_id": oid})

    # ───────────── dashboard queries ───────────── #

    async def count_tab(self, tab):
        return await self.col.count_documents({"open": tab == "p"})

    async def list_tab(self, tab, skip, limit):
        if tab == "p":
            cursor = self.col.find({"open": True}).sort([("user_count", -1), ("created_at", 1)])
        else:
            cursor = self.col.find({"open": False}).sort("closed_at", -1)
        return await cursor.skip(skip).limit(limit).to_list(length=limit)

    async def stats(self):
        pipeline = [{
            "$group": {
                "_id": "$status",
                "n": {"$sum": 1},
                "sent": {"$sum": {"$add": [
                    {"$ifNull": ["$notified.dm", 0]},
                    {"$ifNull": ["$notified.group", 0]},
                ]}},
            }
        }]
        pending = uploaded = rejected = notified = 0
        async for row in self.col.aggregate(pipeline):
            st, n = row["_id"], row["n"]
            notified += row.get("sent", 0)
            if st in OPEN_STATUSES:
                pending += n
            elif st == "uploaded":
                uploaded += n
            else:
                rejected += n
        return {"pending": pending, "uploaded": uploaded, "rejected": rejected, "notified": notified}

    # ───────────── meta ───────────── #

    async def get_meta(self, name):
        return await self.meta.find_one({"_id": name})

    async def set_meta(self, name, data):
        await self.meta.update_one({"_id": name}, {"$set": data}, upsert=True)


rq = RequestDB(_bot_db)
