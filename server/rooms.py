"""Rooms: who is in which room, and sending them the room's state.

Rooms live in a plain dictionary in memory, looked up by room code. They're
gone when the server restarts, which is fine for playing with friends.

Why no locks? The server runs on asyncio, which runs one piece of code at a
time and only switches to another (e.g. another player's connection) at an
`await`. None of the functions that change rooms below contain an `await`, so
"check the name is free, then add the player" can never be interrupted by a
second player joining with the same name in between.
"""

import secrets
from dataclasses import dataclass, field

from fastapi import WebSocket

from protocol import ErrorMessage, PlayerInfo, RoomState

# No I or O: too easy to mix up with 1 and 0 when reading a code out loud.
CODE_LETTERS = "ABCDEFGHJKLMNPQRSTUVWXYZ"
CODE_LENGTH = 4  # 24^4 ≈ 330,000 possible codes


# A problem the player should be told about, e.g. "No room with that code".
# Raising it lets the functions below stop early; the /ws route catches it
# and sends the message to the browser.
class RoomError(Exception):
    pass


# @dataclass writes __init__ for us: Player(id=..., token=..., ...). Unlike the
# Pydantic models in protocol.py it doesn't check anything; these objects are
# made by our own code, not parsed from the outside world.
@dataclass
class Player:
    id: str  # Public, shown to everyone
    token: str  # Secret, never sent to anyone (see protocol.py)
    name: str
    websocket: WebSocket  # This player's connection, to send them messages


@dataclass
class Room:
    code: str
    # In join order. field(default_factory=list) gives each Room its own new
    # list; a plain `= []` default would be one list shared by every Room.
    players: list[Player] = field(default_factory=list)

    # The host is simply whoever has been in the room longest: the first in
    # the list. If the host leaves, the next player becomes host by itself.
    # @property makes it read like a field: room.host, not room.host().
    @property
    def host(self) -> Player:
        return self.players[0]


open_rooms: dict[str, Room] = {}


def new_code() -> str:
    # secrets.choice instead of random.choice: random's output can be
    # predicted from earlier output, so someone could guess other rooms' codes.
    while True:
        code = "".join(secrets.choice(CODE_LETTERS) for _ in range(CODE_LENGTH))
        if code not in open_rooms:  # Taken (very unlikely)? Pick again.
            return code


def new_player(token: str, name: str, websocket: WebSocket) -> Player:
    # token_hex(4): 8 random hex characters like "a1b2c3d4". Only needs to be
    # unique within a room, so it can be short.
    return Player(id=secrets.token_hex(4), token=token, name=name, websocket=websocket)


def create_room(token: str, name: str, websocket: WebSocket) -> tuple[Room, Player]:
    room = Room(code=new_code())
    player = new_player(token, name, websocket)
    room.players.append(player)
    open_rooms[room.code] = room
    return room, player


def join_room(code: str, token: str, name: str, websocket: WebSocket) -> tuple[Room, Player]:
    room = open_rooms.get(code)  # .get gives None instead of a KeyError
    if room is None:
        raise RoomError("No room with that code")
    # casefold() is lower() that also handles special cases like "ß" → "ss".
    if any(other.name.casefold() == name.casefold() for other in room.players):
        raise RoomError("Someone in that room already has that name")
    player = new_player(token, name, websocket)
    room.players.append(player)
    return room, player


def leave_room(room: Room, player: Player) -> None:
    room.players.remove(player)
    if not room.players:  # Empty list: the last player left
        del open_rooms[room.code]


# ---------------------------------------------------------------------------
# Sending. These await, so they come after a change is complete.
# ---------------------------------------------------------------------------

# Send everyone in the room the room's full state. Each player gets their own
# copy, because `you` is different for each of them.
async def broadcast(room: Room) -> None:
    players = [PlayerInfo(id=p.id, name=p.name) for p in room.players]
    # A copy of the list to loop over: while we await, a player can leave and
    # be removed from room.players.
    for player in list(room.players):
        state = RoomState(code=room.code, you=player.id, host=room.host.id, players=players)
        try:
            await player.websocket.send_text(state.model_dump_json())
        except Exception:
            # Their connection just died. Their own /ws handler will notice and
            # remove them; don't let one broken connection stop the others
            # from getting the update.
            pass


async def send_error(websocket: WebSocket, message: str) -> None:
    await websocket.send_text(ErrorMessage(message=message).model_dump_json())
