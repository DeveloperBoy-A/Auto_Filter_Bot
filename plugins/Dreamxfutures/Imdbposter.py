
import re
import asyncio
import aiohttp
import warnings
import logging
from io import BytesIO
from PIL import Image
from info import DREAMXBOTZ_IMAGE_FETCH, TMDB_API_KEY
from imdbkit import IMDBKit


logger = logging.getLogger(__name__)

ia = IMDBKit()

LONG_IMDB_DESCRIPTION = False

Image.MAX_IMAGE_PIXELS = None
warnings.simplefilter("ignore", Image.DecompressionBombWarning)

_session: aiohttp.ClientSession | None = None


async def get_session():
    global _session
    if _session is None or _session.closed:
        _session = aiohttp.ClientSession()
    return _session


def _resize_image_sync(data: bytes, size) -> BytesIO:
    """CPU-heavy PIL work, run off the event loop via asyncio.to_thread."""
    img = Image.open(BytesIO(data))
    img = img.resize(size, Image.LANCZOS)

    out = BytesIO()
    img.save(out, format="JPEG")
    out.seek(0)
    return out


async def fetch_image(url, size=(860, 1200)):
    # Custom fallback poster (URL me title/year encoded hai) -> khud render karo, 16:9 BytesIO
    _custom = _parse_custom_poster_url(url)
    if _custom:
        return await _render_custom_poster(*_custom)

    if not DREAMXBOTZ_IMAGE_FETCH:
        logger.info("Image fetching is disabled.")
        return url

    try:
        session = await get_session()

        async with session.get(url) as response:
            if response.status != 200:
                logger.error(f"Failed to fetch image: {response.status} for {url}")
                return None

            data = await response.read()
            return await asyncio.to_thread(_resize_image_sync, data, size)

    except aiohttp.ClientError as e:
        logger.error(f"HTTP request error in fetch_image: {e}")
    except IOError as e:
        logger.error(f"I/O error in fetch_image: {e}")
    except Exception as e:
        logger.error(f"Unexpected error in fetch_image: {e}")

    return None


async def close_session():
    global _session
    if _session and not _session.closed:
        await _session.close()

def list_to_str(value):
    if value is None:
        return ""

    # Already string
    if isinstance(value, str):
        return value

    # Integer / Float
    if isinstance(value, (int, float)):
        return str(value)

    # List / Tuple / Set
    if isinstance(value, (list, tuple, set)):
        return ", ".join(map(str, value))

    # Anything else
    return str(value)

def _kind_bucket(k):
    """Maps IMDb 'kind' strings to 'series' / 'movie' / None."""
    k = str(k or "").lower().replace(" ", "").replace("_", "")
    if k in ("tvseries", "tvminiseries"):
        return "series"
    if k in ("movie", "tvmovie", "video"):
        return "movie"
    return None


async def get_movie_details(query, id=False, file=None, kind=None):
    """
    kind: "series" | "movie" | None.
    Pass kind so a web series never gets a same-named movie's poster (and vice versa).
    """
    try:
        if not id:
            query = query.strip().lower()
            title = query
            year = re.findall(r'[1-2]\d{3}$', query, re.IGNORECASE)
            if year:
                year = list_to_str(year[:1])
                title = query.replace(year, "").strip()
            elif file is not None:
                year = re.findall(r'[1-2]\d{3}', file, re.IGNORECASE)
                if year:
                    year = list_to_str(year[:1])
            else:
                year = None

            try:
                search_result = await asyncio.to_thread(ia.search_movie, title.lower())
            except Exception as e:
                logger.warning(f"IMDb search failed for '{title}': {e}")
                return None

            if not search_result or not search_result.titles:
                return None

            movie_list = search_result.titles[:10]

            def _ordered(lst):
                # Preferred kind first (series for series, movie for movie), then the rest.
                pref = [m for m in lst if kind and _kind_bucket(getattr(m, "kind", None)) == kind]
                other = [m for m in lst if m not in pref and _kind_bucket(getattr(m, "kind", None))]
                rest = [m for m in lst if m not in pref and m not in other]
                return pref + other + rest

            def _pick(cands):
                for m in _ordered(cands):
                    if _title_matches(getattr(m, "title", "") or "", title):
                        return m
                return None

            def _year_ok(m, tol):
                try:
                    return abs(int(m.year) - int(year)) <= tol
                except Exception:
                    return False

            # Title match is mandatory. Year is enforced for movies (exact, then +-1).
            # For series the year in the filename is the SEASON year, not the show's
            # first-air year, so we don't reject on year there.
            if year and kind != "series":
                best = (_pick([m for m in movie_list if _year_ok(m, 0)])
                        or _pick([m for m in movie_list if _year_ok(m, 1)]))
            else:
                best = _pick(movie_list)

            if not best:
                logger.info(
                    f"[IMDb] No title match for '{title}' ({year}) among search results - "
                    f"skipping poster to avoid a wrong match."
                )
                return None

            movieid = best.imdb_id
        else:
            movieid = query

        movie = await asyncio.to_thread(ia.get_movie, movieid)
        if not movie:
            return None

        if movie.release_date:
            date = movie.release_date
        elif movie.year:
            date = str(movie.year)
        else:
            date = "N/A"

        plot = movie.plot[0] if isinstance(movie.plot, list) else (movie.plot or "")
        if plot and len(plot) > 800:
            plot = plot[:800] + "..."

        imdb_id = movie.imdb_id
        if imdb_id and not str(imdb_id).startswith("tt"):
            imdb_id = f"tt{imdb_id}"

        poster_url = movie.cover_url

        return {
            'title': movie.title,
            'votes': movie.votes,
            "aka": list_to_str(getattr(movie, "title_akas", None)),
            "seasons": (
                len(movie.info_series.display_seasons)
                if getattr(movie, "info_series", None)
                and getattr(movie.info_series, "display_seasons", None)
                else None
            ),
            "box_office": getattr(movie, "worldwide_gross", None),
            'localized_title': getattr(movie, "title_localized", None),
            'kind': movie.kind,
            "imdb_id": imdb_id,
            "cast": list_to_str(getattr(movie, "stars", None)),
            "runtime": list_to_str(getattr(movie, "duration", None)),
            "countries": list_to_str(getattr(movie, "countries", None)),
            "certificates": list_to_str(getattr(movie, "certificates", None)),
            "languages": list_to_str(getattr(movie, "languages", None)),
            "director": list_to_str(getattr(movie, "directors", None)),
            "writer": list_to_str([p.name for p in movie.writers]) if getattr(movie, "writers", None) else "",
            "producer": list_to_str([p.name for p in movie.producers]) if getattr(movie, "producers", None) else "",
            "composer": list_to_str([p.name for p in movie.composers]) if getattr(movie, "composers", None) else "",
            "cinematographer": list_to_str([p.name for p in movie.cinematographers]) if getattr(movie, "cinematographers", None) else "",
            "music_team": list_to_str([p.name for p in movie.music_team]) if getattr(movie, "music_team", None) else "",
            "distributors": list_to_str([c.name for c in movie.distributors]) if getattr(movie, "distributors", None) else "",
            'release_date': date,
            'year': movie.year,
            'genres': list_to_str(getattr(movie, "genres", None)),
            'poster_url': poster_url,
            'plot': plot,
            'rating': str(movie.rating) if getattr(movie, "rating", None) else "N/A",
            'url': getattr(movie, "url", None) or (f'https://www.imdb.com/title/{imdb_id}' if imdb_id else "")
        }
    except Exception as e:
        logger.exception(f"An error occurred in get_movie_details: {e}")
        return None

