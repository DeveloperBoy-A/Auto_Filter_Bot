"""
Manual poster / cover overrides (admin commands se set hote hain).

Collections (users_chats_db wale hi database me):
  poster_overrides : _id = normalized update-post naam (base_name)
                     -> movie update channel ka poster
  manual_covers    : _id = "t:<type>|<title>|<year>"  -> ek title ki SAARI files (strict match)
                     _id = "f:<file_id>"              -> sirf ek specific file
                     (file wala title wale se upar priority leta hai)

Media (files) collection ko yahan chheda nahi gaya, isliye COVERX=False rehne par bhi
koi purana/auto cover galti se nahi dikhega - sirf manual cover dikhega.
"""
import re
import logging
from datetime import datetime

from database.users_chats_db import db

logger = logging.getLogger(__name__)


def _poster_col():
    return db.db.poster_overrides


def _cover_col():
    return db.db.manual_covers


# ------------------------------------------------------------------ matching helpers
def norm_title(text) -> str:
    """'Spider-Man: No Way Home' / 'spider man no way home' -> 'spider man no way home'"""
    return " ".join(re.findall(r"\w+", str(text or "").lower()))


def title_regex(text, exact=True):
    """
    Mongo regex (use with $options 'i').
    exact=True  -> poora naam match (sirf punctuation/spacing ka farak chalega)
    exact=False -> naam kahin bhi aaye (similar suggestions ke liye)
    """
    tokens = re.findall(r"\w+", str(text or "").lower())
    if not tokens:
        return None
    body = r"[\W_]+".join(re.escape(t) for t in tokens)
    if exact:
        return rf"^[\W_]*{body}[\W_]*$"
    return rf"(^|[\W_]){body}([\W_]|$)"


# ------------------------------------------------------------------ POSTER (update channel)
async def get_poster_override(base_name):
    key = norm_title(base_name)
    if not key:
        return None
    try:
        return await _poster_col().find_one({"_id": key})
    except Exception as e:
        logger.warning(f"[POSTER] override lookup failed for '{base_name}': {e}")
        return None


async def set_poster_override(base_name, url, is_backdrop=False, by=None):
    key = norm_title(base_name)
    if not key:
        return False
    await _poster_col().update_one(
        {"_id": key},
        {"$set": {
            "url": url,
            "is_backdrop": bool(is_backdrop),
            "name": base_name,
            "by": by,
            "ts": datetime.utcnow(),
        }},
        upsert=True,
    )
    return True


# ------------------------------------------------------------------ COVER (file send)
def cover_title_key(title, year, media_type) -> str:
    return f"t:{str(media_type or 'movie').lower()}|{norm_title(title)}|{str(year or '').strip()}"


async def get_manual_cover(f):
    """
    f = Media document (umongo) ya dict. Return: cover URL ya None.
    Priority: specific-file cover > title-wide cover.
    """
    try:
        if isinstance(f, dict):
            get = f.get
            fid = f.get("file_id") or f.get("_id")
        else:
            get = lambda k, d=None: getattr(f, k, d)
            fid = getattr(f, "file_id", None)

        keys = []
        if fid:
            keys.append(f"f:{fid}")
        title = get("title", None)
        if title:
            keys.append(cover_title_key(title, get("year", None), get("media_type", None)))
        if not keys:
            return None

        found = {}
        async for d in _cover_col().find({"_id": {"$in": keys}}):
            found[d["_id"]] = d.get("url")
        for k in keys:                      # file key pehle, title key baad me
            if found.get(k):
                return found[k]
    except Exception as e:
        logger.warning(f"[COVER] manual cover lookup failed: {e}")
    return None


async def set_title_cover(title, year, media_type, url, by=None):
    await _cover_col().update_one(
        {"_id": cover_title_key(title, year, media_type)},
        {"$set": {"url": url, "title": title, "year": year, "media_type": media_type,
                  "by": by, "ts": datetime.utcnow()}},
        upsert=True,
    )


async def set_file_cover(file_id, url, name=None, by=None):
    await _cover_col().update_one(
        {"_id": f"f:{file_id}"},
        {"$set": {"url": url, "name": name, "by": by, "ts": datetime.utcnow()}},
        upsert=True,
    )


async def clear_file_covers(file_ids):
    """Given file_ids ke file-level covers hata do. Return: deleted count."""
    ids = [f"f:{i}" for i in file_ids]
    deleted = 0
    for i in range(0, len(ids), 1000):
        res = await _cover_col().delete_many({"_id": {"$in": ids[i:i + 1000]}})
        deleted += res.deleted_count
    return deleted


async def clear_title_cover(title, year, media_type):
    res = await _cover_col().delete_one({"_id": cover_title_key(title, year, media_type)})
    return res.deleted_count
