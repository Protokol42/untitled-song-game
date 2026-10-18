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
- Server: Python (FastAPI + Uvicorn + httpx). WebSockets not built yet
- Python setup: Python 3.14 (pinned in `.python-version`, which Render also reads), one
  virtual environment at the project root (`.venv/`, git-ignored), dependencies pinned in
  the root `requirements.txt` (`pip freeze`)
- Throwaway tests go in `experiments/`

## Current status

- Roadmap step 1 (song-source test) is **done**.
  - Part 1 (iTunes, called directly from the browser) worked and was then deleted.
  - Part 2 (Deezer through a FastAPI server that also serves the page, so page and API
    share one origin and there's no CORS) worked; it grew into `server/` and was deleted.
- Roadmap step 2 (single-player) is in progress, in `server/` + `client/`:
  - The server only supplies tracks: `GET /api/playlist/{id}/tracks` (Deezer, our
    `Track` model, `title_short`, tracks without previews skipped). It also serves
    `client/`. Run from the root: `uvicorn main:app --reload --app-dir server`
  - The game runs in the browser (`client/script.js`): one `state` object, `setState`,
    `render(state)`, phases `START → LOADING → PLAYING ⇄ REVEAL → RESULTS`.
  - Done: playable rounds, countdown bar, scoring, full game with results, dark-theme
    CSS (`client/style.css`: Grid layout, sidebar becomes a top strip below 800px).
    Next: playtest with friends, then decide on roadmap step 3 (multiplayer).
- Collaboration: present the plan and wait for the owner's go-ahead before writing code
  or installing anything. Then Claude writes small, well-commented pieces; the owner
  reads and runs them.
- Don't commit or push without asking first.
