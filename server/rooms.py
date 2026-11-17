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

Reconnecting: a player and their connection are two different things. When a
connection drops, the player stays in the room for RECONNECT_SECONDS with no
connection (websocket None), keeping their name, avatar and score. If the same
browser tab comes back in time with its secret token (`rejoin`), it takes over
that player again. If not, they're removed as if they had left.
"""

import asyncio
import random
import secrets
import time
from dataclasses import dataclass, field

from fastapi import WebSocket

import deezer
from avatars import AVATARS
from game import Game, GameConfig, Phase, Round
from playlists import PLAYLIST_IDS, PLAYLISTS
from protocol import (
    AnswerInfo,
    ChoiceInfo,
    ClipInfo,
    ErrorMessage,
    GameState,
    PlayerInfo,
    PlayerScore,
    PlaylistInfo,
    RoomState,
    RoundResult,
)
from tracks import Track

# No I or O: too easy to mix up with 1 and 0 when reading a code out loud.
CODE_LETTERS = "ABCDEFGHJKLMNPQRSTUVWXYZ"
CODE_LENGTH = 4  # 24^4 ≈ 330,000 possible codes

# One avatar each, all different, so a room holds as many players as there are
# avatars. Worked out from the list rather than written as 8, so the two
# numbers can never disagree.
MAX_PLAYERS = len(AVATARS)

# How long a disconnected player keeps their place (a locked phone, a wifi
# blip, a refresh). Long enough to forgive most of those; past it, they're
# probably gone for good. Tests make it tiny.
RECONNECT_SECONDS = 180


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
    avatar: str  # One of avatars.py's ids; no one else in the room has it
    # This player's connection, to send them messages. None while they're
    # disconnected and we're waiting for them to come back.
    websocket: WebSocket | None
    # While disconnected: the background task that removes them once
    # RECONNECT_SECONDS have passed (drop_later). Cancelled if they rejoin.
    drop_task: asyncio.Task | None = None

    @property
    def connected(self) -> bool:
        return self.websocket is not None


@dataclass
class Room:
    code: str
    # In join order. field(default_factory=list) gives each Room its own new
    # list; a plain `= []` default would be one list shared by every Room.
    players: list[Player] = field(default_factory=list)
    # The lobby's playlist menu. votes: player id → the playlist id they voted
    # for; a dict gives each player at most one vote. playlist_id: the host's
    # choice, which the next game uses. Neither is reset after a game, so the
    # lobby comes back with the same playlist selected.
    votes: dict[str, int] = field(default_factory=dict)
    playlist_id: int = PLAYLISTS[0].id
    game: Game | None = None  # None in the lobby
    # The background task running this room's game (run_game below), or None.
    # Keeping it here also matters for asyncio itself: it only keeps a weak
    # reference to tasks, so a task nobody holds on to can vanish mid-game.
    task: asyncio.Task | None = None
    # Set when the current round ends early (everyone answered), to wake up
    # run_game, which would otherwise wait for the clip's full time.
    round_over: asyncio.Event = field(default_factory=asyncio.Event)

    # The host is whoever has been in the room longest AND is connected: the
    # first connected player in the list. If the host leaves or their phone
    # locks, the next player becomes host by itself, so nobody is stuck
    # waiting for a Start button; when the old host comes back, they're first
    # in the list again and get it back. (If nobody is connected, it doesn't
    # matter who: fall back to the first player.)
    # @property makes it read like a field: room.host, not room.host().
    @property
    def host(self) -> Player:
        # next(generator, default): the first matching item, or the default.
        return next((p for p in self.players if p.connected), self.players[0])

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


def new_player(token: str, name: str, avatar: str, websocket: WebSocket) -> Player:
    # token_hex(4): 8 random hex characters like "a1b2c3d4". Only needs to be
    # unique within a room, so it can be short.
    return Player(
        id=secrets.token_hex(4), token=token, name=name, avatar=avatar, websocket=websocket
    )


# A random avatar that nobody in the room has yet. There's no separate "taken"
# list to keep up to date: what's taken is simply what the players have, so
# when someone leaves, their avatar is free again by itself.
# (random, not secrets: there's nothing to guess or exploit here.)
def free_avatar(room: Room) -> str:
    taken = {player.avatar for player in room.players}  # A set: fast `in` checks
    free = [avatar for avatar in AVATARS if avatar not in taken]
    return random.choice(free)  # join_room checks the room isn't full first


def create_room(token: str, name: str, websocket: WebSocket) -> tuple[Room, Player]:
    room = Room(code=new_code())
    player = new_player(token, name, free_avatar(room), websocket)
    room.players.append(player)
    open_rooms[room.code] = room
    return room, player


def join_room(code: str, token: str, name: str, websocket: WebSocket) -> tuple[Room, Player]:
    room = open_rooms.get(code)  # .get gives None instead of a KeyError
    if room is None:
        raise RoomError("No room with that code")
    if room.phase != "LOBBY":
        raise RoomError("That room is playing; join when it's back in the lobby")
    if len(room.players) >= MAX_PLAYERS:
        raise RoomError(f"That room is full ({MAX_PLAYERS} players)")
    # casefold() is lower() that also handles special cases like "ß" → "ss".
    if any(other.name.casefold() == name.casefold() for other in room.players):
        raise RoomError("Someone in that room already has that name")
    # No await between the full check and here (see the top of the file), so
    # two people joining at the same moment can't both get the last avatar.
    player = new_player(token, name, free_avatar(room), websocket)
    room.players.append(player)
    return room, player


# Switch to another avatar, in the lobby. Like the name check in join_room,
# "is it free?" and "take it" have no await between them, so if two players
# click the same avatar at the same moment, the first gets it and the second
# is told it's taken.
def pick_avatar(room: Room, player: Player, avatar: str) -> None:
    if room.phase != "LOBBY":
        raise RoomError("Avatars can only be changed in the lobby")
    # Browsers can send any text: only accept our own ids (see avatars.py).
    if avatar not in AVATARS:
        raise RoomError("Unknown avatar")
    # `other is not player`: clicking the one you already have is fine (and
    # changes nothing).
    if any(other.avatar == avatar and other is not player for other in room.players):
        raise RoomError("Someone already has that one")
    player.avatar = avatar  # Your old one is free again by itself (free_avatar)


# The same browser tab coming back (after a wifi blip, a locked phone, a
# refresh). The token proves it's them: only their tab knows it, everyone else
# only ever sees the public id. Works in any phase, mid-game too, because it's
# not a new player joining, it's an existing one getting a new connection.
#
# Returns the connection this player had before, if it's still attached, so
# main.py can close it: that happens when the server hasn't noticed yet that
# the old one died, or when the tab was duplicated (a duplicate tab copies
# sessionStorage, token and all). Otherwise None.
def rejoin_room(code: str, token: str, websocket: WebSocket) -> tuple[Room, Player, WebSocket | None]:
    room = open_rooms.get(code)
    if room is None:
        raise RoomError("That room has closed")
    player = next((p for p in room.players if p.token == token), None)
    if player is None:
        raise RoomError("You've been away too long and left that room")
    if player.drop_task is not None:
        player.drop_task.cancel()  # They're back: don't remove them
        player.drop_task = None
    old = player.websocket
    player.websocket = websocket
    return room, player, old


# Their connection dropped: keep their place for RECONNECT_SECONDS. Unlike
# leave_room they stay in room.players and in the game, so their score and
# avatar wait for them.
def disconnect(room: Room, player: Player) -> None:
    player.websocket = None
    player.drop_task = asyncio.create_task(drop_later(room, player))


async def drop_later(room: Room, player: Player) -> None:
    await asyncio.sleep(RECONNECT_SECONDS)
    # Normally rejoin_room cancels this task, so we never get here. Checking
    # again anyway costs nothing and makes sure we never remove someone who
    # is back.
    if player in room.players and not player.connected:
        player.drop_task = None
        leave_room(room, player)
        await broadcast(room)


def leave_room(room: Room, player: Player) -> None:
    room.players.remove(player)
    # Their vote goes with them. pop(key, None) removes it if it's there and
    # does nothing if they never voted (del would raise a KeyError).
    room.votes.pop(player.id, None)
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
# The lobby's playlist menu. Also no awaits.
# ---------------------------------------------------------------------------

# Browsers can send any number, so check it's one of ours: otherwise a
# hand-made message could make the server fetch any playlist on Deezer.
def check_playlist(room: Room, playlist_id: int) -> None:
    if room.phase != "LOBBY":
        raise RoomError("The playlist can only be changed in the lobby")
    if playlist_id not in PLAYLIST_IDS:
        raise RoomError("Unknown playlist")


def vote(room: Room, player: Player, playlist_id: int) -> None:
    check_playlist(room, playlist_id)
    room.votes[player.id] = playlist_id  # Replaces their earlier vote, if any


def choose_playlist(room: Room, player: Player, playlist_id: int) -> None:
    if player is not room.host:
        raise RoomError("Only the host can choose the playlist")
    check_playlist(room, playlist_id)
    room.playlist_id = playlist_id


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


# True if the guess counted, so the caller knows to tell everyone (the guesser
# sees their pick highlighted; the others see that they've answered).
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
        tracks = await deezer.get_playlist_tracks(room.playlist_id)
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


# `now` is time.monotonic() (passed in, like the engine's, so tests can choose it).
def game_state(game: Game, player_id: str, now: float) -> GameState:
    current = game.round
    revealed = game.phase in (Phase.REVEAL, Phase.RESULTS)
    guess = current.guesses.get(player_id)

    # How far into the round we are, while it plays. Normally the browser
    # starts its timer when PLAYING arrives, but someone rejoining mid-round
    # needs to know they've arrived late: their timer bar and clip start this
    # far in.
    elapsed = None
    if game.phase == Phase.PLAYING:
        elapsed = now - current.started_at

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
        # Who has answered: safe to show while the clip plays, since it doesn't
        # say WHAT they picked. Dicts keep the order keys were added, so this
        # is the order they answered in.
        answered=list(current.guesses),
        # `X if condition else None`: only filled in once the round is revealed.
        # What everyone picked, only now: during PLAYING it would let you copy.
        picks=(
            {guesser: their_guess.track_id for guesser, their_guess in current.guesses.items()}
            if revealed
            else None
        ),
        elapsed=elapsed,
        answer=answer_info(current.answer) if revealed else None,
        points=(guess.points if guess else 0) if revealed else None,
        scores=[PlayerScore(id=player_id, points=points) for player_id, points in game.scores()],
        history=[round_result(played, player_id) for played in game.revealed_rounds()],
    )


# The playlist menu: every playlist with who voted for it, most votes first.
def playlist_menu(room: Room) -> list[PlaylistInfo]:
    # Dicts remember the order keys were added, so voters come out in the
    # order they first voted.
    menu = [
        PlaylistInfo(
            id=playlist.id,
            name=playlist.name,
            voters=[voter for voter, voted_for in room.votes.items() if voted_for == playlist.id],
        )
        for playlist in PLAYLISTS
    ]
    # sorted() is "stable": items that compare equal keep their order, so
    # playlists with the same number of votes stay in playlists.py order.
    # The key is the minus count, because sorted() puts the smallest first.
    return sorted(menu, key=lambda info: -len(info.voters))


def room_state(room: Room, player: Player) -> RoomState:
    return RoomState(
        code=room.code,
        you=player.id,
        host=room.host.id,
        players=[
            PlayerInfo(id=p.id, name=p.name, avatar=p.avatar, connected=p.connected)
            for p in room.players
        ],
        playlists=playlist_menu(room),
        playlist=room.playlist_id,
        phase=room.phase,
        game=(
            game_state(room.game, player.id, now=time.monotonic())
            if room.game is not None
            else None
        ),
    )


# ---------------------------------------------------------------------------
# Sending. These await, so they come after a change is complete.
# ---------------------------------------------------------------------------

async def send_state(room: Room, player: Player) -> None:
    # Disconnected: nothing to send to. They get the whole room again when
    # they rejoin, so they don't miss anything that matters.
    websocket = player.websocket
    if websocket is None:
        return
    try:
        await websocket.send_text(room_state(room, player).model_dump_json())
    except Exception:
        # Their connection just died. Their own /ws handler will notice and
        # call disconnect; don't let one broken connection stop the others
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
        if player.websocket is None:
            continue  # Disconnected (see send_state)
        try:
            await send_error(player.websocket, message)
        except Exception:
            pass  # Same as in send_state