def _split_title_year(query: str):
    """Splits a 'Title YYYY' style query into (title, year). year is None if not found."""
    q = str(query).strip()
    m = re.search(r'(?:^|\s)([1-2]\d{3})\s*$', q)
    if m:
        year = m.group(1)
        title = q[:m.start()].strip()
        return title, year
    return q, None


def _year_close(date, year, tol=0) -> bool:
    """True if `date` ('YYYY-MM-DD' or 'YYYY') is within `tol` years of `year`.
    Unknown year / missing date => True (nothing to compare)."""
    if not year or not date:
        return True
    try:
        return abs(int(str(date)[:4]) - int(year)) <= tol
    except ValueError:
        return True


# ============================================================
# Strict Title Match
# ============================================================
# Year alone is not enough to pick the right poster: the same year
# can have "Immortal", "Immortal Combat", "The Immortal", etc. This
# guards against attaching one title's poster to a different title
# that merely shares part of the name.

def _normalize_title_for_match(t) -> str:
    if not t:
        return ""

    t = str(t).lower().strip()
    t = t.replace("&", " and ")
    t = re.sub(r'[^a-z0-9 ]', '', t)
    t = re.sub(r'\s+', ' ', t).strip()

    return t


def _strip_leading_article(s: str) -> str:
    return re.sub(r'^(the|a|an)\s+', '', s)


def _title_matches(candidate_title, expected_title) -> bool:
    """
    Strict (not substring) title comparison. Rejects 'Immortal Combat'
    or 'The Immortal Man' as a match for 'Immortal', while tolerating
    case / punctuation / leading-article / spacing differences
    ('Spider-Man' == 'Spider Man' == 'Spiderman').
    """
    c = _normalize_title_for_match(candidate_title)
    e = _normalize_title_for_match(expected_title)

    if not e:
        return True      # nothing to compare against
    if not c:
        return False     # candidate has no title -> can't verify -> reject

    c = _strip_leading_article(c).replace(" ", "")
    e = _strip_leading_article(e).replace(" ", "")
    return c == e


_TMDB_GENRE_FIX = {"Science Fiction": "Sci-Fi"}


async def _tmdb_search_best(session, media_type, title, year, strict_year):
    """
    Search TMDB (media_type: 'movie' | 'tv') and return the single best result that
    actually matches the title (and year when it makes sense). Returns None otherwise.
    """
    params = {"api_key": TMDB_API_KEY, "query": title, "include_adult": "false"}
    async with session.get(f"https://api.themoviedb.org/3/search/{media_type}", params=params) as resp:
        if resp.status != 200:
            return None
        data = await resp.json()

    results = data.get("results") or []
    name_key = "title" if media_type == "movie" else "name"
    date_key = "release_date" if media_type == "movie" else "first_air_date"

    matches = [
        r for r in results
        if _title_matches(r.get(name_key), title)
        or _title_matches(r.get("original_" + name_key), title)
    ]
    if not matches:
        return None

    # Movies (and any cross-type fallback): year must match, exact first then +-1.
    if year and (media_type == "movie" or strict_year):
        for tol in (0, 1):
            for r in matches:
                if _year_close(r.get(date_key), year, tol):
                    return r
        return None

    # Series: the year in the filename is the season's year. A show can't have
    # started AFTER that, so prefer a show whose first_air_date <= that year.
    if year:
        for r in matches:
            fa = (r.get(date_key) or "")[:4]
            if not fa or (fa.isdigit() and int(fa) <= int(year)):
                return r

    return matches[0]


