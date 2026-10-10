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
- Server: Python (FastAPI + WebSockets), not built yet
- Throwaway tests go in `experiments/`

## Current status

- Roadmap step 1 (song-source test) is in progress.
  - Part 1: iTunes Search API called directly from a single HTML page. Written in
    `experiments/song-source/`; the owner is reading and running it.
  - Part 2: Deezer through a small Python server (Deezer blocks browser calls via CORS).
- Collaboration: Claude writes small, well-commented pieces; the owner reads and runs them.
- Don't commit or push without asking first.
