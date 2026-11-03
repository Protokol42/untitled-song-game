"""Tests for the game engine (server/game.py).

Run from the project root, with the virtual environment active:
    pytest

pytest finds every function whose name starts with test_ and runs it. Inside,
`assert something` fails the test if `something` is false, and pytest shows
the values involved.
"""

import random

import pytest

from game import Game, GameConfig, Phase, score_for
from tracks import Track


# ---------------------------------------------------------------------------
# Helpers to set up a game quickly.
# ---------------------------------------------------------------------------

def make_tracks(count: int) -> list[Track]:
    return [
        Track(id=i, title=f"Song {i}", artist="Artist", preview_url=f"https://example.com/{i}.mp3")
        for i in range(1, count + 1)
    ]


# A game with two players, p1 and p2. **settings collects any extra keyword
# arguments into a dict, e.g. new_game(rounds=3) → settings = {"rounds": 3},
# and GameConfig(**settings) passes them on as GameConfig(rounds=3).
def new_game(players=("p1", "p2"), tracks=10, **settings) -> Game:
    # Seed 1: the same "random" deal every run, so a failure can be repeated.
    return Game(make_tracks(tracks), list(players), GameConfig(**settings), random.Random(1))


def right_answer(game: Game) -> int:
    return game.round.answer.id


def wrong_answer(game: Game) -> int:
    return next(track.id for track in game.round.choices if track.id != game.round.answer.id)


# ---------------------------------------------------------------------------
# Dealing
# ---------------------------------------------------------------------------

def test_answers_never_repeat():
    game = new_game(tracks=10, rounds=10)
    ids = [track.id for track in game.answers]
    assert len(set(ids)) == 10  # A set drops duplicates; still 10 means none


def test_fewer_tracks_than_rounds_means_fewer_rounds():
    game = new_game(tracks=3, rounds=20)
    assert len(game.answers) == 3


def test_needs_at_least_two_tracks():
    # pytest.raises: the test passes only if the code inside raises this error.
    with pytest.raises(ValueError):
        new_game(tracks=1)


def test_every_round_has_distinct_choices_including_the_answer():
    game = new_game(tracks=10, rounds=5, choices=4)
    while game.phase != Phase.RESULTS:
        titles = [track.title for track in game.round.choices]
        assert len(titles) == 4
        assert len(set(titles)) == 4
        assert game.round.answer in game.round.choices
        game.start_round(now=0)
        game.time_up()
        game.next_round()


def test_same_title_is_never_offered_twice():
    tracks = [
        Track(id=1, title="Home", artist="A", preview_url="x"),
        Track(id=2, title="HOME", artist="B", preview_url="x"),  # Same title, other case
        Track(id=3, title="Away", artist="C", preview_url="x"),
    ]
    game = Game(tracks, ["p1"], GameConfig(rounds=3, choices=3), random.Random(1))
    while game.phase != Phase.RESULTS:
        titles = [track.title.casefold() for track in game.round.choices]
        assert len(titles) == len(set(titles))
        game.start_round(now=0)
        game.time_up()
        game.next_round()


def test_clip_start_leaves_room_for_the_whole_clip():
    game = new_game(tracks=10, rounds=10, clip_seconds=10, preview_seconds=30)
    while game.phase != Phase.RESULTS:
        assert 0 <= game.round.clip_start <= 20
        game.start_round(now=0)
        game.time_up()
        game.next_round()


def test_next_clip_is_the_next_round_and_none_after_the_last():
    game = new_game(tracks=10, rounds=2)
    upcoming = game.next_clip()
    game.start_round(now=0)
    game.time_up()
    game.next_round()
    assert upcoming == (game.round.answer, game.round.clip_start)
    assert game.next_clip() is None  # Round 2 of 2: nothing comes after it


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------

# parametrize runs the same test once per line: elapsed=0 should give 1000, etc.
@pytest.mark.parametrize(
    ("elapsed", "points"),
    [
        (0, 1000),  # Instantly: full points
        (5, 500),  # Halfway
        (9.5, 100),  # Very late: 50 by the formula, but the floor is 100
        (10, 100),  # The very last moment still counts
    ],
)
def test_score_for_a_right_answer(elapsed, points):
    assert score_for(True, elapsed, clip_seconds=10) == points


def test_score_for_a_wrong_answer_is_zero():
    assert score_for(False, 0, clip_seconds=10) == 0


def test_guess_is_scored_from_round_start():
    game = new_game()
    game.start_round(now=100)  # The round starts at "100 seconds"
    game.guess("p1", right_answer(game), now=102.5)  # 2.5 s later
    assert game.round.guesses["p1"].points == 750


