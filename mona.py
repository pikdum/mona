import os
import random
import re
from pprint import pprint

import anitopy
import httpx
from bs4 import BeautifulSoup
from fastapi import FastAPI, HTTPException
from fastapi.responses import RedirectResponse
from fastapi_cache import FastAPICache
from fastapi_cache.backends.inmemory import InMemoryBackend
from fastapi_cache.decorator import cache
from loguru import logger

from tvdb import TVDB

app = FastAPI()

tvdb = TVDB(os.environ["TVDB_API_KEY"])


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


def get_search_string(parsed: dict) -> str | None:
    if not parsed.get("anime_title"):
        return None
    search_string = parsed.get("anime_title")
    if parsed.get("anime_year"):
        search_string += f" ({parsed.get('anime_year')})"
    return search_string


async def get_season_image(tvdb_id: int, season_number: str) -> str | None:
    if not season_number or not tvdb_id:
        return None
    seasons = (await tvdb.get_series_extended(tvdb_id)).get("seasons", [])
    season = next(
        (x for x in seasons if x.get("number") == int(season_number)),
        None,
    )
    if season.get("id"):
        season_details = await tvdb.get_season_extended(season.get("id"))
        artwork = season_details.get("artwork", [])
        season_image = next((x for x in artwork if x.get("type") == 7), {}).get("image")
        return season_image
    return None


def priority_sort_key(obj):
    lang_priority = 0 if obj.get("primary_language") == "jpn" else 1
    type_priority = 0 if obj.get("type") == "series" else 1
    return (lang_priority, type_priority)


@cache(expire=86400)
async def find_best_match(search_string: str) -> dict | None:
    results = await tvdb.search(search_string)
    if not results:
        logger.info(f"No results found for: {search_string}")
        return None
    selected = sorted(results, key=priority_sort_key)[0]
    return selected


@cache(expire=86400)
async def get_poster(parsed: dict) -> str | None:
    search_string = get_search_string(parsed)
    series = await find_best_match(search_string)
    if not series:
        return None
    series_image = series.get("image_url")
    season_image = await get_season_image(
        series.get("tvdb_id"), parsed.get("anime_season")
    )
    return season_image or series_image


@cache(expire=86400)
async def subsplease_search(name: str) -> dict:
    metadata = {}
    slug = slugify(name)
    words = slug.split("-")

    async with httpx.AsyncClient(http2=True) as client:
        for _ in range(len(words) + 1):
            url = f"https://subsplease.org/shows/{'-'.join(words)}"
            response = await client.get(url, follow_redirects=True)

            if response.status_code == 200:
                soup = BeautifulSoup(response.text, "lxml")

                try:
                    img_src = soup.find("img")["src"]
                    metadata["image"] = f"https://subsplease.org{img_src}"
                except:
                    pass

                return metadata

            words = words[:-1]

        return metadata


@app.get("/poster/")
@app.get("/poster/show/{filename}")
async def poster(filename: str = None):
    if not filename:
        raise HTTPException(status_code=400, detail="filename is required")
    parsed = anitopy.parse(filename)
    poster = await get_poster(parsed)
    if poster:
        return RedirectResponse(url=poster, status_code=302)
    poster = (await subsplease_search(parsed.get("anime_title"))).get("image")
    if poster:
        return RedirectResponse(url=poster, status_code=302)
    return HTTPException(status_code=404, detail="poster not found")


@cache(expire=86400)
async def get_fanart(parsed: dict) -> list[str] | None:
    search_string = get_search_string(parsed)
    series = await find_best_match(search_string)
    if not series:
        return None
    if series.get("type") == "series":
        artworks = await tvdb.get_series_artworks(series.get("tvdb_id"), type=3)
        return artworks.get("artworks") if artworks else None
    elif series.get("type") == "movie":
        movie = await tvdb.get_movie_extended(series.get("tvdb_id"))
        artworks = movie.get("artworks") if movie else []
        filtered = list(filter(lambda x: x.get("type") == 15, artworks))
        return filtered if filtered else None
    else:
        return None


@app.get("/fanart/")
@app.get("/fanart/show/{filename}")
async def fanart(filename: str = None):
    if not filename:
        raise HTTPException(status_code=400, detail="filename is required")
    fanart = await get_fanart(anitopy.parse(filename))
    if not fanart:
        return HTTPException(status_code=404, detail="fanart not found")
    random_art = random.choice(fanart)
    return RedirectResponse(url=random_art["image"], status_code=302)


@cache(expire=86400)
async def get_torrent_art(url):
    async with httpx.AsyncClient(http2=True) as client:
        response = await client.get(url, follow_redirects=True)
        if response.status_code == 200:
            soup = BeautifulSoup(response.text, "lxml")
            description = soup.find(id="torrent-description").text.strip()
            pattern = r"https?://[^\s]+?\.(?:jpg|jpeg|png|gif)"
            match = re.search(pattern, description)
            return match.group(0) if match else None
    return None


@app.get("/torrent-art/")
async def torrent_art(url: str = None):
    if not url:
        raise HTTPException(status_code=400, detail="url is required")
    image = await get_torrent_art(url)
    if image:
        return RedirectResponse(url=image, status_code=302)
    raise HTTPException(status_code=404, detail="art not found")


@app.get("/healthcheck")
@app.head("/healthcheck")
async def healthcheck():
    return {"status": "ok"}


@app.on_event("startup")
async def startup():
    await tvdb.login()
    FastAPICache.init(InMemoryBackend())
