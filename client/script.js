// ---------------------------------------------------------------------------
// Settings. One place to tune the game (ARCHITECTURE.md: "settings live in a
// config object").
// ---------------------------------------------------------------------------

const CONFIG = {
  playlistId: 9890417302, // Deezer "100% Madeon"
  rounds: 20, // Songs per game (fewer if the playlist is smaller)
  choices: 4, // Answer buttons per round (1 right + 3 wrong)
  clipSeconds: 10, // How much of the song plays = time to answer
};

// ---------------------------------------------------------------------------
// State. Everything the screen depends on lives in this one object.
//
// `phase` is the current state of the game's state machine:
//   START   → the Start button (and the last error, if any)
//   LOADING → waiting for tracks from our server
//   PLAYING → the clip loads, then plays; pick a title before time runs out
//   REVEAL  → right, wrong or time's up, the points; Next starts a new round
//   RESULTS → final score and a summary of every round; Play again
//
//   START → LOADING → PLAYING ⇄ REVEAL → RESULTS
//                        ▲                  │
//                        └── Play again ────┘ (via LOADING)
// ---------------------------------------------------------------------------

let state = {
  phase: "START",
  tracks: [], // Every playable track in the playlist
  error: null,
  // This game's answers, one per round, picked at game start from a shuffled
  // playlist, so no song can be the answer twice.
  answers: [],
  // Every finished round of this game, in order. The total score is computed
  // from this (see totalScore), not stored separately, so the two can't disagree.
  history: [],
  // The current round, once one has started:
  //   number:    1 for the first round, 2 for the second, ...
  //   answer:    the track that's playing
  //   choices:   [track, ...], shuffled; the answer is one of them
  //   startedAt: null while the clip loads, then performance.now() when it starts
  //   pickedId:  the clicked track's id; null if none (yet, or time ran out)
  //   elapsed:   seconds from clip start to answer (set when the round ends)
  //   points:    points for this round (set when the round ends)
  round: null,
};

// The handle of the "time's up" timer, so it can be cancelled when you answer
// first. It's not in `state` because nothing on screen depends on it; it's
// bookkeeping for a side effect, like the <audio> element.
let roundTimer = null;

// The ONLY way to change state. Because every change goes through here, the
// screen is redrawn every time and can never show something out of date.
//
// { ...state, ...changes } builds a new object: copy every field of the old
// state, then overwrite the ones in `changes`. So setState({ phase: "LOADING" })
// changes phase and keeps tracks and error as they were.
function setState(changes) {
  state = { ...state, ...changes };
  render(state);
}

// ---------------------------------------------------------------------------
// Actions: things that happen (clicks, network replies) and update state.
// ---------------------------------------------------------------------------

async function loadTracks() {
  setState({ phase: "LOADING", error: null });

  try {
    const response = await fetch(
      "/api/playlist/" + CONFIG.playlistId + "/tracks",
    );
    // fetch() only throws when the network fails. An error status like 404 or
    // 502 still counts as a "successful" fetch, so check response.ok ourselves.
    if (!response.ok) {
      throw new Error("Couldn't load tracks (HTTP " + response.status + ")");
    }
    const tracks = await response.json();
    // A round needs at least 2 songs (1 right + 1 wrong) to be a choice at all.
    if (tracks.length < 2) {
      throw new Error("This playlist has too few playable songs");
    }
    startGame(tracks);
  } catch (error) {
    // Back to the start screen, which shows the error and lets you try again.
    setState({ phase: "START", error: error.message });
  }
}

// Reset everything for a new game. Start and Play again both come through here
// (via loadTracks), so nothing from an earlier game can carry over.
function startGame(tracks) {
  // Deal from a shuffled deck: the first `rounds` tracks are this game's
  // answers, so no song can come up twice. slice(0, n) takes the first n items
  // (or all of them, if there are fewer).
  const answers = shuffle(tracks).slice(0, CONFIG.rounds);
  setState({ tracks: tracks, answers: answers, history: [] });
  startRound();
}

function startRound() {
  const number = state.history.length + 1; // 0 rounds done → round 1
  const answer = state.answers[number - 1]; // Arrays count from 0
  // { ...a, number } copies everything from pickRound's result and adds number.
  const round = { ...pickRound(answer, state.tracks), number: number };
  setState({ phase: "PLAYING", round: round });
  playClip(answer.preview_url, clipStarted, clipFailed);
}

// Called by the audio code once the clip is actually playing. The clock starts
// now, not when the round started, so download time never eats into your time.
function clipStarted() {
  if (state.phase !== "PLAYING") {
    return;
  }
  setState({ round: { ...state.round, startedAt: performance.now() } });
  roundTimer = setTimeout(timeUp, CONFIG.clipSeconds * 1000);
}

