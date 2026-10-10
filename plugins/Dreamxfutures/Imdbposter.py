
import re
import asyncio
import aiohttp
import warnings
import logging
from io import BytesIO
from PIL import Image
import hashlib
from info import DREAMXBOTZ_IMAGE_FETCH, TMDB_API_KEY
from imdbkit import IMDBKit


logger = logging.getLogger(__name__)

ia = IMDBKit(tmdb_api_key=TMDB_API_KEY, region="IN")

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
        if imdb_id and not str(imdb_id).startswith(("tt", "tmdb:")):
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
            'backdrop_url': getattr(movie, "backdrop_url", None),
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
    
    # ✅ [NEW] Neutralize 'a' and 'e' vowels to bridge Mere vs Mera differences
    t = t.replace('a', 'x').replace('e', 'x')
    
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

    # ✅ [NEW] Strict year verification enforces boundary check for both series and films
    if year:
        for tol in (0, 1):
            for r in matches:
                if _year_close(r.get(date_key), year, tol):
                    return r
        return None

    return matches

    # Series: the year in the filename is the season's year. A show can't have
    # started AFTER that, so prefer a show whose first_air_date <= that year.
    if year:
        for r in matches:
            fa = (r.get(date_key) or "")[:4]
            if fa and fa.isdigit() and abs(int(fa) - int(year)) <= 1:
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
POSTER_BRAND = os.environ.get("POSTER_BRAND", "Tokyo_Updates")
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

    # Leading article
    while toks and toks[0] in ("the", "a", "an"):
        toks = toks[1:]

    # Release noise
    noise = {
        "movie",
        "film",
        "web",
        "series",
        "show",
        "poster",
        "official",
        "hd",
        "hq",
        "full",
        "watch",
        "download",
        "hindi",
        "dubbed",
        "part",
        "episode",
        "season",
        "ep",
        "pt",
    }

    cleaned = []

    for tok in toks:
        if tok in noise:
            continue

        if _YEAR_RE.match(tok):
            continue

        if _SIZE_RE.match(tok):
            continue

        cleaned.append(tok)

    return cleaned


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

            ys = {
                int(t)
                for _, toks in srcs
                for t in toks
                if _YEAR_RE.match(t)
            }

            # Year available hai to verify karo.
            # Year missing hai to poster reject MAT karo.
            if ys:
                if y in ys:
                    score += 8
                elif any(abs(v - y) <= 1 for v in ys):
                    score += 5
                else:
                    # Clearly different year -> reject.
                    return None
            else:
                # Google metadata me year nahi mila,
                # lekin exact title mil gaya -> allow.
                score += 2

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
    """
    Keyless Google Images scraper.

    Google HTML layout change hone par multiple URL patterns try karta hai.
    Thumbnail URLs ko reject karta hai.
    """

    params = {
        "q": query,
        "tbm": "isch",
        "hl": "en",
        "gl": "us",
        "safe": "active",
        "tbs": "iar:t"
    }

    headers = {
        "User-Agent": _BROWSER_UA,
        "Accept-Language": "en-US,en;q=0.9",
        "Accept": "text/html,application/xhtml+xml"
    }

    async with session.get(
        "https://www.google.com/search",
        params=params,
        headers=headers,
        timeout=_GOOGLE_TIMEOUT
    ) as resp:

        if resp.status != 200:
            raise RuntimeError(
                f"HTTP {resp.status}"
            )

        final_url = str(resp.url)
        html = await resp.text(
            errors="ignore"
        )

    low_html = html.lower()

    if (
        "consent.google" in final_url.lower()
        or "unusual traffic" in low_html
        or "/sorry/" in final_url.lower()
        or "captcha" in low_html
    ):
        raise RuntimeError(
            "Google consent/captcha/block page"
        )

    out = []
    seen = set()

    def add_url(url):
        if not url:
            return

        url = str(url)

        if not url.startswith("http"):
            return

        low = url.lower()

        # Google thumbnails / tracking images reject.
        if any(
            x in low
            for x in (
                "gstatic.com",
                "encrypted-tbn",
                "googleusercontent.com"
            )
        ):
            return

        if url in seen:
            return

        seen.add(url)

        out.append({
            "url": url,
            "title": None,
            "context": None,
            "width": None,
            "height": None
        })

    # Pattern 1: old Google image array format.
    for m in re.finditer(
        r'\["(https?://(?:[^"\\]|\\.)+?)",\s*(\d{2,5}),\s*(\d{2,5})\]',
        html
    ):
        raw = m.group(1)

        try:
            url = json.loads(
                f'"{raw}"'
            )
        except Exception:
            url = raw

        add_url(url)

        if out:
            out[-1]["width"] = int(m.group(2))
            out[-1]["height"] = int(m.group(3))

        if len(out) >= 50:
            break

    # Pattern 2: escaped URLs used by newer Google layouts.
    if len(out) < 10:
        for m in re.finditer(
            r'https?://[^"\'\\\s<>]+',
            html
        ):
            raw = m.group(0)

            try:
                url = (
                    raw
                    .replace("\\u003d", "=")
                    .replace("\\u0026", "&")
                    .replace("\\/", "/")
                )
            except Exception:
                url = raw

            # Only likely image URLs.
            if re.search(
                r'\.(?:jpg|jpeg|png|webp)(?:\?|$)',
                url,
                re.IGNORECASE
            ):
                add_url(url)

            if len(out) >= 50:
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

    clean_title = " ".join(
        _gfx_title_tokens(title)
    ).strip()

    if not clean_title:
        logger.info(
            f"[POSTER] Google Images skipped: "
            f"empty clean title for '{title}'"
        )
        return None

    kind_word = (
        "web series poster"
        if kind == "series"
        else "movie poster"
    )

    if year:
        queries = [
            f'{clean_title} {year} {kind_word}',
            f'{clean_title} {year} poster',
            f'{clean_title} poster {year}'
        ]
    else:
        queries = [
            f'{clean_title} {kind_word}',
            f'{clean_title} poster'
        ]

    logger.info(
        f"[POSTER] Google Images fallback started: "
        f'"{clean_title}"'
    )

    try:
        session = await get_session()

        scored = []
        total_candidates = 0

        for query in queries:

            logger.info(
                f"[POSTER] Google query: {query}"
            )

            cands = []

            if GOOGLE_API_KEY and GOOGLE_CSE_ID:
                try:
                    cands = await _google_cse_images(
                        session,
                        query
                    )
                except Exception as e:
                    logger.warning(
                        f"[POSTER] Google CSE API failed: {e}"
                    )

            if not cands:
                try:
                    cands = await _google_images_scrape(
                        session,
                        query
                    )
                except Exception as e:
                    logger.warning(
                        f"[POSTER] Google Images failed: {e}"
                    )
                    cands = []

            total_candidates += len(cands)

            for c in cands:
                s = _score_google_candidate(
                    c,
                    title_tokens,
                    year
                )

                if s is not None:
                    scored.append((s, c))

            # Kisi query se valid candidates mil gaye
            # to unnecessary extra queries mat chalao.
            if scored:
                break

        scored.sort(
            key=lambda x: x[0],
            reverse=True
        )

        if not scored:
            logger.info(
                f"[POSTER] Google Images failed: "
                f"no valid title match among "
                f"{total_candidates} results for '{title}'"
            )
            return None
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
# Custom generated poster (16:9, Tokyo cinematic UI)
# ----------------------------------------------------------------------------

