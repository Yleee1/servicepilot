from __future__ import annotations

import sqlite3
from pathlib import Path

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.checkpoint.sqlite import SqliteSaver


def create_checkpointer(sqlite_path: str | Path | None = None):
    """Return (checkpointer, resource). Keep resource alive for graph lifetime."""

    if sqlite_path is None:
        return InMemorySaver(), None
    path = Path(sqlite_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path, check_same_thread=False)
    return SqliteSaver(connection), connection
