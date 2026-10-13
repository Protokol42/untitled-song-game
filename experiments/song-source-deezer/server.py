"""Song source test, Part 2: Deezer through our own server.

The browser can't call Deezer directly: Deezer's responses don't include an
`Access-Control-Allow-Origin` header, so the browser blocks the page from reading
them (that's CORS). CORS is a browser rule only, so a server can call Deezer
freely and pass the results on.

Run from the project root, with the virtual environment active:
    uvicorn server:app --reload --app-dir experiments/song-source-deezer
"""

from pathlib import Path

import httpx
from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

DEEZER_SEARCH_URL = "https://api.deezer.com/search"
SEARCH_LIMIT = 10  # Max results to ask Deezer for (fewer may come back)

app = FastAPI()


# Our own track shape, as a Pydantic model. It replaces the dict we built by
# hand: FastAPI uses it to check our output and to turn it into JSON.
class Track(BaseModel):
    id: int
    title: str
    artist: str
    preview_url: str


# Routing: "when a GET request comes in for /api/search, call this function".
# `q: str` means "read q from the query string; it must be there". If it's
# missing, FastAPI replies 422 with an explanation before our code even runs.
@app.get("/api/search")
async def search(q: str) -> list[Track]:
    # httpx is async: while we `await` Deezer's reply, the server is free to
    # handle other requests instead of standing still.
    async with httpx.AsyncClient() as client:
        response = await client.get(
            DEEZER_SEARCH_URL, params={"q": q, "limit": SEARCH_LIMIT}
        )
    data = response.json()

    # Deezer sometimes replies 200 with {"error": ...} instead of results.
    # Raising HTTPException sends a proper error response to the browser.
    # 502 "Bad Gateway" means "the service behind me failed".
    if "data" not in data:
        raise HTTPException(status_code=502, detail="Deezer request failed")

    # Return Python objects; FastAPI turns them into JSON and sets the headers.
    # Tracks without a preview (empty string) are skipped: we can't play them.
    return [
        Track(
            id=item["id"],
            title=item["title"],
            artist=item["artist"]["name"],
            preview_url=item["preview"],
        )
        for item in data["data"]
        if item.get("preview")
    ]


# Serve the page itself (static/index.html, script.js) from this same server, so
# the page and the API share one origin (localhost:8000) and CORS never applies.
#
# A mount at "/" matches EVERY path, and FastAPI checks routes in the order they
# were added, so this must come after the API routes or it would swallow them.
# html=True makes "/" serve index.html.
# Path(__file__).parent is this file's folder, so static/ is found no matter
# which directory uvicorn is started from.
STATIC_DIR = Path(__file__).parent / "static"
app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")
