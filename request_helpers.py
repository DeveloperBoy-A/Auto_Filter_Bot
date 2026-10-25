"""
request_helpers.py
Pure-python helpers for the movie request system (no Telegram / DB imports,
so everything here is easy to unit-test).

- parse_request()        : user text  -> clean title / year / season / languages / key
- same_request()         : are two parsed requests the same thing? (dup detection)
- parse_file_info()      : uploaded filename + caption -> tokens / season / year / langs / quality
- request_matches_file() : does an uploaded file satisfy a pending request?
- render_post()          : text of the request-channel post
- render_dashboard()     : text of the "pending requests" page
- user_message()         : DM text sent to the requesters
"""
import os
import re
import html
import difflib
from datetime import datetime

# ───────────────────────────── basics ───────────────────────────── #

def esc(s) -> str:
    return html.escape(str(s if s is not None else ""), quote=False)


# ───────────────────────────── fonts & boxes ───────────────────────────── #

_SC = dict(zip("abcdefghijklmnopqrstuvwxyz", "ᴀʙᴄᴅᴇꜰɢʜɪᴊᴋʟᴍɴᴏᴘǫʀꜱᴛᴜᴠᴡxʏᴢ"))


def sc(text) -> str:
    """ꜱᴍᴀʟʟ ᴄᴀᴘꜱ (labels only – never use on user supplied titles or on strings containing HTML tags)."""
    return "".join(_SC.get(c, c) for c in str(text).lower())


def bs(text) -> str:
    """𝗕𝗼𝗹𝗱 𝗦𝗮𝗻𝘀 (only used for numbers now)."""
    out = []
    for c in str(text):
        if "A" <= c <= "Z":
            out.append(chr(0x1D5D4 + ord(c) - 65))
        elif "a" <= c <= "z":
            out.append(chr(0x1D5EE + ord(c) - 97))
        elif "0" <= c <= "9":
            out.append(chr(0x1D7EC + ord(c) - 48))
        else:
            out.append(c)
    return "".join(out)


# Border width (number of ━). Phone screens are narrow: if a line still wraps, lower this
# (env REQUEST_BOX_WIDTH), e.g. 10.
try:
    BAR_LEN = max(6, int(os.environ.get("REQUEST_BOX_WIDTH", 12)))
except ValueError:
    BAR_LEN = 12
BAR = "━" * BAR_LEN


def top(title="", icon="", small=True):
    """Top border. With title:  ┏━ 📊 ꜱᴜᴍᴍᴀʀʏ ━   (short, never wraps). small=False -> bold digits."""
    if not title:
        return "┏" + BAR
    f = sc if small else bs
    head = f"{icon} {f(title)}" if icon else f(title)
    return f"┏━ {head} ━"


box_top = top                      # old name (kept for imports)
BOX_MID = "┣" + BAR
BOX_END = "┗" + BAR


def boxed(title, icon, lines):
    """box with title + ┃ lines + bottom"""
    body = "\n".join(f"┃ {l}" if l else "┃" for l in lines)
    return f"{box_top(title, icon)}\n{body}\n{BOX_END}"


OPEN_STATUSES = ("pending", "processing", "cam_only")

# status -> (icon, label)
STATUS_META = {
    "pending":        ("⏳", "ᴘᴇɴᴅɪɴɢ"),
    "processing":     ("🛠", "ᴘʀᴏᴄᴇꜱꜱɪɴɢ"),
    "cam_only":       ("🎥", "ʜᴅᴛᴄ / ʟᴏᴡ ǫᴜᴀʟɪᴛʏ"),
    "uploaded":       ("✅", "ᴜᴘʟᴏᴀᴅᴇᴅ"),
    "unavailable":    ("⚠️", "ᴜɴᴀᴠᴀɪʟᴀʙʟᴇ"),
    "already":        ("♻️", "ᴀʟʀᴇᴀᴅʏ ᴀᴠᴀɪʟᴀʙʟᴇ"),
    "not_released":   ("📌", "ɴᴏᴛ ʀᴇʟᴇᴀꜱᴇᴅ"),
    "wrong_spelling": ("♨️", "ᴡʀᴏɴɢ ꜱᴘᴇʟʟɪɴɢ"),
    "no_hindi":       ("⚜️", "ʜɪɴᴅɪ ɴᴏᴛ ᴀᴠᴀɪʟᴀʙʟᴇ"),
    "no_dub":         ("🎙", "ᴅᴜʙʙᴇᴅ ɴᴏᴛ ᴀᴠᴀɪʟᴀʙʟᴇ"),
    "need_details":   ("📝", "ᴍᴏʀᴇ ᴅᴇᴛᴀɪʟꜱ ɴᴇᴇᴅᴇᴅ"),
    "closed":         ("🔕", "ᴄʟᴏꜱᴇᴅ"),
}


