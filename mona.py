import httpx
from bs4 import BeautifulSoup
from fastapi import FastAPI, HTTPException
from fastapi.responses import RedirectResponse
from fastapi_cache import FastAPICache
from fastapi_cache.backends.inmemory import InMemoryBackend
from fastapi_cache.decorator import cache

app = FastAPI()


@cache(expire=86400)
async def get_metadata(name: str) -> str:
    metadata = {}
    words = name.split("-")

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


@app.get("/art/{name}")
async def get_art(name: str):
    data = await get_metadata(name)
    if not data.get("image", None):
        raise HTTPException(status_code=404, detail="art not found")
    return RedirectResponse(url=data["image"], status_code=302)


@app.on_event("startup")
async def startup():
    FastAPICache.init(InMemoryBackend())
