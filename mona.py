import os
import random
import re
import threading

import anitopy
import httpx
import tvdb_v4_official
from bs4 import BeautifulSoup
from fastapi import FastAPI, HTTPException
from fastapi.responses import RedirectResponse
from fastapi_cache import FastAPICache
from fastapi_cache.backends.inmemory import InMemoryBackend
from fastapi_cache.decorator import cache
from loguru import logger

app = FastAPI()
tvdb = None


# use threading.Timer to re-init tvdb every hour
# to prevent token expiration after 30 days
def init_tvdb():
    global tvdb
    tvdb = tvdb_v4_official.TVDB(os.environ["TVDB_API_KEY"])
    logger.info("Refreshed TVDB Token")
    threading.Timer(3600, init_tvdb).start()


init_tvdb()


def slugify(text: str) -> str:
    # lowercase
    text = text.lower()
    # strip bbcode
    text = re.sub(r"\[.*?\]", "", text)
    # remove parens
    text = text.replace("(", "").replace(")", "")
    # remove apostrophes of all sorts
    text = text.replace("'", "").replace("’", "")
    # remove whatever this is
    text = text.replace("+", "").replace("@", "")
    # replace non-alphanumeric with dashes
    text = re.sub(r"[^a-zA-Z0-9_]+", "-", text)
    # strip leading and trailing dashes
    text = text.strip("-")
    return text


@cache(expire=86400)
async def torrent_art(url):
    async with httpx.AsyncClient(http2=True) as client:
        response = await client.get(url, follow_redirects=True)
        if response.status_code == 200:
            soup = BeautifulSoup(response.text, "html.parser")
            description = soup.find(id="torrent-description").text.strip()
            pattern = r"https?://[^\s]+?\.(?:jpg|jpeg|png|gif)"
            match = re.search(pattern, description)
            return match.group(0) if match else None
    return None


@cache(expire=86400)
async def subsplease_search(name: str):
    metadata = {}
    slug = slugify(name)
    words = slug.split("-")

    async with httpx.AsyncClient(http2=True) as client:
        for _ in range(len(words) + 1):
            url = f"https://subsplease.org/shows/{'-'.join(words)}"
            response = await client.get(url, follow_redirects=True)

            if response.status_code == 200:
                soup = BeautifulSoup(response.text, "html.parser")

                try:
                    img_src = soup.find("img")["src"]
                    metadata["image"] = f"https://subsplease.org{img_src}"
                except:
                    pass

                return metadata

            words = words[:-1]

        return metadata


def priority_sort_key(obj):
    lang_priority = 0 if obj.get("primary_language") == "jpn" else 1
    type_priority = 0 if obj.get("type") == "series" else 1
    return (lang_priority, type_priority)


@cache(expire=86400)
async def tvdb_search(name: str):
    original_name = name
    name = re.sub(r"S\d+$", "", name).strip()
    name = re.sub(r"\d+$", "", name).strip()
    # escape parentheses: (2024) -> \(2024\)
    name = re.sub(r"\(|\)", r"\\\g<0>", name).strip()
    data = tvdb.search(name)
    if data:
        selected = sorted(data, key=priority_sort_key)[0]
        logger.info(
            f"{original_name} -> {name} -> {selected['name']} ({selected['tvdb_id']})"
        )
        return selected
    logger.info(f"{original_name} -> {name} -> Not Found")
    return None


@cache(expire=86400)
async def tvdb_artworks(name: str, series_type: int, movie_type: int):
    data = await tvdb_search(name)
    if data:
        tvdb_id = data["tvdb_id"]
        if data.get("type") == "series":
            return tvdb.get_series_artworks(tvdb_id, lang=None, type=series_type)
        elif data.get("type") == "movie":
            movie = tvdb.get_movie_extended(tvdb_id)
            if movie and movie.get("artworks"):
                artworks = movie["artworks"]
                filtered = list(filter(lambda x: x["type"] == movie_type, artworks))
                return {"artworks": filtered} if filtered else None
    return None


@app.get("/poster/show/{show}")
async def get_show_poster(show: str):
    data = await tvdb_search(show)
    if data:
        return RedirectResponse(url=data["image_url"], status_code=302)

    data = await subsplease_search(show)
    if data.get("image"):
        return RedirectResponse(url=data["image"], status_code=302)
    raise HTTPException(status_code=404, detail="poster not found")


@app.get("/fanart/show/{show}")
async def get_show_fanart(show: str):
    data = await tvdb_artworks(show, 3, 15)
    if data and data.get("artworks"):
        random_item = random.choice(data["artworks"])
        return RedirectResponse(url=random_item["image"], status_code=302)
    fallback = "https://raw.githubusercontent.com/pikdum/plugin.video.haru/master/fanart-greyscale.jpg"
    return RedirectResponse(url=fallback, status_code=302)


@app.get("/torrent-art/")
async def get_torrent_art(url: str = None):
    if not url:
        raise HTTPException(status_code=400, detail="url is required")
    image = await torrent_art(url)
    if image:
        return RedirectResponse(url=image, status_code=302)
    raise HTTPException(status_code=404, detail="art not found")


def get_show_from_filename(filename: str):
    parsed = anitopy.parse(filename)
    title = parsed.get("anime_title", None)
    year = parsed.get("anime_year", None)
    show = f"{title} ({year})" if title and year else title
    logger.info(f"Filename: {filename} -> {show}")
    return show


@app.get("/poster/")
async def get_file_poster(filename: str = None):
    if not filename:
        raise HTTPException(status_code=400, detail="filename is required")
    show = get_show_from_filename(filename)
    if not show:
        raise HTTPException(status_code=404, detail="show not found")
    return await get_show_poster(show)


@app.get("/fanart/")
async def get_file_fanart(filename: str = None):
    if not filename:
        raise HTTPException(status_code=400, detail="filename is required")
    show = get_show_from_filename(filename)
    if not show:
        raise HTTPException(status_code=404, detail="show not found")
    return await get_show_fanart(show)


@app.get("/healthcheck")
@app.head("/healthcheck")
async def healthcheck():
    return {"status": "ok"}


@app.on_event("startup")
async def startup():
    FastAPICache.init(InMemoryBackend())
