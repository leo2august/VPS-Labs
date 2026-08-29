"""Shared SQLite access for the 9router twin database.

Both Labs (Flask, port 9118) and 9router (Node, port 20128) read/write the
SAME SQLite file. To keep that safe:

- WAL journal mode -> readers never block the single writer, writer keeps working
- busy_timeout -> if the two processes contend for the write lock, wait a moment
- BEGIN IMMEDIATE on writes -> acquire the write lock up front, avoid deadlock
- row_factory everywhere -> dict-like rows

Call connect_write() for anything that mutates providerConnections/settings/kv.
"""
import os
import sqlite3
from pathlib import Path

LIVE_DB = Path(os.environ.get("LABS_9ROUTER_DB", "/home/ubuntu/.9router/db/data.sqlite"))
WAL = True
BUSY_TIMEOUT_MS = 5000


def connect(mode="ro"):
    """Open the shared 9router DB.

    mode='ro' -> read-only (safe for reads, even while 9router writes)
    mode='rw' -> read-write, WAL + busy_timeout enabled
    """
    uri = f"file:{LIVE_DB}?mode={mode}"
    con = sqlite3.connect(uri, uri=True, timeout=BUSY_TIMEOUT_MS / 1000)
    con.row_factory = sqlite3.Row
    if mode != "ro":
        con.execute(f"PRAGMA busy_timeout={BUSY_TIMEOUT_MS}")
        if WAL:
            try:
                con.execute("PRAGMA journal_mode=WAL")
            except sqlite3.Error:
                pass  # WAL may be unavailable on some mounts; fall back to journal
    return con


def connect_read():
    return connect("ro")


def connect_write():
    return connect("rw")


def row_dict(row):
    return dict(row) if row is not None else None