async def _tmdb_full_details(session, media_type, tmdb_id):
    params = {"api_key": TMDB_API_KEY, "append_to_response": "credits,external_ids"}
    async with session.get(f"https://api.themoviedb.org/3/{media_type}/{tmdb_id}", params=params) as resp:
        if resp.status != 200:
            return None
        full = await resp.json()

    is_tv = media_type == "tv"
    credits = full.get("credits", {}) or {}
    crew = credits.get("crew", []) or []
    cast = credits.get("cast", []) or []

    def crew_names(job):
        return ", ".join(c["name"] for c in crew if c.get("job") == job) or None

    genres = []
    for g in full.get("genres", []) or []:
        for part in g["name"].split("&"):   # TV: "Action & Adventure" -> Action, Adventure
            part = part.strip()
            genres.append(_TMDB_GENRE_FIX.get(part, part))

    date = full.get("first_air_date") if is_tv else full.get("release_date")
    if is_tv:
        ep_rt = full.get("episode_run_time") or []
        runtime = ep_rt[0] if ep_rt else None
        director = ", ".join(c["name"] for c in (full.get("created_by") or [])) or crew_names("Director")
        imdb_id = (full.get("external_ids") or {}).get("imdb_id")
    else:
        runtime = full.get("runtime")
        director = crew_names("Director")
        imdb_id = full.get("imdb_id")

    poster_path = full.get("poster_path")
    backdrop_path = full.get("backdrop_path")

    return {
        "title": full.get("name") if is_tv else full.get("title"),
        "kind": "tv series" if is_tv else "movie",
        "year": (date or "")[:4] or None,
        "release_date": date,
        "rating": round(full.get("vote_average") or 0, 1),
        "votes": int(full.get("vote_count") or 0),
        "runtime": runtime,
        "seasons": full.get("number_of_seasons") if is_tv else None,
        "certificates": None,
        "tmdb_url": f"https://www.themoviedb.org/{media_type}/{tmdb_id}",
        "genres": genres,
        "languages": [full.get("original_language")] if full.get("original_language") else [],
        "countries": [c["name"] for c in full.get("production_countries", []) or []],
        "director": director,
        "writer": crew_names("Writer") or crew_names("Screenplay"),
        "producer": crew_names("Producer"),
        "composer": crew_names("Original Music Composer"),
        "cinematographer": crew_names("Director of Photography"),
        "cast": ", ".join(c["name"] for c in cast[:6]) or None,
        "plot": full.get("overview"),
        "tagline": full.get("tagline"),
        "box_office": None,
        "distributors": [],
        "imdb_id": imdb_id,
        "tmdb_id": tmdb_id,
        "poster_url": f"https://image.tmdb.org/t/p/w1280{poster_path}" if poster_path else None,
        "backdrop_url": f"https://image.tmdb.org/t/p/w1280{backdrop_path}" if backdrop_path else None,
    }


async def _search_official_tmdb(title: str, year: str | None, kind: str | None = None):
    """
    Official TMDB search. Searches /tv first for series and /movie first for movies
    (previously only /movie was searched, so web series got a random movie's poster).
    Returns a details dict, or None if nothing matches the title (+year).
    """
    if not TMDB_API_KEY:
        return None
    try:
        session = await get_session()
        order = ["tv", "movie"] if kind == "series" else ["movie", "tv"]

        for i, media_type in enumerate(order):
            # Primary type: movie => strict year, tv => lenient year (season year).
            # Fallback type: always strict so we never attach a loosely-related poster.
            strict_year = (i > 0) or (media_type == "movie")
            chosen = await _tmdb_search_best(session, media_type, title, year, strict_year)
            if not chosen:
                continue
            details = await _tmdb_full_details(session, media_type, chosen.get("id"))
            if details and (details.get("poster_url") or details.get("backdrop_url")):
                return details

        logger.info(f"[TMDB] No exact title/year match for '{title}' ({year}, kind={kind})")
        return None
    except Exception as e:
        logger.warning(f"[TMDB] Official API error for '{title}' ({year}): {e}")
        return None


