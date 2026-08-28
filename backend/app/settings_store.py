"""Admin-level settings: OAuth app credentials (client id/secret) that this
whole FileDrive deployment registers ONE of with Google/Microsoft/Box —
unlike per-connection details (a FileNet server, an Alfresco URL), an OAuth
app is inherently shared by every connection to that provider, the same way
a Slack or Zapier install has one registered Google app that all its users
consent through. Edited via Admin Settings in the UI; persisted here so it
survives restarts without touching .env. Falls back to the .env-configured
default for anyone who set that up already.
"""

import sqlite3

from .config import DATA_DIR

_DB_PATH = DATA_DIR / "settings.db"


def _conn() -> sqlite3.Connection:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(_DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def init_db() -> None:
    conn = _conn()
    try:
        conn.execute("CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
        conn.commit()
    finally:
        conn.close()


def get_setting(key: str, default: str = "") -> str:
    conn = _conn()
    try:
        row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else default
    finally:
        conn.close()


def set_setting(key: str, value: str) -> None:
    conn = _conn()
    try:
        conn.execute(
            "INSERT INTO settings (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )
        conn.commit()
    finally:
        conn.close()


def get_settings(keys: list[str], defaults: dict[str, str]) -> dict[str, str]:
    conn = _conn()
    try:
        rows = conn.execute(
            f"SELECT key, value FROM settings WHERE key IN ({','.join('?' * len(keys))})", keys
        ).fetchall()
        stored = {r["key"]: r["value"] for r in rows}
        return {k: stored.get(k, defaults.get(k, "")) for k in keys}
    finally:
        conn.close()
