"""The game engine: the rules of one game, as plain Python.

It knows nothing about WebSockets, asyncio or the real clock:
- Time is passed in: guess(..., now=12.5) instead of reading the clock here.
  A test can say "this guess came 2 s after the round started" without waiting.
- Randomness is passed in: a random.Random object. Tests give it a fixed seed,
  so the "random" deal comes out the same every run.
- Waiting is someone else's job. The engine only says which moves are allowed
  in which phase; the code that runs a room (roadmap step 3, part 4) waits
  ("3 s countdown, then start the round") and calls the methods below.

Phases (a room with no game is in its lobby; that's not the engine's concern):

    COUNTDOWN → PLAYING → REVEAL → (next round: COUNTDOWN) → … → RESULTS

Every method checks the phase first and does nothing if the move isn't allowed
right now. It returns True if something changed, so the caller knows whether
to tell the players.
"""

import random
from dataclasses import dataclass, field
from enum import StrEnum

from tracks import Track


# StrEnum: a fixed set of named values that are also plain strings. Phase.PLAYING
# == "PLAYING", so it goes into JSON as "PLAYING", but a typo like Phase.PLAYNG
# is an error straight away instead of a phase that silently never matches.
class Phase(StrEnum):
    COUNTDOWN = "COUNTDOWN"  # Players' browsers load the clip
    PLAYING = "PLAYING"  # The clip plays; answers count
    REVEAL = "REVEAL"  # The answer and this round's points are shown
    RESULTS = "RESULTS"  # The game is over


# The settings for one game. The defaults match the browser's CONFIG for now;
# host settings (roadmap step 4) will create a GameConfig with other values.
@dataclass
class GameConfig:
    rounds: int = 20  # Songs per game (fewer if the playlist is smaller)
    choices: int = 4  # Answer buttons per round (1 right + the rest wrong)
    clip_seconds: float = 10  # How much of the song plays = time to answer
    countdown_seconds: float = 3  # Before the first round, to load its clip
    reveal_seconds: float = 5  # How long the answer stays up before the next round
    preview_seconds: float = 30  # Roughly how long Deezer's previews are


@dataclass
class Guess:
    track_id: int  # The track they picked
    elapsed: float  # Seconds from round start to the guess
    points: int


@dataclass
class Round:
    number: int  # 1 for the first round, 2 for the second, ...
    answer: Track
    choices: list[Track]  # Shuffled; the answer is one of them
    clip_start: float  # Seconds into the preview where the clip starts
    started_at: float | None = None  # `now` when PLAYING began; None during COUNTDOWN
    # Player id → their guess. Someone missing from here didn't answer (yet).
    guesses: dict[str, Guess] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Rules as plain functions (the same ones script.js has now).
# ---------------------------------------------------------------------------

# Points for one answer (formula from ARCHITECTURE.md). Right answers earn 1000
# at once, dropping steadily to 100 at the end; wrong answers earn 0.
#
# Python's round() rounds exact halves to the nearest EVEN number (round(2.5)
# is 2), unlike JavaScript's Math.round. Half a point either way doesn't matter.
def score_for(correct: bool, elapsed: float, clip_seconds: float) -> int:
    if not correct:
        return 0
    time_left = 1 - elapsed / clip_seconds  # 1 at the start → 0 at the end
    return round(max(100, 1000 * time_left))


# The answer plus wrong choices from the same playlist, all with different
# titles (ignoring case), so no two buttons say the same thing.
def pick_choices(answer: Track, tracks: list[Track], count: int, rng: random.Random) -> list[Track]:
    others = [track for track in tracks if track.id != answer.id]
    rng.shuffle(others)  # Shuffles the list in place (Fisher–Yates, like script.js)

    choices = [answer]
    used_titles = {answer.title.casefold()}  # A set, like the Set in script.js
    for track in others:
        if len(choices) == count:
            break
        title = track.title.casefold()
        if title not in used_titles:
            used_titles.add(title)
            choices.append(track)

    rng.shuffle(choices)  # Or the answer would always be the first button
    return choices


# ---------------------------------------------------------------------------
# One game.
# ---------------------------------------------------------------------------