async def get_movie_detailsx(query, id=False, file=None, kind=None):
    """
    kind: "series" | "movie" | None (see get_movie_details).
    Order: official TMDB (strict) -> proxy (movies only, now validated) -> IMDb (strict).
    A missing poster is preferred over a wrong poster.
    """
    # base_url = "https://bharath-boy-api.vercel.app/api/movie-posters" Monthly limit reached
    base_url = "https://tmdb.blazeposters.workers.dev/api/movie-posters"
    q = str(query).strip()
    title, year = _split_title_year(q)

    # --- Step 0: Official TMDB search (accurate, title + year checked) ---
    official = await _search_official_tmdb(title, year, kind)
    if official and (official.get("poster_url") or official.get("backdrop_url")):
        return official

    # The proxy does a plain name search with no title/year checks of its own and is
    # movie-oriented, so for series go straight to the (strict) IMDb fallback.
    if kind == "series":
        return await get_movie_details(q, kind=kind)

    try:
        session = await get_session()
        params = {"query": q, "api_key": TMDB_API_KEY}

        async with session.get(base_url, params=params) as resp:
            if resp.status != 200:
                logger.error(f"API failed [{resp.status}] → switching to IMDb fallback")
                return await get_movie_details(q, kind=kind)

            data = await resp.json()
    except Exception as e:
        logger.error(f"API down → fallback IMDb: {e}")
        return await get_movie_details(q, kind=kind)

    # ✅ Validate the proxy answer. Before, whatever the proxy returned was trusted when
    # the filename had no year (typical for series) -> wrong poster on the post.
    proxy_titles = [data.get('title'), data.get('localized_title'), data.get('original_title')]
    if not any(_title_matches(t, title) for t in proxy_titles if t):
        logger.info(
            f"[TMDB proxy] Title mismatch for '{title}': got '{data.get('title')}' "
            f"— falling back to IMDb"
        )
        return await get_movie_details(q, kind=kind)

    if year and not _year_close(data.get("release_date") or data.get("year"), year, 1):
        logger.info(
            f"[TMDB proxy] Year mismatch for '{title}' ({year}): got "
            f"'{data.get('title')}' ({data.get('release_date')}) — falling back to IMDb"
        )
        return await get_movie_details(q, kind=kind)

    try:
        # Normalize fields
        details = {}
        details['title'] = data.get('title') or data.get('localized_title')
        details['year'] = data.get('year') or None
        details['release_date'] = data.get('release_date')
        details['rating'] = round(float(data.get('rating') or 0), 1) if data.get('rating') is not None else None
        details['votes'] = int(data.get('votes') or 0)
        details['runtime'] = data.get('runtime')
        details['certificates'] = data.get('certificates')
        details['tmdb_url'] = data.get('url')

        for key in ('genres', 'languages', 'countries'):
            raw = data.get(key)
            details[key] = [s.strip() for s in raw.split(',')] if raw else []
        for role in ('director', 'writer', 'producer', 'composer', 'cinematographer', 'cast'):
            raw = data.get(role)
            details[role] = [s.strip() for s in raw.split(',')] if raw else []

        details['plot'] = data.get('plot')
        details['tagline'] = data.get('tagline')
        details['box_office'] = data.get('box_office') or None
        raw_dist = data.get('distributors')
        details['distributors'] = [d.strip() for d in raw_dist.split(',')] if raw_dist else []
        details['imdb_id'] = data.get('imdb_id')
        details['tmdb_id'] = data.get('tmdb_id')

        posters = data.get('images', {}).get('posters', {})
        original_language = data.get('images', {}).get('original_language')
        poster_url = data.get('poster_url')
        if not poster_url:
            for key in ('en', original_language, 'xx'):
                if key and posters.get(key):
                    poster_url = posters[key][0]
                    break
        details['poster_url'] = poster_url.replace("/original/", "/w1280/") if poster_url else None
    except Exception as e:
        logger.error(f"Failed to parse TMDB response for '{q}', falling back to IMDb: {e}")
        return await get_movie_details(q, kind=kind)

    backdrops = data.get('images', {}).get('backdrops', {})
    original_language = data.get('images', {}).get('original_language')
    backdrop_url = None
    for key in ('en', original_language, 'xx'):
        if key and backdrops.get(key):
            backdrop_url = backdrops[key][0]
            break
    details['backdrop_url'] = backdrop_url.replace("/original/", "/w1280/") if backdrop_url else None

    # Proxy matched the title but had no image -> take the poster from (strict) IMDb.
    if not details.get('poster_url') and not details.get('backdrop_url'):
        try:
            imdb_details = await get_movie_details(q, kind=kind)
            if imdb_details and imdb_details.get('poster_url'):
                details['poster_url'] = imdb_details['poster_url']
                logger.info(f"[POSTER] TMDB se poster nahi mila, IMDb se liya: '{q}'")
        except Exception as e:
            logger.warning(f"IMDb poster fallback failed for '{q}': {e}")

    return details


# ============================================================================
# POSTER FALLBACK (ADDITIVE BLOCK)
#   Official TMDB -> TMDB Proxy -> IMDb -> Google Images -> Custom poster
#
# Is block se upar ka koi existing code change nahi hua. Sirf:
#   1) fetch_image() ke shuru me 3 lines add hui (custom poster ke liye)
#   2) get_movie_detailsx() ka purana version `_get_movie_detailsx_core` naam se
#      save hota hai aur neeche naya wrapper usi naam `get_movie_detailsx` se chalta hai.
# ============================================================================
import os
import json
import random
from urllib.parse import quote, unquote, urlparse, parse_qs

from PIL import ImageDraw, ImageFont, ImageFilter, ImageOps


def _env_on(name, default=True):
    return os.environ.get(name, str(default)).strip().lower() not in ("false", "0", "off", "no", "")