def status_meta(status):
    return STATUS_META.get(status, ("❔", sc(str(status).replace("_", " "))))


# Admin option buttons: code -> config
#   open=True  => request stays open (will still be auto-notified on upload)
#   notify     => send message to every requester
ADMIN_ACTIONS = {
    "up": {"status": "uploaded",       "open": False, "notify": True,  "btn": "✅ ᴜᴘʟᴏᴀᴅᴇᴅ ✅"},
    "un": {"status": "unavailable",    "open": False, "notify": True,  "btn": "⚠️ ᴜɴᴀᴠᴀɪʟᴀʙʟᴇ ⚠️"},
    "aa": {"status": "already",        "open": False, "notify": True,  "btn": "♻️ ᴀʟʀᴇᴀᴅʏ ᴀᴠᴀɪʟᴀʙʟᴇ ♻️"},
    "nr": {"status": "not_released",   "open": False, "notify": True,  "btn": "📌 ɴᴏᴛ ʀᴇʟᴇᴀꜱᴇᴅ"},
    "ws": {"status": "wrong_spelling", "open": False, "notify": True,  "btn": "♨️ ᴡʀᴏɴɢ ꜱᴘᴇʟʟɪɴɢ"},
    "hn": {"status": "no_hindi",       "open": False, "notify": True,  "btn": "⚜️ ʜɪɴᴅɪ ɴᴏᴛ ᴀᴠᴀɪʟᴀʙʟᴇ"},
    "dd": {"status": "no_dub",         "open": False, "notify": True,  "btn": "🎙 ᴅᴜʙ ɴᴏᴛ ᴀᴠᴀɪʟᴀʙʟᴇ"},
    "pr": {"status": "processing",     "open": True,  "notify": True,  "btn": "🛠 ᴘʀᴏᴄᴇꜱꜱɪɴɢ / ꜱᴏᴏɴ"},
    "cm": {"status": "cam_only",       "open": True,  "notify": True,  "btn": "🎥 ʜᴅᴛᴄ / ʟᴏᴡ ǫᴜᴀʟɪᴛʏ"},
    "dt": {"status": "need_details",   "open": False, "notify": True,  "btn": "📝 ɴᴇᴇᴅ ᴅᴇᴛᴀɪʟꜱ"},
    "nu": {"status": "closed",         "open": False, "notify": False, "btn": "🔕 ᴄʟᴏꜱᴇ ꜱɪʟᴇɴᴛʟʏ"},
}


# ───────────────────────────── tokenizing ───────────────────────────── #

# split on anything that's not a letter/digit (keeps Indic / Arabic combining marks)
_SEP = re.compile(r"[^\w\u0300-\u036f\u0600-\u06ff\u0900-\u0dff]+", re.UNICODE)

_SEASON_TOKEN = re.compile(r"^s(\d{1,2})(?:e\d{1,3})?$")
_EP_TOKEN = re.compile(r"^e(?:p)?\d{1,3}$")
_YEAR = re.compile(r"^(?:19|20)\d{2}$")
_QUALITY = re.compile(r"^(?:\d{3,4}p|[248]k)$")

