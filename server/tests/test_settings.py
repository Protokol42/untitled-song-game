"""Tests for the host's game settings (server/settings.py, server/rooms.py).

As in test_rooms.py, a plain object() stands in for each player's WebSocket.
"""

from types import SimpleNamespace

import pytest

import rooms
import settings
from game import Phase
from rooms import RoomError


@pytest.fixture(autouse=True)
def empty_rooms():
    yield
    rooms.open_rooms.clear()


def lobby():
    room, host = rooms.create_room("t1", "Nick", object())
    _, alex = rooms.join_room(room.code, "t2", "Alex", object())
    return room, host, alex


# ---------------------------------------------------------------------------
# settings.Range: which slider values are allowed
# ---------------------------------------------------------------------------

def test_a_range_allows_every_step_from_min_to_max():
    allowed = [value for value in range(0, 60) if settings.ROUNDS.allows(value)]
    assert allowed == [5, 10, 15, 20, 25, 30, 35, 40, 45, 50]


def test_answer_time_is_5_10_or_15():
    allowed = [value for value in range(0, 30) if settings.CLIP_SECONDS.allows(value)]
    assert allowed == [5, 10, 15]


# ---------------------------------------------------------------------------
# Changing them
# ---------------------------------------------------------------------------

def test_a_new_room_has_the_defaults():
    room, _, _ = lobby()
    assert room.settings == settings.default_settings()


def test_the_host_can_change_the_settings():
    room, host, _ = lobby()
    rooms.change_settings(room, host, rounds=25, clip_seconds=15, choices=6)
    assert (room.settings.rounds, room.settings.clip_seconds, room.settings.choices) == (25, 15, 6)


def test_changing_keeps_the_settings_players_cant_change():
    room, host, _ = lobby()
    before = room.settings
    rooms.change_settings(room, host, rounds=25, clip_seconds=15, choices=6)
    assert room.settings.reveal_seconds == before.reveal_seconds
    assert room.settings.countdown_seconds == before.countdown_seconds


def test_changing_one_setting_leaves_the_others_alone():
    # Two quick changes, each sending only its own setting: the second
    # mustn't undo the first.
    room, host, _ = lobby()
    rooms.change_settings(room, host, rounds=35)
    rooms.change_settings(room, host, choices=6)
    assert (room.settings.rounds, room.settings.choices) == (35, 6)
    assert room.settings.clip_seconds == settings.default_settings().clip_seconds


def test_only_the_host_changes_the_settings():
    room, _, alex = lobby()
    with pytest.raises(RoomError):
        rooms.change_settings(room, alex, rounds=25, clip_seconds=15, choices=6)


@pytest.mark.parametrize(
    "rounds, clip_seconds, choices",
    [
        (0, 10, 4),  # Rounds below the minimum
        (55, 10, 4),  # Rounds above the maximum
        (12, 10, 4),  # Rounds between two steps
        (10, 20, 4),  # Answer time above the maximum
        (10, 10, 3),  # Choices not on the list
    ],
)
def test_values_settings_py_doesnt_allow_are_refused(rounds, clip_seconds, choices):
    # parametrize runs this test once per line above, with those values.
    room, host, _ = lobby()
    before = room.settings
    with pytest.raises(RoomError):
        rooms.change_settings(room, host, rounds, clip_seconds, choices)
    assert room.settings == before  # Nothing changed


def test_no_changes_during_a_game():
    room, host, _ = lobby()
    room.game = SimpleNamespace(phase=Phase.PLAYING)  # See fake_game in test_rooms.py
    with pytest.raises(RoomError):
        rooms.change_settings(room, host, rounds=25, clip_seconds=15, choices=6)


def test_after_a_game_the_lobby_keeps_the_settings():
    room, host, _ = lobby()
    rooms.change_settings(room, host, rounds=25, clip_seconds=15, choices=6)
    room.game = SimpleNamespace(phase=Phase.RESULTS)
    rooms.back_to_lobby(room, host)
    assert room.settings.rounds == 25


# ---------------------------------------------------------------------------
# What the browser gets
# ---------------------------------------------------------------------------

def test_everyone_sees_the_settings_and_what_can_be_chosen():
    room, host, alex = lobby()
    rooms.change_settings(room, host, rounds=25, clip_seconds=15, choices=6)
    seen = rooms.room_state(room, alex)
    assert (seen.settings.rounds, seen.settings.clip_seconds, seen.settings.choices) == (25, 15, 6)
    assert (seen.setting_options.rounds.min, seen.setting_options.rounds.max) == (5, 50)
    assert seen.setting_options.choices == [2, 4, 6]
