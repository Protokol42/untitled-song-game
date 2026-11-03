"""Rooms: who is in which room, running each room's game, and sending every
player their view of it.

This is the layer between the WebSocket (main.py) and the game rules
(game.py): main.py hands it messages, it calls the engine, and it decides who
gets told what. The engine itself never sees a WebSocket or a clock.

Rooms live in a plain dictionary in memory, looked up by room code. They're
gone when the server restarts, which is fine for playing with friends.

Why no locks? The server runs on asyncio, which runs one piece of code at a
time and only switches to another (e.g. another player's connection) at an
`await`. None of the functions that change rooms below contain an `await`, so
"check the name is free, then add the player" can never be interrupted by a
second player joining with the same name in between.
"""

import asyncio
import random
import secrets
import time
from dataclasses import dataclass, field

from fastapi import WebSocket

import deezer
from game import Game, GameConfig, Phase, Round
from protocol import (
    AnswerInfo,
    ChoiceInfo,
    ClipInfo,
    ErrorMessage,
    GameState,
    PlayerInfo,
    PlayerScore,
    RoomState,
    RoundResult,
)
from tracks import Track

# No I or O: too easy to mix up with 1 and 0 when reading a code out loud.
CODE_LETTERS = "ABCDEFGHJKLMNPQRSTUVWXYZ"
CODE_LENGTH = 4  # 24^4 ≈ 330,000 possible codes

PLAYLIST_ID = 9890417302  # Deezer "100% Madeon". Host settings can change this later.


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
    game: Game | None = None  # None in the lobby
    # The background task running this room's game (run_game below), or None.
    # Keeping it here also matters for asyncio itself: it only keeps a weak
    # reference to tasks, so a task nobody holds on to can vanish mid-game.
    task: asyncio.Task | None = None
    # Set when the current round ends early (everyone answered), to wake up
    # run_game, which would otherwise wait for the clip's full time.
    round_over: asyncio.Event = field(default_factory=asyncio.Event)

    # The host is simply whoever has been in the room longest: the first in
    # the list. If the host leaves, the next player becomes host by itself.
    # @property makes it read like a field: room.host, not room.host().
    @property
    def host(self) -> Player:
        return self.players[0]

    # Where the room is, as one word for the browser.
    @property
    def phase(self) -> str:
        if self.game is not None:
            return self.game.phase  # COUNTDOWN, PLAYING, REVEAL or RESULTS
        if self.task is not None:
            return "LOADING"  # Started, still fetching the songs
        return "LOBBY"


open_rooms: dict[str, Room] = {}


# ---------------------------------------------------------------------------
# Joining and leaving. No awaits (see the top of the file).
# ---------------------------------------------------------------------------

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
    if room.phase != "LOBBY":
        raise RoomError("That room is playing; join when it's back in the lobby")
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
        if room.task is not None:
            # Stop the game: the task's current `await` (a sleep, say) raises
            # CancelledError, which ends run_game.
            room.task.cancel()
        return
    if room.game is not None:
        room.game.remove_player(player.id)
        wake_if_round_over(room)


# ---------------------------------------------------------------------------
# Playing: the messages from the browser that drive a game.
# ---------------------------------------------------------------------------

def start_game(room: Room, player: Player) -> None:
    if player is not room.host:  # `is`: the very same Player object
        raise RoomError("Only the host can start the game")
    if room.phase != "LOBBY":
        raise RoomError("The game has already started")
    # create_task starts run_game in the background and returns right away,
    # so this player's connection keeps handling messages while the game runs.
    room.task = asyncio.create_task(run_game(room))


# True if the guess counted, so the caller knows to send the guesser their
# updated view (their pick highlighted).
def guess(room: Room, player: Player, track_id: int) -> bool:
    if room.game is None:
        return False
    accepted = room.game.guess(player.id, track_id, now=time.monotonic())
    wake_if_round_over(room)
    return accepted


def back_to_lobby(room: Room, player: Player) -> None:
    if player is not room.host:
        raise RoomError("Only the host can go back to the lobby")
    if room.phase != Phase.RESULTS:
        raise RoomError("The game isn't over yet")
    room.game = None
    room.task = None


# If the round just ended early (everyone answered, or the last one we were
# waiting for left), wake up run_game, which is waiting on room.round_over.
def wake_if_round_over(room: Room) -> None:
    if room.game is not None and room.game.phase == Phase.REVEAL:
        room.round_over.set()


# ---------------------------------------------------------------------------
# The game loop: one per room, running in the background (start_game).
#
# It's the only code that WAITS: the countdown, the clip's time, the reveal.
# Everything is timed with time.monotonic(), a clock that only ever moves
# forward. The normal clock (time.time) can jump when the computer adjusts its
# time, which would make a round seem negative or minutes long.
# ---------------------------------------------------------------------------