# full language names only (short codes like "ben", "mal" can be real title words: "Ben 10")
REQ_LANG = {
    "hindi": "Hindi", "hin": "Hindi", "english": "English", "eng": "English",
    "tamil": "Tamil", "telugu": "Telugu", "malayalam": "Malayalam",
    "kannada": "Kannada", "bengali": "Bengali", "punjabi": "Punjabi",
    "marathi": "Marathi", "gujarati": "Gujarati", "urdu": "Urdu",
    "korean": "Korean", "japanese": "Japanese", "bhojpuri": "Bhojpuri",
    "chinese": "Chinese", "spanish": "Spanish",
}
# file names / captions use short codes a lot
FILE_LANG = dict(REQ_LANG)
FILE_LANG.update({
    "tam": "Tamil", "tel": "Telugu", "mal": "Malayalam", "kan": "Kannada",
    "ben": "Bengali", "pun": "Punjabi", "pbi": "Punjabi", "mar": "Marathi",
    "guj": "Gujarati", "kor": "Korean", "jpn": "Japanese",
})

REQ_NOISE = {
    "full", "movie", "movies", "film", "series", "web", "webseries", "download",
    "please", "pls", "plz", "plss", "sir", "bro", "bhai", "bhaiya", "upload",
    "link", "hd", "dubbed", "dub", "sub", "subs", "esub", "esubs", "chahiye",
    "chaiye", "kardo", "krdo", "dual", "audio", "multi", "hdrip", "webrip",
    "webdl", "dl", "bluray", "brrip", "bdrip", "hdcam", "camrip", "hevc",
    "x264", "x265", "h264", "h265", "10bit", "aac", "print", "request",
}

CAM_TOKENS = {"cam", "camrip", "hdcam", "hqcam", "hdts", "hdtc", "telesync", "predvd", "pdvd", "dvdscr", "tc"}

_FILE_JUNK = REQ_NOISE | set(FILE_LANG) | CAM_TOKENS | {
    "complete", "combined", "season", "episode", "ep", "avc", "ac3", "ddp", "dd",
    "msub", "org", "ott", "uncut", "extended", "proper", "remastered", "amzn",
    "nf", "netflix", "zee5", "hotstar", "prime", "sonyliv", "jio", "atmos",
    "mkv", "mp4", "mb", "gb", "web", "dl", "bluray", "hdtv", "dvdrip", "ts",
}


def tokenize(text) -> list:
    text = (text or "").lower().replace("_", " ")
    text = re.sub(r"['’`]", "", text)
    return [t for t in _SEP.split(text) if t]


def _season_from(tokens):
    """Return season number found in a token list, or None."""
    for i, t in enumerate(tokens):
        m = _SEASON_TOKEN.match(t)
        if m:
            return int(m.group(1))
        if t in ("season", "saison") and i + 1 < len(tokens):
            n = tokens[i + 1]
            if n.isdigit() and len(n) <= 2:
                return int(n)
    return None


# ───────────────────────────── request parsing ───────────────────────────── #

def parse_request(raw):
    """Turn free text from a user into a normalised request. None if no title."""
    text = re.sub(r"https?://\S+", " ", raw or "")
    text = re.sub(r"@\w+", " ", text)
    text = re.sub(r"(?i)(?<!\w)[/#]request\w*", " ", text)
    toks = tokenize(text)

    season = None
    langs = []
    title = []
    i = 0
    while i < len(toks):
        t = toks[i]
        m = _SEASON_TOKEN.match(t)
        if m:
            season = int(m.group(1))
            i += 1
            continue
        if t in ("season", "saison") and i + 1 < len(toks) and toks[i + 1].isdigit() and len(toks[i + 1]) <= 2:
            season = int(toks[i + 1])
            i += 2
            continue
        if season is not None and _EP_TOKEN.match(t):
            i += 1
            continue
        if _QUALITY.match(t) or t in REQ_NOISE:
            i += 1
            continue
        if t in REQ_LANG:
            lang = REQ_LANG[t]
            if lang not in langs:
                langs.append(lang)
            i += 1
            continue
        title.append(t)
        i += 1

    # year = last 19xx/20xx token, but never if it would leave the title empty
    year = None
    if len(title) > 1:
        for idx in range(len(title) - 1, -1, -1):
            if _YEAR.match(title[idx]):
                year = title.pop(idx)
                break

    title = title[:10]
    if not title:
        return None

    title_key = " ".join(title)
    key = title_key
    if season is not None:
        key += f" s{season:02d}"
    if year:
        key += f" {year}"
    if langs:
        key += " [" + ",".join(sorted(langs)) + "]"

    return {
        "title": " ".join(w.capitalize() for w in title),
        "title_key": title_key,
        "year": year,
        "season": season,
        "langs": langs,
        "key": key,
    }


