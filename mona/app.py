#!/usr/bin/env python3
import os
import random
import re
from datetime import datetime, timedelta

import anitopy
import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import RedirectResponse
from loguru import logger
from lxml import html
from theine import Cache, Memoize

from mona.tvdb import TVDB

app = FastAPI(docs_url="/", redoc_url=None)
tvdb = TVDB(os.environ["TVDB_API_KEY"])
cache = Cache("clockpro", 10000)


@app.middleware("http")
async def tvdb_login(request: Request, call_next):
    if tvdb.token is None:
        await tvdb.login()
    elif (
        tvdb.token_expires is not None
        and datetime.now().timestamp() > tvdb.token_expires
    ):
        await tvdb.login()
    response = await call_next(request)
    return response


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
    image_url = obj.get("image_url")
    missing_image = 1 if not image_url or "missing" in image_url else 0
    lang_priority = 0 if obj.get("primary_language") in ["jpn", "kor", "zho"] else 1
    type_priority = 0 if obj.get("type") == "series" else 1
    data = str(obj).lower()
    genre_priority = 0 if any(x in data for x in ["anime", "crunchyroll"]) else 1
    return (missing_image, lang_priority, type_priority, genre_priority)


async def find_best_match(parsed: dict[str, str]) -> dict | None:
    if file_name := parsed.get("file_name"):
        if results := await tvdb.search(file_name):
            selected = sorted(results, key=priority_sort_key)[0]
            return selected
    search_string = get_search_string(parsed)
    if not search_string:
        return None
    results = await tvdb.search(search_string)
    if not results:
        logger.info(f"No results found for: {search_string}")
        return None
    selected = sorted(results, key=priority_sort_key)[0]
    return selected


async def get_tvdb_poster(parsed: dict[str, str]) -> str | None:
    series = await find_best_match(parsed)
    if not series:
        return None
    series_image = series.get("image_url")
    if not (series_id := series.get("tvdb_id")) or not (
        season := parsed.get("anime_season")
    ):
        return series_image
    season_image = await get_season_image(series_id, season)
    return season_image or series_image


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
async def poster(query: str):
    cache_key = f"p:{query}"
    if cached := cache.get(cache_key):
        return RedirectResponse(url=cached, status_code=302)
    if not (parsed := anitopy.parse(query)) or not (title := parsed.get("anime_title")):
        raise HTTPException(status_code=400, detail="query is invalid")
    poster = await get_tvdb_poster(parsed)
    if poster:
        cache.set(cache_key, poster, timedelta(days=1))
        return RedirectResponse(url=poster, status_code=302)
    poster = await get_subsplease_poster(title)
    if poster:
        cache.set(cache_key, poster, timedelta(days=1))
        return RedirectResponse(url=poster, status_code=302)
    raise HTTPException(status_code=404, detail="poster not found")


async def get_fanart(parsed: dict[str, str]) -> list[dict] | None:
    series = await find_best_match(parsed)
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
async def fanart(query: str):
    cache_key = f"f:{query}"
    if cached := cache.get(cache_key):
        return RedirectResponse(url=cached, status_code=302)
    if not (parsed := anitopy.parse(query)):
        raise HTTPException(status_code=400, detail="query is invalid")
    fanart = await get_fanart(parsed)
    if not fanart or not (image := random.choice(fanart).get("image")):
        raise HTTPException(status_code=404, detail="fanart not found")
    cache.set(cache_key, image, timedelta(days=1))
    return RedirectResponse(url=image, status_code=302)


@Memoize(cache, None, typed=True)
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
async def torrent_art(url: str):
    if not url.startswith(("https://nyaa.si", "https://sukebei.nyaa.si/")):
        raise HTTPException(status_code=400, detail="invalid url")
    image = await get_torrent_art(url)
    if image:
        return RedirectResponse(url=image, status_code=302)
    raise HTTPException(status_code=404, detail="art not found")


@app.get("/healthcheck")
@app.head("/healthcheck")
async def healthcheck():
    return {"status": "ok"}
