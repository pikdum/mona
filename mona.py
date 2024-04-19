import os
import random
import re
import threading

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


@cache(expire=86400)
async def tvdb_search(name: str):
    original_name = name
    name = re.sub(r"S\d+$", "", name).strip()
    name = re.sub(r"\d+$", "", name).strip()
    # escape parentheses: (2024) -> \(2024\)
    name = re.sub(r"\(|\)", r"\\\g<0>", name).strip()
    data = tvdb.search(name)
    if data:
        selected_data = data[0]
        for item in data:
            if item.get("primary_language") == "jpn" and item.get("type") == "series":
                selected_data = item
                logger.info(
                    f"{original_name} -> {name} -> {item['name']} ({item['tvdb_id']})"
                )
                break
        return selected_data
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


@app.get("/healthcheck")
@app.head("/healthcheck")
async def healthcheck():
    return {"status": "ok"}


@app.on_event("startup")
async def startup():
    FastAPICache.init(InMemoryBackend())
