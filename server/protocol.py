"""The protocol: every message the browser and the server can send each other
over the WebSocket. If it isn't defined here, it isn't part of the game.

Each message is a JSON object with a "type" field saying what kind it is.
Pydantic checks incoming messages against these models, so the rest of the
server only ever sees well-formed messages.
"""

from typing import Annotated, Literal

from pydantic import BaseModel, Field, StringConstraints, TypeAdapter

# Reusable field types. Annotated[str, ...] means "a str, with these rules":
# surrounding spaces are removed first, then the length is checked.
Name = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=20)]
Token = Annotated[str, StringConstraints(min_length=1, max_length=100)]
RoomCode = Annotated[str, StringConstraints(strip_whitespace=True, to_upper=True)]


# ---------------------------------------------------------------------------
# Browser → server
#
# `token` is the player's secret, kept in the browser (sessionStorage). It's
# how a player will prove "it's me again" when reconnecting (roadmap step 4).
# It's never sent to other players; they see a separate public id instead.
# ---------------------------------------------------------------------------

# Literal["create"] means the field must be exactly the string "create".
class CreateRoom(BaseModel):
    type: Literal["create"]
    token: Token
    name: Name


class JoinRoom(BaseModel):
    type: Literal["join"]
    token: Token
    name: Name
    code: RoomCode  # "kxqp " is accepted as "KXQP"


# Coming back after the connection dropped (or the page was refreshed): the
# same tab, proving it's the same player with its token. Any phase.
class RejoinRoom(BaseModel):
    type: Literal["rejoin"]
    token: Token
    code: RoomCode


class LeaveRoom(BaseModel):
    type: Literal["leave"]


# Host only, in the lobby: fetch songs and start a game.
class StartGame(BaseModel):
    type: Literal["start"]


# Anyone, in the lobby: vote for the playlist you'd like to play. Each player
# has one vote; voting again moves it. playlist_id is one from playlists.py.
class VotePlaylist(BaseModel):
    type: Literal["vote"]
    playlist_id: int


# Host only, in the lobby: the playlist the next game will use.
class ChoosePlaylist(BaseModel):
    type: Literal["choose"]
    playlist_id: int


# Anyone, in the lobby: switch to another avatar nobody has. avatar is an id
# from avatars.py (rooms.py checks it's one of ours and that it's free).
class PickAvatar(BaseModel):
    type: Literal["avatar"]
    avatar: str


# Pick a title while the clip plays. track_id is one of the round's choices.
class MakeGuess(BaseModel):
    type: Literal["guess"]
    track_id: int


# Host only, on the results screen: take everyone back to the lobby.
class BackToLobby(BaseModel):
    type: Literal["lobby"]


# Any one of the messages above. The discriminator tells Pydantic to look at
# "type" first and check the message against that one model only, so errors
# are about the right model ("name is too long"), not a list of every model
# that didn't match.
ClientMessage = Annotated[
    CreateRoom
    | JoinRoom
    | RejoinRoom
    | LeaveRoom
    | VotePlaylist
    | ChoosePlaylist
    | PickAvatar
    | StartGame
    | MakeGuess
    | BackToLobby,
    Field(discriminator="type"),
]

# BaseModel classes can check data themselves (CreateRoom.model_validate_json),
# but ClientMessage is a union, not a class. A TypeAdapter gives any type the
# same abilities: client_message.validate_json(text) → a CreateRoom, a JoinRoom, ...
# (one of the models above), or a ValidationError.
client_message = TypeAdapter(ClientMessage)


# ---------------------------------------------------------------------------
# Server → browser
#
# These have defaults for `type`, so the server can write RoomState(code=...)
# without repeating type="room" every time.
# ---------------------------------------------------------------------------

class PlayerInfo(BaseModel):
    id: str  # Public: safe for everyone in the room to see
    name: str
    avatar: str  # An id from avatars.py; the browser decides how it looks
    # False while their connection is down and the room keeps their place
    # (see RECONNECT_SECONDS in rooms.py).
    connected: bool


# One row of the lobby's playlist menu.
class PlaylistInfo(BaseModel):
    id: int
    name: str
    voters: list[str]  # PlayerInfo ids, in the order they voted


# A button: the title only. The artist would make it too easy.
class ChoiceInfo(BaseModel):
    id: int
    title: str


# The right answer, once it may be shown.
class AnswerInfo(BaseModel):
    id: int
    title: str
    artist: str


# Just enough to download a clip in advance: no title, no artist.
class ClipInfo(BaseModel):
    preview_url: str
    clip_start: float


class PlayerScore(BaseModel):
    id: str  # A PlayerInfo id
    points: int


# One finished round, as seen by you (for the results screen).
class RoundResult(BaseModel):
    number: int
    answer: AnswerInfo
    picked: int | None  # The track you picked; None if you didn't answer
    points: int  # What you scored


# The game, as ONE player may see it. The server builds a separate copy for
# each player, and never puts in anything they shouldn't know yet: while the
# clip plays there's no answer, no points and no one else's picks (only who
# has answered).
class GameState(BaseModel):
    round: int  # 1 for the first round, ...
    rounds: int  # ... out of this many
    clip_seconds: float  # Time to answer, for the timer bar
    # This round's clip, sent from COUNTDOWN on so the browser can load it in advance.
    preview_url: str
    clip_start: float  # Seconds into the preview; the same for everyone
    # The NEXT round's clip, during REVEAL only (None otherwise, and after the
    # last round), so the browser can download it while the answer is shown.
    next_clip: ClipInfo | None
    choices: list[ChoiceInfo]
    picked: int | None  # The track YOU picked this round, if any
    # Everyone who has answered this round (PlayerInfo ids, in answer order).
    # Shown while the clip plays: it says WHO answered, never what.
    answered: list[str]
    # What everyone picked: PlayerInfo id → track id. None until REVEAL, so
    # nobody can wait and copy a quicker player.
    picks: dict[str, int] | None
    # Seconds since this round's clip started, during PLAYING (None otherwise).
    # For someone who rejoins mid-round: their timer and clip start this far in.
    elapsed: float | None
    answer: AnswerInfo | None  # None until REVEAL
    points: int | None  # Your points for this round; None until REVEAL
    scores: list[PlayerScore]  # Revealed rounds only, highest first
    history: list[RoundResult]  # Every revealed round, in order


# The whole room, sent to everyone in it after every change.
class RoomState(BaseModel):
    type: Literal["room"] = "room"
    code: str
    you: str  # Your own public id, so the browser knows which player is you
    host: str  # The host's public id
    players: list[PlayerInfo]  # In join order
    playlists: list[PlaylistInfo]  # Most votes first; ties in playlists.py order
    playlist: int  # The id of the playlist the host chose
    # LOBBY, LOADING (fetching songs), or the game's phase: COUNTDOWN, PLAYING,
    # REVEAL, RESULTS.
    phase: str
    game: GameState | None  # None in LOBBY and LOADING


class ErrorMessage(BaseModel):
    type: Literal["error"] = "error"
    message: str  # Shown to the player as is


# ---------------------------------------------------------------------------
# WebSocket close codes
#
# When a connection is closed on purpose, the closer can give a number saying
# why. 1000–2999 are defined by the WebSocket standard; 4000–4999 are free for
# apps to use. The browser sees it as event.code in its onclose handler.
# ---------------------------------------------------------------------------

# This player was taken over by a newer connection with the same token (e.g.
# a duplicated tab). The old tab must NOT reconnect, or the two would keep
# taking the player back from each other.
CLOSE_REPLACED = 4000
