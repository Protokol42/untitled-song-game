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
- Python setup: one virtual environment at the project root (`.venv/`, git-ignored),
  dependencies pinned in the root `requirements.txt` (`pip freeze`)
- Throwaway tests go in `experiments/`

## Current status

- Roadmap step 1 (song-source test) is **done**.
  - Part 1 (iTunes, called directly from the browser) worked and was then deleted.
  - Part 2 lives in `experiments/song-source-deezer/`: a FastAPI server with
    `GET /api/search?q=...` that calls Deezer and returns our own `Track` model, and
    also serves the page from `static/`, so page and API share one origin (no CORS).
    Run from the root: `uvicorn server:app --reload --app-dir experiments/song-source-deezer`
- Next: roadmap step 2 (single-player). Open question: where tracks come from, since
  the roadmap said "all in the browser" but Deezer needs our server.
- Collaboration: present the plan and wait for the owner's go-ahead before writing code
  or installing anything. Then Claude writes small, well-commented pieces; the owner
  reads and runs them.
- Don't commit or push without asking first.