# ---------------------------------------------------------------------------
# Which guesses count
# ---------------------------------------------------------------------------

def test_guess_during_countdown_is_ignored():
    game = new_game()
    assert game.guess("p1", right_answer(game), now=0) is False
    assert game.round.guesses == {}


def test_guess_after_reveal_is_ignored():
    game = new_game()
    game.start_round(now=0)
    game.time_up()
    assert game.guess("p1", right_answer(game), now=1) is False


def test_second_guess_is_ignored():
    game = new_game()
    game.start_round(now=0)
    game.guess("p1", wrong_answer(game), now=1)
    assert game.guess("p1", right_answer(game), now=2) is False
    assert game.round.guesses["p1"].points == 0  # The first (wrong) guess stands


def test_guess_for_a_track_that_is_not_a_choice_is_ignored():
    game = new_game()
    game.start_round(now=0)
    assert game.guess("p1", 999, now=1) is False


def test_guess_from_an_unknown_player_is_ignored():
    game = new_game()
    game.start_round(now=0)
    assert game.guess("stranger", right_answer(game), now=1) is False


def test_guess_after_time_ran_out_is_ignored():
    # The timer that calls time_up() may fire a moment late; a guess in that
    # moment still shouldn't count.
    game = new_game(clip_seconds=10)
    game.start_round(now=0)
    assert game.guess("p1", right_answer(game), now=10.5) is False


# ---------------------------------------------------------------------------
# Moving between phases
# ---------------------------------------------------------------------------

def test_game_starts_in_countdown_for_round_one():
    game = new_game()
    assert game.phase == Phase.COUNTDOWN
    assert game.round.number == 1


def test_round_waits_until_everyone_has_answered():
    game = new_game(players=("p1", "p2"))
    game.start_round(now=0)
    game.guess("p1", right_answer(game), now=1)
    assert game.phase == Phase.PLAYING  # p2 hasn't answered yet
    game.guess("p2", wrong_answer(game), now=2)
    assert game.phase == Phase.REVEAL


def test_time_up_ends_the_round_and_non_answerers_get_nothing():
    game = new_game(players=("p1", "p2"))
    game.start_round(now=0)
    game.guess("p1", right_answer(game), now=1)
    game.time_up()
    assert game.phase == Phase.REVEAL
    assert "p2" not in game.round.guesses
    assert dict(game.scores())["p2"] == 0


def test_time_up_is_ignored_outside_playing():
    game = new_game()
    assert game.time_up() is False  # Still in COUNTDOWN
    assert game.phase == Phase.COUNTDOWN


def test_player_leaving_can_end_the_round():
    game = new_game(players=("p1", "p2"))
    game.start_round(now=0)
    game.guess("p1", right_answer(game), now=1)
    game.remove_player("p2")  # The only one we were still waiting for
    assert game.phase == Phase.REVEAL


def test_next_round_goes_back_to_countdown():
    game = new_game(rounds=3)
    game.start_round(now=0)
    game.time_up()
    game.next_round()
    assert game.phase == Phase.COUNTDOWN
    assert game.round.number == 2


def test_results_after_the_last_round():
    game = new_game(rounds=2)
    for _ in range(2):
        game.start_round(now=0)
        game.time_up()
        game.next_round()
    assert game.phase == Phase.RESULTS
    assert game.next_round() is False  # Nothing comes after RESULTS


# ---------------------------------------------------------------------------
# Scores
# ---------------------------------------------------------------------------

def test_scores_add_up_over_rounds_highest_first():
    game = new_game(players=("p1", "p2"), rounds=2)
    # Round 1: p1 right at 0 s (1000), p2 wrong (0)
    game.start_round(now=0)
    game.guess("p1", right_answer(game), now=0)
    game.guess("p2", wrong_answer(game), now=0)
    game.next_round()
    # Round 2: p2 right at 5 s (500), p1 doesn't answer
    game.start_round(now=0)
    game.guess("p2", right_answer(game), now=5)
    game.time_up()
    assert game.scores() == [("p1", 1000), ("p2", 500)]


def test_scores_ignore_the_current_round_until_it_is_revealed():
    game = new_game(players=("p1", "p2"))
    game.start_round(now=0)
    game.guess("p1", right_answer(game), now=0)
    assert dict(game.scores())["p1"] == 0  # Would give away that p1 was right
    game.time_up()
    assert dict(game.scores())["p1"] == 1000


def test_players_who_left_drop_off_the_scores():
    game = new_game(players=("p1", "p2"))
    game.remove_player("p2")
    assert game.scores() == [("p1", 0)]