def display_name(req) -> str:
    name = req.get("title") or req.get("title_key") or "Unknown"
    if req.get("year"):
        name += f" ({req['year']})"
    if req.get("season") is not None:
        name += f" S{int(req['season']):02d}"
    return name


def same_request(a, b) -> bool:
    """Duplicate detection between two parsed/stored requests."""
    if a.get("season") != b.get("season"):
        return False
    ya, yb = a.get("year"), b.get("year")
    if ya and yb and ya != yb:
        return False
    la, lb = set(a.get("langs") or []), set(b.get("langs") or [])
    if la and lb and la != lb:
        return False
    ca = (a.get("title_key") or "").replace(" ", "")
    cb = (b.get("title_key") or "").replace(" ", "")
    if not ca or not cb:
        return False
    if ca == cb:
        return True
    # numbers must match exactly ("panda 3" != "panda 4")
    if re.findall(r"\d+", ca) != re.findall(r"\d+", cb):
        return False
    if min(len(ca), len(cb)) < 5:
        return False
    return difflib.SequenceMatcher(None, ca, cb).ratio() >= 0.9


# ───────────────────────────── file info / matching ───────────────────────────── #

_SOURCE_PRETTY = {
    "bluray": "BluRay", "brrip": "BRRip", "bdrip": "BDRip", "webrip": "WEBRip",
    "webdl": "WEB-DL", "hdrip": "HDRip", "hdtv": "HDTV", "dvdrip": "DVDRip",
    "hdcam": "HDCAM", "camrip": "CAMRip", "cam": "CAM", "hdtc": "HDTC",
    "hdts": "HDTS", "predvd": "PreDVD", "dvdscr": "DVDScr", "telesync": "TeleSync",
}


def parse_file_info(file_name, caption=""):
    name_tokens = tokenize(file_name)
    cap_tokens = tokenize((caption or "")[:300])
    all_tokens = name_tokens + cap_tokens

    season = _season_from(name_tokens)
    if season is None:
        season = _season_from(cap_tokens)

    years = {t for t in all_tokens if _YEAR.match(t)}
    langs = {FILE_LANG[t] for t in all_tokens if t in FILE_LANG}
    is_cam = any(t in CAM_TOKENS for t in all_tokens)

    resolution = next((t.upper() for t in all_tokens if _QUALITY.match(t)), None)
    source = None
    if "web" in all_tokens and "dl" in all_tokens:
        source = "WEB-DL"
    else:
        for t in all_tokens:
            if t in _SOURCE_PRETTY:
                source = _SOURCE_PRETTY[t]
                break
    quality = " ".join(x for x in (resolution, source) if x) or None

    return {
        "name_tokens": name_tokens,
        "cap_tokens": cap_tokens,
        "season": season,
        "years": years,
        "langs": langs,
        "is_cam": is_cam,
        "quality": quality,
        "file_name": file_name or "",
    }


def _find_phrase(tokens, phrase, max_start=6):
    """
    Find `phrase` (list of tokens) near the start of `tokens`.
    Works on exact tokens AND compact form (so "spiderman" == "spider man").
    Returns (start, end) or None.
    """
    rc = "".join(phrase)
    if not rc:
        return None
    for i in range(0, min(len(tokens), max_start)):
        acc = ""
        for j in range(i, min(len(tokens), i + 12)):
            acc += tokens[j]
            if acc == rc:
                return i, j + 1
            if not rc.startswith(acc):
                break
    return None


def _is_title_extender(tok) -> bool:
    """True if `tok` (the word right after the requested title) looks like MORE title."""
    if _YEAR.match(tok) or _QUALITY.match(tok) or _SEASON_TOKEN.match(tok) or _EP_TOKEN.match(tok):
        return False
    if tok in _FILE_JUNK:
        return False
    if tok.isdigit():
        return len(tok) <= 2          # "Race 3" -> sequel number
    if re.search(r"\d", tok) and re.search(r"[^\W\d_]", tok):
        return False                  # x265, ddp5, 10bit ...
    return True


