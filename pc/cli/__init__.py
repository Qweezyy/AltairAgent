"""The terminal face of Altair (`altair`): one more window onto the same chats.

It is a client of the backend over the same WebSocket the desktop window uses, so a chat started
in the terminal shows up live in the window and the other way round, and settings, keys, skills,
memory and reminders are the very same. With no backend running it starts one of its own in the
background and stops it on exit.
"""
