"""Tests for avatars, the room size limit and who sees whose picks (server/rooms.py).

Like test_rooms.py, nothing here sends anything, so None stands in for each
player's WebSocket.
"""

import random
from types import SimpleNamespace

import pytest

import rooms
from avatars import AVATARS
from game import Game, GameConfig, Phase
from rooms import MAX_PLAYERS, RoomError
from tracks import Track


@pytest.fixture(autouse=True)
def empty_rooms():
    yield
    rooms.open_rooms.clear()


# A room with `count` players: the host "P0", then "P1", "P2", ...
def room_of(count):
    room, _ = rooms.create_room("t0", "P0", None)
    for i in range(1, count):
        rooms.join_room(room.code, f"t{i}", f"P{i}", None)
    return room


def avatars_in(room):
    return [player.avatar for player in room.players]


# ---------------------------------------------------------------------------
# Avatars and the size limit
# ---------------------------------------------------------------------------

def test_the_limit_is_the_number_of_avatars():
    assert MAX_PLAYERS == len(AVATARS) == 8


def test_everyone_gets_a_different_avatar():
    room = room_of(MAX_PLAYERS)
    # A full room has used every avatar exactly once.
    assert sorted(avatars_in(room)) == sorted(AVATARS)


def test_a_full_room_turns_people_away():
    room = room_of(MAX_PLAYERS)
    with pytest.raises(RoomError):
        rooms.join_room(room.code, "late", "Late", None)
    assert len(room.players) == MAX_PLAYERS


def test_leaving_frees_your_avatar_and_your_place():
    room = room_of(MAX_PLAYERS)
    leaver = room.players[3]
    rooms.leave_room(room, leaver)
    _, newcomer = rooms.join_room(room.code, "new", "New", None)
    # The only avatar left over was the leaver's.
    assert newcomer.avatar == leaver.avatar
    assert sorted(avatars_in(room)) == sorted(AVATARS)


# ---------------------------------------------------------------------------
# Picking your own avatar in the lobby
# ---------------------------------------------------------------------------

def a_free_avatar(room):
    taken = avatars_in(room)
    return next(avatar for avatar in AVATARS if avatar not in taken)


def test_you_can_switch_to_a_free_avatar():
    room = room_of(3)
    me = room.players[1]
    wanted = a_free_avatar(room)
    rooms.pick_avatar(room, me, wanted)
    assert me.avatar == wanted


def test_switching_frees_your_old_avatar():
    room = room_of(2)
    me, other = room.players
    old = me.avatar
    rooms.pick_avatar(room, me, a_free_avatar(room))
    rooms.pick_avatar(room, other, old)  # Now free, so it's allowed
    assert other.avatar == old


def test_you_cant_take_someone_elses_avatar():
    room = room_of(2)
    me, other = room.players
    mine = me.avatar
    with pytest.raises(RoomError):
        rooms.pick_avatar(room, me, other.avatar)
    assert me.avatar == mine  # Unchanged


def test_picking_the_one_you_have_is_fine():
    room = room_of(2)
    me = room.players[0]
    rooms.pick_avatar(room, me, me.avatar)  # No error


def test_unknown_avatars_are_refused():
    room = room_of(1)
    with pytest.raises(RoomError):
        rooms.pick_avatar(room, room.players[0], "dragon")


def test_no_switching_during_a_game():
    room = room_of(2)
    room.game = SimpleNamespace(phase=Phase.PLAYING)  # See fake_game in test_rooms.py
    with pytest.raises(RoomError):
        rooms.pick_avatar(room, room.players[0], a_free_avatar(room))


# ---------------------------------------------------------------------------
# What game_state shows about other players' picks
# ---------------------------------------------------------------------------

def playing_game():
    tracks = [
        Track(id=i, title=f"Song {i}", artist="Artist", preview_url=f"https://example.com/{i}.mp3")
        for i in range(1, 11)
    ]
    game = Game(tracks, ["p1", "p2", "p3"], GameConfig(), random.Random(1))
    game.start_round(now=0)
    return game


def test_while_playing_you_see_who_answered_but_not_what():
    game = playing_game()
    first_choice = game.round.choices[0].id
    game.guess("p2", first_choice, now=1)

    seen_by_p1 = rooms.game_state(game, "p1")
    assert seen_by_p1.answered == ["p2"]
    assert seen_by_p1.picks is None
    assert seen_by_p1.picked is None  # p1 hasn't answered


def test_at_reveal_everyone_sees_every_pick():
    game = playing_game()
    first, second = game.round.choices[0].id, game.round.choices[1].id
    game.guess("p2", first, now=1)
    game.guess("p1", second, now=2)
    game.time_up()  # p3 never answers

    seen_by_p3 = rooms.game_state(game, "p3")
    assert seen_by_p3.answered == ["p2", "p1"]  # In the order they answered
    assert seen_by_p3.picks == {"p2": first, "p1": second}
