import os
import random
import re

import anitopy
import httpx
from fastapi import FastAPI, HTTPException
from fastapi.responses import RedirectResponse
from fastapi_cache import FastAPICache
from fastapi_cache.backends.inmemory import InMemoryBackend
from fastapi_cache.decorator import cache
from loguru import logger
from lxml import html

from tvdb import TVDB

app = FastAPI(docs_url="/", redoc_url=None)

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


def get_search_string(parsed: dict[str, str]) -> str | None:
    if not (search_string := parsed.get("anime_title")):
        return None
    if parsed.get("anime_year"):
        search_string += f" ({parsed.get('anime_year')})"
    return search_string


async def get_season_image(tvdb_id: int, season_number: str | list[str]) -> str | None:
    if not season_number or not tvdb_id:
        return None
    season_number = (
        season_number if isinstance(season_number, str) else season_number[0]
    )
    series = await tvdb.get_series_extended(tvdb_id)
    if not series or not (seasons := series.get("seasons")):
        return None
    season = next(
        (x for x in seasons if x.get("number") == int(season_number)),
        None,
    )
    if season:
        season_details = await tvdb.get_season_extended(season.get("id"))
        if not season_details:
            return None
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
async def get_tvdb_poster(parsed: dict[str, str]) -> str | None:
    search_string = get_search_string(parsed)
    if not search_string:
        return None
    series = await find_best_match(search_string)
    if not series:
        return None
    series_image = series.get("image_url")
    if not (series_id := series.get("tvdb_id")) or not (
        season := parsed.get("anime_season")
    ):
        return series_image
    season_image = await get_season_image(series_id, season)
    return season_image or series_image


@cache(expire=86400)
async def get_subsplease_poster(name: str) -> str | None:
    logger.info(f"Searching for: {name}")
    words = slugify(name).split("-")
    async with httpx.AsyncClient(http2=True) as client:
        for _ in range(len(words) + 1):
            url = f"https://subsplease.org/shows/{'-'.join(words)}"
            response = await client.get(url, follow_redirects=True)
            if response.status_code == 200:
                img_src = html.fromstring(response.text).xpath("//img/@src")
                if img_src:
                    return f"https://subsplease.org{img_src[0]}"
            words = words[:-1]
    return None


@app.get("/poster")
@app.get("/poster/")
@app.get("/poster/show/{filename}")
async def poster(filename: str | None = None, query: str | None = None):
    if not filename and not query:
        raise HTTPException(status_code=400, detail="query is required")
    query = filename or query or ""
    parsed = anitopy.parse(query)
    if not parsed or not (title := parsed.get("anime_title")):
        raise HTTPException(status_code=400, detail="query is invalid")
    poster = await get_tvdb_poster(parsed)
    if poster:
        return RedirectResponse(url=poster, status_code=302)
    poster = await get_subsplease_poster(title)
    if poster:
        return RedirectResponse(url=poster, status_code=302)
    return HTTPException(status_code=404, detail="poster not found")


@cache(expire=86400)
async def get_fanart(parsed: dict[str, str]) -> list[dict] | None:
    search_string = get_search_string(parsed)
    if not search_string:
        return None
    series = await find_best_match(search_string)
    if (
        not series
        or not (series_id := series.get("tvdb_id"))
        or not (series_type := series.get("type"))
    ):
        return None
    if series_type == "series":
        artworks = await tvdb.get_series_artworks(series_id, type=3)
        return artworks.get("artworks") if artworks else None
    elif series_type == "movie":
        movie = await tvdb.get_movie_extended(series_id)
        if not movie or not (artworks := movie.get("artworks")):
            return None
        filtered = list(filter(lambda x: x.get("type") == 15, artworks))
        return filtered if filtered else None
    else:
        return None


@app.get("/fanart")
@app.get("/fanart/")
@app.get("/fanart/show/{filename}")
async def fanart(filename: str | None = None, query: str | None = None):
    if not filename and not query:
        raise HTTPException(status_code=400, detail="query is required")
    query = filename or query or ""
    fanart = await get_fanart(anitopy.parse(query))
    if not fanart or not (image := random.choice(fanart).get("image")):
        return HTTPException(status_code=404, detail="fanart not found")
    return RedirectResponse(url=image, status_code=302)


@cache(expire=86400)
async def get_torrent_art(url: str):
    async with httpx.AsyncClient(http2=True) as client:
        response = await client.get(url, follow_redirects=True)
        if response.status_code == 200:
            description = html.fromstring(response.text).xpath(
                "string(//div[@id='torrent-description'])"
            )
            if not description:
                return None
            pattern = r"https?://[^\s]+?\.(?:jpg|jpeg|png|gif)"
            match = re.search(pattern, description)
            return match.group(0) if match else None
    return None


@app.get("/torrent-art")
@app.get("/torrent-art/")
async def torrent_art(url: str | None = None):
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
