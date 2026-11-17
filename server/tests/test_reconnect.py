"""Tests for reconnecting (server/rooms.py): keeping a disconnected player's
place, taking it back with the token, and removing them if they don't return.

disconnect() starts a background task (asyncio.create_task), which only works
while an event loop is running. So those tests write their steps as an
`async def` and run it with asyncio.run(), which starts a loop, runs the
function to the end, and stops the loop again.

As in test_rooms.py, a plain object() stands in for each WebSocket.
"""

import asyncio
import random

import pytest

import rooms
from game import Game, GameConfig
from rooms import RoomError
from tracks import Track


@pytest.fixture(autouse=True)
def empty_rooms():
    yield
    rooms.open_rooms.clear()


# Wait seconds instead of minutes. monkeypatch is a fixture built into pytest:
# setattr changes a module's value for this one test and puts the old value
# back afterwards.
@pytest.fixture
def short_grace(monkeypatch):
    monkeypatch.setattr(rooms, "RECONNECT_SECONDS", 0.01)


# A room with a host (Nick, token "t1") and Alex (token "t2").
def lobby():
    room, nick = rooms.create_room("t1", "Nick", object())
    _, alex = rooms.join_room(room.code, "t2", "Alex", object())
    return room, nick, alex


# ---------------------------------------------------------------------------
# Disconnecting and coming back
# ---------------------------------------------------------------------------

def test_disconnecting_keeps_your_place():
    async def steps():
        room, nick, alex = lobby()
        rooms.disconnect(room, alex)
        assert alex in room.players
        assert not alex.connected
        rooms.room_state(room, nick)  # Builds fine with someone disconnected

    asyncio.run(steps())


def test_rejoining_with_your_token_gives_you_your_place_back():
    async def steps():
        room, _, alex = lobby()
        avatar = alex.avatar
        rooms.disconnect(room, alex)

        new_connection = object()
        back_room, back_player, old = rooms.rejoin_room(room.code, "t2", new_connection)

        assert back_room is room
        assert back_player is alex  # The same player, not a new one
        assert alex.websocket is new_connection
        assert alex.avatar == avatar
        assert alex.drop_task is None  # The removal timer was cancelled
        assert old is None  # Nothing left to close

    asyncio.run(steps())


def test_rejoining_while_still_connected_hands_back_the_old_connection():
    # E.g. a duplicated tab: main.py closes the old one.
    room, _, alex = lobby()
    first = alex.websocket
    _, _, old = rooms.rejoin_room(room.code, "t2", object())
    assert old is first


def test_a_wrong_token_cant_take_over_anyone():
    room, _, _ = lobby()
    with pytest.raises(RoomError):
        rooms.rejoin_room(room.code, "not-a-real-token", object())


def test_rejoining_a_room_that_closed_fails():
    with pytest.raises(RoomError):
        rooms.rejoin_room("ZZZZ", "t1", object())


# ---------------------------------------------------------------------------
# The grace period running out
# ---------------------------------------------------------------------------

def test_not_coming_back_in_time_removes_you(short_grace):
    async def steps():
        room, _, alex = lobby()
        rooms.disconnect(room, alex)
        await asyncio.sleep(0.05)  # Longer than the (shortened) grace period
        assert alex not in room.players

    asyncio.run(steps())


def test_coming_back_in_time_cancels_the_removal(short_grace):
    async def steps():
        room, _, alex = lobby()
        rooms.disconnect(room, alex)
        rooms.rejoin_room(room.code, "t2", object())
        await asyncio.sleep(0.05)
        assert alex in room.players

    asyncio.run(steps())


def test_the_room_closes_when_the_last_player_runs_out_of_time(short_grace):
    async def steps():
        room, nick = rooms.create_room("t1", "Nick", object())
        rooms.disconnect(room, nick)
        await asyncio.sleep(0.05)
        assert room.code not in rooms.open_rooms

    asyncio.run(steps())


def test_your_name_and_avatar_stay_yours_while_youre_away():
    async def steps():
        room, _, alex = lobby()
        rooms.disconnect(room, alex)
        with pytest.raises(RoomError):
            rooms.join_room(room.code, "t3", "Alex", object())  # Name still taken
        _, sam = rooms.join_room(room.code, "t3", "Sam", object())
        assert sam.avatar != alex.avatar

    asyncio.run(steps())


# ---------------------------------------------------------------------------
# The host
# ---------------------------------------------------------------------------

def test_the_host_passes_on_while_theyre_away_and_comes_back_with_them():
    async def steps():
        room, nick, alex = lobby()
        assert room.host is nick

        rooms.disconnect(room, nick)
        assert room.host is alex  # Alex can start the game meanwhile

        rooms.rejoin_room(room.code, "t1", object())
        assert room.host is nick  # First in the list again

    asyncio.run(steps())


# ---------------------------------------------------------------------------
# Catching up mid-round
# ---------------------------------------------------------------------------

def test_the_game_says_how_far_into_the_round_it_is():
    tracks = [
        Track(id=i, title=f"Song {i}", artist="Artist", preview_url=f"https://example.com/{i}.mp3")
        for i in range(1, 11)
    ]
    game = Game(tracks, ["p1", "p2"], GameConfig(), random.Random(1))
    assert rooms.game_state(game, "p1", now=0).elapsed is None  # COUNTDOWN

    game.start_round(now=100)
    assert rooms.game_state(game, "p1", now=104.5).elapsed == 4.5

    game.time_up()
    assert rooms.game_state(game, "p1", now=200).elapsed is None  # REVEAL
