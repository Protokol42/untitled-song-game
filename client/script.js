// ---------------------------------------------------------------------------
// State. Everything the screen depends on lives in this one object.
//
// The server runs the game (server/rooms.py) and sends the whole room after
// every change. This page shows it, plays the music and sends clicks back. So
// the game's state machine lives on the server, and the screen follows the
// room's phase:
//
//   (no room) HOME → enter your name; create a room, or join one with its code
//               (or, after a refresh, rejoin the room this tab was in)
//   LOBBY     → the code to share, who's in it, the playlist menu (everyone
//               votes, the host chooses); the host can Start. Everyone has
//               their own avatar (an animal), handed out by the server.
//   LOADING   → the server is fetching songs
//   COUNTDOWN → first round only: its clip loads; the choices are shown but
//               not clickable
//   PLAYING   → the clip plays; pick a title before time runs out. Avatars
//               light up as players answer (not WHAT they picked)
//   REVEAL    → the answer, right or wrong, your points, and everyone's
//               avatar on the choice they picked; the song plays on
//               while the next round's clip downloads in the background
//   RESULTS   → final scores and your rounds; the host goes back to the lobby
//
//   HOME ⇄ LOBBY → LOADING → COUNTDOWN → PLAYING → REVEAL → … → RESULTS
//            ▲                              ▲         │            │
//            │                              └─ next ──┘            │
//            └─────────────── host: Back to lobby ─────────────────┘
// ---------------------------------------------------------------------------

let state = {
  connected: false, // Is the WebSocket to the server open?
  joining: false, // Asked the server to create/join/rejoin a room, no answer yet
  // Asked to rejoin (after a refresh or a dropped connection), no answer yet.
  // If the answer is an error, the room is gone for us: back to HOME.
  rejoining: false,
  // The connection dropped while in a room and we're trying to get it back.
  // The screen stays as it was, with a banner on top.
  reconnecting: false,
  // The server's latest "room" message (see server/protocol.py, RoomState):
  // { code, you, host, players, playlists, playlist, phase, game }.
  // null when not in a room.
  room: null,
  error: null,
  // performance.now() when this round's PLAYING arrived, for the timer bar.
  playingSince: null,
  clipError: null, // Set if this round's clip couldn't play
  copied: false, // Just copied the room code: the button says "Copied!" for a moment
};

// The timer that turns "Copied!" back into "Copy". Bookkeeping, like socket.
let copiedTimer = null;

// What's typed into the home screen's fields so far. Not in `state` on
// purpose: setState redraws the screen, and a redraw on every keystroke would
// rebuild the field you're typing in. Kept here, the text survives when the
// screen IS redrawn for another reason (homeScreen puts it back).
// The name is remembered between visits in localStorage (it stays until
// cleared). `??` means "if the left side is null, use the right side":
// getItem gives null the first time.
// `search` is what's typed into the lobby's playlist search.
const drafts = {
  name: localStorage.getItem("name") ?? "",
  code: "",
  search: "",
};

// The ONLY way to change state. Because every change goes through here, the
// screen is redrawn every time and can never show something out of date.
//
// { ...state, ...changes } builds a new object: copy every field of the old
// state, then overwrite the ones in `changes`. So setState({ error: null })
// changes error and keeps everything else as it was.
function setState(changes) {
  state = { ...state, ...changes };
  render(state);
}

// Which screen to show: the room's phase, or HOME when not in a room.
function phaseOf(state) {
  return state.room === null ? "HOME" : state.room.phase;
}

// ---------------------------------------------------------------------------
// Actions: things the player does. Most just send a message; the server's
// reply (see receive) is what changes the screen.
// ---------------------------------------------------------------------------

// The name from the home screen, or null (after showing why) if it's empty.
// The server checks names too; this is just a quicker, friendlier message.
function checkedName() {
  const name = drafts.name.trim(); // trim() removes spaces at both ends
  if (name === "") {
    setState({ error: "Enter your name first" });
    return null;
  }
  localStorage.setItem("name", name);
  return name;
}

function createRoom() {
  const name = checkedName();
  if (name === null) {
    return;
  }
  unlockAudio(); // This is a click: the moment browsers allow it (see Audio)
  setState({ joining: true, error: null });
  send({ type: "create", token: playerToken(), name: name });
}

function joinRoom() {
  const name = checkedName();
  if (name === null) {
    return;
  }
  const code = drafts.code.trim().toUpperCase();
  if (code.length !== 4) {
    setState({ error: "Room codes are 4 letters" });
    return;
  }
  unlockAudio();
  setState({ joining: true, error: null });
  send({ type: "join", token: playerToken(), name: name, code: code });
}

function leaveRoom() {
  send({ type: "leave" });
  stopClip();
  forgetRoom();
  setState({ room: null, reconnecting: false, error: null });
}

// After a refresh: the "Rejoin room ABCD" button on the home screen. It's a
// click, so it also unlocks the audio, which is why we don't rejoin by
// ourselves after a refresh (see Audio).
function rejoinRoom() {
  unlockAudio();
  setState({ joining: true, rejoining: true, error: null });
  send({ type: "rejoin", token: playerToken(), code: savedRoom() });
}

// --- The room we're in, remembered in sessionStorage ---
// Like the token: it survives a refresh of this tab, and other tabs don't see
// it. That's all a refreshed page needs to offer "Rejoin room ABCD".

function rememberRoom(code) {
  sessionStorage.setItem("room", code);
}

function forgetRoom() {
  sessionStorage.removeItem("room");
}

function savedRoom() {
  return sessionStorage.getItem("room"); // null if there's none
}

