"""Tests for the lobby's playlist menu (server/rooms.py).

The functions tested here never send anything, so the players need no real
WebSocket: None stands in for it.
"""

from types import SimpleNamespace

import pytest

import rooms
from game import Phase
from playlists import PLAYLISTS
from rooms import RoomError

FIRST, SECOND, THIRD = (playlist.id for playlist in PLAYLISTS[:3])


# A fixture is set-up code that pytest runs for every test that names it as
# an argument. autouse=True: for every test in this file, without naming it.
# `yield` hands control to the test; the code after it runs when the test is
# done, so rooms made by one test never leak into the next.
@pytest.fixture(autouse=True)
def empty_rooms():
    yield
    rooms.open_rooms.clear()


# A room with a host (Nick) and two other players.
def lobby():
    room, host = rooms.create_room("t1", "Nick", None)
    _, alex = rooms.join_room(room.code, "t2", "Alex", None)
    _, sam = rooms.join_room(room.code, "t3", "Sam", None)
    return room, host, alex, sam


# Pretend a game is in progress: Room.phase only looks at game.phase, so a
# simple object with that one attribute is enough.
def fake_game(phase):
    return SimpleNamespace(phase=phase)


def menu_ids(room):
    return [info.id for info in rooms.playlist_menu(room)]


def test_a_new_room_has_the_first_playlist_selected():
    room, _, _, _ = lobby()
    assert room.playlist_id == FIRST


def test_without_votes_the_menu_is_in_playlists_py_order():
    room, _, _, _ = lobby()
    assert menu_ids(room) == [playlist.id for playlist in PLAYLISTS]


def test_most_votes_come_first():
    room, host, alex, sam = lobby()
    rooms.vote(room, alex, THIRD)
    rooms.vote(room, sam, THIRD)
    rooms.vote(room, host, SECOND)
    assert menu_ids(room)[:3] == [THIRD, SECOND, FIRST]


def test_the_menu_says_who_voted():
    room, _, alex, sam = lobby()
    rooms.vote(room, alex, SECOND)
    rooms.vote(room, sam, SECOND)
    second = rooms.playlist_menu(room)[0]
    assert second.id == SECOND
    assert second.voters == [alex.id, sam.id]


def test_voting_again_moves_your_vote():
    room, _, alex, _ = lobby()
    rooms.vote(room, alex, SECOND)
    rooms.vote(room, alex, THIRD)
    assert room.votes == {alex.id: THIRD}


def test_leaving_takes_your_vote_with_you():
    room, _, alex, _ = lobby()
    rooms.vote(room, alex, SECOND)
    rooms.leave_room(room, alex)
    assert room.votes == {}


def test_unknown_playlists_are_refused():
    room, host, alex, _ = lobby()
    with pytest.raises(RoomError):
        rooms.vote(room, alex, 12345)
    with pytest.raises(RoomError):
        rooms.choose_playlist(room, host, 12345)


def test_only_the_host_chooses():
    room, host, alex, _ = lobby()
    with pytest.raises(RoomError):
        rooms.choose_playlist(room, alex, SECOND)
    rooms.choose_playlist(room, host, SECOND)
    assert room.playlist_id == SECOND


def test_no_changes_during_a_game():
    room, host, alex, _ = lobby()
    room.game = fake_game(Phase.PLAYING)
    with pytest.raises(RoomError):
        rooms.vote(room, alex, SECOND)
    with pytest.raises(RoomError):
        rooms.choose_playlist(room, host, SECOND)


def test_after_a_game_the_lobby_keeps_the_playlist_and_votes():
    room, host, alex, _ = lobby()
    rooms.choose_playlist(room, host, SECOND)
    rooms.vote(room, alex, THIRD)
    room.game = fake_game(Phase.RESULTS)
    rooms.back_to_lobby(room, host)
    assert room.phase == "LOBBY"
    assert room.playlist_id == SECOND
    assert room.votes == {alex.id: THIRD}
