# Architecture

A browser-based multiplayer "guess the song" game. Players join a room, hear a short
snippet of a song, and pick the correct title from multiple choices. Points are awarded
for correctness and speed; after a set number of rounds the winner is announced.

**Current scope:** a private game for playing with friends (room codes).
**Future scope:** possible public release, so the structure should allow growth without
being over-engineered today.

---

## Overview

```
[ Browser UI ]  ⇄  [ WebSocket layer ]  ⇄  [ Game engine ]  →  [ Music provider ]
  shows state       translates messages     pure rules,         Deezer / iTunes /
  sends clicks      in and out              no networking       own clips
```

- **Client:** HTML, CSS and vanilla JavaScript.
- **Server:** Python (FastAPI) with WebSockets.
- **Storage:** none yet. Rooms live in memory.

---

## Design principles

### 1. The server is the authority
The server owns the game state, picks the songs, keeps time and computes scores. Clients
only display what the server sends and report player input.

- This handles fairness, cheating and keeping players in sync in one place.
- Never send the client anything it shouldn't know yet. The correct answer is revealed
  only after the round ends.
- Response time is measured by the server from the round's start, not from when each
  player's audio began.

### 2. Keep the game logic separate from the networking
Game rules (rounds, scoring, state transitions) are plain Python with no knowledge of
WebSockets.

- The engine can be unit-tested with `pytest` without a browser or network.
- The transport can change later without touching the rules.

### 3. Model the game as an explicit state machine

```
LOBBY → COUNTDOWN → PLAYING → REVEAL → (next round: COUNTDOWN) → … → RESULTS
```

Every incoming action is checked against the current state. For example, a guess that
arrives during `REVEAL` is ignored. Most multiplayer bugs are actions arriving in the
wrong state; this makes them visible and easy to handle.

### 4. Put the music API behind our own interface
The game uses its own `Track` model (id, title, artist, preview URL) and a provider
interface such as `get_playlist_tracks(playlist_id)`. Each music service has an adapter
that converts its JSON into our model at the boundary, so provider details never reach
the game code.

- Swapping or adding providers (Deezer, iTunes, own clips) doesn't affect the engine.
- **Store track IDs, never preview URLs.** Deezer preview URLs are signed and expire;
  request fresh URLs when a game starts.

### 5. The UI is a function of state
The client keeps a single `state` object received from the server and one
`render(state)` function that draws the screen from it. Avoid scattered show/hide
logic. This keeps vanilla JS manageable and makes a later move to a framework simple.

### 6. Keep it simple until we need more
No database, accounts, Redis or Docker until there's a concrete need. An in-memory
dictionary of rooms is enough for playing with friends.

---

## Decisions made now

These are cheap now and painful to add later.

| Decision | Why |
|---|---|
| **Each player gets a stable ID** (a token stored in the browser), separate from their WebSocket connection | Phones lock, wifi drops and pages refresh. A reconnecting player keeps their identity and score instead of counting as a new player. |
| **The message protocol is defined as typed models** (Pydantic), e.g. `{"type": "guess", "choice": 2}` | One place documents everything the client and server can say to each other, and invalid messages are rejected automatically. |
| **The server sends the full room state** rather than incremental updates | Simpler, and reconnection is free: just send the current state again. |
| **Rooms are self-contained objects**, looked up by room code | Keeps rooms independent, which is what allows scaling out to multiple servers later. |
| **Game settings live in a config object** (rounds, clip length, number of choices, scoring curve) | Host settings and gameplay tuning become easy to add. |
| **Folder layout:** `server/`, `client/`, `experiments/` | Separates real code from throwaway tests. |

---

## Gameplay mechanics

- **Answer format:** multiple choice (e.g. 4 options). Fairer to score than typed
  answers, which would need fuzzy matching.
- **Scoring (starting point):** `correct ? max(100, 1000 × (1 − elapsed / roundLength)) : 0`,
  so a late correct answer still earns something. To be tuned during playtesting.
- **Timing fairness:** clients download the clip during the countdown and confirm they're
  ready; playback starts together, and timing is measured from the round start.

---

## Music sources

Findings from testing the APIs (October 2026):

| | iTunes Search API | Deezer API |
|---|---|---|
| Preview clips | ~30 s `.m4a` (`previewUrl`) | ~30 s `.mp3` (`preview`) |
| Callable directly from the browser (CORS) | Yes (`access-control-allow-origin: *`) | **No.** Must be called from our server. |
| Preview URLs expire | No (as observed) | **Yes** (signed `hdnea=exp=…` token) |
| Playlists / charts | Search only | Yes. Useful for themed games. |
| Auth required | No | No (for search and lookups) |

Spotify is not an option: it stopped providing preview URLs to new apps in late 2024.

The plan is to use **Deezer, accessed through our server**, for its playlists, with iTunes
as an alternative behind the same provider interface.

---

## Roadmap

1. **Song-source test** (`experiments/`): fetch tracks and play a clip in the browser.
   First iTunes directly, then Deezer through a small Python server.
2. **Single-player version, all in the browser:** clip plays, pick from 4 choices, get a
   score, next round. Confirms the game is fun before adding networking.
3. **Multiplayer:** a FastAPI server with rooms, a lobby, the state machine and scores sent
   to every player.
4. **Polish:** reconnection handling, host settings, animations, sound effects, deployment.

---

## Decide later

- **Choosing the wrong answers:** should be plausible (same playlist, era or genre). This
  is an engine detail and easy to change.
- **Before a public release:**
  - Review the Deezer and Apple API terms for public apps
  - Add rate limiting
  - Cap room and player counts
  - Filter display names
  - Choose a deployment target (e.g. a small VPS or Fly.io)
- **Accounts and persistent stats:** only if wanted. Stable player IDs make them easier to add.
