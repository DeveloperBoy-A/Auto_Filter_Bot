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
    """ꜱᴍᴀʟʟ ᴄᴀᴘꜱ (labels only – never use on user supplied titles)."""
    return "".join(_SC.get(c, c) for c in str(text).lower())


def bs(text) -> str:
    """𝗕𝗼𝗹𝗱 𝗦𝗮𝗻𝘀 (headings only)."""
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


def top(title="", icon=""):
    """Top border. With title:  ┏━ 📊 𝗦𝗨𝗠𝗠𝗔𝗥𝗬 ━   (short, never wraps)"""
    if not title:
        return "┏" + BAR
    head = f"{icon} {bs(title)}" if icon else bs(title)
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
    "pending":        ("⏳", "Pending"),
    "processing":     ("🛠", "Processing"),
    "cam_only":       ("🎥", "CAM / Low Quality Only"),
    "uploaded":       ("✅", "Uploaded"),
    "unavailable":    ("⚠️", "Unavailable"),
    "already":        ("♻️", "Already Available"),
    "not_released":   ("📌", "Not Released"),
    "wrong_spelling": ("♨️", "Wrong Spelling"),
    "no_hindi":       ("⚜️", "Hindi Not Available"),
    "no_dub":         ("🎙", "Dubbed Not Available"),
    "need_details":   ("📝", "More Details Needed"),
    "closed":         ("🔕", "Closed"),
}


def status_meta(status):
    return STATUS_META.get(status, ("❔", str(status).title()))