// Copy the room code to the clipboard, so it can be pasted into a chat.
async function copyRoomCode() {
  const code = state.room.code;
  try {
    // navigator.clipboard is the modern way, but browsers only offer it on
    // secure pages (https:// or localhost). On http://192.168.x.x (a phone
    // on your wifi) it's undefined, so fall back to the old way.
    if (navigator.clipboard) {
      await navigator.clipboard.writeText(code); // A promise: wait for it
    } else {
      copyTheOldWay(code);
    }
  } catch (error) {
    console.error("Copy failed:", error);
    setState({ error: "Couldn't copy. The code is " + code });
    return;
  }

  setState({ copied: true });
  // Back to "Copy" after 1.5 s. Clear any earlier timer first, so clicking
  // twice quickly doesn't switch it back early.
  clearTimeout(copiedTimer);
  copiedTimer = setTimeout(() => setState({ copied: false }), 1500);
}

// The pre-clipboard-API way: put the text in an invisible text box, select
// it, and ask the browser to copy the selection. execCommand is officially
// outdated, but every browser still supports it, and it works on http://.
function copyTheOldWay(text) {
  const box = document.createElement("textarea");
  box.value = text;
  box.style.position = "fixed"; // Don't scroll the page to where it's added
  box.style.opacity = "0";
  document.body.append(box);
  box.select();
  const copied = document.execCommand("copy"); // true if it worked
  box.remove();
  if (!copied) {
    throw new Error("execCommand('copy') failed");
  }
}

// The playlist menu. The host's click chooses; anyone else's votes. Like
// most actions, these only send: the server's reply redraws the menu.
function votePlaylist(playlistId) {
  setState({ error: null });
  send({ type: "vote", playlist_id: playlistId });
}

// Host only (the server checks too).
function choosePlaylist(playlistId) {
  setState({ error: null });
  send({ type: "choose", playlist_id: playlistId });
}

// The avatar menu. Taken avatars can't be clicked, but someone could grab
// the same free one a moment before us: then the server says so (an error).
function pickAvatar(avatarId) {
  setState({ error: null });
  send({ type: "avatar", avatar: avatarId });
}

// Typing in the playlist search. Not a state change (see drafts), but the
// list has to be filtered again, so redraw. render() keeps the cursor in the
// search box (see rememberView).
function searchPlaylists(text) {
  drafts.search = text;
  render(state);
}

// Host only (the server checks too). Sends only the setting that changed,
// e.g. changeSettings({ rounds: 25 }); the server keeps the others. (Sending
// all of them from state.room.settings would go wrong if the host changes two
// quickly: the second would still carry the old value of the first.)
function changeSettings(changes) {
  setState({ error: null });
  send({ type: "settings", ...changes });
}

// Host only (the server checks too).
function startGame() {
  setState({ error: null });
  send({ type: "start" });
}

// Host only, on the results screen.
function backToLobby() {
  send({ type: "lobby" });
}

function pickChoice(trackId) {
  // Guard: one pick per round, only while the clip plays. The buttons are
  // disabled otherwise too, and the server ignores extra guesses anyway;
  // this just saves sending a message that can't count.
  const game = state.room.game;
  if (phaseOf(state) !== "PLAYING" || game.picked !== null) {
    return;
  }
  // No setState here: the server answers with our pick filled in, and that
  // redraws the screen with the button highlighted.
  send({ type: "guess", track_id: trackId });
}

// ---------------------------------------------------------------------------
// Connection: the WebSocket to our server. Like audio, it's a side effect: it
// talks to the outside world, and turns what it hears into state changes.
// ---------------------------------------------------------------------------

// The open connection. Bookkeeping, so not in `state`; what the screen needs
// to know about it is in state.connected.
let socket = null;

// How long to wait before the next attempt to reconnect, in ms. It doubles
// after every failed attempt (1 s, 2 s, 4 s, then 5 s from then on), so a
// server that's down isn't flooded with attempts, and goes back to 1 s once
// connected. This pattern is called "exponential backoff".
const FIRST_RETRY_MS = 1000;
const MAX_RETRY_MS = 5000;
let retryMs = FIRST_RETRY_MS;

// Must match CLOSE_REPLACED in server/protocol.py: the server closed this
// connection because the same player rejoined from another tab (e.g. a
// duplicated one). Reconnecting would take the player back from that tab,
// which would then take it back again, forever.
const CLOSE_REPLACED = 4000;

function connect() {
  // ws:// normally, wss:// (encrypted) when the page itself came over https://,
  // as it will once deployed. location.host is the server that sent this page.
  const scheme = location.protocol === "https:" ? "wss://" : "ws://";
  socket = new WebSocket(scheme + location.host + "/ws");

  socket.onopen = connected;
  // The server sends JSON text; JSON.parse turns it into an object.
  socket.onmessage = (event) => receive(JSON.parse(event.data));
  // The server stopped or restarted, the network dropped, or the phone went
  // to sleep. event.code says why, if the server closed it on purpose.
  socket.onclose = (event) => connectionLost(event.code);
}

function connected() {
  retryMs = FIRST_RETRY_MS;
  // We were in a room when the connection dropped: ask for our place back.
  // The page never reloaded, so the audio is still unlocked and the game can
  // carry on by itself (unlike after a refresh, see rejoinRoom).
  if (state.room !== null) {
    send({ type: "rejoin", token: playerToken(), code: state.room.code });
    setState({ connected: true, joining: true, rejoining: true });
  } else {
    setState({ connected: true });
  }
}

function connectionLost(closeCode) {
  if (closeCode === CLOSE_REPLACED) {
    // This room is now open in another tab. Back to the start, and don't
    // offer to rejoin (that would take it back from the other tab).
    stopClip();
    forgetRoom();
    setState({
      connected: false,
      joining: false,
      rejoining: false,
      reconnecting: false,
      room: null,
      error: "You opened this room in another tab, so this one left it.",
    });
  } else {
    // Keep the room on screen (and the music playing, if it still can):
    // the server keeps our place for a few minutes. connected() rejoins.
    setState({
      connected: false,
      joining: false,
      rejoining: false,
      reconnecting: state.room !== null,
    });
  }

  // Try again later; each failed attempt ends up back here, waiting longer.
  setTimeout(connect, retryMs);
  retryMs = Math.min(retryMs * 2, MAX_RETRY_MS);
}