// Called by the audio code if the clip can't load or the browser blocks it.
// Most likely cause: Deezer's preview links expire 15 minutes after we fetched
// them. Pressing Start fetches the tracks again, which gives fresh links.
function clipFailed(error) {
  if (state.phase !== "PLAYING") {
    return;
  }
  console.error("Clip failed:", error);
  // Cancel what we scheduled. (Not endRound: this round never really happened,
  // so it shouldn't be recorded in the history.)
  clearTimeout(roundTimer);
  stopClip();
  setState({
    phase: "START",
    error: "Couldn't play the clip. Press Start to reload the songs.",
  });
}

function pickChoice(track) {
  // State machine guard: a pick only counts while the clip is playing.
  // A second click, or one after time ran out, is ignored. (The buttons are
  // also disabled until the clip starts; this guard doesn't rely on that.)
  if (state.phase !== "PLAYING" || state.round.startedAt === null) {
    return;
  }
  const elapsed = (performance.now() - state.round.startedAt) / 1000; // ms → s
  endRound(track.id, elapsed);
}

// Called by the timer when the clip's time is up. If you answered first, the
// timer was cancelled; the guard is a second safety net.
function timeUp() {
  if (state.phase !== "PLAYING") {
    return;
  }
  endRound(null, CONFIG.clipSeconds);
}

// The one place a round ends, whether by answer or time-out.
function endRound(pickedId, elapsed) {
  // Cancel what we scheduled: the timer and the audio's handlers. (If time ran
  // out, the timer has already fired; clearing it again is harmless.)
  clearTimeout(roundTimer);
  stopClip();

  const correct = pickedId === state.round.answer.id;
  const points = scoreFor(correct, elapsed);

  // A new round object: copy the old one and fill in the result. We build a new
  // object instead of changing state.round directly, so ALL changes go through
  // setState and the screen always gets redrawn.
  const finished = {
    ...state.round,
    pickedId: pickedId,
    elapsed: elapsed,
    points: points,
  };
  // Same idea for the history: a new array with the old rounds plus this one.
  setState({
    phase: "REVEAL",
    round: finished,
    history: [...state.history, finished],
  });
}

function showResults() {
  if (state.phase !== "REVEAL") {
    return;
  }
  setState({ phase: "RESULTS" });
}

// ---------------------------------------------------------------------------
// Game rules: plain functions that compute things from data. No state, no
// screen, no audio, which makes them easy to read and to test.
// ---------------------------------------------------------------------------

// Return a shuffled COPY of an array (Fisher–Yates shuffle).
//
// Walk from the last position down to the second. For each position i, pick a
// random position j from 0..i (the part not yet fixed) and swap them. Every
// item is equally likely to land in each spot, so every order is equally likely.
// (The popular `sort(() => Math.random() - 0.5)` is NOT fair: sorting assumes
// comparisons are consistent, and random answers make some orders more common.)
function shuffle(array) {
  const result = [...array]; // Copy, so the caller's array isn't changed
  for (let i = result.length - 1; i > 0; i--) {
    const j = Math.floor(Math.random() * (i + 1)); // Random whole number 0..i
    [result[i], result[j]] = [result[j], result[i]]; // Swap
  }
  return result;
}

// Set up a round for the given answer: add wrong choices from the same
// playlist, all with different titles (ignoring upper/lower case), so no two
// buttons say the same thing.
function pickRound(answer, tracks) {
  // Every track except the answer, in random order. filter() keeps the items
  // for which the function returns true.
  const others = shuffle(tracks.filter((track) => track.id !== answer.id));

  const choices = [answer];
  // A Set holds each value at most once and answers "have I seen this?" fast.
  const usedTitles = new Set([answer.title.toLowerCase()]);

  for (const track of others) {
    if (choices.length === CONFIG.choices) {
      break; // Enough choices
    }
    const title = track.title.toLowerCase();
    if (!usedTitles.has(title)) {
      usedTitles.add(title);
      choices.push(track);
    }
  }

  // Shuffle again, or the answer would always be the first button.
  return {
    answer: answer,
    choices: shuffle(choices),
    startedAt: null,
    pickedId: null,
    elapsed: null,
    points: null,
  };
}

// Points for one answer (formula from ARCHITECTURE.md). Right answers earn
// 1000 at once, dropping steadily to 100 at the end; wrong answers earn 0.
// Math.max(100, ...) is the floor, so a late right answer still earns something.
function scoreFor(correct, elapsedSeconds) {
  if (!correct) {
    return 0;
  }
  const timeLeft = 1 - elapsedSeconds / CONFIG.clipSeconds; // 1 at start → 0 at end
  return Math.round(Math.max(100, 1000 * timeLeft));
}