_CUSTOM_MARK = "tpbposter"
_CP_W, _CP_H = 1280, 720

def _make_custom_poster_url(title, year=None):
    """Create an internal URL marker for generated custom posters."""
    return (
        f"https://{_CUSTOM_MARK}/poster?"
        f"title={quote(str(title or ''))}"
        f"&year={quote(str(year or ''))}"
    )


def _parse_custom_poster_url(url):
    """Parse internal custom poster URL and return (title, year)."""
    try:
        if not url:
            return None

        parsed = urlparse(str(url))

        if parsed.netloc != _CUSTOM_MARK:
            return None

        params = parse_qs(parsed.query)

        title = unquote(
            params.get("title", [""])[0]
        ).strip()

        year = unquote(
            params.get("year", [""])[0]
        ).strip() or None

        if not title:
            return None

        return title, year

    except Exception as e:
        logger.warning(
            f"[POSTER] Failed to parse custom poster URL: {e}"
        )
        return None

# Local Tokyo cinematic backgrounds.
# Randomly one background is selected for every generated poster.
_CP_BG_DIRS = (
    os.path.join(os.getcwd(), "assets", "poster_bg"),
    os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                 "assets", "poster_bg"),
)

_CP_BG_NAMES = (
    "tokyo_street_01.jpg",
    "tokyo_street_02.jpg",
    "tokyo_tower_01.jpg",
    "tokyo_neon_01.jpg",
    "tokyo_city_01.jpg",
    "StreetPunk - Midjourney.jpeg",
    "StreetPunk - Midjourney.jpg",
    "StreetPunk_Midjourney.jpeg",
    "StreetPunk.jpeg",
)

