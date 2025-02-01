import os

import pytest
from mona.tvdb import TVDB


@pytest.mark.asyncio
async def test_tvdb_api():
    tvdb = TVDB(os.environ["TVDB_API_KEY"])
    token = await tvdb.login()
    results = await tvdb.search("Yuyushiki")
    series_id = results[0]["tvdb_id"]
    series = await tvdb.get_series_extended(series_id)
    artworks = await tvdb.get_series_artworks(series_id)
    movie = await tvdb.get_movie_extended(16609)
    assert token
    assert results
    assert series_id
    assert series
    assert artworks
    assert movie
