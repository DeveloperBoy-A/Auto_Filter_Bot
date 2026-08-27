
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

async def get_movie_details(query, id=False, file=None):
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

            if year:
                filtered = [m for m in movie_list if m.year and str(m.year) == str(year)]
                if not filtered:
                    filtered = movie_list
            else:
                filtered = movie_list

            kind_filter = ['movie', 'tv series', 'tvSeries', 'tvMiniSeries', 'tvMovie']
            filtered_kind = [m for m in filtered if m.kind and m.kind in kind_filter]
            if not filtered_kind:
                logger.info("No matches found for kind 'movie' or 'tv series', falling back to filtered list.")
                filtered_kind = filtered

            if not filtered_kind:
                return None

            movieid = filtered_kind[0].imdb_id
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


def _release_year_matches(release_date, year) -> bool:
    if not year:
        return True
    if not release_date:
        return True
    return str(release_date)[:4] == str(year)


async def _search_official_tmdb(title: str, year: str | None):
    """Direct TMDB search (accurate, year-filtered). Returns a details dict or None."""
    if not TMDB_API_KEY:
        return None
    try:
        session = await get_session()
        params = {"api_key": TMDB_API_KEY, "query": title, "include_adult": "false"}
        if year:
            params["year"] = year

        async with session.get(
            "https://api.themoviedb.org/3/search/movie", params=params
        ) as resp:
            if resp.status != 200:
                return None
            data = await resp.json()
            results = data.get("results") or []
            if not results:
                return None

            chosen = None
            if year:
                for r in results:
                    if _release_year_matches(r.get("release_date"), year):
                        chosen = r
                        break
            if not chosen:
                chosen = results[0]
                if year and not _release_year_matches(chosen.get("release_date"), year):
                    logger.info(
                        f"[TMDB] No {year} match for '{title}', closest is "
                        f"'{chosen.get('title')}' ({chosen.get('release_date')}) — skipping"
                    )
                    return None

        # Fetch full details (genres, cast, plot, etc.) using the correctly-matched movie id
        movie_id = chosen.get("id")
        async with session.get(
            f"https://api.themoviedb.org/3/movie/{movie_id}",
            params={"api_key": TMDB_API_KEY, "append_to_response": "credits"},
        ) as resp:
            if resp.status != 200:
                return None
            full = await resp.json()

        credits = full.get("credits", {})
        crew = credits.get("crew", []) or []
        cast = credits.get("cast", []) or []

        def crew_names(job):
            return ", ".join(c["name"] for c in crew if c.get("job") == job) or None

        poster_path = full.get("poster_path")
        backdrop_path = full.get("backdrop_path")

        return {
            "title": full.get("title"),
            "year": (full.get("release_date") or "")[:4] or None,
            "release_date": full.get("release_date"),
            "rating": round(full.get("vote_average") or 0, 1),
            "votes": int(full.get("vote_count") or 0),
            "runtime": full.get("runtime"),
            "certificates": None,
            "tmdb_url": f"https://www.themoviedb.org/movie/{movie_id}",
            "genres": [g["name"] for g in full.get("genres", [])],
            "languages": [full.get("original_language")] if full.get("original_language") else [],
            "countries": [c["name"] for c in full.get("production_countries", [])],
            "director": crew_names("Director"),
            "writer": crew_names("Writer") or crew_names("Screenplay"),
            "producer": crew_names("Producer"),
            "composer": crew_names("Original Music Composer"),
            "cinematographer": crew_names("Director of Photography"),
            "cast": ", ".join(c["name"] for c in cast[:6]) or None,
            "plot": full.get("overview"),
            "tagline": full.get("tagline"),
            "box_office": None,
            "distributors": [],
            "imdb_id": full.get("imdb_id"),
            "tmdb_id": movie_id,
            "poster_url": f"https://image.tmdb.org/t/p/w1280{poster_path}" if poster_path else None,
            "backdrop_url": f"https://image.tmdb.org/t/p/w1280{backdrop_path}" if backdrop_path else None,
        }
    except Exception as e:
        logger.warning(f"[TMDB] Official API error for '{title}' ({year}): {e}")
        return None


async def get_movie_detailsx(query, id=False, file=None):
    # base_url = "https://bharath-boy-api.vercel.app/api/movie-posters" Monthly limit reached
    base_url = "https://tmdb.blazeposters.workers.dev/api/movie-posters"
    q = str(query).strip()
    title, year = _split_title_year(q)

    # --- Step 0: Official TMDB search (accurate, year-filtered) ---
    official = await _search_official_tmdb(title, year)
    if official and (official.get("poster_url") or official.get("backdrop_url")):
        return official

    try:
        session = await get_session()
        params = {"query": q, "api_key": TMDB_API_KEY}

        async with session.get(base_url, params=params) as resp:
            if resp.status != 200:
                logger.error(f"API failed [{resp.status}] → switching to IMDb fallback")
                return await get_movie_details(q)

            data = await resp.json()
    except Exception as e:
        logger.error(f"API down → fallback IMDb: {e}")
        return await get_movie_details(q)

    # Reject a same-named-but-wrong-year match from the proxy (it has no year filter of its own)
    if year and not _release_year_matches(data.get("release_date"), year):
        logger.info(
            f"[TMDB proxy] Year mismatch for '{title}' ({year}): got "
            f"'{data.get('title')}' ({data.get('release_date')}) — falling back to IMDb"
        )
        return await get_movie_details(q)

    # ✅ FIX: Ab poori normalization try/except ke andar hai. Pehle 'votes' field
    # crash kar sakta tha (agar API "votes": null bheje), aur crash try/except ke
    # BAHAR hone ki wajah se IMDb fallback tak kabhi pahunchta hi nahi tha — isliye
    # kai baar poster hi missing aata tha. Ab koi bhi parsing error ho, IMDb fallback
    # zaroor try hoga.
    try:
        # Normalize fields
        details = {}
        details['title'] = data.get('title') or data.get('localized_title')
        details['year'] = data.get('year') or None
        details['release_date'] = data.get('release_date')
        details['rating'] = round(float(data.get('rating') or 0), 1) if data.get('rating') is not None else None
        # ✅ FIX: 'votes' None hone par crash nahi hoga ab
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
        return await get_movie_details(q)

    backdrops = data.get('images', {}).get('backdrops', {})
    original_language = data.get('images', {}).get('original_language')
    backdrop_url = None
    # ✅ FIX: 'xx' or 'no_lang' hamesha 'xx' hi banta tha (dead code) — clean kar diya
    for key in ('en', original_language, 'xx'):
        if key and backdrops.get(key):
            backdrop_url = backdrops[key][0]
            break
    details['backdrop_url'] = backdrop_url.replace("/original/", "/w1280/") if backdrop_url else None

    # ✅ FIX: "real poster aana hi chahiye" — agar TMDB se poster/backdrop dono nahi
    # mile (API success hui par data khaali tha), to IMDb se poster try karo aur
    # sirf poster_url (aur zaroorat pade to backdrop) us par se le lo, baaki saari
    # rich TMDB details (cast, genres, plot, etc.) waisi hi rakho.
    if not details.get('poster_url') and not details.get('backdrop_url'):
        try:
            imdb_details = await get_movie_details(q)
            if imdb_details and imdb_details.get('poster_url'):
                details['poster_url'] = imdb_details['poster_url']
                logger.info(f"[POSTER] TMDB se poster nahi mila, IMDb se liya: '{q}'")
        except Exception as e:
            logger.warning(f"IMDb poster fallback failed for '{q}': {e}")

    return details