POSTER_BRAND = os.environ.get(
    "POSTER_BRAND",
    "[Tokyo_Updates]"
)

CUSTOM_POSTER_BG = os.environ.get(
    "CUSTOM_POSTER_BG",
    ""
)

CUSTOM_POSTER_FONT = os.environ.get(
    "CUSTOM_POSTER_FONT",
    ""
)


def _find_backgrounds():
    """
    Find all available poster backgrounds.

    Priority:
    1. CUSTOM_POSTER_BG
    2. assets/poster_bg/
    3. old StreetPunk fallback
    """
    found = []

    # Explicit custom background
    if CUSTOM_POSTER_BG:
        if os.path.isabs(CUSTOM_POSTER_BG):
            if os.path.isfile(CUSTOM_POSTER_BG):
                found.append(CUSTOM_POSTER_BG)
        else:
            for d in _CP_BG_DIRS:
                p = os.path.join(d, CUSTOM_POSTER_BG)
                if os.path.isfile(p):
                    found.append(p)

    # Standard Tokyo backgrounds
    for d in _CP_BG_DIRS:
        if not os.path.isdir(d):
            continue

        for name in _CP_BG_NAMES:
            p = os.path.join(d, name)
            if os.path.isfile(p) and p not in found:
                found.append(p)

        # Also pick any jpg/jpeg/png/webp from poster_bg
        try:
            for name in sorted(os.listdir(d)):
                if name.lower().endswith(
                    (".jpg", ".jpeg", ".png", ".webp")
                ):
                    p = os.path.join(d, name)
                    if os.path.isfile(p) and p not in found:
                        found.append(p)
        except Exception:
            pass

    return found


def _pick_background(title):
    """
    Deterministic-random background.

    Same movie title -> same background during repeated rendering,
    but different movie titles get different backgrounds.
    """
    backgrounds = _find_backgrounds()

    if not backgrounds:
        return None

    digest = hashlib.md5(
        str(title).encode("utf-8", errors="ignore")
    ).hexdigest()

    index = int(digest[:8], 16) % len(backgrounds)

    return backgrounds[index]