// Send a message to the server. send() only takes text, so turn the object
// into JSON text first. Every message we can send is listed in protocol.py.
//
// Only while the connection is open: while reconnecting, a click (Leave, a
// vote) has nowhere to go, and socket.send would throw an error while the
// socket is still connecting. The server sends the latest state on rejoin
// anyway.
function send(message) {
  if (socket.readyState === WebSocket.OPEN) {
    socket.send(JSON.stringify(message));
  }
}

// Everything the server says arrives here and becomes a state change.
function receive(message) {
  if (message.type === "room") {
    receiveRoom(message);
  } else if (message.type === "error" && state.rejoining) {
    // Rejoin refused: the room closed (e.g. the server restarted) or we were
    // away too long and the server removed us. Nothing to go back to.
    stopClip();
    forgetRoom();
    setState({
      joining: false,
      rejoining: false,
      reconnecting: false,
      room: null,
      error: message.message,
    });
  } else if (message.type === "error") {
    setState({ joining: false, error: message.message });
  }
}

function receiveRoom(room) {
  // Not in a room and didn't ask to be: an update that was already on its way
  // when we pressed Leave. Ignore it, or it would pull us straight back in.
  if (state.room === null && !state.joining) {
    return;
  }

  // In a room (again): remember its code, so a refresh can offer to rejoin.
  rememberRoom(room.code);
  const changes = {
    room: room,
    joining: false,
    rejoining: false,
    reconnecting: false,
  };

  // The audio follows the game, but only when it moves on to a new step: not
  // on an update within one (like our own pick being confirmed), or the clip
  // would restart.
  if (isNewStep(state.room, room)) {
    if (room.phase === "COUNTDOWN") {
      loadClip(room.game); // First round: download it now, so it's ready to play
      changes.clipError = null;
    } else if (room.phase === "PLAYING") {
      // How far into the round we are: about 0 normally, more if we've just
      // rejoined mid-round. Both the clip and the timer bar start that far in.
      const elapsed = room.game.elapsed;
      // The first round's clip was loaded during COUNTDOWN. Later rounds come
      // straight from REVEAL, so load it now: the preloader already
      // downloaded it, so it comes from the browser's cache, not the network.
      // Also load it if we're more than a moment late, so it starts further in.
      if (state.room?.phase !== "COUNTDOWN" || elapsed > 1) {
        loadClip(room.game, elapsed);
      }
      playClip();
      // performance.now() counts milliseconds; elapsed is in seconds.
      changes.playingSince = performance.now() - elapsed * 1000;
      changes.clipError = null;
    } else if (room.phase === "REVEAL") {
      // The song plays on. Meanwhile, fetch the next round's clip (if there
      // is a next round).
      if (room.game.next_clip !== null) {
        preload(room.game.next_clip.preview_url);
      }
    } else {
      stopClip(); // LOBBY, LOADING, RESULTS
    }
  }

  setState(changes);
}

// Did the game move on (a new phase, or the next round's COUNTDOWN), rather
// than change within the same step?
function isNewStep(before, after) {
  if (before === null || before.phase !== after.phase) {
    return true;
  }
  // ?. ("optional chaining") gives undefined instead of an error when game
  // is null, e.g. in the lobby.
  return before.game?.round !== after.game?.round;
}

// This browser tab's secret token (see server/protocol.py), made once and then
// kept in sessionStorage. sessionStorage belongs to one tab: it survives a
// refresh but a new tab starts empty, so two tabs are two different players.
// (localStorage, used for the name, is shared by every tab of this site.)
function playerToken() {
  let token = sessionStorage.getItem("token");
  if (token === null) {
    // 16 random bytes written as 32 hex characters. crypto.randomUUID() would
    // be shorter, but browsers only allow it on https:// or localhost, and a
    // phone testing on your wifi opens http://192.168.x.x.
    // toString(16) writes a number in hex; padStart makes 5 into "05".
    const bytes = crypto.getRandomValues(new Uint8Array(16));
    token = Array.from(bytes, (byte) =>
      byte.toString(16).padStart(2, "0"),
    ).join("");
    sessionStorage.setItem("token", token);
  }
  return token;
}

// ---------------------------------------------------------------------------
// Audio: a side effect, so it's started and stopped by receiveRoom, never by
// render(). The <audio> element lives outside #app in index.html, so redrawing
// the screen never destroys it.
//
// Browsers only let a page play sound after the player has clicked something.
// Here the music starts when a SERVER message arrives, not on a click. Chrome
// and Firefox are fine with that once there's been any click on the page;
// Safari wants this same <audio> element to have been played during a click.
// So Create and Join "unlock" it by playing a moment of silence (unlockAudio).
// ---------------------------------------------------------------------------

const player = document.getElementById("player");
player.preload = "auto"; // Download the whole clip when given a src, not just its start

// A WAV file with no sound in it, written out in full as a "data:" URL (the
// file's bytes, base64-encoded), so there's nothing to download.
const SILENCE =
  "data:audio/wav;base64,UklGRiQAAABXQVZFZm10IBAAAAABAAEAQB8AAEAfAAABAAgAZGF0YQAAAAA=";

function unlockAudio() {
  player.src = SILENCE;
  // Nothing to hear, so it doesn't matter if this fails.
  player.play().catch(() => {});
}

