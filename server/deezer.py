"""Deezer provider: everything that knows about Deezer's API lives here.

Adding another service later (e.g. iTunes) means writing another file like
this one that also returns Tracks; the code that uses it doesn't change.
"""

import httpx

from tracks import Track

DEEZER_API_URL = "https://api.deezer.com"
PLAYLIST_TRACK_LIMIT = 100  # Max tracks to ask Deezer for (bigger playlists get cut off)
DEEZER_NO_DATA = 800  # Deezer's error code for "no such playlist/track/..."


# Our own errors, so code that uses this file doesn't need to know about HTTP
# or httpx. The HTTP route turns them into status codes; the game turns them
# into a message for the players.
class DeezerError(Exception):
    pass


# A subclass: a PlaylistNotFound IS a DeezerError, so `except DeezerError`
# catches both, while `except PlaylistNotFound` catches only this one.
class PlaylistNotFound(DeezerError):
    pass


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
    # httpx raises its own errors when Deezer can't be reached (no network,
    # timeout) or doesn't answer with JSON. `raise ... from error` turns them
    # into ours while keeping the original attached, for debugging.
    try:
        async with httpx.AsyncClient() as client:
            response = await client.get(url, params={"limit": PLAYLIST_TRACK_LIMIT})
        data = response.json()
    except (httpx.HTTPError, ValueError) as error:
        raise DeezerError("Couldn't reach Deezer") from error

    # Deezer reports errors inside a 200 response: {"error": {"code": 800, ...}}.
    # Code 800 ("no data") means the playlist doesn't exist.
    if "error" in data:
        if data["error"].get("code") == DEEZER_NO_DATA:
            raise PlaylistNotFound("Playlist not found")
        raise DeezerError("Deezer request failed")

    # Many playlist tracks have no preview (empty string), about 30% in the
    # ones we tried. Skip them; we can't play them.
    return [to_track(item) for item in data["data"] if item.get("preview")]