def request_matches_file(req, finfo) -> bool:
    rt = (req.get("title_key") or "").split()
    if not rt:
        return False

    rs = req.get("season")
    if rs is not None and finfo["season"] != rs:
        return False

    ry = req.get("year")
    if ry and finfo["years"] and ry not in finfo["years"]:
        return False

    rl = set(req.get("langs") or [])
    if rl and finfo["langs"] and not (rl & finfo["langs"]):
        return False

    short = len("".join(rt)) < 8
    for toks in (finfo["name_tokens"], finfo["cap_tokens"]):
        span = _find_phrase(toks, rt)
        if not span:
            continue
        end = span[1]
        nxt = toks[end] if end < len(toks) else None
        if nxt is None or not _is_title_extender(nxt):
            return True
        # the file's title continues after our phrase ("Race" vs "Race 3", "Dune" vs "Dune Part Two")
        if short:
            continue
        if nxt.isdigit() and not rt[-1].isdigit():
            continue
        return True
    return False


# ───────────────────────────── time helpers ───────────────────────────── #

def ago(dt, now=None) -> str:
    if not dt:
        return "-"
    secs = int(((now or datetime.utcnow()) - dt).total_seconds())
    if secs < 60:
        return sc("just now")
    m = secs // 60
    if m < 60:
        return sc(f"{m}m ago")
    h = m // 60
    if h < 24:
        return sc(f"{h}h ago")
    return sc(f"{h // 24}d ago")


def user_mention(u) -> str:
    return f'<a href="tg://user?id={int(u["id"])}">{esc(u.get("name") or "User")}</a>'


# ───────────────────────────── request channel post ───────────────────────────── #

try:
    MAX_NAMES = max(1, int(os.environ.get("REQUEST_POST_MAX_NAMES", 60)))
except ValueError:
    MAX_NAMES = 60
try:
    FILE_NAME_LEN = max(4, int(os.environ.get("REQUEST_FILE_NAME_LEN", 12)))
except ValueError:
    FILE_NAME_LEN = 12


def _group_url(u):
    """Best clickable link for the group a user requested from."""
    return u.get("group_link") or u.get("link")


def _groups_of(users):
    """Distinct groups (keep order) -> [(title, url)]."""
    seen, out = set(), []
    for u in users:
        gid = u.get("group_id") or u.get("group_title")
        if not gid or gid in seen:
            continue
        seen.add(gid)
        if u.get("group_title"):
            out.append((u["group_title"], _group_url(u)))
    return out


def _sent_line(nt, n):
    """'5/5 ꜱᴇɴᴛ ꜱᴜᴄᴄᴇꜱꜱꜰᴜʟʟʏ ✅' (or plain 'ꜱᴇɴᴛ' if some failed)."""
    total = nt.get("total", n)
    sent = nt.get("dm", 0) + nt.get("group", 0)
    if sent > 0 and not nt.get("failed", 0):
        tail = f"{sc('sent successfully')} ✅"
    else:
        tail = sc("sent")
    return sent, total, tail


