const SNIPPET_SECONDS = 5;

const player = document.getElementById("player");

// Ask OUR server for songs matching the search term. It calls Deezer for us and
// returns a list of our own tracks: { id, title, artist, preview_url }.
//
// The URL has no "https://host" part, so the browser sends it to the same
// origin the page came from (localhost:8000). Same origin means no CORS check.
async function search(term) {
  const url = "/api/search?q=" + encodeURIComponent(term);

  const response = await fetch(url);
  // fetch() only throws when the network fails. An error status like 422 or
  // 502 still counts as a "successful" fetch, so check response.ok ourselves.
  if (!response.ok) {
    throw new Error("Search failed: HTTP " + response.status);
  }
  return await response.json(); // Already a plain array of our tracks
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
// The field names are ours (title, artist, preview_url), not Deezer's.
function showResults(tracks) {
  const list = document.getElementById("results");
  list.innerHTML = ""; // Clear results from any previous search

  for (const track of tracks) {
    const item = document.createElement("li");
    const button = document.createElement("button");
    button.textContent = "Play " + SNIPPET_SECONDS + "s";
    button.addEventListener("click", () => playSnippet(track.preview_url));

    item.append(button, " " + track.title + " — " + track.artist);
    list.appendChild(item);
  }
}

document.getElementById("search").addEventListener("click", async () => {
  const term = document.getElementById("query").value;
  // try/catch: if search() throws (see the response.ok check), show the error
  // instead of failing silently.
  try {
    const tracks = await search(term);
    console.log(tracks); // Inspect our simplified tracks in DevTools
    showResults(tracks);
  } catch (error) {
    console.error(error);
    alert(error.message);
  }
});