// Point the player at this round's clip and jump to the start the server
// picked (the same for everyone). Doesn't play by itself; playClip() does.
// Called on COUNTDOWN (first round, so it downloads in advance) or right
// before playClip (later rounds, already in the cache thanks to preload).
// `late`: seconds the round has already been playing, when we rejoin mid-round.
function loadClip(game, late = 0) {
  stopClip();

  // Runs once the browser has read the clip's header and knows its real length.
  player.onloadedmetadata = () => {
    // The server picked the start assuming a ~30 s preview. If this one is
    // shorter, start earlier so the whole clip still fits.
    const latestStart = Math.max(0, player.duration - game.clip_seconds);
    player.currentTime = Math.min(game.clip_start, latestStart) + late;
  };
  // Fires if the file can't be loaded, e.g. an expired preview link.
  player.onerror = () => clipFailed(player.error);

  player.src = game.preview_url; // Starts downloading
}

// PLAYING: go. If the clip hasn't finished loading, play() waits for it.
// play() returns a promise that fails if the browser blocks playback.
function playClip() {
  player.play().catch(clipFailed);
}

// Stop the audio and forget its handlers, so nothing set up for the last clip
// can still fire later (e.g. an old onerror reporting a failure this round).
function stopClip() {
  player.onloadedmetadata = null;
  player.onerror = null;
  player.pause();
}

// Download a clip ahead of time without playing it, using the second <audio>
// element (index.html), because `player` is still playing the current song.
//
// How the downloaded file gets from one element to the other: the browser
// keeps downloaded files in its HTTP cache, and Deezer's preview files say
// they may be kept ("cache-control: public, max-age=11731004", about 4.5
// months). So when `player` later asks for the same link, the browser answers
// from the cache instead of downloading it again.
const preloader = document.getElementById("preloader");

function preload(previewUrl) {
  preloader.src = previewUrl;
}

// The clip didn't play. The round goes on without it (the server keeps time),
// so just say so; you can still guess.
function clipFailed(error) {
  console.error("Clip failed:", error);
  const phase = phaseOf(state);
  if (phase === "COUNTDOWN" || phase === "PLAYING") {
    setState({ clipError: "Couldn't play the clip. You can still guess!" });
  }
}

// ---------------------------------------------------------------------------
// Rendering: draws the screen from state. It only READS state, never changes
// it; changes happen in the actions above.
// ---------------------------------------------------------------------------

const app = document.getElementById("app");

// Create an element with plain text inside, e.g. el("p", "Hello") → <p>Hello</p>.
// textContent always treats the text as text, never as HTML, so a song title
// or player name containing "<" or "&" can't break the page or inject code (XSS).
// className is optional: el("p", "Hi", "message") → <p class="message">Hi</p>.
// The look of each class lives in style.css; JavaScript only decides WHAT is
// on screen, CSS decides how it looks.
function el(tag, text = "", className = "") {
  const element = document.createElement(tag);
  element.textContent = text;
  element.className = className;
  return element;
}

// A button with a click handler, e.g. button("Join", joinRoom).
function button(text, onClick, className = "") {
  const element = el("button", text, className);
  element.addEventListener("click", onClick);
  return element;
}

// A one-line text field. onInput(text) runs on every change; onEnter() when
// Enter is pressed in it.
function textInput(value, placeholder, maxLength, onInput, onEnter) {
  const input = document.createElement("input");
  input.value = value;
  input.placeholder = placeholder; // Grey hint shown while it's empty
  input.maxLength = maxLength; // The browser stops typing after this many
  input.autocomplete = "off"; // No suggestions from earlier visits
  input.addEventListener("input", () => onInput(input.value));
  input.addEventListener("keydown", (event) => {
    if (event.key === "Enter") {
      onEnter();
    }
  });
  return input;
}

// A player from their public id. find() returns the first item for which the
// function is true, or undefined if none is (e.g. a player who has left but
// still has a guess in this round).
function playerById(room, id) {
  return room.players.find((p) => p.id === id);
}

function playerName(room, id) {
  const player = playerById(room, id);
  return player ? player.name : "?";
}

// --- Avatars ---

// What each avatar id from the server (server/avatars.py) looks like. For now
// an emoji; its circle's colour is in style.css (.avatar-fox etc.). Swapping
// these for pictures later only changes this file and style.css.
const AVATAR_EMOJI = {
  fox: "🦊",
  frog: "🐸",
  octopus: "🐙",
  panda: "🐼",
  lion: "🦁",
  penguin: "🐧",
  unicorn: "🦄",
  pig: "🐷",
};

// One avatar per player, so this is also the room size. The server decides
// (MAX_PLAYERS in rooms.py); this is only for the "3 / 8" in the lobby.
const MAX_PLAYERS = Object.keys(AVATAR_EMOJI).length;

// An avatar: a small coloured circle with its emoji. `label` is what pops up
// when you hover over it (title) and what screen readers say (aria-label,
// instead of "fox face"). `size` is an extra class for style.css ("small" on
// the choice buttons).
function avatarCircle(id, label, size = "") {
  // ?? "?" in case the server sends an id this file doesn't know yet.
  const circle = el("span", AVATAR_EMOJI[id] ?? "?", "avatar avatar-" + id);
  if (size !== "") {
    circle.classList.add(size);
  }
  circle.title = label;
  circle.setAttribute("aria-label", label);
  return circle;
}

// A player's avatar, labelled with their name.
function avatar(player, size = "") {
  return avatarCircle(player.avatar, player.name, size);
}

// An avatar and a name side by side, for lists of players. Faded while the
// player's connection is down (see .away in style.css).
function playerLabel(room, id) {
  const label = el("span", "", "player-label");
  const player = playerById(room, id);
  if (player) {
    label.append(avatar(player));
    if (!player.connected) {
      label.classList.add("away");
      label.title = player.name + " is reconnecting…";
    }
  }
  label.append(el("span", playerName(room, id)));
  return label;
}

// --- Centred screens ---

