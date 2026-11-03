// ---------------------------------------------------------------------------
// State. Everything the screen depends on lives in this one object.
//
// The server runs the game (server/rooms.py) and sends the whole room after
// every change. This page shows it, plays the music and sends clicks back. So
// the game's state machine lives on the server, and the screen follows the
// room's phase:
//
//   (no room) HOME → enter your name; create a room, or join one with its code
//   LOBBY     → the code to share, who's in it; the host can Start
//   LOADING   → the server is fetching songs
//   COUNTDOWN → first round only: its clip loads; the choices are shown but
//               not clickable
//   PLAYING   → the clip plays; pick a title before time runs out
//   REVEAL    → the answer, right or wrong, your points; the song plays on
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
  joining: false, // Asked the server to create/join a room, no answer yet
  // The server's latest "room" message (see server/protocol.py, RoomState):
  // { code, you, host, players, phase, game }. null when not in a room.
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
const drafts = { name: localStorage.getItem("name") ?? "", code: "" };

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
  setState({ room: null, error: null });
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

function connect() {
  // ws:// normally, wss:// (encrypted) when the page itself came over https://,
  // as it will once deployed. location.host is the server that sent this page.
  const scheme = location.protocol === "https:" ? "wss://" : "ws://";
  socket = new WebSocket(scheme + location.host + "/ws");

  socket.onopen = () => setState({ connected: true });
  // The server sends JSON text; JSON.parse turns it into an object.
  socket.onmessage = (event) => receive(JSON.parse(event.data));
  // The server stopped or restarted, or the network dropped. The server has
  // already counted us as gone from the room, so back to the start. (Coming
  // back into the room automatically is roadmap step 4.)
  socket.onclose = connectionLost;
}

// Send a message to the server. send() only takes text, so turn the object
// into JSON text first. Every message we can send is listed in protocol.py.
function send(message) {
  socket.send(JSON.stringify(message));
}

// Everything the server says arrives here and becomes a state change.
function receive(message) {
  if (message.type === "room") {
    receiveRoom(message);
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

  const changes = { room: room, joining: false };

  // The audio follows the game, but only when it moves on to a new step: not
  // on an update within one (like our own pick being confirmed), or the clip
  // would restart.
  if (isNewStep(state.room, room)) {
    if (room.phase === "COUNTDOWN") {
      loadClip(room.game); // First round: download it now, so it's ready to play
      changes.clipError = null;
    } else if (room.phase === "PLAYING") {
      // The first round's clip was loaded during COUNTDOWN. Later rounds come
      // straight from REVEAL, so load it now: the preloader already
      // downloaded it, so it comes from the browser's cache, not the network.
      if (state.room?.phase !== "COUNTDOWN") {
        loadClip(room.game);
      }
      playClip();
      changes.playingSince = performance.now();
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

function connectionLost() {
  stopClip();
  setState({
    connected: false,
    joining: false,
    room: null,
    error: "Lost the connection to the server. Refresh the page to reconnect.",
  });
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
    token = Array.from(bytes, (byte) => byte.toString(16).padStart(2, "0")).join("");
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
function loadClip(game) {
  stopClip();

  // Runs once the browser has read the clip's header and knows its real length.
  player.onloadedmetadata = () => {
    // The server picked the start assuming a ~30 s preview. If this one is
    // shorter, start earlier so the whole clip still fits.
    const latestStart = Math.max(0, player.duration - game.clip_seconds);
    player.currentTime = Math.min(game.clip_start, latestStart);
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

// A player's name from their public id. find() returns the first item for
// which the function is true (or undefined if none is).
function playerName(room, id) {
  const player = room.players.find((p) => p.id === id);
  return player ? player.name : "?";
}

// --- Centred screens ---

function homeScreen(state) {
  // Disabled until connected, and while waiting for the server's answer, so
  // you can't click twice and create two rooms.
  const busy = !state.connected || state.joining;

  // The arrow functions save what's typed into `drafts` (see the top of the file).
  const name = textInput(drafts.name, "Your name", 20, (text) => {
    drafts.name = text;
  }, createRoom);
  const create = button("Create room", createRoom, "primary");
  const code = textInput(drafts.code, "Code", 4, (text) => {
    drafts.code = text;
  }, joinRoom);
  code.classList.add("code-input");
  const join = button("Join", joinRoom);
  for (const element of [name, create, code, join]) {
    element.disabled = busy;
  }

  const joinRow = el("div", "", "join-row");
  joinRow.append(code, join);
  const form = el("div", "", "home-form");
  form.append(name, create, el("p", "or join a friend's room", "divider"), joinRow);

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

  // One row per player: the name, then "host" and/or "you" in grey.
  const list = el("ul", "", "lobby-players");
  for (const player of players) {
    const tags = [];
    if (player.id === host) {
      tags.push("host");
    }
    if (player.id === you) {
      tags.push("you");
    }
    const row = el("li");
    row.append(el("span", player.name), el("span", tags.join(" · "), "tags"));
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

  const elements = [
    el("p", "Room code", "label"),
    codeRow,
    el("p", "Share this code with your friends", "message"),
    el("p", "Players · " + players.length, "label"),
    list,
    start,
    button("Leave room", leaveRoom),
  ];
  if (state.error) {
    elements.push(el("p", state.error, "error"));
  }
  return elements;
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
    row.append(el("span", playerName(state.room, score.id)), el("span", String(score.points)));
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
    row.append(el("span", result.answer.title + " — " + result.answer.artist), el("span", text));
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
  return roundLayout(state, [el("p", "Get ready…", "message")], timeBar(null, 0), buttons);
}

function playingScreen(state) {
  const game = state.room.game;
  const picked = game.picked !== null;

  // One button per choice, showing the title only (the artist would give too
  // much away; it's revealed afterwards). After you pick, they're all disabled
  // and yours is highlighted. Whether you were right waits for REVEAL.
  const buttons = game.choices.map((choice) => {
    const choiceButton = button(choice.title, () => pickChoice(choice.id), "choice");
    choiceButton.disabled = picked;
    if (choice.id === game.picked) {
      choiceButton.classList.add("picked");
    }
    return choiceButton;
  });

  const stage = [
    el("p", picked ? "Locked in! Waiting for the others…" : "Which song is this?", "message"),
  ];
  if (state.clipError) {
    stage.push(el("p", state.clipError, "error"));
  }
  return roundLayout(state, stage, timeBar(state.playingSince, game.clip_seconds), buttons);
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
  // green for the answer, red for a wrong pick, the rest greyed out.
  const buttons = choices.map((choice) => {
    const choiceButton = el("button", choice.title, "choice");
    choiceButton.disabled = true;
    if (choice.id === answer.id) {
      choiceButton.classList.add("correct"); // classList adds a class to the ones it has
    } else if (choice.id === picked) {
      choiceButton.classList.add("wrong");
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
  stage.append(el("p", "Round " + game.round + " / " + game.rounds, "round-counter"), ...stageItems);

  const choices = el("div", "", "choices");
  choices.append(...buttons);

  const bottom = el("div", "", "bottom");
  bottom.append(bar, choices);

  return [stage, scoreSidebar(state), bottom];
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
    row.append(el("span", playerName(state.room, score.id)), el("span", String(score.points)));
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
  app.className = layout;
  app.replaceChildren(...elements);
}

// When the page loads: draw the first screen, then connect to the server.
render(state);
connect();
