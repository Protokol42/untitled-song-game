# Song source test, Part 1: iTunes

Goal: fetch songs from the iTunes Search API and play a short snippet in the browser.

## Run it

From this folder:

```
python3 -m http.server 8000
```

Then open <http://localhost:8000> and press **Search**.

## Things to try

1. **Look at the raw data.** Open DevTools (F12) → **Console** and expand the logged array.
   Find `trackName`, `artistName` and `previewUrl`. What other fields could be useful
   for the game (hint: artwork, genre, release date)?
2. **Watch the network.** DevTools → **Network** tab. Press Search and find the request to
   `itunes.apple.com`. Then play a song and find the `.m4a` audio download.
3. **Play the same song several times.** Check the console: the start point changes each
   time, so you hear different parts of the same preview.

## Fixed timing bugs

The first version stopped the snippet with `setTimeout(pause, 5000)` right after calling
`play()`. That had three problems:

- **Snippets shorter than 5 seconds.** The timer started before the audio had downloaded,
  so loading time ate into the snippet.
- **Two clicks in a row.** The first song's timer still fired and paused the second song
  early.
- **Assumed 30 s previews.** The random start was picked as if every preview were 30 s
  long; a shorter preview could start near or past its end.

The fix: wait for `loadedmetadata` to learn the real `player.duration`, and stop on
`timeupdate` once the audio's own position reaches `start + 5`, instead of using a
wall-clock timer. Handlers are assigned with `player.onX = ...`, so a new click replaces
the old song's handlers.

These are the same kind of timing issues the real game has to handle fairly for every
player, which is why the server will control timing there.