GOOGLE_POSTER_FALLBACK = _env_on("GOOGLE_POSTER_FALLBACK", True)
CUSTOM_POSTER_FALLBACK = _env_on("CUSTOM_POSTER_FALLBACK", True)
# Optional: Google Custom Search JSON API (sirf purane customers ke liye; 1 Jan 2027 ko band ho rahi hai)
GOOGLE_API_KEY = os.environ.get("GOOGLE_API_KEY", "")
GOOGLE_CSE_ID = os.environ.get("GOOGLE_CSE_ID", "")
# Branding / background / font (sab optional, env se badal sakte ho)
POSTER_BRAND = os.environ.get("POSTER_BRAND", "Tokyo PrincessBot")
CUSTOM_POSTER_BG = os.environ.get("CUSTOM_POSTER_BG", "")      # full path, ya file ka naam
CUSTOM_POSTER_FONT = os.environ.get("CUSTOM_POSTER_FONT", "")  # .ttf ka path

_GOOGLE_TIMEOUT = aiohttp.ClientTimeout(total=10)
_MAX_IMAGE_BYTES = 8 * 1024 * 1024
_GOOGLE_MAX_TRIES = 5
_GOOGLE_TOTAL_BUDGET = 30  # seconds

_BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)


# ----------------------------------------------------------------------------
# Strict matching for Google results (same principle as _title_matches)
# ----------------------------------------------------------------------------
# Title phrase ke aas-paas sirf ye "noise" words / year / size tokens allowed hain.
# "Immortal Combat", "The Immortal Man" jaisa koi aur word chipka ho to REJECT.
_GFX_NOISE = frozenset({
    "the", "a", "an", "movie", "film", "poster", "posters", "official", "hd", "hq", "uhd",
    "full", "watch", "download", "imdb", "wallpaper", "web", "series", "season", "tv", "show",
    "new", "latest", "trailer", "first", "look", "review", "cast", "hindi", "dubbed", "free",
    "online", "dvd", "bluray", "cover", "art", "key", "theatrical", "release", "image", "images",
    "photo", "jpg", "jpeg", "png", "webp", "large", "original", "medium", "thumb", "small",
    "wikipedia", "wiki", "tmdb", "themoviedb", "letterboxd", "mubi", "rotten", "tomatoes",
    "amazon", "netflix", "com", "org", "net", "www", "http", "https", "upload", "media", "en",
    "of", "on", "in", "and", "s",
})
_YEAR_RE = re.compile(r"^(?:19|20)\d{2}$")
_SIZE_RE = re.compile(r"^(?:\d+px|[wh]\d{2,4}|\d{2,4}x\d{2,4}|\d{3,4}p|s\d{1,2}|season\d{1,2}|v\d{1,2})$")


def _gfx_tokens(text):
    text = unquote(str(text or "")).lower().replace("&", " and ")
    return re.sub(r"[^a-z0-9]+", " ", text).split()


def _gfx_title_tokens(title):
    toks = _gfx_tokens(title)
    if len(toks) > 1 and toks[0] in ("the", "a", "an"):
        toks = toks[1:]
    return toks


def _gfx_neighbor_ok(tok):
    if tok is None:
        return True
    return tok in _GFX_NOISE or bool(_YEAR_RE.match(tok)) or bool(_SIZE_RE.match(tok))


def _gfx_phrase_status(tokens, title_tokens):
    """'match' = title milta hai aur aas-paas sirf noise/year; 'conflict' = title kisi
    lambe/dusre naam ka hissa hai; 'none' = title mila hi nahi."""
    n = len(title_tokens)
    if not n or len(tokens) < n:
        return "none"
    found = False
    for i in range(len(tokens) - n + 1):
        if tokens[i:i + n] == title_tokens:
            found = True
            before = tokens[i - 1] if i > 0 else None
            after = tokens[i + n] if i + n < len(tokens) else None
            if _gfx_neighbor_ok(before) and _gfx_neighbor_ok(after):
                return "match"
    return "conflict" if found else "none"


def _gfx_url_text(url):
    try:
        p = urlparse(str(url))
        return f"{p.path} {p.query}"
    except Exception:
        return ""


def _score_google_candidate(c, title_tokens, year):
    """Candidate ko score do; None = reject. c: {url,width,height,title,context}"""
    w, h = c.get("width") or 0, c.get("height") or 0
    if w and h:
        if min(w, h) < 300 or h < 450:
            return None                      # tiny / thumbnail
        if not (1.25 <= h / w <= 1.8):
            return None                      # poster-shaped nahi (stretch ho jata)

    srcs = []
    if c.get("title"):
        srcs.append(("title", _gfx_tokens(c["title"])))
    srcs.append(("url", _gfx_tokens(_gfx_url_text(c["url"]))))
    if c.get("context"):
        srcs.append(("context", _gfx_tokens(_gfx_url_text(c["context"]))))

    matches, title_hit = 0, False
    for name, toks in srcs:
        st = _gfx_phrase_status(toks, title_tokens)
        if st == "conflict":
            return None                      # kisi aur (lambe) title ka poster
        if st == "match":
            matches += 1
            title_hit = title_hit or name == "title"
    if not matches:
        return None                          # title ka koi saboot nahi

    score = matches * 10 + (4 if title_hit else 0)

    if year:
        try:
            y = int(year)
            ys = {int(t) for _, toks in srcs for t in toks if _YEAR_RE.match(t)}
            if ys:
                if any(abs(v - y) <= 1 for v in ys):
                    score += 8 if y in ys else 5
                else:
                    return None              # alag saal ka (remake/dusri film)
        except ValueError:
            pass

    if h:
        score += min(h, 1800) / 600.0
    return score


