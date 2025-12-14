import re
import aiohttp
import warnings
import logging
from io import BytesIO
from PIL import Image
from imdb import Cinemagoer

from info import DREAMXBOTZ_IMAGE_FETCH, TMDB_API_KEY

logger = logging.getLogger(__name__)
ia = Cinemagoer()

LONG_IMDB_DESCRIPTION = False

Image.MAX_IMAGE_PIXELS = None
warnings.simplefilter("ignore", Image.DecompressionBombWarning)


def list_to_str(lst):
    if lst:
        return ", ".join(map(str, lst))
    return ""


async def fetch_image(url, size=(860, 1200)):
    if not DREAMXBOTZ_IMAGE_FETCH:
        logger.info("Image fetching is disabled.")
        return None

    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url) as response:
                if response.status != 200:
                    logger.error(f"Failed to fetch image: {response.status}")
                    return None

                data = await response.read()
                img = Image.open(BytesIO(data))
                img = img.resize(size, Image.LANCZOS)

                out = BytesIO()
                img.save(out, format="JPEG")
                out.seek(0)
                return out

    except aiohttp.ClientError as e:
        logger.error(f"HTTP error in fetch_image: {e}")
    except IOError as e:
        logger.error(f"I/O error in fetch_image: {e}")
    except Exception as e:
        logger.error(f"Unexpected error in fetch_image: {e}")

    return None


async def get_movie_details(query, id=False, file=None):
    try:
        if not id:
            query = query.strip().lower()
            title = query

            year = re.findall(r'[1-2]\d{3}$', query)
            if year:
                year = year[0]
                title = query.replace(year, "").strip()
            elif file:
                year = re.findall(r'[1-2]\d{3}', file)
                year = year[0] if year else None
            else:
                year = None

            results = ia.search_movie(title, results=10)
            if not results:
                return None

            if year:
                results = [r for r in results if str(r.get('year')) == str(year)] or results

            results = [r for r in results if r.get('kind') in ('movie', 'tv series')] or results
            movieid = results[0].movieID
        else:
            movieid = query

        movie = ia.get_movie(movieid)
        ia.update(movie, info=['main', 'vote details'])

        date = (
            movie.get("original air date")
            or movie.get("year")
            or "N/A"
        )

        plot = movie.get("plot")
        if plot:
            plot = plot[0]
        else:
            plot = movie.get("plot outline")

        if plot and len(plot) > 800:
            plot = plot[:800] + "..."

        return {
            "title": movie.get("title"),
            "year": movie.get("year"),
            "rating": movie.get("rating", "N/A"),
            "votes": movie.get("votes"),
            "genres": list_to_str(movie.get("genres")),
            "runtime": list_to_str(movie.get("runtimes")),
            "countries": list_to_str(movie.get("countries")),
            "languages": list_to_str(movie.get("languages")),
            "director": list_to_str(movie.get("director")),
            "writer": list_to_str(movie.get("writer")),
            "cast": list_to_str(movie.get("cast")),
            "plot": plot,
            "release_date": date,
            "poster_url": movie.get("full-size cover url"),
            "imdb_id": f"tt{movie.get('imdbID')}",
            "url": f"https://www.imdb.com/title/tt{movie.get('imdbID')}"
        }

    except Exception as e:
        logger.error(f"get_movie_details error: {e}")
        return None


async def get_movie_detailsx(query):
    base_url = "https://bharath-boy-api.vercel.app/api/movie-posters"
    q = str(query).strip()

    try:
        async with aiohttp.ClientSession() as session:
            params = {"query": q, "api_key": TMDB_API_KEY}
            async with session.get(base_url, params=params) as resp:
                if resp.status != 200:
                    logger.error(f"API failed [{resp.status}]")
                    return None
                data = await resp.json()

    except Exception as e:
        logger.error(f"get_movie_detailsx error: {e}")
        return None

    details = {
        "title": data.get("title"),
        "year": data.get("year"),
        "rating": data.get("rating"),
        "votes": data.get("votes"),
        "runtime": data.get("runtime"),
        "genres": data.get("genres"),
        "languages": data.get("languages"),
        "countries": data.get("countries"),
        "plot": data.get("plot"),
        "poster_url": data.get("poster_url"),
        "imdb_id": data.get("imdb_id"),
        "tmdb_id": data.get("tmdb_id"),
        "tmdb_url": data.get("url"),
    }

    return details