#!/usr/bin/env python3
from datetime import datetime

import httpx
from loguru import logger


class TVDB:
    def __init__(self, apikey: str, pin: str = "hello world"):
        self.token: str | None = None
        self.token_expires: float | None = None
        self.apikey: str = apikey
        self.pin: str = pin
        self.api_base: str = "https://api4.thetvdb.com/v4"

    async def login(self) -> str | None:
        async with httpx.AsyncClient(http2=True) as client:
            response = await client.post(
                f"{self.api_base}/login",
                json={"apikey": self.apikey, "pin": self.pin},
            )
            if response.status_code == 200:
                self.token = response.json().get("data", {}).get("token")
                # set token to expire in an hour
                self.token_expires = datetime.now().timestamp() + 3600
                logger.info("TVDB token refreshed!")
            else:
                logger.error("TVDB token refresh failed!")
        return self.token

    async def search(self, query: str) -> list[dict]:
        async with httpx.AsyncClient(http2=True) as client:
            response = await client.get(
                f"{self.api_base}/search",
                params={"query": query},
                headers={"Authorization": f"Bearer {self.token}"},
            )
            if response.status_code == 200:
                return response.json().get("data", [])
        return []

    async def get_series_extended(self, series_id: int) -> dict | None:
        async with httpx.AsyncClient(http2=True) as client:
            response = await client.get(
                f"{self.api_base}/series/{series_id}/extended",
                headers={"Authorization": f"Bearer {self.token}"},
            )
            if response.status_code == 200:
                return response.json().get("data")
        return None

    async def get_series_artworks(
        self, series_id: int, lang: str | None = None, type: int | None = None
    ) -> dict | None:
        params = {}
        if lang:
            params["lang"] = lang
        if type:
            params["type"] = type
        async with httpx.AsyncClient(http2=True) as client:
            response = await client.get(
                f"{self.api_base}/series/{series_id}/artworks",
                params=params,
                headers={"Authorization": f"Bearer {self.token}"},
            )
            if response.status_code == 200:
                return response.json().get("data")
        return None

    async def get_movie_extended(self, movie_id: int) -> dict | None:
        async with httpx.AsyncClient(http2=True) as client:
            response = await client.get(
                f"{self.api_base}/movies/{movie_id}/extended",
                headers={"Authorization": f"Bearer {self.token}"},
            )
            if response.status_code == 200:
                return response.json().get("data")
        return None

    async def get_season_extended(self, season_id: int) -> dict | None:
        async with httpx.AsyncClient(http2=True) as client:
            response = await client.get(
                f"{self.api_base}/seasons/{season_id}/extended",
                headers={"Authorization": f"Bearer {self.token}"},
            )
            if response.status_code == 200:
                return response.json().get("data")
        return None