class Game:
    def __init__(
        self,
        tracks: list[Track],
        player_ids: list[str],
        config: GameConfig,
        rng: random.Random,
    ) -> None:
        # One right and one wrong choice is the least that's still a choice.
        if len(tracks) < 2:
            raise ValueError("A game needs at least 2 tracks")
        self.tracks = tracks
        self.player_ids = list(player_ids)  # Our own copy; the caller's list can change
        self.config = config
        self.rng = rng

        # This game's answers, one per round. sample() picks k different items
        # in random order, so no song can be the answer twice.
        self.answers = rng.sample(tracks, k=min(config.rounds, len(tracks)))
        # And where each round's clip starts: a random point that leaves room
        # for a whole clip inside the preview. Picked now, not when each round
        # is dealt, so the NEXT round's clip is already known during REVEAL and
        # browsers can download it while the answer is on screen.
        latest_start = max(0, config.preview_seconds - config.clip_seconds)
        self.clip_starts = [rng.uniform(0, latest_start) for _ in self.answers]

        self.rounds: list[Round] = []  # Played and current rounds, in order
        self.phase = Phase.COUNTDOWN
        self._deal_round()

    # The current round: always the last one in the list.
    @property
    def round(self) -> Round:
        return self.rounds[-1]

    # Set up the next round. The leading _ means "only used inside this class".
    def _deal_round(self) -> None:
        number = len(self.rounds) + 1
        answer = self.answers[number - 1]  # Lists count from 0
        self.rounds.append(
            Round(
                number=number,
                answer=answer,
                choices=pick_choices(answer, self.tracks, self.config.choices, self.rng),
                clip_start=self.clip_starts[number - 1],  # The same for everyone
            )
        )

    # The track and clip start of the round after the current one, or None if
    # this is the last round. (The track is the next ANSWER, so never send it
    # as is; rooms.py only passes on its preview link.)
    def next_clip(self) -> tuple[Track, float] | None:
        number = len(self.rounds) + 1
        if number > len(self.answers):
            return None
        return self.answers[number - 1], self.clip_starts[number - 1]

    # COUNTDOWN → PLAYING. The clock for this round starts now.
    def start_round(self, now: float) -> bool:
        if self.phase != Phase.COUNTDOWN:
            return False
        self.round.started_at = now
        self.phase = Phase.PLAYING
        return True

    # A player picks a track. Counts only once per player per round, only for
    # one of this round's choices, and only while the clip is playing.
    def guess(self, player_id: str, track_id: int, now: float) -> bool:
        if self.phase != Phase.PLAYING:
            return False
        if player_id not in self.player_ids:
            return False
        if player_id in self.round.guesses:  # Already answered
            return False
        if track_id not in [track.id for track in self.round.choices]:
            return False
        elapsed = now - self.round.started_at
        # Time's up, even if the timer that calls time_up hasn't fired yet.
        if elapsed > self.config.clip_seconds:
            return False

        correct = track_id == self.round.answer.id
        points = score_for(correct, elapsed, self.config.clip_seconds)
        self.round.guesses[player_id] = Guess(track_id=track_id, elapsed=elapsed, points=points)
        self._end_round_if_all_answered()
        return True

    # PLAYING → REVEAL when the clip's time runs out. Whoever hasn't answered
    # gets nothing for this round (they're simply missing from guesses).
    def time_up(self) -> bool:
        if self.phase != Phase.PLAYING:
            return False
        self.phase = Phase.REVEAL
        return True

    # REVEAL → the next round's COUNTDOWN, or RESULTS after the last round.
    def next_round(self) -> bool:
        if self.phase != Phase.REVEAL:
            return False
        if len(self.rounds) == len(self.answers):
            self.phase = Phase.RESULTS
        else:
            self._deal_round()
            self.phase = Phase.COUNTDOWN
        return True

    # A player left mid-game. If everyone still here has answered, the round
    # shouldn't keep waiting for someone who's gone.
    def remove_player(self, player_id: str) -> bool:
        if player_id not in self.player_ids:
            return False
        self.player_ids.remove(player_id)
        if self.phase == Phase.PLAYING:
            self._end_round_if_all_answered()
        return True

    # A round ends early once every player has answered. Called after every
    # change that could make that true: a guess, or a player leaving.
    def _end_round_if_all_answered(self) -> None:
        # all() is True if the condition holds for every item (and for none).
        if all(player_id in self.round.guesses for player_id in self.player_ids):
            self.phase = Phase.REVEAL

    # The rounds whose answer everyone may know: all of them once the current
    # one is revealed, otherwise all but the current one.
    def revealed_rounds(self) -> list[Round]:
        if self.phase in (Phase.REVEAL, Phase.RESULTS):
            return self.rounds
        return self.rounds[:-1]  # [:-1] = everything except the last item

    # Each player's total so far: [(player_id, points), ...], highest first.
    # Computed from the rounds rather than stored, so it can't disagree with them.
    # Only revealed rounds count: if your total went up the moment you
    # answered, the scoreboard would tell you whether you were right.
    def scores(self) -> list[tuple[str, int]]:
        totals = {player_id: 0 for player_id in self.player_ids}
        for played in self.revealed_rounds():  # Not `round`: that would hide Python's round()
            for player_id, guess in played.guesses.items():
                if player_id in totals:  # Skip players who have left
                    totals[player_id] += guess.points
        # sorted() by the points (item[1]), biggest first. Python's sort keeps
        # the original order for ties, so equal scores stay in join order.
        return sorted(totals.items(), key=lambda item: item[1], reverse=True)
