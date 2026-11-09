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
| **The token is secret; other players see a separate public `id`** | If the token were broadcast, anyone could copy it and act as that player. |
| **The token lives in `sessionStorage`** (per tab), the name in `localStorage` | A token survives a refresh, but each tab is its own player, so testing with several tabs works. Made with `crypto.getRandomValues`, because `crypto.randomUUID` only works on https:// or localhost, not on `http://192.168.x.x` from a phone. |
| **The message protocol is defined as typed models** (Pydantic), e.g. `{"type": "guess", "choice": 2}` | One place documents everything the client and server can say to each other, and invalid messages are rejected automatically. |
| **The server sends the full room state** rather than incremental updates | Simpler, and reconnection is free: just send the current state again. |
| **Rooms are self-contained objects**, looked up by room code | Keeps rooms independent, which is what allows scaling out to multiple servers later. |
| **Game settings live in a config object** (rounds, clip length, number of choices, scoring curve) | Host settings and gameplay tuning become easy to add. |
| **Folder layout:** `server/`, `client/`, `experiments/` | Separates real code from throwaway tests. |

---

## Rooms

Built in roadmap step 3, part 2. Server code: `server/protocol.py` (messages), `server/rooms.py`
(rooms), the `/ws` route in `server/main.py`.

- **Room codes** are 4 letters without I and O (easy to mix up with 1 and 0), made
  with `secrets` so they can't be predicted.
- **The host is whoever joined first** (`players[0]`). When the host leaves, the next
  player becomes host without any extra code.
- **Names** are 1–20 characters and unique within a room (ignoring case).
- **An empty room is deleted.**
- **Disconnecting counts as leaving, for now.** Rejoining with the token is roadmap step 4.
- **No locks needed:** asyncio only switches between connections at an `await`, and the
  functions that change rooms contain none.
- The browser only moves from HOME to LOBBY when it asked to join (`state.joining`), so
  a room update that was already on its way can't pull a player back in after Leave.

### Protocol so far

| Direction | Message |
|---|---|
| Browser → server | `create` (token, name), `join` (token, name, code), `leave`, `vote` (playlist_id, in LOBBY), `choose` (playlist_id, host, in LOBBY), `start` (host, in LOBBY), `guess` (track_id), `lobby` (host, in RESULTS) |
| Server → browser | `room` (code, you, host, players, playlists, playlist, phase, game), `error` (message) |

Invalid messages get an `error` reply; the connection stays open.

- **`phase`** is the room's phase: `LOBBY`, `LOADING` (fetching songs), then the game's
  `COUNTDOWN`, `PLAYING`, `REVEAL`, `RESULTS`. The host's `lobby` message returns from
  RESULTS to LOBBY. Players can only join in LOBBY.
- **`game`** is built separately for each player and only holds what that player may
  know: no answer, no round points and nobody else's picks until REVEAL. Scores only
  count revealed rounds, or a total going up would give away a right answer.
- **A guess is only answered to the guesser** (their pick shown). Everyone else learns
  about it at REVEAL.
- **Each room's game runs as one asyncio task** (`run_game` in `rooms.py`): fetch fresh
  tracks, then COUNTDOWN (`countdown_seconds`, first round only, browsers load the clip)
  → PLAYING (until `clip_seconds` pass, or an `asyncio.Event` says everyone answered) →
  REVEAL (`reveal_seconds`) → PLAYING → … → RESULTS. Timing uses `time.monotonic()`.
  The task is cancelled when the room empties.
- **No countdown between rounds:** during REVEAL, `game.next_clip` carries the next
  round's preview link and start (not its title), and a second, hidden `<audio>`
  element downloads it while the revealed song keeps playing. When PLAYING arrives,
  the main player loads the same link from the browser's cache. All clip starts are
  picked when the game is created, so the next one is known in advance.

### Playlists

- **The choice of playlists is `server/playlists.py`**: a list of Deezer playlist ids
  with the names players see. Adding one is adding a line. The first is selected in a
  new room; playlists with equal votes keep the list's order.
- **Everyone votes, the host chooses.** Each player has one vote (`room.votes`, player
  id → playlist id; voting again moves it, leaving removes it). The host's click sets
  `room.playlist_id`, which `run_game` fetches. Only ids from `playlists.py` are
  accepted, so a hand-made message can't make the server fetch any playlist.
- **Votes and the choice are kept after a game**, so the lobby comes back with the
  last playlist selected.
- **The server sorts, the browser filters.** `room.playlists` arrives sorted (most votes
  first, live: rows can move when someone votes). The search box only filters what's
  shown and never reaches the server. The list scrolls inside a fixed-height box.
- **The selected playlist is also shown above the search**, so it's visible without
  scrolling and whatever the search. It's only a display; it stays in the list too.
- **Redraws keep focus and scroll:** `render()` rebuilds the screen on every message,
  so it carries the focus, cursor and scroll position over to the new elements with
  the same `id` (`rememberView`/`restoreView`). Otherwise a friend's vote would knock
  you out of the search box. (Not yet tested on an iPhone, which may close the
  keyboard when the focused field is replaced.)

---

## Gameplay mechanics

- **Answer format:** multiple choice (e.g. 4 options). Fairer to score than typed
  answers, which would need fuzzy matching.