// The total score: the points of every finished round, added up.
function totalScore(history) {
  let total = 0;
  for (const round of history) {
    total += round.points;
  }
  return total;
}

// ---------------------------------------------------------------------------
// Audio: a side effect, so it's started and stopped by actions, never by
// render(). The <audio> element lives outside #app in index.html, so redrawing
// the screen never destroys it, and one shared element keeps browsers' autoplay
// rules happy.
// ---------------------------------------------------------------------------

const player = document.getElementById("player");

// Start playing a preview at a random point inside it. It keeps playing until
// stopClip() is called; the round timer decides when that is.
//
// onStart and onError are callbacks: functions we're handed so we can call
// them later. onStart() runs once the audio is actually playing; onError(error)
// runs if it can't load or the browser blocks it. This keeps the audio code
// free of game logic: it just reports what happened.
//
// The <audio> element works through events: we ask it to do something, and it
// tells us later when that has happened. We react with `player.onX = ...`
// handlers. Assigning a handler replaces the previous one.
function playClip(previewUrl, onStart, onError) {
  stopClip();

  // Runs once the browser has read the clip's header and knows its real length.
  player.onloadedmetadata = () => {
    // Pick a start so the clip never runs past the end of the preview.
    // player.duration is the real length (usually ~30 s, sometimes less).
    // Math.max(0, ...) covers a preview shorter than the clip itself.
    const latestStart = Math.max(0, player.duration - CONFIG.clipSeconds);
    player.currentTime = Math.random() * latestStart;

    // play() returns a promise that fails if the browser blocks playback
    // (autoplay rules).
    player.play().catch(onError);
  };

  // "playing" fires when audio actually starts coming out, but ALSO every time
  // it resumes after a pause or a buffering stall. We only want the first, so
  // the handler removes itself.
  player.onplaying = () => {
    player.onplaying = null;
    onStart();
  };

  // Fires if the file can't be loaded, e.g. an expired preview link.
  player.onerror = () => onError(player.error);

  player.src = previewUrl; // Point the player at the clip (starts downloading)
}

// Stop the audio and forget its handlers, so nothing we set up for this clip
// can still fire later. E.g. without clearing onloadedmetadata, answering
// before the clip had loaded would start the music during REVEAL.
function stopClip() {
  player.onloadedmetadata = null;
  player.onplaying = null;
  player.onerror = null;
  player.pause();
}

// ---------------------------------------------------------------------------
// Rendering: draws the screen from state. It only READS state, never changes
// it; changes happen in the actions above.
// ---------------------------------------------------------------------------

const app = document.getElementById("app");

// Create an element with plain text inside, e.g. el("p", "Hello") → <p>Hello</p>.
// textContent always treats the text as text, never as HTML, so a song title
// containing "<" or "&" can't break the page or inject code (XSS).
// className is optional: el("p", "Hi", "message") → <p class="message">Hi</p>.
// The look of each class lives in style.css; JavaScript only decides WHAT is
// on screen, CSS decides how it looks.
function el(tag, text = "", className = "") {
  const element = document.createElement(tag);
  element.textContent = text;
  element.className = className;
  return element;
}

// --- Centred screens ---

function startScreen(state) {
  const button = el("button", "Start", "primary");
  button.addEventListener("click", loadTracks);

  const elements = [el("h1", "Guess the Song"), button];
  if (state.error) {
    elements.push(el("p", state.error, "error"));
  }
  return elements;
}

function loadingScreen() {
  return [el("p", "Loading tracks…", "message")];
}

function resultsScreen(state) {
  // <ol> is a numbered list. Each row: title and artist on the left, the
  // result on the right (two <span>s; the CSS spreads them apart).
  const list = el("ol", "", "results");
  for (const round of state.history) {
    let result;
    if (round.pickedId === null) {
      result = "time's up";
    } else if (round.pickedId === round.answer.id) {
      result = "+" + round.points + " (" + round.elapsed.toFixed(1) + " s)";
    } else {
      result = "✗ wrong";
    }
    const row = el("li");
    row.append(
      el("span", round.answer.title + " — " + round.answer.artist),
      el("span", result),
    );
    list.append(row);
  }

  // Play again goes through loadTracks, so every game gets fresh preview links
  // (Deezer's expire after 15 minutes).
  const again = el("button", "Play again", "primary");
  again.addEventListener("click", loadTracks);

  return [
    el("p", "Final score", "message"),
    el("p", String(totalScore(state.history)), "final-score"),
    list,
    again,
  ];
}

