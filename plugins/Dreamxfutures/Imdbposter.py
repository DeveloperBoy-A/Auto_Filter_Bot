
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
