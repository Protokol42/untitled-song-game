"""The playlists players can choose from in the lobby.

To add one: find the playlist on deezer.com, copy the number from its link
(deezer.com/playlist/248297032 → 248297032) and add a line below. The name
is what players see; it doesn't have to match Deezer's.

The order matters a little: the FIRST playlist is selected in a new room,
and playlists with the same number of votes are listed in this order.
"""

from dataclasses import dataclass


# frozen=True: a Playlist can't be changed after it's made. These are
# settings, not something the game should ever edit by accident.
@dataclass(frozen=True)
class Playlist:
    id: int  # Deezer's playlist id
    name: str


PLAYLISTS = [
    Playlist(id=1977689462, name="2000s Party Hits"),
    Playlist(id=8282573142, name="2010s Pop"),
    Playlist(id=13650203641, name="2020s Pop"),
    Playlist(id=1282495565, name="Radar Weekly"),
    Playlist(id=8454338222, name="Christmas Hits"),
]

# Every valid id, to check the ids that browsers send. A set answers
# "is this in it?" instantly, however long the list gets.
PLAYLIST_IDS = {playlist.id for playlist in PLAYLISTS}