def _cp_background(title):
    W, H = _CP_W, _CP_H
    path = _pick_background(title)

    if path:
        try:
            bg = ImageOps.fit(
                Image.open(path).convert("RGB"),
                (W, H),
                Image.LANCZOS
            )

            # Strong cinematic dark overlay.
            shade = Image.new("RGB", (W, H), (4, 5, 12))
            bg = Image.blend(bg, shade, 0.48)

            # Bottom dark gradient for title readability.
            gradient = Image.new("L", (1, H))

            for y in range(H):
                if y < int(H * 0.38):
                    value = 25
                else:
                    value = int(
                        25 +
                        ((y - H * 0.38) / (H * 0.62)) * 210
                    )

                gradient.putpixel((0, y), min(255, value))

            gradient = gradient.resize((W, H))

            dark = Image.new("RGB", (W, H), (0, 0, 0))
            bg = Image.composite(dark, bg, gradient)

            return bg

        except Exception as e:
            logger.warning(
                f"[POSTER] Tokyo background load failed "
                f"({path}): {e}"
            )

    # No image found -> procedural Tokyo-style fallback.
    # This prevents the poster system from breaking.
    bg = Image.new("RGB", (W, H), (7, 8, 18))
    draw = ImageDraw.Draw(bg)

    # Skyline
    random.seed(
        int(
            hashlib.md5(
                str(title).encode(
                    "utf-8",
                    errors="ignore"
                )
            ).hexdigest()[:8],
            16
        )
    )

    x = 0

    while x < W:
        bw = random.randint(35, 90)
        bh = random.randint(90, 390)

        draw.rectangle(
            (
                x,
                H - bh,
                x + bw,
                H
            ),
            fill=(
                random.randint(10, 25),
                random.randint(10, 25),
                random.randint(25, 50)
            )
        )

        # Building windows
        for wy in range(
            H - bh + 25,
            H - 20,
            28
        ):
            for wx in range(
                x + 10,
                x + bw - 8,
                20
            ):
                if random.random() > 0.45:
                    draw.rectangle(
                        (
                            wx,
                            wy,
                            wx + 6,
                            wy + 10
                        ),
                        fill=(
                            255,
                            random.randint(100, 220),
                            random.randint(40, 130)
                        )
                    )

        x += bw + random.randint(8, 18)

    # Tokyo Tower-like silhouette
    tx = int(W * 0.77)

    draw.polygon(
        [
            (tx - 50, H - 40),
            (tx, 180),
            (tx + 50, H - 40)
        ],
        fill=(20, 22, 35)
    )

    draw.line(
        (tx, 180, tx, H - 40),
        fill=(180, 50, 90),
        width=6
    )

    draw.line(
        (tx - 50, H - 40, tx, 180),
        fill=(130, 40, 70),
        width=4
    )

    draw.line(
        (tx + 50, H - 40, tx, 180),
        fill=(130, 40, 70),
        width=4
    )

    # Neon glow
    glow = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    gd = ImageDraw.Draw(glow)

    gd.ellipse(
        (-250, -220, 500, 420),
        fill=(255, 25, 130, 110)
    )

    gd.ellipse(
        (W - 520, H - 380, W + 220, H + 250),
        fill=(0, 150, 255, 100)
    )

    glow = glow.filter(
        ImageFilter.GaussianBlur(130)
    )

    bg = Image.alpha_composite(
        bg.convert("RGBA"),
        glow
    ).convert("RGB")

    return bg


def _cp_font(size):
    cands = [
        CUSTOM_POSTER_FONT,
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
        "/usr/share/fonts/truetype/freefont/FreeSansBold.ttf",
        "DejaVuSans-Bold.ttf",
        "arialbd.ttf"
    ]

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
    lines = []
    cur = ""

    for word in text.split():
        trial = f"{cur} {word}".strip()

        if draw.textlength(
            trial,
            font=font
        ) <= max_w or not cur:
            cur = trial
        else:
            lines.append(cur)
            cur = word

    if cur:
        lines.append(cur)

    return lines


def _draw_rounded_box(
    draw,
    xy,
    radius,
    fill,
    outline=None,
    width=1
):
    draw.rounded_rectangle(
        xy,
        radius=radius,
        fill=fill,
        outline=outline,
        width=width
    )