function homeScreen(state) {
  // Disabled until connected, and while waiting for the server's answer, so
  // you can't click twice and create two rooms.
  const busy = !state.connected || state.joining;

  // The arrow functions save what's typed into `drafts` (see the top of the file).
  const name = textInput(
    drafts.name,
    "Your name",
    20,
    (text) => {
      drafts.name = text;
    },
    createRoom,
  );
  const create = button("Create room", createRoom, "primary");
  const code = textInput(
    drafts.code,
    "Code",
    4,
    (text) => {
      drafts.code = text;
    },
    joinRoom,
  );
  code.classList.add("code-input");
  const join = button("Join", joinRoom);
  for (const element of [name, create, code, join]) {
    element.disabled = busy;
  }

  const joinRow = el("div", "", "join-row");
  joinRow.append(code, join);
  const form = el("div", "", "home-form");

  // This tab was in a room before a refresh: offer to go back. It's the main
  // button then, and Create becomes an ordinary one.
  const roomCode = savedRoom();
  if (roomCode !== null) {
    const rejoin = button("Rejoin room " + roomCode, rejoinRoom, "primary");
    rejoin.disabled = busy;
    create.classList.remove("primary");
    form.append(rejoin, el("p", "or start over", "divider"));
  }

  form.append(
    name,
    create,
    el("p", "or join a friend's room", "divider"),
    joinRow,
  );

  const elements = [el("h1", "Guess the Song"), form];
  if (state.error) {
    elements.push(el("p", state.error, "error"));
  } else if (!state.connected) {
    elements.push(el("p", "Connecting to the server…", "message"));
  }
  return elements;
}

function lobbyScreen(state) {
  const { code, you, host, players } = state.room;

  // One row per player: their avatar and name, then "host" and/or "you" in grey.
  const list = el("ul", "", "lobby-players");
  for (const player of players) {
    const tags = [];
    if (player.id === host) {
      tags.push("host");
    }
    if (player.id === you) {
      tags.push("you");
    }
    if (!player.connected) {
      tags.push("reconnecting…");
    }
    const row = el("li");
    if (!player.connected) {
      row.classList.add("away");
    }
    row.append(
      playerLabel(state.room, player.id),
      el("span", tags.join(" · "), "tags"),
    );
    list.append(row);
  }

  // Only the host starts the game; everyone else waits.
  const start =
    you === host
      ? button("Start game", startGame, "primary")
      : el("p", "Waiting for the host to start…", "message");

  // The code with a small Copy button beside it.
  const codeRow = el("div", "", "room-code-row");
  codeRow.append(
    el("p", code, "room-code"),
    button(state.copied ? "Copied!" : "Copy", copyRoomCode, "small"),
  );

  // Players and playlists side by side; on a narrow screen the playlists
  // wrap underneath (see .lobby-columns in style.css).
  const playersColumn = el("div", "", "lobby-column");
  playersColumn.append(
    el("p", "Players · " + players.length + " / " + MAX_PLAYERS, "label"),
    list,
    settingsBox(state.room),
  );
  const columns = el("div", "", "lobby-columns");
  columns.append(playersColumn, playlistMenu(state.room));

  const elements = [
    // The game's name, smaller than on the home screen: the room code below
    // is what players need to read, so it stays the biggest thing here.
    el("h1", "Guess the Song", "lobby-title"),
    el("p", "Room code", "label"),
    codeRow,
    el("p", "Share this code with your friends", "message"),
    avatarMenu(state.room),
    columns,
    start,
    button("Leave room", leaveRoom),
  ];
  if (state.error) {
    elements.push(el("p", state.error, "error"));
  }
  return elements;
}

// The next game's settings. The host changes them; everyone else sees the
// same controls, not usable, so they know what's coming. What can be chosen
// comes from the server (room.setting_options, from settings.py).
function settingsBox(room) {
  const isHost = room.you === room.host;
  const { settings, setting_options: options } = room; // Renamed while unpacking

  const box = el("div", "", "settings");
  box.append(
    el(
      "p",
      isHost ? "Game settings" : "Game settings · set by the host",
      "label",
    ),
    settingSlider(
      "Rounds",
      "",
      settings.rounds,
      options.rounds,
      isHost,
      (value) => changeSettings({ rounds: value }),
    ),
    settingSlider(
      "Answer time",
      " s",
      settings.clip_seconds,
      options.clip_seconds,
      isHost,
      (value) => changeSettings({ clip_seconds: value }),
    ),
    choicesSetting(settings.choices, options.choices, isHost),
  );
  return box;
}

// One slider: its name and current value above, the slider below.
// <input type="range"> is the browser's own slider; min, max and step decide
// where it can stop (5, 10, 15 ...).
function settingSlider(name, unit, value, range, isHost, onPick) {
  const shown = el("span", value + unit, "setting-value");
  const heading = el("div", "", "setting-heading");
  heading.append(el("span", name), shown);

  const slider = document.createElement("input");
  slider.type = "range";
  slider.min = range.min;
  slider.max = range.max;
  slider.step = range.step;
  slider.value = value;
  slider.disabled = !isHost;
  slider.setAttribute("aria-label", name);

  // Two events: "input" fires on every step while dragging, "change" once
  // when you let go. While dragging, only the number next to the name is
  // updated, directly, without a redraw: redrawing would replace the slider
  // you're holding. Letting go sends it to the server, whose answer redraws.
  // slider.value is text ("25"); Number() makes it a number for the server.
  slider.addEventListener("input", () => {
    shown.textContent = slider.value + unit;
  });
  slider.addEventListener("change", () => onPick(Number(slider.value)));

  const row = el("div", "", "setting");
  row.append(heading, slider);
  return row;
}

// Choices: only a few values, so buttons instead of a slider.
function choicesSetting(current, allowed, isHost) {
  const buttons = el("div", "", "setting-buttons");
  for (const count of allowed) {
    const option = isHost
      ? button(String(count), () => changeSettings({ choices: count }))
      : el("button", String(count));
    option.disabled = !isHost;
    if (count === current) {
      option.classList.add("selected");
    }
    buttons.append(option);
  }

  const heading = el("div", "", "setting-heading");
  heading.append(el("span", "Choices"));
  const row = el("div", "", "setting");
  row.append(heading, buttons);
  return row;
}

