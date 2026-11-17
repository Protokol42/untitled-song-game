"""The host's game settings: which values are allowed, and the defaults.

Like playlists.py, this is the one place to change them. The server only
accepts these values (rooms.change_settings), so a hand-made message can't ask
for 10,000 rounds or a 0-second clip. The browser draws its sliders and
buttons from what the server sends (setting_options in protocol.py), so the
allowed values are only written down here.
"""

from dataclasses import dataclass

from game import GameConfig


# A slider's range: min, max and the step between values, e.g. 5, 10, ... 50.
# frozen=True: these never change, and Python refuses if code tries.
@dataclass(frozen=True)
class Range:
    min: int
    max: int
    step: int

    # True for min, min + step, min + 2 × step, ... up to max. % is the
    # remainder after dividing: 0 means value is a whole number of steps up.
    def allows(self, value: int) -> bool:
        return self.min <= value <= self.max and (value - self.min) % self.step == 0


ROUNDS = Range(min=5, max=50, step=5)  # Songs per game
CLIP_SECONDS = Range(min=5, max=15, step=5)  # How long the clip plays = time to answer
CHOICES = [2, 4, 6]  # Answer buttons per round


# A new room's settings. A function, so each room gets its own GameConfig.
def default_settings() -> GameConfig:
    return GameConfig(rounds=10, clip_seconds=10, choices=4)