# Admin option buttons: code -> config
#   open=True  => request stays open (will still be auto-notified on upload)
#   notify     => send message to every requester
ADMIN_ACTIONS = {
    "up": {"status": "uploaded",       "open": False, "notify": True,  "btn": "✅ ᴜᴘʟᴏᴀᴅᴇᴅ ✅"},
    "un": {"status": "unavailable",    "open": False, "notify": True,  "btn": "⚠️ ᴜɴᴀᴠᴀɪʟᴀʙʟᴇ ⚠️"},
    "aa": {"status": "already",        "open": False, "notify": True,  "btn": "♻️ ᴀʟʀᴇᴀᴅʏ ᴀᴠᴀɪʟᴀʙʟᴇ ♻️"},
    "nr": {"status": "not_released",   "open": False, "notify": True,  "btn": "📌 Not Released"},
    "ws": {"status": "wrong_spelling", "open": False, "notify": True,  "btn": "♨️ Wrong Spelling"},
    "hn": {"status": "no_hindi",       "open": False, "notify": True,  "btn": "⚜️ Hindi Not Available"},
    "dd": {"status": "no_dub",         "open": False, "notify": True,  "btn": "🎙 Dub Not Available"},
    "pr": {"status": "processing",     "open": True,  "notify": True,  "btn": "🛠 Processing / Soon"},
    "cm": {"status": "cam_only",       "open": True,  "notify": True,  "btn": "🎥 CAM Only"},
    "dt": {"status": "need_details",   "open": False, "notify": True,  "btn": "📝 Need Details"},
    "nu": {"status": "closed",         "open": False, "notify": False, "btn": "🔕 Close Silently"},
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
        return "just now"
    m = secs // 60
    if m < 60:
        return f"{m}m ago"
    h = m // 60
    if h < 24:
        return f"{h}h ago"
    return f"{h // 24}d ago"


def user_mention(u) -> str:
    return f'<a href="tg://user?id={int(u["id"])}">{esc(u.get("name") or "User")}</a>'


# ───────────────────────────── request channel post ───────────────────────────── #

def render_post(req) -> str:
    status = req.get("status", "pending")
    icon, label = status_meta(status)
    is_open = bool(req.get("open", True))
    users = req.get("users") or []
    n = req.get("user_count", len(users))
    name = esc(display_name(req))

    name_html = f"<code>{name}</code>" if is_open else f"<s>{name}</s>"
    lines = [f"🎞 {sc('name')}  : {name_html}"]
    if req.get("langs"):
        lines.append(f"🌐 {sc('lang')}  : {esc(', '.join(req['langs']))}")
    lines.append(f"👥 {sc('users')} : <b>{n}</b> {'users' if n != 1 else 'user'}")

    head = "NEW REQUEST" if is_open else "REQUEST CLOSED"
    out = [box_top(head, "🎬")]
    out += [f"┃ {l}" for l in lines]

    if users:
        shown = users if len(users) <= 3 else users[:3]
        names = ", ".join(user_mention(u) for u in shown)
        if len(users) > len(shown):
            names += f" +{len(users) - len(shown)} more"
        first = users[0]
        out.append(BOX_MID)
        out.append(f"┃ 👤 {sc('requested by')}")
        out.append(f"┃ {names}")
        out.append(f"┃ 🆔 {sc('first id')} : <code>{first['id']}</code>")
        if first.get("group_title"):
            out.append(f"┃ 🏘 {sc('group')} : <code>{esc(first['group_title'])}</code>")

    out.append(BOX_MID)
    out.append(f"┃ 📊 {sc('status')} : {icon} <b>{label}</b>")

    mf = req.get("matched_file")
    if mf:
        out.append(f"┃ 📁 {sc('file')} : <code>{esc((mf.get('name') or '')[:28])}</code>")
        if mf.get("quality"):
            out.append(f"┃ 📀 {sc('quality')} : {esc(mf['quality'])}")

    nt = req.get("notified")
    if nt:
        total = nt.get("total", n)
        sent = nt.get("dm", 0) + nt.get("group", 0)
        out.append(f"┃ 📨 {sc('notified')} : <b>{sent}/{total} sent</b>")
        out.append(f"┃ 💬 DM {nt.get('dm', 0)} • 👥 Grp {nt.get('group', 0)} • ❌ {nt.get('failed', 0)}")

    if is_open:
        out.append(f"┃ 🕒 {sc('requested')} : {ago(req.get('created_at'))}")
    else:
        out.append(f"┃ 🕒 {sc('closed')} : {ago(req.get('closed_at'))}")
    out.append(BOX_END)
    out.append(f"#ʀᴇǫᴜᴇꜱᴛ {'⚡️' if is_open else icon}")
    return "\n".join(out)


# ───────────────────────────── REQUEST PAGE (dashboard) ───────────────────────────── #

# tab code -> (icon, heading, empty text)
TABS = {
    "p": ("⏳", "PENDING LIST",  "Koi pending request nahi hai 🎉"),
    "u": ("✅", "UPLOADED LIST", "Abhi tak koi request upload nahi hui."),
    "r": ("❌", "REJECTED LIST", "Koi rejected request nahi hai."),
}
MAX_TEXT = 3950          # Telegram hard limit is 4096


def _short(text, n=28) -> str:
    text = str(text)
    return text if len(text) <= n else text[: n - 1] + "…"


def _item_box(tab, number, r) -> str:
    name = esc(_short(display_name(r)))
    link = r.get("post_link")
    name_html = f'<a href="{esc(link)}">{name}</a>' if link else name
    n = r.get("user_count", len(r.get("users") or []))
    users_txt = f"<b>{n}</b> {'users' if n != 1 else 'user'}"
    icon, label = status_meta(r.get("status"))
    lines = [f"🎬 <b>{name_html}</b>"]

    if tab == "p":
        info = f"👥 {users_txt}"
        if r.get("langs"):
            info += f" • 🌐 {esc(', '.join(r['langs']))}"
        lines.append(info)
        stat = "" if r.get("status") == "pending" else f"{icon} {label}  •  "
        lines.append(f"{stat}🕒 {ago(r.get('created_at'))}")
    elif tab == "u":
        nt = r.get("notified") or {}
        sent = nt.get("dm", 0) + nt.get("group", 0)
        lines.append(f"📨 <b>{sent}/{nt.get('total', n)}</b> users notified")
        if nt:
            lines.append(f"💬 {nt.get('dm', 0)} • 👥 {nt.get('group', 0)} • ❌ {nt.get('failed', 0)}")
        q = (r.get("matched_file") or {}).get("quality")
        if q:
            lines.append(f"📀 {esc(q)}")
        lines.append(f"🕒 {ago(r.get('closed_at'))}")
    else:
        lines.append(f"{icon} {label}")
        lines.append(f"👥 {users_txt}")
        lines.append(f"🕒 {ago(r.get('closed_at'))}")

    body = "\n".join(f"┃ {l}" for l in lines)
    return f"{top(str(number))}\n{body}\n{BOX_END}"


def render_dashboard(tab, page, pages, items, stats, now_text, skip=0) -> str:
    """
    tab   : 'p' pending | 'u' uploaded | 'r' rejected  (never mixed on one page)
    stats : dict(pending, uploaded, rejected, notified)
    """
    if tab not in TABS:
        tab = "p"
    t_icon, t_head, t_empty = TABS[tab]

    header = "\n".join([
        top(),
        f"┃ 📋 {bs('REQUEST PAGE')}",
        f"┃ {sc('this is the request page')}",
        f"┃ {sc('all user requests here')}",
        BOX_END,
    ])
    summary = "\n".join([
        top("SUMMARY", "📊"),
        f"┃ ⏳ {sc('pending')}   : <b>{stats.get('pending', 0)}</b>",
        f"┃ ✅ {sc('uploaded')}  : <b>{stats.get('uploaded', 0)}</b>",
        f"┃ ❌ {sc('rejected')}  : <b>{stats.get('rejected', 0)}</b>",
        f"┃ 📨 {sc('notified')}  : <b>{stats.get('notified', 0)}</b> users",
        BOX_END,
    ])
    section = f"{t_icon} {bs(t_head)}  <i>({page}/{pages})</i>"
    footer = f"🕒 <i>{sc('updated')}: {now_text}</i>"

    boxes = [_item_box(tab, skip + i, r) for i, r in enumerate(items, start=1)]
    if not boxes:
        boxes = [f"<i>{t_empty}</i>"]

    def build(bx, note=""):
        parts = [header, summary, section, "\n\n".join(bx)]
        if note:
            parts.append(note)
        parts.append(footer)
        return "\n\n".join(parts)

    text = build(boxes)
    trimmed = False
    while len(text) > MAX_TEXT and len(boxes) > 1:      # never exceed Telegram's limit
        boxes.pop()
        trimmed = True
        text = build(boxes, "<i>… page chhota kiya gaya (length limit)</i>")
    return text


# ───────────────────────────── DM text for requesters ───────────────────────────── #

def user_message(status, mention, title, n_users=1, finfo=None) -> str:
    t = esc(title)
    hello = f"👋 {sc('hey')} {mention},"

    def card(head, icon):
        return f"{top(head, icon)}\n┃ 🎬 <b>{t}</b>\n{BOX_END}"

    if status == "uploaded":
        extra = []
        if finfo:
            if finfo.get("quality"):
                extra.append(f"┃ 📀 <b>{esc(finfo['quality'])}</b>")
            if finfo.get("langs"):
                extra.append(f"┃ 🌐 <b>{esc(', '.join(sorted(finfo['langs'])))}</b>")
        extra.append(f"┃ 👥 {sc('users')} : <b>{n_users}</b>")
        box = f"{top('UPLOADED', '🎉')}\n┃ 🎬 <b>{t}</b>\n" + "\n".join(extra) + f"\n{BOX_END}"
        return (
            f"{box}\n\n{hello}\n"
            f"✅ Aapki request <b>upload ho gayi</b> hai!\n"
            f"🔍 Group me search karke download kar lo.\n"
            f"📨 Sabhi users ko notification bhej di gayi."
        )
    if status == "cam_only":
        return (f"{card('CAM / LOW QUALITY', '🎥')}\n\n{hello}\n"
                f"⚠️ Abhi sirf <b>CAM / low-quality</b> print aaya hai.\n"
                f"🔔 HD print aate hi aapko dobara notification mil jayegi.")
    if status == "processing":
        return (f"{card('REQUEST ACCEPTED', '🛠')}\n\n{hello}\n"
                f"⏳ Is par kaam chalu hai.\n🔔 Upload hote hi aapko notification mil jayegi.")
    if status == "unavailable":
        return f"{card('UNAVAILABLE', '⚠️')}\n\n{hello}\n💔 Ye abhi available nahi hai."
    if status == "already":
        return (f"{card('ALREADY AVAILABLE', '♻️')}\n\n{hello}\n"
                f"✅ Ye pehle se available hai.\n🔍 Group me search karke dekho.")
    if status == "not_released":
        return f"{card('NOT RELEASED', '📌')}\n\n{hello}\n🕊️ Ye abhi release nahi hui hai."
    if status == "wrong_spelling":
        return (f"{card('WRONG SPELLING', '♨️')}\n\n{hello}\n"
                f"❗ Naam galat hai.\n✍️ Sahi spelling ke saath dobara request karo.")
    if status == "no_hindi":
        return f"{card('HINDI NOT AVAILABLE', '⚜️')}\n\n{hello}\n❌ Ye Hindi me abhi available nahi hai."
    if status == "no_dub":
        return f"{card('DUB NOT AVAILABLE', '🎙')}\n\n{hello}\n❌ Dubbed version abhi available nahi hai."
    if status == "need_details":
        return (f"{card('MORE DETAILS NEEDED', '📝')}\n\n{hello}\n"
                f"✍️ Naam + Year + Language ke saath dobara request karo:\n"
                f"<code>/request Stree 2 2024 Hindi</code>")
    return f"{card(str(status).upper(), 'ℹ️')}\n\n{hello}"
