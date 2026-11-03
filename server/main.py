"""Game server: the routes, i.e. what the browser can ask for. The work itself
happens in the other files:
    tracks.py   our Track model
    deezer.py   fetching tracks from Deezer
    protocol.py the messages sent over the WebSocket
    rooms.py    who is in which room, running each room's game
    game.py     the rules of the game

Run from the project root, with the virtual environment active:
    uvicorn main:app --reload --app-dir server

--app-dir server puts server/ on Python's import path, which is why the
imports below are plain `import deezer` rather than `from server import ...`.
"""

from pathlib import Path

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles
from pydantic import ValidationError

import deezer
import rooms
from protocol import (
    BackToLobby,
    CreateRoom,
    JoinRoom,
    LeaveRoom,
    MakeGuess,
    StartGame,
    client_message,
)
from rooms import RoomError
from tracks import Track

app = FastAPI()


# {playlist_id} in the path becomes the function's playlist_id argument.
# The `int` type hint means FastAPI rejects /api/playlist/abc/tracks with a 422
# before our code runs.
#
# deezer.py raises its own errors; here they become HTTP status codes:
#   404 Not Found:   the playlist doesn't exist
#   502 Bad Gateway: anything else, i.e. "the service behind me failed"
# The more specific except comes first: a PlaylistNotFound is also a DeezerError.
@app.get("/api/playlist/{playlist_id}/tracks")
async def playlist_tracks(playlist_id: int) -> list[Track]:
    try:
        return await deezer.get_playlist_tracks(playlist_id)
    except deezer.PlaylistNotFound:
        raise HTTPException(status_code=404, detail="Playlist not found")
    except deezer.DeezerError:
        raise HTTPException(status_code=502, detail="Deezer request failed")


# One player's connection. This function runs once per open tab, all at the
# same time, for as long as that tab stays connected.
#
# `room` and `player` are this connection's own variables: which room it's in
# and as whom. Both are None until it creates or joins a room.
@app.websocket("/ws")
async def game_socket(websocket: WebSocket) -> None:
    await websocket.accept()
    room = None
    player = None

    try:
        while True:
            text = await websocket.receive_text()

            # Check the message against the protocol. Anything that doesn't fit
            # (not JSON, unknown type, name too long, ...) gets an error reply
            # and is otherwise ignored; the connection stays open.
            try:
                message = client_message.validate_json(text)
            except ValidationError as error:
                first = error.errors()[0]  # Reporting the first problem is enough
                await rooms.send_error(websocket, f"Invalid message: {first['msg']}")
                continue  # Back to the top of the loop: wait for the next message

            # `match` picks the FIRST case whose pattern fits. CreateRoom() means
            # "is a CreateRoom"; `if` adds a condition. The order matters: the
            # guard cases come before the cases they protect.
            try:
                match message:
                    case CreateRoom() | JoinRoom() if room is not None:
                        raise RoomError("You're already in a room")
                    case CreateRoom():
                        room, player = rooms.create_room(message.token, message.name, websocket)
                    case JoinRoom():
                        room, player = rooms.join_room(
                            message.code, message.token, message.name, websocket
                        )
                    # `_` matches anything: every other message needs a room.
                    case _ if room is None:
                        raise RoomError("You're not in a room")
                    case LeaveRoom():
                        rooms.leave_room(room, player)
                        # Tell the others; the browser that left needs no reply,
                        # it goes back to its home screen by itself.
                        left = room
                        room = None
                        player = None
                        await rooms.broadcast(left)
                        continue
                    case StartGame():
                        rooms.start_game(room, player)
                    case BackToLobby():
                        rooms.back_to_lobby(room, player)
                    case MakeGuess():
                        # Only the guesser hears about it now (their pick shown).
                        # The others find out at REVEAL, so nobody can copy.
                        if rooms.guess(room, player, message.track_id):
                            await rooms.send_state(room, player)
                        continue
            except RoomError as error:
                await rooms.send_error(websocket, str(error))
                continue

            # The room changed (created, joined, game started, back to the
            # lobby): tell everyone in it.
            await rooms.broadcast(room)

    except WebSocketDisconnect:
        # The tab closed, refreshed or lost its network. For now that counts as
        # leaving (rejoining with the token comes in roadmap step 4).
        if room is not None:
            rooms.leave_room(room, player)
            await rooms.broadcast(room)


# Serve the game page (client/index.html, script.js) from this same server, so
# the page and the API share one origin (localhost:8000) and CORS never applies.
#
# A mount at "/" matches EVERY path, and FastAPI checks routes in the order they
# were added, so this must come after the API routes or it would swallow them.
# html=True makes "/" serve index.html.
# Path(__file__).parent is server/; one more .parent is the project root.
CLIENT_DIR = Path(__file__).parent.parent / "client"
app.mount("/", StaticFiles(directory=CLIENT_DIR, html=True), name="client")
