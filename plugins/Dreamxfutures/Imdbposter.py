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


# ---------------------- UTILS ----------------------
def list_to_str(lst):
    return ", ".join(map(str, lst)) if lst else ""


def api_safe_query(query: str):
    # remove usernames, symbols, seasons
    q = query
    q = re.sub(r'@\w+', '', q)
    q = re.sub(r'^[\-\_]+', '', q)
    q = re.sub(r'\bS\d{1,2}E\d{1,2}\b', '', q, flags=re.I)
    q = re.sub(r'\bS\d{1,2}\b', '', q, flags=re.I)
    q = re.sub(r'\bE\d{1,2}\b', '', q, flags=re.I)
    q = re.sub(r'season\s*\d+', '', q, flags=re.I)
    q = q.replace('.', ' ')
    q = re.sub(r'[^a-zA-Z0-9 ]+', '', q)
    q = re.sub(r'\s+', ' ', q)
    return q.strip()


def strip_season(query: str):
    return re.sub(r'\bS\d{1,2}\b', '', query, flags=re.I).strip()


# ---------------------- IMAGE FETCH ----------------------
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


# ---------------------- TMDB FETCH ----------------------
async def _fetch_tmdb(base_url, query):
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(base_url, params={"query": query, "api_key": TMDB_API_KEY}) as resp:
                if resp.status != 200:
                    return {}
                return await resp.json()
    except Exception:
        return {}


async def get_movie_detailsx(query, id=False, file=None):
    base_url = "https://bharath-boy-api.vercel.app/api/movie-posters"
    safe_query = api_safe_query(query)

    # try with season
    data = await _fetch_tmdb(base_url, safe_query)

    # fallback without season if poster missing
    if not data or not data.get("poster_url"):
        clean_query = strip_season(safe_query)
        if clean_query != safe_query:
            data = await _fetch_tmdb(base_url, clean_query)

    # fallback to default poster
    if not data.get("poster_url"):
        data["poster_url"] = "https://i.imgur.com/placeholder.png"  # default poster

    return data or {}


# ---------------------- IMDB FALLBACK ----------------------
async def get_movie_details(query, id=False, file=None):
    try:
        title = api_safe_query(query)
        results = ia.search_movie(title, results=10)
        if not results:
            return {"poster_url": "https://i.imgur.com/placeholder.png"}  # default poster

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
            "poster_url": movie.get("full-size cover url") or "https://i.imgur.com/placeholder.png",
            "kind": movie.get("kind"),
            "imdb_id": f"tt{movie.movieID}"
        }
    except Exception as e:
        logger.error(f"IMDB error: {e}")
        return {"poster_url": "https://i.imgur.com/placeholder.png"}


# ---------------------- HANDLER ----------------------
import tempfile
from PIL import Image

async def imdb_poster_handler(client, message, text):
    query = api_safe_query(text)
    details = await get_movie_detailsx(query)
    if not details or not details.get("poster_url"):
        details = await get_movie_details(query)

    caption = f"🎬 **{text}**\n\n"
    if details:
        caption += f"⭐ Rating: {details.get('rating', 'N/A')}\n"
        caption += f"📅 Year: {details.get('year', 'N/A')}\n\n"
        caption += details.get("plot", "") or ""

    poster_bytes = await fetch_image(details.get("poster_url")) if details else None

    if poster_bytes:
        # Save to temp file
        with tempfile.NamedTemporaryFile(suffix=".jpg") as tmp:
            img = Image.open(poster_bytes)
            img.save(tmp.name)
            await client.send_photo(
                chat_id=message.chat.id,
                photo=tmp.name,
                caption=caption,
                has_spoiler=True
            )
    else:
        await message.reply_text(caption)