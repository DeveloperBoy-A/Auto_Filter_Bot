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
LONG_IMDB_DESCRIPTION = False

# -----------------------------
# Utility function
# -----------------------------
def list_to_str(lst):
    if lst:
        return ", ".join(map(str, lst))
    return ""

# Avoid DecompressionBombError
Image.MAX_IMAGE_PIXELS = None
warnings.simplefilter("ignore", Image.DecompressionBombWarning)

# -----------------------------
# Fetch Image
# -----------------------------
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
    except Exception as e:
        logger.error(f"Unexpected error in fetch_image: {e}")
        return None


# -----------------------------
# IMDB Movie Details🍿
# -----------------------------
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

            results = ia.search_movie(title, results=10)
            if not results:
                return None

            if year:
                results = [m for m in results if str(m.get("year")) == str(year)] or results

            results = [m for m in results if m.get("kind") in ("movie", "tv series")] or results
            movieid = results[0].movieID
        else:
            movieid = query

        movie = ia.get_movie(movieid)

        # 🔥 THIS IS THE MAIN FIX
        ia.update(movie, info=[
            "main",
            "plot",
            "votes",
            "images"   # 👈 poster yahin se aata hai
        ])

        # ✅ Poster extraction (FIXED)
        poster = None
        if movie.get("full-size cover url"):
            poster = movie.get("full-size cover url")
        elif movie.get("cover url"):
            poster = movie.get("cover url")

        plot = movie.get("plot")
        plot = plot[0] if plot else movie.get("plot outline")
        if plot and len(plot) > 800:
            plot = plot[:800] + "..."

        return {
            "title": movie.get("title"),
            "year": movie.get("year"),
            "rating": str(movie.get("rating", "N/A")),
            "votes": movie.get("votes"),
            "plot": plot,
            "genres": list_to_str(movie.get("genres")),
            "poster_url": poster,   # ✅ NOW ALWAYS FILLED IF EXISTS
            "imdb_id": f"tt{movie.get('imdbID')}",
            "url": f"https://www.imdb.com/title/tt{movieid}",
        }

    except Exception as e:
        logger.error(f"IMDb error: {e}")
        return None

# -----------------------------
# TMDB Movie Details (Crash-Proof)
# -----------------------------
async def get_movie_detailsx(query, id=False, file=None):
    base_url = "https://bharath-boy-api.vercel.app/api/movie-posters"
    q = str(query).strip()
    try:
        async with aiohttp.ClientSession() as session:
            params = {"query": q, "api_key": TMDB_API_KEY}
            async with session.get(base_url, params=params) as resp:
                try:
                    data = await resp.json()
                except Exception:
                    text = await resp.text()
                    logger.error(f"API returned invalid JSON [{resp.status}] for query={q}\n{text}")
                    return {"error": f"API returned invalid JSON [{resp.status}]"}

                if resp.status != 200:
                    logger.error(f"API request failed [{resp.status}] for query={q}")
                    return {"error": f"API failed [{resp.status}]"}
    except Exception as e:
        logger.error(f"An error occurred in get_movie_detailsx: {e}")
        return {"error": str(e)}

    # Normalize fields safely
    details = {}
    details['title'] = data.get('title') or data.get('localized_title')
    details['year'] = (data.get('year', 0)) if data.get('year') else None
    details['release_date'] = data.get('release_date')
    details['rating'] = round(float(data.get('rating', 0)), 1) if data.get('rating') is not None else None
    details['votes'] = int(data.get('votes', 0))
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
    details['box_office'] = (data.get('box_office', 0)) if data.get('box_office') else None
    raw_dist = data.get('distributors')
    details['distributors'] = [d.strip() for d in raw_dist.split(',')] if raw_dist else []
    details['imdb_id'] = data.get('imdb_id')
    details['tmdb_id'] = data.get('tmdb_id')

    # Poster handling
    posters = data.get('images', {}).get('posters', {})
    original_language = data.get('images', {}).get('original_language')
    poster_url = data.get('poster_url')
    if not poster_url:
        for key in ('en', original_language, 'xx'):
            if key and posters.get(key):
                poster_url = posters[key][0]
                break
    details['poster_url'] = poster_url

    # Backdrop handling
    backdrops = data.get('images', {}).get('backdrops', {})
    backdrop_url = None
    for key in ('en', original_language, 'xx'):
        if key and backdrops.get(key):
            backdrop_url = backdrops[key][0]
            break
    details['backdrop_url'] = backdrop_url

    return details

# ---------- POSTER PRIORITY HELPER ----------
async def get_best_poster(imdb_data=None, tmdb_data=None):
    # 1️⃣ TMDB poster (best quality)
    if tmdb_data:
        poster = tmdb_data.get("poster_url")
        if poster:
            return poster

    # 2️⃣ IMDb poster
    if imdb_data:
        poster = imdb_data.get("poster_url")
        if poster:
            return poster

    return None