# ----------------------------------------------------------------------------
# Google Images: candidate collection (async, existing aiohttp session reuse)
# ----------------------------------------------------------------------------
async def _google_cse_images(session, query):
    params = {"key": GOOGLE_API_KEY, "cx": GOOGLE_CSE_ID, "q": query,
              "searchType": "image", "num": 10, "safe": "active"}
    async with session.get("https://www.googleapis.com/customsearch/v1",
                           params=params, timeout=_GOOGLE_TIMEOUT) as resp:
        data = await resp.json(content_type=None)
        if resp.status != 200:
            msg = (data.get("error") or {}).get("message") if isinstance(data, dict) else ""
            raise RuntimeError(f"CSE HTTP {resp.status}: {msg}")
    out = []
    for it in data.get("items") or []:
        img = it.get("image") or {}
        out.append({
            "url": it.get("link"), "title": it.get("title"),
            "context": img.get("contextLink"),
            "width": img.get("width"), "height": img.get("height"),
        })
    return [c for c in out if c["url"]]


async def _google_images_scrape(session, query):
    """Keyless best-effort: Google Images HTML se original image URLs nikalta hai.
    Fragile hai (Google layout badalta rehta hai / cloud IPs par block ho sakta hai)."""
    params = {"q": query, "tbm": "isch", "hl": "en", "gl": "us", "safe": "active", "tbs": "iar:t"}
    headers = {"User-Agent": _BROWSER_UA, "Accept-Language": "en-US,en;q=0.9"}
    async with session.get("https://www.google.com/search", params=params,
                           headers=headers, timeout=_GOOGLE_TIMEOUT) as resp:
        if resp.status != 200:
            raise RuntimeError(f"HTTP {resp.status}")
        final_url = str(resp.url)
        html = await resp.text()
    if "consent.google" in final_url or "unusual traffic" in html or "/sorry/" in final_url:
        raise RuntimeError("Google ne consent/captcha page diya (blocked)")

    out, seen = [], set()
    for m in re.finditer(r'\["(https?://(?:[^"\\]|\\.)+?)",\s*\d{2,5},\s*\d{2,5}\]', html):
        raw = m.group(1)
        try:
            url = json.loads(f'"{raw}"')
        except Exception:
            url = raw
        if "gstatic.com" in url or "encrypted-tbn" in url or url in seen:
            continue
        seen.add(url)
        out.append({"url": url, "title": None, "context": None, "width": None, "height": None})
        if len(out) >= 40:
            break
    return out


def _check_image_sync(data):
    img = Image.open(BytesIO(data))
    img.verify()
    img = Image.open(BytesIO(data))
    return img.size


async def _validate_remote_image(session, url):
    """Wahi request jo fetch_image() baad me karega: download + valid image + poster-shape."""
    try:
        async with session.get(url, timeout=_GOOGLE_TIMEOUT) as resp:
            if resp.status != 200:
                return False
            ctype = (resp.headers.get("Content-Type") or "").lower()
            if ctype and not (ctype.startswith("image/") or "octet-stream" in ctype):
                return False
            data = await resp.content.read(_MAX_IMAGE_BYTES + 1)
        if not data or len(data) > _MAX_IMAGE_BYTES:
            return False
        w, h = await asyncio.to_thread(_check_image_sync, data)
        return min(w, h) >= 300 and h >= 450 and 1.25 <= h / w <= 1.8
    except Exception as e:
        logger.debug(f"[POSTER] Google candidate invalid ({url[:80]}): {e}")
        return False


async def _google_poster_fallback(title, year, kind=None):
    """Return: valid poster image URL, ya None."""
    if not GOOGLE_POSTER_FALLBACK:
        return None
    title_tokens = _gfx_title_tokens(title)
    if len("".join(title_tokens)) < 3:
        logger.info(f"[POSTER] Google Images skipped (title too short): '{title}'")
        return None

    kind_word = "web series poster" if kind == "series" else "movie poster"
    query = f'"{title}" {year} {kind_word}' if year else f'"{title}" {kind_word}'
    logger.info(f"[POSTER] Google Images fallback started: {query}")

    try:
        session = await get_session()
        cands = []
        if GOOGLE_API_KEY and GOOGLE_CSE_ID:
            try:
                cands = await _google_cse_images(session, query)
            except Exception as e:
                logger.warning(f"[POSTER] Google CSE API failed: {e}")
        if not cands:
            try:
                cands = await _google_images_scrape(session, query)
            except Exception as e:
                logger.warning(f"[POSTER] Google Images failed: {e}")
                return None

        scored = []
        for c in cands:
            s = _score_google_candidate(c, title_tokens, year)
            if s is not None:
                scored.append((s, c))
        scored.sort(key=lambda x: x[0], reverse=True)

        if not scored:
            logger.info(f"[POSTER] Google Images failed: no strict title/year match "
                        f"among {len(cands)} results for '{title}'")
            return None

        for _, c in scored[:_GOOGLE_MAX_TRIES]:
            if await _validate_remote_image(session, c["url"]):
                logger.info(f"[POSTER] Google poster found: {c['url']}")
                return c["url"]

        logger.info("[POSTER] Google Images failed: matching candidates were not valid images")
    except Exception as e:
        logger.warning(f"[POSTER] Google Images failed: {e}")
    return None