// Every avatar as a button: yours highlighted, other players' greyed out and
// unclickable, the free ones clickable. The ORDER of the buttons is the
// order in AVATAR_EMOJI, so they don't move around when someone switches.
function avatarMenu(room) {
  const row = el("div", "", "avatar-menu");
  for (const id of Object.keys(AVATAR_EMOJI)) {
    // Who has this one, if anyone (undefined if it's free).
    const owner = room.players.find((p) => p.avatar === id);

    let label = "Pick this one";
    if (owner?.id === room.you) {
      label = "You";
    } else if (owner) {
      label = "Taken by " + owner.name;
    }

    // Only a free avatar gets a click handler. Yours and taken ones are
    // buttons that do nothing. Not `disabled`: some browsers then don't
    // show the hover text, and "Taken by Alex" is the point. aria-disabled
    // tells screen readers it can't be used instead.
    let option;
    if (owner) {
      option = el("button", "", "avatar-option");
      option.classList.add(owner.id === room.you ? "mine" : "taken");
      option.setAttribute("aria-disabled", "true");
    } else {
      option = button("", () => pickAvatar(id), "avatar-option");
    }
    option.append(avatarCircle(id, label));
    row.append(option);
  }

  const menu = el("div", "", "lobby-column avatar-column");
  menu.append(el("p", "Your animal", "label"), row);
  return menu;
}

// The lobby's playlist menu: a heading, the search box and a scrolling list.
// The server sends the playlists already sorted (most votes first); here they
// are only filtered by the search, which never leaves this browser.
function playlistMenu(room) {
  const isHost = room.you === room.host;

  const search = textInput(
    drafts.search,
    "Search playlists",
    40,
    searchPlaylists,
    () => {},
  );
  search.type = "search"; // Adds a ✕ to clear it, and a search key on phone keyboards
  search.id = "playlist-search"; // An id, so render() can keep it focused (rememberView)
  search.setAttribute("aria-label", "Search playlists"); // A name for screen readers

  // includes() is true if the text appears anywhere in the name. Both sides
  // lower case, so "HITS" finds "00s Hits".
  const query = drafts.search.trim().toLowerCase();
  const shown = room.playlists.filter((playlist) =>
    playlist.name.toLowerCase().includes(query),
  );

  const list = el("div", "", "playlist-list");
  list.id = "playlist-list"; // So render() can keep its scroll position
  for (const playlist of shown) {
    list.append(playlistRow(room, playlist, isHost));
  }

  const column = el("div", "", "lobby-column playlist-column");
  column.append(
    el("p", "Playlists · " + room.playlists.length, "label"),
    el(
      "p",
      isHost
        ? "Most votes on top. Tap one to play it."
        : "Most votes on top. Tap one to vote.",
      "hint",
    ),
    selectedBox(room, isHost),
    search,
    list,
  );
  if (query !== "") {
    const count =
      shown.length +
      " of " +
      room.playlists.length +
      ' match "' +
      drafts.search.trim() +
      '"';
    column.append(el("p", count, "hint"));
  }
  return column;
}

// One playlist: its name and who voted on the left; labels and the number of
// votes on the right. The whole row is a button.
function playlistRow(room, playlist, isHost) {
  const selected = playlist.id === room.playlist;
  const yourVote = playlist.voters.includes(room.you);
  const action = isHost ? choosePlaylist : votePlaylist;
  const row = button("", () => action(playlist.id), "playlist-row");
  if (selected) {
    row.classList.add("selected");
  }
  if (yourVote) {
    row.classList.add("voted");
  }

  const left = el("span", "", "playlist-text");
  left.append(el("span", playlist.name, "playlist-name"));
  if (playlist.voters.length > 0) {
    // Who voted for it: their avatars, in the order they voted. Hovering one
    // shows the name (avatar() sets it as the title).
    const voters = el("span", "", "playlist-voters");
    for (const id of playlist.voters) {
      const voter = playerById(room, id);
      if (voter) {
        voters.append(avatar(voter, "small"));
      }
    }
    left.append(voters);
  }

  const right = el("span", "", "playlist-side");
  if (selected) {
    right.append(
      el("span", isHost ? "Selected" : "Host's pick", "playlist-tag selected"),
    );
  }
  if (yourVote) {
    right.append(el("span", "Your vote", "playlist-tag"));
  }
  right.append(el("span", voteCount(playlist)));

  row.append(left, right);
  return row;
}

// The playlist the game will use, in a box above the search, so everyone can
// see it without scrolling, whatever they've searched for. Only a display, not
// a button: it's also in the list below, where you vote for it.
function selectedBox(room, isHost) {
  // find() returns the first playlist whose id matches room.playlist.
  const playlist = room.playlists.find((p) => p.id === room.playlist);

  const left = el("span", "", "playlist-text");
  left.append(
    el("span", isHost ? "Selected" : "Host's pick", "playlist-tag selected"),
    el("span", playlist.name, "playlist-name"),
  );

  const box = el("div", "", "playlist-row playlist-current");
  box.append(left, el("span", voteCount(playlist)));
  return box;
}

// "–", "1 vote", "3 votes"
function voteCount(playlist) {
  const votes = playlist.voters.length;
  if (votes === 0) {
    return "–";
  }
  return votes === 1 ? "1 vote" : votes + " votes";
}

function loadingScreen() {
  return [el("p", "Loading songs…", "message")];
}

