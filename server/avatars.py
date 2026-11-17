"""The avatars a room hands out: one per player, never two the same.

Only ids live here. What an id LOOKS like (for now an animal emoji on a
coloured circle) is up to the browser: see AVATARS in client/script.js. So
swapping the emoji for real pictures later only changes the client.

The number of avatars is also the room's size limit (MAX_PLAYERS in
rooms.py): with every avatar taken, there's none left for a new player.
Adding one here makes rooms one bigger; give it a look in script.js too.
"""

AVATARS = ["fox", "frog", "octopus", "panda", "lion", "penguin", "unicorn", "pig"]
