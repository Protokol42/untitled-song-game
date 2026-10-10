const SNIPPET_SECONDS = 5;

const player = document.getElementById("player");

// Ask iTunes for songs matching the search term and return an array of tracks.
// `async` lets us use `await`, which pauses this function until the network
// request finishes without freezing the page.
async function search(term) {
  // encodeURIComponent turns spaces and symbols into URL-safe text,
  // e.g. "don't stop" becomes "don't%20stop".
  const url = "https://itunes.apple.com/search?entity=song&limit=10&term="
    + encodeURIComponent(term);

  const response = await fetch(url);     // 1. Send the HTTP request
  const data = await response.json();    // 2. Parse the body as JSON
  return data.results;                   // 3. The tracks live in "results"
}

// Play SNIPPET_SECONDS of a preview, starting at a random point inside it.
//
// The <audio> element works through events: we ask it to do something, and it
// tells us later when that has happened. We react with `player.onX = ...`
// handlers. Assigning a handler replaces the previous one, so a click on a new
// song automatically cancels the old song's handlers.
function playSnippet(previewUrl) {
  // Forget the previous song's stop check before switching clips.
  player.ontimeupdate = null;

  // Runs once the browser has read the clip's header and knows its real length.
  player.onloadedmetadata = () => {
    // Pick a start so the snippet never runs past the end of the preview.
    // player.duration is the real length (usually ~30 s, sometimes less).
    // Math.max(0, ...) covers a preview shorter than the snippet itself.
    const latestStart = Math.max(0, player.duration - SNIPPET_SECONDS);
    const start = Math.random() * latestStart;
    console.log("Preview is " + player.duration.toFixed(1) + " s, starting at "
      + start.toFixed(1) + " s");

    // Fires several times a second while playing (and after seeks). Instead of
    // a wall-clock timer, check how far the audio itself has got, so time spent
    // downloading or buffering never eats into the snippet.
    player.ontimeupdate = () => {
      if (player.currentTime >= start + SNIPPET_SECONDS) {
        player.pause();
        player.ontimeupdate = null;
      }
    };

    player.currentTime = start;
    player.play();
  };

  player.src = previewUrl; // Point the player at the clip (starts downloading)
}

// Build one list item per track: a Play button plus "Title — Artist".
function showResults(tracks) {
  const list = document.getElementById("results");
  list.innerHTML = ""; // Clear results from any previous search

  for (const track of tracks) {
    const item = document.createElement("li");
    const button = document.createElement("button");
    button.textContent = "Play " + SNIPPET_SECONDS + "s";
    button.addEventListener("click", () => playSnippet(track.previewUrl));

    item.append(button, " " + track.trackName + " — " + track.artistName);
    list.appendChild(item);
  }
}

document.getElementById("search").addEventListener("click", async () => {
  const term = document.getElementById("query").value;
  const tracks = await search(term);
  console.log(tracks); // Inspect the full track objects in DevTools
  showResults(tracks);
});