function resultsScreen(state) {
  const { you, host, game } = state.room;

  // The headline: the winner, or a tie if the top two have the same points.
  // (scores is sorted highest first by the server.)
  const [first, second] = game.scores; // The first two items (second may be undefined)
  const headline =
    second !== undefined && second.points === first.points
      ? "It's a tie!"
      : playerName(state.room, first.id) + " wins!";

  // Everyone's final score, in order. <ol> is a numbered list.
  const ranking = el("ol", "", "results");
  for (const score of game.scores) {
    const row = el("li");
    if (score.id === you) {
      row.classList.add("you");
    }
    row.append(playerLabel(state.room, score.id), pointsAndStreak(score));
    ranking.append(row);
  }

  // Your own rounds: title and artist on the left, how you did on the right.
  const rounds = el("ol", "", "results");
  for (const result of game.history) {
    let text;
    if (result.picked === null) {
      text = "time's up";
    } else if (result.picked === result.answer.id) {
      text = "+" + result.points;
    } else {
      text = "✗ wrong";
    }
    const row = el("li");
    row.append(
      el("span", result.answer.title + " — " + result.answer.artist),
      el("span", text),
    );
    rounds.append(row);
  }

  const next =
    you === host
      ? button("Back to lobby", backToLobby, "primary")
      : el("p", "Waiting for the host…", "message");

  return [
    el("h1", headline),
    el("p", "Final scores", "label"),
    ranking,
    el("p", "Your rounds", "label"),
    rounds,
    next,
    button("Leave room", leaveRoom),
  ];
}

// --- Round screens (COUNTDOWN, PLAYING and REVEAL share one layout) ---

function countdownScreen(state) {
  const game = state.room.game;
  // The choices already show, so everyone can read them while the clip loads,
  // but they can't be clicked until it plays.
  const buttons = game.choices.map((choice) => {
    const choiceButton = el("button", choice.title, "choice");
    choiceButton.disabled = true;
    return choiceButton;
  });
  // The answered strip shows already (everyone still waiting), so the layout
  // doesn't jump when PLAYING starts.
  const stage = [el("p", "Get ready…", "message"), answeredStrip(state.room)];
  return roundLayout(state, stage, timeBar(null, 0), buttons);
}

// Everyone's avatar in a row: lit up once they've answered, faded until then.
// Only WHO has answered: what they picked waits for REVEAL (pickStack).
function answeredStrip(room) {
  const strip = el("div", "", "answered-strip");
  for (const player of room.players) {
    const circle = avatar(player);
    const done = room.game.answered.includes(player.id);
    circle.classList.add(done ? "answered" : "waiting");
    strip.append(circle);
  }
  return strip;
}

// REVEAL: the avatars of everyone who picked this choice, in the order they
// answered, for the button's top-right corner. null if nobody picked it.
function pickStack(room, choiceId) {
  const { answered, picks } = room.game;
  const stack = el("span", "", "pick-stack");
  for (const id of answered) {
    const player = playerById(room, id);
    // Skip players who have left since (their guess still counts on the server).
    if (picks[id] === choiceId && player) {
      stack.append(avatar(player, "small"));
    }
  }
  return stack.childElementCount > 0 ? stack : null;
}

function playingScreen(state) {
  const game = state.room.game;
  const picked = game.picked !== null;

  // One button per choice, showing the title only (the artist would give too
  // much away; it's revealed afterwards). After you pick, they're all disabled
  // and yours is highlighted. Whether you were right waits for REVEAL.
  const buttons = game.choices.map((choice) => {
    const choiceButton = button(
      choice.title,
      () => pickChoice(choice.id),
      "choice",
    );
    choiceButton.disabled = picked;
    if (choice.id === game.picked) {
      choiceButton.classList.add("picked");
    }
    return choiceButton;
  });

  const stage = [
    el(
      "p",
      picked ? "Locked in! Waiting for the others…" : "Which song is this?",
      "message",
    ),
    answeredStrip(state.room),
  ];
  if (state.clipError) {
    stage.push(el("p", state.clipError, "error"));
  }
  return roundLayout(
    state,
    stage,
    timeBar(state.playingSince, game.clip_seconds),
    buttons,
  );
}

function revealScreen(state) {
  // Destructuring: several variables at once from the game object.
  const { answer, choices, picked, points, round, rounds } = state.room.game;

  // The verdict text, and a class to colour it (see .verdict.* in style.css).
  let verdict = "Wrong!";
  let verdictClass = "wrong";
  if (picked === answer.id) {
    verdict = "Correct!";
    verdictClass = "correct";
  } else if (picked === null) {
    verdict = "Time's up!";
  }

  // The same buttons as while playing, but disabled and coloured in place:
  // green for the answer, red for a wrong pick, the rest greyed out. In the
  // corner of each, the avatars of everyone who picked it (you included).
  const buttons = choices.map((choice) => {
    const choiceButton = el("button", choice.title, "choice");
    choiceButton.disabled = true;
    if (choice.id === answer.id) {
      choiceButton.classList.add("correct"); // classList adds a class to the ones it has
    } else if (choice.id === picked) {
      choiceButton.classList.add("wrong");
    }
    const stack = pickStack(state.room, choice.id);
    if (stack !== null) {
      choiceButton.classList.add("chosen"); // Not greyed out: see style.css
      choiceButton.append(stack);
    }
    return choiceButton;
  });

  // No Next button: the server moves everyone on together after a few seconds.
  const next = round === rounds ? "Results coming up…" : "Next song coming up…";

  return roundLayout(
    state,
    [
      el("p", verdict, "verdict " + verdictClass), // Two classes, space-separated
      el("p", 'It was "' + answer.title + '" by ' + answer.artist),
      el("p", "+" + points + " points", "message"),
      el("p", next, "message"),
    ],
    timeBar(null, 0), // Empty bar: time's over, but keep the layout from jumping
    buttons,
  );
}