async def run_game(room: Room) -> None:
    # Fresh tracks for every game: Deezer's preview links expire after 15 minutes.
    try:
        tracks = await deezer.get_playlist_tracks(PLAYLIST_ID)
        player_ids = [player.id for player in room.players]
        # random.Random() with no seed: different every game. (Tests pass a seed.)
        room.game = Game(tracks, player_ids, GameConfig(), random.Random())
    except (deezer.DeezerError, ValueError) as error:
        # ValueError: Game() refuses a playlist with fewer than 2 playable songs.
        room.task = None  # Back to the LOBBY phase
        await broadcast_error(room, f"Couldn't start the game: {error}")
        await broadcast(room)
        return

    game = room.game
    config = game.config
    while game.phase != Phase.RESULTS:
        # COUNTDOWN, before the first round only: everyone's browser gets the
        # clip's link and loads it. Later rounds start straight from REVEAL:
        # browsers already downloaded their clip while the answer was shown
        # (next_clip in game_state). The engine still passes through COUNTDOWN,
        # but with no await between next_round() and start_round() below,
        # nothing can happen while it's there.
        if game.round.number == 1:
            await broadcast(room)
            await asyncio.sleep(config.countdown_seconds)

        # PLAYING: the clock starts. A new Event for this round, so a leftover
        # set() from the last round can't end this one at once.
        room.round_over = asyncio.Event()
        game.start_round(now=time.monotonic())
        await broadcast(room)
        # Wait until everyone has answered (round_over gets set) or the time
        # is up, whichever comes first. wait_for gives up after `timeout`
        # seconds by raising TimeoutError.
        try:
            await asyncio.wait_for(room.round_over.wait(), timeout=config.clip_seconds)
        except TimeoutError:
            game.time_up()

        # REVEAL: the answer and everyone's points, then on to the next round
        # (or RESULTS after the last one).
        await broadcast(room)
        await asyncio.sleep(config.reveal_seconds)
        game.next_round()

    await broadcast(room)  # RESULTS


# ---------------------------------------------------------------------------
# What each player gets to see. Built fresh for every message, from the
# room and the engine, for one player at a time.
# ---------------------------------------------------------------------------

def answer_info(track: Track) -> AnswerInfo:
    return AnswerInfo(id=track.id, title=track.title, artist=track.artist)


def round_result(played: Round, player_id: str) -> RoundResult:
    guess = played.guesses.get(player_id)  # None if they didn't answer
    return RoundResult(
        number=played.number,
        answer=answer_info(played.answer),
        picked=guess.track_id if guess else None,
        points=guess.points if guess else 0,
    )


def game_state(game: Game, player_id: str) -> GameState:
    current = game.round
    revealed = game.phase in (Phase.REVEAL, Phase.RESULTS)
    guess = current.guesses.get(player_id)

    # During REVEAL, the next round's clip, to download in advance. Only its
    # link and start: the track's title would give the next answer away.
    next_clip = None
    upcoming = game.next_clip()
    if game.phase == Phase.REVEAL and upcoming is not None:
        track, clip_start = upcoming  # Unpack the (track, start) pair
        next_clip = ClipInfo(preview_url=track.preview_url, clip_start=clip_start)

    return GameState(
        round=current.number,
        rounds=len(game.answers),
        clip_seconds=game.config.clip_seconds,
        preview_url=current.answer.preview_url,
        clip_start=current.clip_start,
        next_clip=next_clip,
        choices=[ChoiceInfo(id=track.id, title=track.title) for track in current.choices],
        picked=guess.track_id if guess else None,
        # `X if condition else None`: only filled in once the round is revealed.
        answer=answer_info(current.answer) if revealed else None,
        points=(guess.points if guess else 0) if revealed else None,
        scores=[PlayerScore(id=player_id, points=points) for player_id, points in game.scores()],
        history=[round_result(played, player_id) for played in game.revealed_rounds()],
    )


def room_state(room: Room, player: Player) -> RoomState:
    return RoomState(
        code=room.code,
        you=player.id,
        host=room.host.id,
        players=[PlayerInfo(id=p.id, name=p.name) for p in room.players],
        phase=room.phase,
        game=game_state(room.game, player.id) if room.game is not None else None,
    )


# ---------------------------------------------------------------------------
# Sending. These await, so they come after a change is complete.
# ---------------------------------------------------------------------------

async def send_state(room: Room, player: Player) -> None:
    try:
        await player.websocket.send_text(room_state(room, player).model_dump_json())
    except Exception:
        # Their connection just died. Their own /ws handler will notice and
        # remove them; don't let one broken connection stop the others
        # from getting the update.
        pass


# Send everyone in the room their own view of it. Each player gets a separate
# copy: `you`, their picks and their round results differ.
async def broadcast(room: Room) -> None:
    # A copy of the list to loop over: while we await, a player can leave and
    # be removed from room.players.
    for player in list(room.players):
        await send_state(room, player)


async def send_error(websocket: WebSocket, message: str) -> None:
    await websocket.send_text(ErrorMessage(message=message).model_dump_json())


async def broadcast_error(room: Room, message: str) -> None:
    for player in list(room.players):
        try:
            await send_error(player.websocket, message)
        except Exception:
            pass  # Same as in send_state
