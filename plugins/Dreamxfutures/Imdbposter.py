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

ANIME_KEYWORDS = [
    "anime", "naruto", "one piece", "chainsaw man",
    "attack on titan", "demon slayer",
    "jujutsu kaisen", "bleach", "death note"
]


def list_to_str(lst):
    return ", ".join(map(str, lst)) if lst else ""


def is_anime(title: str) -> bool:
    t = title.lower()
    return any(k in t for k in ANIME_KEYWORDS)


def api_safe_query(query: str):
    """
    API ke liye season / episode hatao
    """
    q = query
    q = re.sub(r'\bS\d{1,2}E\d{1,2}\b', '', q, flags=re.I)
    q = re.sub(r'\bS\d{1,2}\b', '', q, flags=re.I)
    q = re.sub(r'\bE\d{1,2}\b', '', q, flags=re.I)
    q = re.sub(r'season\s*\d+', '', q, flags=re.I)
    q = re.sub(r'\.', ' ', q)
    q = re.sub(r'\s+', ' ', q)
    return q.strip()


def extract_from_filename(name: str):
    """
    Movie.Name.S01E02.1080p.mkv -> Movie Name S01
    """
    clean = re.sub(r'\.(mkv|mp4|avi|mov|webm)$', '', name, flags=re.I)
    season = re.search(r'(S\d{1,2})', clean, re.I)
    title = api_safe_query(clean)

    if season:
        return f"{title} {season.group(1).upper()}"
    return title


# =====================================================
# IMAGE FETCH
# =====================================================

async def fetch_image(url, size=(860, 1200)):
    if not DREAMXBOTZ_IMAGE_FETCH or not url:
        return None

    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url) as resp:
                if resp.status != 200:
                    logger.error(f"Poster fetch failed [{resp.status}]")
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
# IMDB FETCH (NON-ANIME)
# =====================================================

async def get_imdb_details(query):
    try:
        results = ia.search_movie(query, results=10)
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
            "imdb_id": f"tt{movie.movieID}",
            "kind": movie.get("kind")
        }

    except Exception as e:
        logger.error(f"IMDB error: {e}")
        return None


# =====================================================
# TMDB / POSTER API (MOVIE + ANIME)
# =====================================================

async def get_tmdb_details(query):
    base_url = "https://bharath-boy-api.vercel.app/api/movie-posters"

    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                base_url,
                params={"query": query, "api_key": TMDB_API_KEY}
            ) as resp:

                if resp.status != 200:
                    logger.error(f"TMDB API failed [{resp.status}] for {query}")
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
# MAIN DREAMXFUTURES HANDLER
# =====================================================

async def imdb_poster_handler(client, message, text):
    """
    Dreamxfutures entry point
    """

    query = extract_from_filename(text)
    api_query = api_safe_query(query)

    # Anime → TMDB only
    if is_anime(query):
        details = await get_tmdb_details(api_query)
    else:
        details = await get_tmdb_details(api_query)
        if not details:
            details = await get_imdb_details(api_query)

    caption = f"🎬 **{query}**\n\n"

    if details:
        caption += f"⭐ Rating: {details.get('rating', 'N/A')}\n"
        caption += f"📅 Year: {details.get('year', 'N/A')}\n\n"
        caption += details.get("plot", "") or ""

    poster = await fetch_image(details.get("poster_url")) if details else None

    if poster:
        await client.send_photo(
            chat_id=message.chat.id,
            photo=poster,
            caption=caption,
            has_spoiler=POSTER_SPOILER
        )
    else:
        await message.reply_text(caption)