// The shared layout: three areas that style.css places with CSS Grid.
//   stage:   round number, then the given stage items, centred
//   sidebar: scores, top right
//   bottom:  timer bar, then the choice buttons in a grid
function roundLayout(state, stageItems, bar, buttons) {
  const game = state.room.game;
  const stage = el("div", "", "stage");
  stage.append(
    el("p", "Round " + game.round + " / " + game.rounds, "round-counter"),
    ...stageItems,
  );

  const choices = el("div", "", "choices");
  choices.append(...buttons);

  const bottom = el("div", "", "bottom");
  bottom.append(bar, choices);

  return [stage, scoreSidebar(state), bottom];
}

// A streak is shown from this many right answers in a row. The server always
// sends the real number (score.streak); this is the one place that decides
// when it's worth a 🔥.
const STREAK_MIN = 4;

// A player's points, then their streak as a small "🔥 7" badge if it's long
// enough: the right-hand side of a row in the score sidebar and the results.
// Like the points, streaks only change at REVEAL.
function pointsAndStreak(score) {
  const side = el("span", "", "points-side");
  side.append(el("span", String(score.points)));
  if (score.streak >= STREAK_MIN) {
    const badge = el("span", "🔥 " + score.streak, "streak");
    badge.title = score.streak + " right in a row"; // Shown on hover
    side.append(badge);
  }
  return side;
}

// Every player's total, highest first (the server sorts them). Totals only
// change at REVEAL: counting a guess earlier would give the answer away.
function scoreSidebar(state) {
  const { you, game } = state.room;
  const sidebar = el("aside", "", "sidebar"); // <aside> = content beside the main part
  const list = el("ul", "", "players");
  for (const score of game.scores) {
    const row = el("li");
    if (score.id === you) {
      row.classList.add("you");
    }
    row.append(playerLabel(state.room, score.id), pointsAndStreak(score));
    list.append(row);
  }
  sidebar.append(el("h2", "Scores"), list);
  return sidebar;
}

// A bar that shrinks from full to empty while the clip plays. With startedAt
// null (countdown, or round over) it's just the empty background.
//
// The browser animates it by itself (element.animate, the "Web Animations"
// API), so we don't redraw the screen every fraction of a second. Redrawing
// would rebuild the answer buttons, and a click that lands mid-rebuild gets lost.
//
// It starts from however much time is LEFT, not always from full, so when the
// screen is redrawn mid-round (e.g. after you pick) the bar carries on where it was.
function timeBar(startedAt, clipSeconds) {
  const background = el("div", "", "timebar");
  if (startedAt === null) {
    return background;
  }

  const totalMs = clipSeconds * 1000;
  const leftMs = Math.max(0, totalMs - (performance.now() - startedAt));

  const fill = el("div", "", "timebar-fill");
  fill.animate(
    [{ width: (leftMs / totalMs) * 100 + "%" }, { width: "0%" }], // From → to
    { duration: leftMs, easing: "linear", fill: "forwards" }, // "forwards": stay at 0% after
  );

  background.append(fill);
  return background;
}

// Pick the screen for the current phase and replace whatever #app showed before.
// The layout class on #app tells style.css how to arrange that screen.
// replaceChildren(...elements) removes the old children and adds the new ones;
// the ... spreads the array into separate arguments.
function render(state) {
  let elements;
  let layout = "layout-center";
  switch (phaseOf(state)) {
    case "HOME":
      elements = homeScreen(state);
      break;
    case "LOBBY":
      elements = lobbyScreen(state);
      break;
    case "LOADING":
      elements = loadingScreen();
      break;
    case "COUNTDOWN":
      elements = countdownScreen(state);
      layout = "layout-round";
      break;
    case "PLAYING":
      elements = playingScreen(state);
      layout = "layout-round";
      break;
    case "REVEAL":
      elements = revealScreen(state);
      layout = "layout-round";
      break;
    case "RESULTS":
      elements = resultsScreen(state);
      break;
  }
  // Lost the connection mid-room: the screen stays as it was, with a banner
  // across the top while connect() keeps trying (see connectionLost).
  if (state.reconnecting) {
    elements.unshift(el("p", "Connection lost. Reconnecting…", "banner")); // unshift: add at the front
  }
  const view = rememberView();
  app.className = layout;
  app.replaceChildren(...elements);
  restoreView(view);
}

// render() throws the old screen away and builds a new one, so the element
// you were typing in, or the list you'd scrolled halfway down, is replaced by
// a fresh copy: no cursor, scrolled to the top. Mostly that doesn't matter,
// but in the lobby a friend's vote can redraw the screen at any moment.
//
// So, for elements that have an id, render() carries three things over from
// the old element to the new one with the same id: which one is focused,
// where the cursor is in it, and how far it's scrolled.
function rememberView() {
  const focused = document.activeElement; // The element that has the focus
  const view = { focusedId: focused.id, cursor: null, scrolls: [] };
  // Only text fields have a cursor; for other elements selectionStart is
  // undefined (or null for some kinds of input).
  if (typeof focused.selectionStart === "number") {
    view.cursor = [focused.selectionStart, focused.selectionEnd];
  }
  // querySelectorAll("[id]"): every element inside #app with an id.
  for (const element of app.querySelectorAll("[id]")) {
    if (element.scrollTop > 0) {
      view.scrolls.push([element.id, element.scrollTop]);
    }
  }
  return view;
}

function restoreView(view) {
  for (const [id, scrollTop] of view.scrolls) {
    const element = document.getElementById(id);
    if (element !== null) {
      element.scrollTop = scrollTop;
    }
  }
  // focusedId is "" when the focused element had no id (or nothing had focus).
  const focused =
    view.focusedId === "" ? null : document.getElementById(view.focusedId);
  if (focused !== null) {
    focused.focus({ preventScroll: true }); // Don't scroll the page to it
    if (view.cursor !== null) {
      focused.setSelectionRange(...view.cursor);
    }
  }
}

// When the page loads: draw the first screen, then connect to the server.
render(state);
connect();
