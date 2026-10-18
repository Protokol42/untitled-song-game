"""Game server. For roadmap step 2 (single-player) it only supplies tracks;
the game itself runs in the browser.

Run from the project root, with the virtual environment active:
    uvicorn main:app --reload --app-dir server
"""

from pathlib import Path

import httpx
from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

DEEZER_API_URL = "https://api.deezer.com"
PLAYLIST_TRACK_LIMIT = 100  # Max tracks to ask Deezer for (bigger playlists get cut off)
DEEZER_NO_DATA = 800  # Deezer's error code for "no such playlist/track/..."

app = FastAPI()


# ---------------------------------------------------------------------------
# Our own model. The rest of the code (and the browser) only ever sees this.
# ---------------------------------------------------------------------------

class Track(BaseModel):
    id: int
    title: str
    artist: str
    preview_url: str


# ---------------------------------------------------------------------------
# Deezer provider: everything that knows about Deezer's API lives here.
# Adding another service later (e.g. iTunes) means writing another section
# like this one that also returns Tracks; the routes below don't change.
# ---------------------------------------------------------------------------

# The adapter: turns one item from Deezer's JSON into our Track.
# title_short drops extras like "(2015 Remaster)" or "(From the Film ...)",
# which would make the right answer stand out among the choices.
def to_track(item: dict) -> Track:
    return Track(
        id=item["id"],
        title=item["title_short"],
        artist=item["artist"]["name"],
        preview_url=item["preview"],
    )


async def get_playlist_tracks(playlist_id: int) -> list[Track]:
    url = f"{DEEZER_API_URL}/playlist/{playlist_id}/tracks"
    async with httpx.AsyncClient() as client:
        response = await client.get(url, params={"limit": PLAYLIST_TRACK_LIMIT})
    data = response.json()

    # Deezer reports errors inside a 200 response: {"error": {"code": 800, ...}}.
    # Turn them into proper HTTP errors for the browser:
    #   404 Not Found:   the playlist doesn't exist (code 800, "no data")
    #   502 Bad Gateway: anything else, i.e. "the service behind me failed"
    if "error" in data:
        if data["error"].get("code") == DEEZER_NO_DATA:
            raise HTTPException(status_code=404, detail="Playlist not found")
        raise HTTPException(status_code=502, detail="Deezer request failed")

    # Many playlist tracks have no preview (empty string), about 30% in the
    # ones we tried. Skip them; we can't play them.
    return [to_track(item) for item in data["data"] if item.get("preview")]


# ---------------------------------------------------------------------------
# Routes: what the browser can ask for.
# ---------------------------------------------------------------------------

# {playlist_id} in the path becomes the function's playlist_id argument.
# The `int` type hint means FastAPI rejects /api/playlist/abc/tracks with a 422
# before our code runs.
@app.get("/api/playlist/{playlist_id}/tracks")
async def playlist_tracks(playlist_id: int) -> list[Track]:
    return await get_playlist_tracks(playlist_id)


# Serve the game page (client/index.html, script.js) from this same server, so
# the page and the API share one origin (localhost:8000) and CORS never applies.
#
# A mount at "/" matches EVERY path, and FastAPI checks routes in the order they
# were added, so this must come after the API routes or it would swallow them.
# html=True makes "/" serve index.html.
# Path(__file__).parent is server/; one more .parent is the project root.
CLIENT_DIR = Path(__file__).parent.parent / "client"
app.mount("/", StaticFiles(directory=CLIENT_DIR, html=True), name="client")