// --- Round screens (PLAYING and REVEAL share one layout) ---

function playingScreen(state) {
  const started = state.round.startedAt !== null;

  // One button per choice, showing the title only (the artist would give
  // too much away; it's revealed afterwards). Disabled until the clip plays,
  // so you can't score by clicking blindly while it loads.
  const buttons = state.round.choices.map((track) => {
    const button = el("button", track.title, "choice");
    button.disabled = !started;
    button.addEventListener("click", () => pickChoice(track));
    return button;
  });

  const message = started ? "Which song is this?" : "Loading clip…";
  return roundLayout(
    state,
    [el("p", message, "message")],
    timeBar(state.round.startedAt),
    buttons,
  );
}

function revealScreen(state) {
  // Destructuring: several variables at once from the round object.
  const { answer, choices, pickedId, elapsed, points } = state.round;

  // The verdict text, and a class to colour it (see .verdict.* in style.css).
  let verdict = "Wrong!";
  let verdictClass = "wrong";
  if (pickedId === answer.id) {
    verdict = "Correct!";
    verdictClass = "correct";
  } else if (pickedId === null) {
    verdict = "Time's up!";
  }

  // toFixed(1) shows one decimal: 2.6312 → "2.6"
  let pointsText = "+" + points + " points";
  if (pickedId !== null) {
    pointsText += " (" + elapsed.toFixed(1) + " s)";
  }

  // The same buttons as while playing, but disabled and coloured in place:
  // green for the answer, red for a wrong pick, the rest greyed out.
  const buttons = choices.map((track) => {
    const button = el("button", track.title, "choice");
    button.disabled = true;
    if (track.id === answer.id) {
      button.classList.add("correct"); // classList adds a class to the ones it has
    } else if (track.id === pickedId) {
      button.classList.add("wrong");
    }
    return button;
  });

  // After the last round, the button leads to the results instead.
  const lastRound = state.round.number === state.answers.length;
  const next = el("button", lastRound ? "See results" : "Next song", "primary");
  next.addEventListener("click", lastRound ? showResults : startRound);

  return roundLayout(
    state,
    [
      el("p", verdict, "verdict " + verdictClass), // Two classes, space-separated
      el("p", 'It was "' + answer.title + '" by ' + answer.artist),
      el("p", pointsText, "message"),
      next,
    ],
    timeBar(null), // Empty bar: time's over, but keep the layout from jumping
    buttons,
  );
}

// The shared layout: three areas that style.css places with CSS Grid.
//   stage:   round number, then the given stage items, centred
//   sidebar: scores, top right
//   bottom:  timer bar, then the choice buttons in a grid
function roundLayout(state, stageItems, bar, buttons) {
  const stage = el("div", "", "stage");
  stage.append(roundCounter(state), ...stageItems);

  const choices = el("div", "", "choices");
  choices.append(...buttons);

  const bottom = el("div", "", "bottom");
  bottom.append(bar, choices);

  return [stage, scoreSidebar(state), bottom];
}

// "Round 2 / 5"
function roundCounter(state) {
  return el(
    "p",
    "Round " + state.round.number + " / " + state.answers.length,
    "round-counter",
  );
}

// The scores, as a list of players. Just "You" for now; in multiplayer, more
// rows appear here without changing the layout.
function scoreSidebar(state) {
  const sidebar = el("aside", "", "sidebar"); // <aside> = content beside the main part
  const players = el("ul", "", "players");
  const you = el("li");
  you.append(el("span", "You"), el("span", String(totalScore(state.history))));
  players.append(you);
  sidebar.append(el("h2", "Score"), players);
  return sidebar;
}

// A bar that shrinks from full to empty while the clip plays. With startedAt
// null (clip still loading, or round over) it's just the empty background.
//
// The browser animates it by itself (element.animate, the "Web Animations"
// API), so we don't redraw the screen every fraction of a second. Redrawing
// would rebuild the answer buttons, and a click that lands mid-rebuild gets lost.
//
// It starts from however much time is LEFT, not always from full, so if the
// screen is ever redrawn mid-round the bar carries on where it was.
function timeBar(startedAt) {
  const background = el("div", "", "timebar");
  if (startedAt === null) {
    return background;
  }

  const totalMs = CONFIG.clipSeconds * 1000;
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
  switch (state.phase) {
    case "START":
      elements = startScreen(state);
      break;
    case "LOADING":
      elements = loadingScreen();
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

// Draw the first screen when the page loads.
render(state);
