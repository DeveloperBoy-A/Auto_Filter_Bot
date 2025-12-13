import re
import aiohttp
import warnings
import logging
from io import BytesIO
from PIL import Image
from imdb import Cinemagoer

from info import DREAMXBOTZ_IMAGE_FETCH, TMDB_API_KEY, POSTER_SPOILER

logger = logging.getLogger(__name__)
ia = Cinemagoer()

Image.MAX_IMAGE_PIXELS = None
warnings.simplefilter("ignore", Image.DecompressionBombWarning)

# =====================================================
# UTILS
# =====================================================

def api_safe_query(query: str):
    q = query
    q = re.sub(r'\bS\d{1,2}E\d{1,2}\b', '', q, flags=re.I)
    q = re.sub(r'\bS\d{1,2}\b', '', q, flags=re.I)
    q = re.sub(r'\bE\d{1,2}\b', '', q, flags=re.I)
    q = re.sub(r'season\s*\d+', '', q, flags=re.I)
    q = re.sub(r'\.', ' ', q)
    q = re.sub(r'\s+', ' ', q)
    return q.strip()


# =====================================================
# IMAGE FETCH  (channel.py expects this)
# =====================================================

async def fetch_image(url, size=(860, 1200)):
    if not DREAMXBOTZ_IMAGE_FETCH or not url:
        return None

    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url) as resp:
                if resp.status != 200:
                    return None

                img = Image.open(BytesIO(await resp.read()))
                img = img.resize(size, Image.LANCZOS)

                out = BytesIO()
                img.save(out, format="JPEG")
                out.seek(0)
                return out

    except Exception as e:
        logger.error(f"fetch_image error: {e}")
        return None


# =====================================================
# TMDB / POSTER API  (NAME FIXED)
# =====================================================

async def get_movie_detailsx(query, id=False, file=None):
    """
    channel.py expects THIS NAME
    """
    base_url = "https://bharath-boy-api.vercel.app/api/movie-posters"
    safe_query = api_safe_query(query)

    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                base_url,
                params={"query": safe_query, "api_key": TMDB_API_KEY}
            ) as resp:

                if resp.status != 200:
                    text = await resp.text()
                    logger.error(
                        f"API request failed [{resp.status}] for query={safe_query}\n{text}"
                    )
                    return None

                data = await resp.json()

    except Exception as e:
        logger.error(f"TMDB API error: {e}")
        return None

    return {
        "title": data.get("title"),
        "year": data.get("year"),
        "rating": data.get("rating"),
        "votes": data.get("votes"),
        "plot": data.get("plot"),
        "poster_url": data.get("poster_url"),
        "tmdb_id": data.get("tmdb_id"),
        "imdb_id": data.get("imdb_id")
    }


# =====================================================
# IMDB FALLBACK  (NAME FIXED)
# =====================================================

async def get_movie_details(query, id=False, file=None):
    """
    channel.py expects THIS NAME
    """
    try:
        title = api_safe_query(query)

        results = ia.search_movie(title, results=10)
        if not results:
            return None

        movie = ia.get_movie(results[0].movieID)
        ia.update(movie, info=["main", "vote details"])

        plot = movie.get("plot")
        plot = plot[0] if plot else movie.get("plot outline")

        return {
            "title": movie.get("title"),
            "year": movie.get("year"),
            "rating": movie.get("rating"),
            "votes": movie.get("votes"),
            "plot": plot,
            "poster_url": movie.get("full-size cover url"),
            "kind": movie.get("kind"),
            "imdb_id": f"tt{movie.movieID}"
        }

    except Exception as e:
        logger.error(f"IMDB error: {e}")
        return None