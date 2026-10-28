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


# Any one of the messages above. The discriminator tells Pydantic to look at
# "type" first and check the message against that one model only, so errors
# are about the right model ("name is too long"), not a list of every model
# that didn't match.
ClientMessage = Annotated[CreateRoom | JoinRoom | LeaveRoom, Field(discriminator="type")]

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


# The whole room, sent to everyone in it after every change.
class RoomState(BaseModel):
    type: Literal["room"] = "room"
    code: str
    you: str  # Your own public id, so the browser knows which player is you
    host: str  # The host's public id
    players: list[PlayerInfo]  # In join order


class ErrorMessage(BaseModel):
    type: Literal["error"] = "error"
    message: str  # Shown to the player as is