def render_post(req, _max_names=None) -> str:
    status = req.get("status", "pending")
    icon, label = status_meta(status)
    is_open = bool(req.get("open", True))
    users = req.get("users") or []
    n = req.get("user_count", len(users))
    name = esc(display_name(req))

    # struck through when the request is closed, or when the HDTC / low-quality notice
    # has already been sent (request itself stays open for the HD upload)
    struck = (not is_open) or (status == "cam_only" and bool(req.get("notified")))

    def W(text):
        return f"<s>{text}</s>" if struck else text

    # title is tap-to-copy while the post is live; plain (struck) afterwards
    title_val = name if struck else f"<code>{name}</code>"

    lines = [W(f"🎞 {sc('title')}  : {title_val}")]
    if req.get("langs"):
        lines.append(W(f"🌐 {sc('lang')}  : {esc(', '.join(req['langs']))}"))
    lines.append(W(f"👥 {sc('users')} : <b>{n}</b> {sc('users' if n != 1 else 'user')}"))

    head = "NEW REQUEST" if is_open else "REQUEST CLOSED"
    out = [box_top(head, "🎬")]
    out += [f"┃ {l}" for l in lines]

    if users:
        out.append(BOX_MID)
        out.append("┃ " + W(f"👤 {sc('requested by')}"))
        cap = _max_names or MAX_NAMES
        shown = users[:cap]
        for i, u in enumerate(shown, start=1):
            out.append("┃ " + W(f"{i}. {user_mention(u)}"))
        if len(users) > len(shown):
            out.append("┃ " + W(f"+{len(users) - len(shown)} {sc('more')}"))
        out.append("┃")                                   # gap between names and 🆔
        first = users[0]
        id_val = first["id"] if struck else f"<code>{first['id']}</code>"
        out.append("┃ " + W(f"🆔 {sc('first id')} : {id_val}"))
        groups = _groups_of(users)
        for idx, (title, url) in enumerate(groups):
            label_txt = f"🏘 {sc('group')} : " if idx == 0 else "🏘 "
            gname = esc(title)
            if url:
                gname = f'<a href="{esc(url)}">{gname}</a>'
            out.append("┃ " + W(label_txt + gname))

    out.append(BOX_MID)
    out.append("┃ " + W(f"📊 {sc('status')} : {icon} <b>{label}</b>"))

    mf = req.get("matched_file")
    if mf:
        fname = (mf.get("name") or "").strip()
        short_name = fname[:FILE_NAME_LEN].rstrip() + ("…" if len(fname) > FILE_NAME_LEN else "")
        out.append("┃ " + W(f"📁 {sc('file')} : {esc(short_name)}"))
        if mf.get("quality"):
            out.append("┃ " + W(f"📀 {sc('quality')} : {esc(mf['quality'])}"))

    nt = req.get("notified")
    if nt:
        sent, total, tail = _sent_line(nt, n)
        out.append("┃ " + W(f"📨 {sc('notified')} : <b>{sent}/{total}</b> {tail}"))
        out.append("┃ " + W(
            f"💬 {sc('dm')} {nt.get('dm', 0)} • 👥 {sc('grp')} {nt.get('group', 0)} • ❌ {nt.get('failed', 0)}"
        ))

    if is_open:
        out.append("┃ " + W(f"🕒 {sc('requested')} : {ago(req.get('created_at'))}"))
    else:
        out.append("┃ " + W(f"🕒 {sc('closed')} : {ago(req.get('closed_at'))}"))
    out.append(BOX_END)
    out.append(W(f"#ʀᴇǫᴜᴇꜱᴛ {'⚡️' if not struck else icon}"))

    text = "\n".join(out)
    # safety: Telegram hard limit 4096 -> shrink the name list instead of failing the edit
    cap = _max_names or MAX_NAMES
    if len(text) > 4000 and cap > 5:
        return render_post(req, _max_names=cap // 2)
    return text


# ───────────────────────────── REQUEST PAGE (dashboard) ───────────────────────────── #

# tab code -> (icon, heading, empty text)   (everything gets small caps when rendered)

# ───────────────────────────── REQUEST PAGE (dashboard) ───────────────────────────── #

TABS = {
    "p": ("⏳", "PENDING LIST",  sc("koi pending request nahi hai") + " 🎉"),
    "u": ("✅", "UPLOADED LIST", sc("abhi tak koi request upload nahi hui")),
    "r": ("❌", "REJECTED LIST", sc("koi rejected request nahi hai")),
}
MAX_TEXT = 3950          # Telegram hard limit is 4096

W = "━" * 17                                   # change here if lines wrap on phone
D_TOP = top("REQUEST PAGE", "📋", small=False) + "┓"   # bold title (small=False kept)
D_MID = f"┣{W}┫"
D_END = f"┗{W}┛"


def _short(text, n=26) -> str:
    text = str(text)
    return text if len(text) <= n else text[: n - 1] + "…"


def _d_sum() -> str:
    return f"┣━━━━📊 {sc('summary')} ━━━━┫"


def _d_sep(number) -> str:
    return f"┣━━━━━━━ {bs(f'{number:02d}')} ━━━━━━━┫"     # bold digits (small=False kept)


def _item_lines(tab, r) -> list:
    """Always exactly 5 lines (blank ┃ fills the gap), so layout never changes."""
    name = esc(_short(display_name(r)))
    link = r.get("post_link")
    name_html = f'<a href="{esc(link)}">{name}</a>' if link else name
    n = r.get("user_count", len(r.get("users") or []))
    icon, label = status_meta(r.get("status"))
    lines = [f"┃ 🎬 {name_html}"]

    if tab == "p":
        hot = " 🔥" if n >= 5 else ""
        has_lang = bool(r.get("langs"))
        if not has_lang:
            lines.append("┃")
        lines.append(f"┃ 👥 {sc('users')}  : {n}{hot}")
        if has_lang:
            lines.append(f"┃ 🌐 {sc('lang')}   : {esc(', '.join(r['langs']))}")
        lines.append(f"┃ 📊 {sc('status')} : {icon} {label}")
        lines.append(f"┃ 🕒 {sc('waiting')} : {ago(r.get('created_at'))}")
    elif tab == "u":
        nt = r.get("notified") or {}
        sent, total, _ = _sent_line(nt, n) if nt else (0, n, "")
        q = (r.get("matched_file") or {}).get("quality")
        lines.append(f"┃ 📀 {sc('quality')} : {esc(q)}" if q else "┃")
        lines.append(f"┃ 📨 {sc('notified')} : {sent}/{total}")
        lines.append(f"┃ 💬 {sc('dm')} {nt.get('dm', 0)} • 👥 {sc('grp')} {nt.get('group', 0)} • ❌ {nt.get('failed', 0)}")
        lines.append(f"┃ 🕒 {sc('uploaded')} : {ago(r.get('closed_at') or r.get('updated_at'))}")
    else:
        lines.append("┃")
        lines.append(f"┃ 👥 {sc('users')}  : {n}")
        lines.append(f"┃ 📊 {sc('status')} : {icon} {label}")
        lines.append(f"┃ 🕒 {sc('closed')} : {ago(r.get('closed_at'))}")
    return lines


def _bar(done, total, width=5) -> str:
    k = round(width * done / total) if total else 0
    return "▰" * k + "▱" * (width - k)


def render_dashboard(tab, page, pages, items, stats, now_text, skip=0) -> str:
    """
    tab   : 'p' pending | 'u' uploaded | 'r' rejected  (never mixed on one page)
    stats : dict(pending, uploaded, rejected, notified)
    """
    if tab not in TABS:
        tab = "p"
    t_icon, t_head, t_empty = TABS[tab]

    p, u, r = stats.get("pending", 0), stats.get("uploaded", 0), stats.get("rejected", 0)
    total = p + u + r
    rate = round(u * 100 / total) if total else 0

    head = [
        D_TOP,
        f"┃ 🎬 {sc('all user requests live here.')}",
        f"┃ 🔔 {sc('auto notification on upload')}",
        _d_sum(),
        f"┃ 📦 {sc('total')}     : {total}",
        f"┃ ⏳ {sc('pending')}   : {p}",
        f"┃ ✅ {sc('uploaded')}  : {u}",
        f"┃ ❌ {sc('rejected')}  : {r}",
        f"┃ 📨 {sc('notified')}  : {stats.get('notified', 0)} {sc('users')}",
        D_MID,
        f"┃🎯{sc('success')}: {_bar(u, total)}{rate}%",
        D_MID,
        f"┃ {t_icon} {sc(t_head)}  ({page}/{pages})",
    ]
    footer = f"\n🕒 {sc('updated')}: {now_text}"

    blocks = [[_d_sep(skip + i)] + _item_lines(tab, it) for i, it in enumerate(items, start=1)]
    if not blocks:
        blocks = [[D_MID, f"┃ <i>{t_empty}</i>"]]

    def build(bl):
        lines = list(head)
        for b in bl:
            lines += b
        lines.append(D_END)
        return "\n".join(lines) + "\n" + footer

    text = build(blocks)
    while len(text) > MAX_TEXT and len(blocks) > 1:     # never exceed Telegram's limit
        blocks.pop()
        text = build(blocks)
    return text

# ───────────────────────────── DM text for requesters ───────────────────────────── #

def user_message(status, mention, title, n_users=1, finfo=None) -> str:
    t = esc(title)
    hello = f"👋 {sc('hey')} {mention},"

    # Sabhi cards ke liye function (Isme upar ek line ka gap add kiya gaya hai)
    def card(head, icon):
        return f"{top(head, icon)}\n┃ \n┃ 🎬 <b>{t}</b>\n┃ 👥 {sc('users')} : <b>{n_users}</b>\n{BOX_END}"

    if status == "uploaded":
        extra = []
        if finfo:
            if finfo.get("quality"):
                extra.append(f"┃ 📀 {sc('quality')} : <b>{esc(finfo['quality'])}</b>")
            if finfo.get("langs"):
                extra.append(f"┃ 🌐 {sc('lang')} : <b>{esc(', '.join(sorted(finfo['langs'])))}</b>")
        extra.append(f"┃ 👥 {sc('users')} : <b>{n_users}</b>")
        
        # Uploaded me bhi upar ek line ka gap add kiya gaya hai
        box = f"{top('REQ MOVIE UPLOADED ✅', '🎉')}\n┃ \n┃ 🎬 <b>{t}</b>\n" + "\n".join(extra) + f"\n{BOX_END}"
        return (
            f"{box}\n\n{hello}\n"
            f"✅ {sc('aapki request')} <b>{sc('upload ho gayi')}</b> {sc('hai!')}\n"
            f"🔍 {sc('group me search karke download kar lo.')}\n"
            f"📨 {sc('sabhi users ko notification')} {sc('sent successfully')} ✅"
        )
        
    if status == "cam_only":
        return (f"{card('HDTC / LOW QUALITY ✅', '🎥')}\n\n{hello}\n"
                f"⚠️ {sc('abhi sirf')} <b>{sc('hdtc / low quality')}</b> {sc('print aaya hai.')}\n"
                f"🔔 {sc('HD print aate hi aapko dobara notification mil jayegi.')}")
                
    if status == "processing":
        return (f"{card('REQUEST ACCEPTED', '🛠')}\n\n{hello}\n"
                f"⏳ {sc('is par kaam chalu hai.')}\n"
                f"🔔 {sc('upload hote hi aapko notification mil jayegi.')}")
                
    if status == "unavailable":
        return f"{card('UNAVAILABLE', '⚠️')}\n\n{hello}\n💔 {sc('ye abhi available nahi hai.')}"
        
    if status == "already":
        return (f"{card('ALREADY AVAILABLE', '♻️')}\n\n{hello}\n"
                f"✅ {sc('ye pehle se available hai.')}\n🔍 {sc('group me search karke dekho.')}")
                
    if status == "not_released":
        return f"{card('NOT RELEASED', '📌')}\n\n{hello}\n🕊️ {sc('ye abhi release nahi hui hai.')}"
        
    if status == "wrong_spelling":
        return (f"{card('WRONG SPELLING', '♨️')}\n\n{hello}\n"
                f"❗ {sc('naam galat hai.')}\n✍️ {sc('sahi spelling ke saath dobara request karo.')}")
                
    if status == "no_hindi":
        return f"{card('HINDI NOT AVAILABLE', '⚜️')}\n\n{hello}\n❌ {sc('ye hindi me abhi available nahi hai.')}"
        
    if status == "no_dub":
        return f"{card('DUB NOT AVAILABLE', '🎙')}\n\n{hello}\n❌ {sc('ye dubbed version abhi available nahi hai.')}"
        
    if status == "need_details":
        return (f"{card('MORE DETAILS NEEDED', '📝')}\n\n{hello}\n"
                f"✍️ {sc('naam + year + language ke saath dobara request karo:')}\n"
                f"<code>/request Stree 2 2024 Hindi</code>")
                
    return f"{card(str(status).replace('_', ' ').upper(), 'ℹ️')}\n\n{hello}"
