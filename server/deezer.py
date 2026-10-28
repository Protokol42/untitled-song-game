"""Deezer provider: everything that knows about Deezer's API lives here.

Adding another service later (e.g. iTunes) means writing another file like
this one that also returns Tracks; the code that uses it doesn't change.
"""

import httpx
from fastapi import HTTPException

from tracks import Track

DEEZER_API_URL = "https://api.deezer.com"
PLAYLIST_TRACK_LIMIT = 100  # Max tracks to ask Deezer for (bigger playlists get cut off)
DEEZER_NO_DATA = 800  # Deezer's error code for "no such playlist/track/..."


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
