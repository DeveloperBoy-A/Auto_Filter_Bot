import re
import aiohttp
import warnings
import logging
from io import BytesIO
from PIL import Image
from info import DREAMXBOTZ_IMAGE_FETCH, TMDB_API_KEY
from imdb import Cinemagoer

logger = logging.getLogger(__name__)
ia = Cinemagoer()

Image.MAX_IMAGE_PIXELS = None
warnings.simplefilter("ignore", Image.DecompressionBombWarning)

_session: aiohttp.ClientSession | None = None


# ================= SESSION ================= #

async def get_session():
    global _session
    if _session is None or _session.closed:
        _session = aiohttp.ClientSession()
    return _session


async def close_session():
    global _session
    if _session and not _session.closed:
        await _session.close()


# ================= IMAGE ================= #

async def fetch_image(url, size=(860, 1200)):
    if not DREAMXBOTZ_IMAGE_FETCH:
        return url

    if not url:
        return None

    try:
        session = await get_session()
        async with session.get(url) as resp:
            if resp.status != 200:
                logger.error(f"Image fetch failed [{resp.status}] {url}")
                return None

            data = await resp.read()
            img = Image.open(BytesIO(data))
            img = img.resize(size, Image.LANCZOS)

            out = BytesIO()
            img.save(out, format="JPEG")
            out.seek(0)
            return out

    except Exception as e:
        logger.error(f"fetch_image error: {e}")
        return None


# ================= UTILS ================= #

def list_to_str(lst):
    return ", ".join(map(str, lst)) if lst else ""


def clean_title_and_year(text: str):
    """
    सही तरह से year निकालता है (2021, 2012 etc)
    """
    year = re.findall(r'\b(19\d{2}|20\d{2})\b', text)
    title = re.sub(r'\b(19\d{2}|20\d{2})\b', '', text).strip()
    return title, year[0] if year else None


# ================= IMDb (SAFE) ================= #

async def get_movie_details(query, id=False, file=None):
    try:
        if not id:
            query = query.strip().lower()
            title, year = clean_title_and_year(query)

            try:
                results = ia.search_movie(title, results=10)
            except Exception as e:
                logger.error(f"IMDb search failed: {e}")
                return None

            if not results:
                return None

            # soft year filter
            if year:
                year_filtered = [
                    m for m in results
                    if str(m.get("year")) == str(year)
                ]
                if year_filtered:
                    results = year_filtered

            # kind filter
            kind_filtered = [
                m for m in results
                if m.get("kind") in ("movie", "tv series")
            ]
            if kind_filtered:
                results = kind_filtered

            movieid = results[0].movieID
        else:
            movieid = query

        try:
            movie = ia.get_movie(movieid)
            ia.update(movie, info=["main", "vote details"])
        except Exception as e:
            logger.error(f"IMDb get_movie failed: {e}")
            return None

        # plot
        plot = None
        if movie.get("plot"):
            plot = movie["plot"][0]
        elif movie.get("plot outline"):
            plot = movie.get("plot outline")

        if plot and len(plot) > 800:
            plot = plot[:800] + "..."

        poster_url = movie.get("full-size cover url")

        return {
            "title": movie.get("title"),
            "year": movie.get("year"),
            "release_date": movie.get("original air date") or movie.get("year"),
            "rating": str(movie.get("rating", "N/A")),
            "votes": movie.get("votes"),
            "genres": list_to_str(movie.get("genres")),
            "runtime": list_to_str(movie.get("runtimes")),
            "languages": list_to_str(movie.get("languages")),
            "countries": list_to_str(movie.get("countries")),
            "certificates": list_to_str(movie.get("certificates")),
            "director": list_to_str(movie.get("director")),
            "writer": list_to_str(movie.get("writer")),
            "producer": list_to_str(movie.get("producer")),
            "composer": list_to_str(movie.get("composer")),
            "cinematographer": list_to_str(movie.get("cinematographer")),
            "cast": list_to_str(movie.get("cast")),
            "plot": plot,
            "poster_url": poster_url,
            "imdb_id": f"tt{movie.get('imdbID')}",
            "url": f"https://www.imdb.com/title/tt{movieid}"
        }

    except Exception as e:
        logger.exception(f"IMDb fatal error: {e}")
        return None


# ================= TMDB API (FALLBACK) ================= #

async def get_movie_detailsx(query, id=False, file=None):
    base_url = "https://bharath-boy-api.vercel.app/api/movie-posters"

    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                base_url,
                params={"query": query, "api_key": TMDB_API_KEY}
            ) as resp:
                if resp.status != 200:
                    text = await resp.text()
                    logger.error(f"TMDB API failed [{resp.status}] {text}")
                    return None
                data = await resp.json()

    except Exception as e:
        logger.error(f"TMDB request error: {e}")
        return None

    poster_url = data.get("poster_url")
    if poster_url:
        poster_url = poster_url.replace("/original/", "/w1280/")

    return {
        "title": data.get("title"),
        "year": data.get("year"),
        "release_date": data.get("release_date"),
        "rating": data.get("rating"),
        "votes": data.get("votes"),
        "genres": data.get("genres"),
        "languages": data.get("languages"),
        "countries": data.get("countries"),
        "runtime": data.get("runtime"),
        "plot": data.get("plot"),
        "poster_url": poster_url,
        "imdb_id": data.get("imdb_id"),
        "tmdb_id": data.get("tmdb_id"),
        "url": data.get("url"),
    }