- **Scoring (starting point):** `correct ? max(100, 1000 × (1 − elapsed / clipSeconds)) : 0`,
  so a late correct answer still earns something. To be tuned during playtesting.
- **Round length = clip length:** you answer while the clip plays; when it ends, time's up.
  The server's clock starts when it sends PLAYING, and the answer buttons are disabled
  until that arrives.
- **Timing fairness (simple version, built):** clients download the clip in advance
  (during COUNTDOWN for round 1, during the previous REVEAL after that), so it's ready
  when PLAYING arrives. Network lag (usually 20–100 ms) counts against the
  player, which is fine among friends. If it feels unfair in playtests: have clients
  confirm "clip loaded" before the server starts PLAYING.
- **During COUNTDOWN** (round 1 only) the choices are already visible (but disabled).
  From round 2 on, the choices appear as the clip starts. **During REVEAL** the song
  keeps playing. If reading the choices feels rushed in playtests, a short countdown
  (~1 s) can come back.
- **Round end:** a round ends when every player has answered or the timer runs out,
  whichever comes first. On each guess the server checks whether everyone has answered
  and, if so, moves straight to `REVEAL`.
- **Next round:** `REVEAL` lasts a set time (`reveal_seconds` in `GameConfig`) and the
  server then moves everyone to the next round together; no "Next" button.
- **Autoplay:** browsers only allow audio after the user has interacted with the page.
  Chrome and Firefox remember one earlier click, so timed rounds can start on their own;
  Safari tends to allow only the same `<audio>` element that was first played after a
  click. So the client uses **one shared `<audio>` element** for the whole game, and
  Create/Join "unlock" it by playing a silent WAV during the click. (Not yet tested on
  an iPhone.)
- **Background tabs:** Chrome doesn't load media in a hidden tab, so a player who switches
  tabs hears nothing until they come back. Test with separate windows, not tabs.

---

## Music sources

Findings from testing the APIs (October 2026):

| | iTunes Search API | Deezer API |
|---|---|---|
| Preview clips | ~30 s `.m4a` (`previewUrl`) | ~30 s `.mp3` (`preview`) |
| Callable directly from the browser (CORS) | Yes (`access-control-allow-origin: *`) | **No.** Must be called from our server. |
| Preview URLs expire | No (as observed) | **Yes**, 15 minutes after fetching (signed `hdnea=exp=…` token) |
| Browser may cache the preview file | | Yes: `cache-control: public, max-age=11731004` (~4.5 months) |
| Playlists / charts | Search only | Yes. Useful for themed games. |
| Auth required | No | No (for search and lookups) |

Spotify is not an option: it stopped providing preview URLs to new apps in late 2024.

The plan is to use **Deezer, accessed through our server**, for its playlists, with iTunes
as an alternative behind the same provider interface.

**Confirmed in the song-source test:** a FastAPI endpoint calls Deezer with `httpx`
(async, so waiting on Deezer doesn't block other requests), converts each result into our
`Track` model and returns it as JSON. The same server also serves the page, so page and
API share one origin and CORS never applies. The audio itself streams straight from
Deezer's CDN: `<audio>` may play media from any origin, since CORS only stops scripts
from reading responses. Deezer sometimes answers `200` with `{"error": …}` in the body,
so success is checked by the presence of `data`, not only the status code.

### Snippets and randomization

- Each song has **one fixed preview** per service. Requesting it again returns the same
  audio (verified for both Deezer and iTunes).
- For variety, the **server picks a random start offset** inside the ~30 s preview (e.g.
  0–25 s for a 5 s snippet) and sends it with the round, so every player hears the same
  part.
- **Full songs are out of scope.** The options are Spotify or Apple Music SDKs (every
  player needs a subscription), YouTube embeds (unreliable, and the title spoils the
  answer) or our own files (not allowed for a public release). If wanted for private games
  later, a "local library" provider can be added behind the same provider interface
  without changing the engine.

---

## Roadmap

1. ✅ **Song-source test** (`experiments/song-source-deezer/`): fetch tracks and play a
   clip in the browser. iTunes worked directly from the browser; Deezer works through a
   small FastAPI server.
2. ✅ **Single-player version, all in the browser:** clip plays, pick from 4 choices, get a
   score, next round. Confirms the game is fun before adding networking.
3. **Multiplayer:** a FastAPI server with rooms, a lobby, the state machine and scores sent
   to every player. In parts:
   1. ✅ WebSocket experiment (a tiny chat; deleted afterwards)
   2. ✅ Rooms and a lobby (see [Rooms](#rooms)). The host's Start still runs the
      single-player game in the host's browser only.
   3. ✅ Game engine in pure Python (`server/game.py`), tested with `pytest`
      (`server/tests/`). Time and randomness are passed in; timers stay outside the
      engine. A room with no game is in its lobby.
   4. ✅ Connect the engine to the rooms: the server runs the rounds on its own timers
      (see [Protocol so far](#protocol-so-far)). Tested with Python clients; the
      browser doesn't use it yet.
   5. ✅ Rework the client to show the server's game state; scoreboard for every player.
      `script.js` no longer has game rules: the screen follows `room.phase`, and the
      audio is loaded on COUNTDOWN and played on PLAYING.
   6. Playtest on home wifi, then on Render

   Done early from step 4: the lobby's playlist menu (see [Playlists](#playlists)).
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