# ----------------------------------------------------------------------------
# Custom generated poster (16:9, dark cinematic) - fetch_image() ke saath compatible
# ----------------------------------------------------------------------------
_CUSTOM_MARK = "tpbposter"
_CP_W, _CP_H = 1280, 720
_BG_NAMES = ("StreetPunk - Midjourney.jpeg", "StreetPunk - Midjourney.jpg",
             "StreetPunk_Midjourney.jpeg", "StreetPunk.jpeg")


def _custom_poster_base():
    try:
        from info import URL
        if URL and str(URL).startswith("http"):
            return str(URL).rstrip("/") + "/"
    except Exception:
        pass
    return "https://telegram.org/"


def _make_custom_poster_url(title, year):
    """Valid https URL (caption ke <a href> me chalega, bot ke web page par jata hai);
    title+year isi me encoded hai taaki DB me sirf string store ho aur fetch_image
    isse poster khud render kar le. Koi IMDb/TMDB link nahi."""
    val = quote(str(title), safe="") + "~" + (str(year) if year else "")
    return f"{_custom_poster_base()}?{_CUSTOM_MARK}={val}"


def _parse_custom_poster_url(url):
    if not isinstance(url, str) or f"{_CUSTOM_MARK}=" not in url:
        return None
    try:
        val = parse_qs(urlparse(url).query).get(_CUSTOM_MARK, [""])[0]
        title, _, year = val.rpartition("~")
        return (title, year or None) if title else None
    except Exception:
        return None


def _find_background():
    here = os.path.dirname(os.path.abspath(__file__))
    root = os.path.dirname(os.path.dirname(here))
    dirs = [os.getcwd(), root, here, os.path.join(root, "assets"),
            os.path.join(root, "images"), os.path.join(root, "plugins")]
    names = ([CUSTOM_POSTER_BG] if CUSTOM_POSTER_BG else []) + list(_BG_NAMES)
    for n in names:
        if os.path.isabs(n) and os.path.isfile(n):
            return n
        for d in dirs:
            p = os.path.join(d, n)
            if os.path.isfile(p):
                return p
    return None


def _cp_background():
    W, H = _CP_W, _CP_H
    path = _find_background()
    if path:
        try:
            bg = ImageOps.fit(Image.open(path).convert("RGB"), (W, H), Image.LANCZOS)
            shade = Image.new("RGB", (W, H), (0, 0, 0))
            bg = Image.blend(bg, shade, 0.55)           # text readable rahe
            return bg
        except Exception as e:
            logger.warning(f"[POSTER] Custom background load failed ({path}): {e}")

    # StreetPunk image nahi mili -> procedural dark cinematic background
    mask = Image.linear_gradient("L").resize((W, H))
    top, bottom = Image.new("RGB", (W, H), (8, 8, 16)), Image.new("RGB", (W, H), (30, 12, 44))
    bg = Image.composite(bottom, top, mask)
    glow = Image.new("RGB", (W, H), (0, 0, 0))
    gd = ImageDraw.Draw(glow)
    gd.ellipse((-220, -260, 520, 380), fill=(255, 45, 149))
    gd.ellipse((W - 560, H - 330, W + 240, H + 260), fill=(0, 190, 255))
    glow = glow.filter(ImageFilter.GaussianBlur(160))
    bg = Image.blend(bg, glow, 0.35)
    vign = Image.radial_gradient("L").resize((W, H))
    bg = Image.composite(bg, Image.new("RGB", (W, H), (0, 0, 0)), vign.point(lambda v: 255 - int(v * 0.55)))
    grain = Image.effect_noise((W, H), 22).convert("RGB")
    return Image.blend(bg, grain, 0.05)


def _cp_font(size):
    cands = [CUSTOM_POSTER_FONT,
             "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
             "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
             "/usr/share/fonts/truetype/freefont/FreeSansBold.ttf",
             "DejaVuSans-Bold.ttf", "arialbd.ttf"]
    for p in cands:
        if not p:
            continue
        try:
            return ImageFont.truetype(p, size)
        except Exception:
            continue
    try:
        return ImageFont.load_default(size=size)
    except TypeError:
        return ImageFont.load_default()


def _cp_wrap(draw, text, font, max_w):
    lines, cur = [], ""
    for word in text.split():
        trial = f"{cur} {word}".strip()
        if draw.textlength(trial, font=font) <= max_w or not cur:
            cur = trial
        else:
            lines.append(cur)
            cur = word
    if cur:
        lines.append(cur)
    return lines