def _render_custom_poster_sync(title, year) -> BytesIO:
    W, H = _CP_W, _CP_H

    img = _cp_background(title).convert("RGBA")
    draw = ImageDraw.Draw(img)

    title_txt = re.sub(
        r"\s+",
        " ",
        str(title or "").strip()
    ).upper()

    if not title_txt:
        title_txt = "UNKNOWN TITLE"

    # ------------------------------------------------------------
    # TOP UI
    # ------------------------------------------------------------

    top_font = _cp_font(24)

    # Left badge
    _draw_rounded_box(
        draw,
        (48, 40, 270, 84),
        18,
        fill=(8, 10, 20, 215),
        outline=(255, 255, 255, 80),
        width=2
    )

    draw.text(
        (68, 49),
        "TOKYO CINEMA",
        font=top_font,
        fill=(255, 255, 255)
    )

    # Right badge
    right_txt = (
        "WEB SERIES"
        if year
        else "MOVIE"
    )

    rf = _cp_font(22)
    rw = draw.textlength(
        right_txt,
        font=rf
    )

    _draw_rounded_box(
        draw,
        (
            W - rw - 105,
            40,
            W - 48,
            84
        ),
        18,
        fill=(8, 10, 20, 215),
        outline=(255, 255, 255, 80),
        width=2
    )

    draw.text(
        (
            W - rw - 76,
            50
        ),
        right_txt,
        font=rf,
        fill=(240, 240, 250)
    )

    # ------------------------------------------------------------
    # CINEMATIC BORDER
    # ------------------------------------------------------------

    border_color = (255, 255, 255, 75)

    draw.rounded_rectangle(
        (
            24,
            24,
            W - 24,
            H - 24
        ),
        radius=22,
        outline=border_color,
        width=2
    )

    # Small corner accents
    accent = (255, 205, 80, 170)

    draw.line(
        (42, 110, 42, 155),
        fill=accent,
        width=4
    )

    draw.line(
        (42, 110, 87, 110),
        fill=accent,
        width=4
    )

    draw.line(
        (W - 42, H - 110, W - 42, H - 155),
        fill=accent,
        width=4
    )

    draw.line(
        (W - 42, H - 110, W - 87, H - 110),
        fill=accent,
        width=4
    )

    # ------------------------------------------------------------
    # TITLE
    # ------------------------------------------------------------

    max_w = int(W * 0.78)
    max_h = 300

    lines = [title_txt]
    font = _cp_font(56)
    line_h = 66

    for size in range(125, 48, -5):
        f = _cp_font(size)
        ls = _cp_wrap(
            draw,
            title_txt,
            f,
            max_w
        )

        lh = int(size * 1.12)

        if len(ls) <= 3 and len(ls) * lh <= max_h:
            lines = ls
            font = f
            line_h = lh
            break

    block_h = len(lines) * line_h

    year_font = _cp_font(46) if year else None
    year_txt = str(year) if year else ""

    year_w = (
        draw.textlength(
            year_txt,
            font=year_font
        )
        if year_font
        else 0
    )

    total_h = block_h + (
        72 if year else 0
    )

    y0 = int(
        (H - total_h) / 2
    ) - 12

    # Shadow layer
    shadow = Image.new(
        "RGBA",
        (W, H),
        (0, 0, 0, 0)
    )

    sd = ImageDraw.Draw(shadow)

    for i, line in enumerate(lines):
        tw = sd.textlength(
            line,
            font=font
        )

        sd.text(
            (
                (W - tw) / 2 + 6,
                y0 + i * line_h + 8
            ),
            line,
            font=font,
            fill=(0, 0, 0, 230)
        )

    shadow = shadow.filter(
        ImageFilter.GaussianBlur(9)
    )

    img = Image.alpha_composite(
        img,
        shadow
    )

    draw = ImageDraw.Draw(img)

    # Title panel
    panel_top = y0 - 28
    panel_bottom = y0 + block_h + (
        70 if year else 20
    )

    _draw_rounded_box(
        draw,
        (
            110,
            panel_top,
            W - 110,
            panel_bottom
        ),
        28,
        fill=(4, 6, 14, 115),
        outline=(255, 255, 255, 55),
        width=2
    )

    for i, line in enumerate(lines):
        tw = draw.textlength(
            line,
            font=font
        )

        # Slight title highlight/shadow
        draw.text(
            (
                (W - tw) / 2 + 2,
                y0 + i * line_h + 3
            ),
            line,
            font=font,
            fill=(10, 10, 18)
        )

        draw.text(
            (
                (W - tw) / 2,
                y0 + i * line_h
            ),
            line,
            font=font,
            fill=(255, 255, 255)
        )

    # ------------------------------------------------------------
    # YEAR BADGE
    # ------------------------------------------------------------

    if year:
        yy = y0 + block_h + 18

        badge_w = int(year_w + 58)

        _draw_rounded_box(
            draw,
            (
                (W - badge_w) / 2,
                yy,
                (W + badge_w) / 2,
                yy + 54
            ),
            18,
            fill=(15, 16, 28, 230),
            outline=(255, 190, 70, 190),
            width=2
        )

        draw.text(
            (
                (W - year_w) / 2,
                yy + 4
            ),
            year_txt,
            font=year_font,
            fill=(255, 205, 80)
        )

    # ------------------------------------------------------------
    # BOTTOM UI
    # ------------------------------------------------------------

    bottom_font = _cp_font(22)

    info_txt = "MOVIE • SERIES • WEB CONTENT"

    iw = draw.textlength(
        info_txt,
        font=bottom_font
    )

    draw.text(
        (
            (W - iw) / 2,
            H - 118
        ),
        info_txt,
        font=bottom_font,
        fill=(210, 215, 230)
    )

    # Divider
    draw.line(
        (
            int(W * 0.30),
            H - 83,
            int(W * 0.70),
            H - 83
        ),
        fill=(255, 255, 255, 100),
        width=2
    )

    # Branding + Telegram icon
    brand_font = _cp_font(30)

    brand_text = "Tokyo_Updates"

    # Telegram-style icon
    icon_size = 42
    icon_x = 0
    icon_y = H - 76

    brand_w = draw.textlength(
        brand_text,
        font=brand_font
    )

    total_brand_w = icon_size + 12 + brand_w

    start_x = (W - total_brand_w) / 2

    # Cyan circular icon
    draw.ellipse(
        (
            start_x,
            icon_y,
            start_x + icon_size,
            icon_y + icon_size
        ),
        fill=(40, 169, 224, 255)
    )

    # White Telegram paper-plane
    ix = start_x
    iy = icon_y

    draw.polygon(
        [
            (ix + 8, iy + 21),
            (ix + 34, iy + 8),
            (ix + 25, iy + 34),
            (ix + 20, iy + 25),
            (ix + 8, iy + 21),
        ],
        fill=(255, 255, 255, 255)
    )

    draw.polygon(
        [
            (ix + 20, iy + 25),
            (ix + 34, iy + 8),
            (ix + 25, iy + 34),
        ],
        fill=(235, 235, 235, 255)
    )

    draw.text(
        (
            start_x + icon_size + 12,
            H - 72
        ),
        brand_text,
        font=brand_font,
        fill=(255, 255, 255)
    )

    # Small corner branding
    small_font = _cp_font(17)

    draw.text(
        (50, H - 52),
        "© TOKYO UPDATES",
        font=small_font,
        fill=(185, 190, 205)
    )

    draw.text(
        (W - 190, H - 52),
        "OFFICIAL",
        font=small_font,
        fill=(185, 190, 205)
    )

    out = BytesIO()

    img.convert("RGB").save(
        out,
        format="JPEG",
        quality=92,
        optimize=True
    )

    out.seek(0)

    return out


async def _render_custom_poster(title, year):
    try:
        return await asyncio.to_thread(
            _render_custom_poster_sync,
            title,
            year
        )

    except Exception as e:
        logger.error(
            f"[POSTER] Custom poster render failed "
            f"for '{title}': {e}"
        )

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
