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


class LeaveRoom(BaseModel):
    type: Literal["leave"]


# Host only, in the lobby: fetch songs and start a game.
class StartGame(BaseModel):
    type: Literal["start"]


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
    CreateRoom | JoinRoom | LeaveRoom | StartGame | MakeGuess | BackToLobby,
    Field(discriminator="type"),
]

# BaseModel classes can check data themselves (CreateRoom.model_validate_json),
# but ClientMessage is a union, not a class. A TypeAdapter gives any type the
# same abilities: client_message.validate_json(text) → a CreateRoom, JoinRoom or
# LeaveRoom, or a ValidationError.
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
# clip plays there's no answer, no points and no one else's picks.
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
    # LOBBY, LOADING (fetching songs), or the game's phase: COUNTDOWN, PLAYING,
    # REVEAL, RESULTS.
    phase: str
    game: GameState | None  # None in LOBBY and LOADING


class ErrorMessage(BaseModel):
    type: Literal["error"] = "error"
    message: str  # Shown to the player as is
