"""Our own track model. Every music provider (deezer.py, maybe iTunes later)
converts its data into this, so the rest of the code (and the browser) never
sees a provider's own format.
"""

from pydantic import BaseModel


class Track(BaseModel):
    id: int
    title: str
    artist: str
    preview_url: str