def _render_custom_poster_sync(title, year) -> BytesIO:
    W, H = _CP_W, _CP_H
    img = _cp_background().convert("RGB")
    draw = ImageDraw.Draw(img)
    title_txt = str(title).strip().upper()

    # title ko width/height me fit karo (max 3 lines)
    max_w, max_h = int(W * 0.82), 330
    lines, font, lh = [title_txt], _cp_font(56), 70
    for size in range(150, 55, -6):
        f = _cp_font(size)
        ls = _cp_wrap(draw, title_txt, f, max_w)
        lh_ = int(size * 1.18)
        if len(ls) <= 3 and len(ls) * lh_ <= max_h:
            lines, font, lh = ls, f, lh_
            break
    else:
        font = _cp_font(56)
        lines, lh = _cp_wrap(draw, title_txt, font, max_w)[:3], 66

    block_h = len(lines) * lh
    year_h = 90 if year else 0
    y0 = (H - (block_h + year_h)) // 2 - 20

    # soft shadow
    shadow = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    sd = ImageDraw.Draw(shadow)
    for i, line in enumerate(lines):
        tw = sd.textlength(line, font=font)
        sd.text(((W - tw) / 2 + 4, y0 + i * lh + 6), line, font=font, fill=(0, 0, 0, 230))
    shadow = shadow.filter(ImageFilter.GaussianBlur(7))
    img = Image.alpha_composite(img.convert("RGBA"), shadow).convert("RGB")
    draw = ImageDraw.Draw(img)

    for i, line in enumerate(lines):
        tw = draw.textlength(line, font=font)
        draw.text(((W - tw) / 2, y0 + i * lh), line, font=font, fill=(255, 255, 255))

    if year:
        yf = _cp_font(58)
        spaced = " ".join(str(year))
        yw = draw.textlength(spaced, font=yf)
        yy = y0 + block_h + 18
        draw.text(((W - yw) / 2, yy), spaced, font=yf, fill=(255, 196, 61))
        draw.line(((W - 260) / 2, yy + 78, (W + 260) / 2, yy + 78), fill=(255, 45, 149), width=3)

    # brand: "Tokyo PrincessBot" + chhota TM (glyph-safe)
    bf, tmf = _cp_font(34), _cp_font(15)
    bw = draw.textlength(POSTER_BRAND, font=bf)
    tmw = draw.textlength("TM", font=tmf)
    bx = (W - (bw + tmw + 3)) / 2
    by = H - 78
    draw.text((bx, by), POSTER_BRAND, font=bf, fill=(235, 235, 245))
    draw.text((bx + bw + 3, by - 2), "TM", font=tmf, fill=(235, 235, 245))

    out = BytesIO()
    img.save(out, format="JPEG", quality=90)
    out.seek(0)
    return out


async def _render_custom_poster(title, year):
    try:
        return await asyncio.to_thread(_render_custom_poster_sync, title, year)
    except Exception as e:
        logger.error(f"[POSTER] Custom poster render failed for '{title}': {e}")
        return None


# ----------------------------------------------------------------------------
# Wrapper: TMDB -> Proxy -> IMDb (existing, unchanged) -> Google -> Custom
# ----------------------------------------------------------------------------
_get_movie_detailsx_core = get_movie_detailsx   # EXISTING function, bilkul unchanged


def _has_poster(d):
    return bool(d and (d.get("poster_url") or d.get("backdrop_url")))


def _fallback_details(base, title, year, poster_url, source):
    """Existing get_movie_detailsx() jaisi structure. base me (title-matched) metadata ho
    to wo rakhte hain, warna safe defaults. Custom poster ke liye koi IMDb/TMDB link nahi."""
    if base:
        d = dict(base)
        d.setdefault("tmdb_url", d.get("url") or "")
    else:
        d = {
            "title": title, "year": year or None,
            "release_date": str(year) if year else None,
            "rating": "N/A", "votes": 0, "runtime": None, "certificates": None,
            "tmdb_url": "", "url": "",
            "genres": [], "languages": [], "countries": [],
            "director": [], "writer": [], "producer": [], "composer": [],
            "cinematographer": [], "cast": [],
            "plot": "", "tagline": None, "box_office": None, "distributors": [],
            "imdb_id": None, "tmdb_id": None, "backdrop_url": None,
        }
    d["poster_url"] = poster_url
    d["poster_source"] = source
    return d


async def get_movie_detailsx(query, id=False, file=None, kind=None, poster_fallback=None):
    """
    Wrapper: pehle purana get_movie_detailsx (TMDB -> Proxy -> IMDb), phir
    Google Images, phir Custom poster.

    poster_fallback: None => auto (sirf jab `kind` diya ho, yani channel update flow).
    Isse post_handler / utils.get_posterx (jo poster_url seedha Telegram ko dete hain)
    pehle jaise hi chalte hain - unhe custom/Google fallback nahi milta.
    """
    result = None
    try:
        result = await _get_movie_detailsx_core(query, id=id, file=file, kind=kind)
    except Exception as e:
        logger.error(f"[POSTER] Primary poster lookup crashed for '{query}': {e}")

    use_fallback = (kind is not None) if poster_fallback is None else bool(poster_fallback)

    if _has_poster(result):
        if use_fallback:
            src = "TMDB" if result.get("tmdb_id") or result.get("tmdb_url") else "IMDb"
            logger.info(f"[POSTER] {src} poster found: {result.get('title') or query}")
        return result

    if not use_fallback or id:
        return result

    title, year = _split_title_year(str(query).strip())

    # ---- Google Images ----
    try:
        g_url = await asyncio.wait_for(
            _google_poster_fallback(title, year, kind), timeout=_GOOGLE_TOTAL_BUDGET
        )
    except Exception as e:
        logger.warning(f"[POSTER] Google Images failed: {e}")
        g_url = None
    if g_url:
        return _fallback_details(result, title, year, g_url, "google")

    # ---- Custom generated poster ----
    if CUSTOM_POSTER_FALLBACK:
        test = await _render_custom_poster(title, year)    # chal sakta hai ya nahi, pehle check
        if test is not None:
            logger.info(f"[POSTER] Using custom fallback poster: {title} {year or ''}".strip())
            return _fallback_details(result, title, year, _make_custom_poster_url(title, year), "custom")

    logger.error(f"[POSTER] All poster fallbacks failed: {query}")
    return result
