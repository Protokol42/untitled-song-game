# CLAUDE.md

Browser-based multiplayer "guess the song" game: players hear a short snippet, pick the
title from multiple choices, and score on correctness and speed.

**Read [ARCHITECTURE.md](ARCHITECTURE.md) first.** It holds the agreed design principles,
decisions, music-source findings and roadmap. Keep it up to date when decisions change.

## Working style

- The owner wants to **understand everything well enough to rebuild it without help**.
  Explain the why behind each step, keep steps small, and prefer clear code over clever code.
- Teach concepts as they come up (e.g. CORS, WebSockets, state machines) instead of
  hiding them behind libraries.
- Don't add features, frameworks or infrastructure ahead of the roadmap.

## Stack

- Client: HTML, CSS, vanilla JavaScript
- Server: Python (FastAPI + Uvicorn + httpx + websockets)
- Python setup: Python 3.14 (pinned in `.python-version`, which Render also reads), one
  virtual environment at the project root (`.venv/`, git-ignored), dependencies pinned in
  the root `requirements.txt` (`pip freeze`)
- Throwaway tests go in `experiments/`

## Current status

- Roadmap step 1 (song-source test) is **done**.
  - Part 1 (iTunes, called directly from the browser) worked and was then deleted.
  - Part 2 (Deezer through a FastAPI server that also serves the page, so page and API
    share one origin and there's no CORS) worked; it grew into `server/` and was deleted.
- Roadmap step 2 (single-player) is **done**.
- Roadmap step 3 (multiplayer) is in progress; parts 1–5 of 6 are done (see the roadmap in
  ARCHITECTURE.md). Run from the root: `uvicorn main:app --reload --app-dir server`.
  Tests: `pytest` from the root (`pytest.ini` points it at `server/tests`).
  - Server (`server/`): `main.py` (routes only: `GET /api/playlist/{id}/tracks`, `/ws`,
    serves `client/`), `tracks.py` (`Track`), `deezer.py` (provider), `protocol.py`
    (WebSocket messages as Pydantic models), `rooms.py` (in-memory rooms, playlist votes, the
    per-room `run_game` task, each player's view, broadcast), `playlists.py` (the
    playlist menu's list), `game.py` (the engine: `Game`,
    `Phase`, `score_for`). `deezer.py` raises its own `DeezerError`/`PlaylistNotFound`.
  - Client (`client/script.js`): one `state` object (mainly the server's latest `room`
    message), `setState`, `render(state)`. The screen follows `room.phase` (HOME when not
    in a room). No game rules in the browser any more; `receiveRoom` loads the clip on
    COUNTDOWN (round 1) and plays it on PLAYING (`isNewStep`); during REVEAL a hidden
    `#preloader` `<audio>` downloads `game.next_clip`, so later rounds start straight
    from REVEAL with no countdown. Actions just `send()` messages.
  - `GET /api/playlist/{id}/tracks` is no longer used by the client; kept for now.
  - Lobby playlist menu (done ahead of step 4's host settings): `playlists.py` is the list
    of Deezer playlists; everyone votes (`vote`), the host chooses (`choose`), the game
    uses `room.playlist_id`. The client filters by search and keeps focus/scroll across
    redraws (`rememberView`/`restoreView` in `render`).
  - Next: part 6, playtest on home wifi (`--host 0.0.0.0`), then on Render.
- Collaboration: present the plan and wait for the owner's go-ahead before writing code
  or installing anything. Then Claude writes small, well-commented pieces; the owner
  reads and runs them.
- Don't commit or push without asking first.
