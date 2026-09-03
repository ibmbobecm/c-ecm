"""Tears down everything seed_load_test_data.py created: deletes the Load
Test Repository connection (which cascades to remove its files/folders/
tags/comments/workflow instances/etc.), deletes every load-test user
account, and deletes the Load Test Approval workflow definition. Safe to
run multiple times.

Usage:
    ..\\backend\\.venv\\Scripts\\python.exe cleanup_load_test_data.py
"""
import json
from pathlib import Path

import requests

STATE_PATH = Path(__file__).parent / "seed_state.json"


def _login(api_base: str, username: str, password: str) -> str:
    resp = requests.post(f"{api_base}/auth/login", json={"username": username, "password": password}, timeout=10)
    resp.raise_for_status()
    return resp.json()["access_token"]


def main() -> None:
    if not STATE_PATH.exists():
        print("No seed_state.json found — nothing to clean up.")
        return

    state = json.loads(STATE_PATH.read_text(encoding="utf-8"))
    api_base = state["api_base"]

    print(f"Logging in as admin ({api_base})...")
    token = _login(api_base, "admin", "admin")
    headers = {"Authorization": f"Bearer {token}"}

    conn_id = state.get("connection_id")
    if conn_id:
        resp = requests.delete(f"{api_base}/connections/{conn_id}", headers=headers, timeout=15)
        print(f"Deleted connection {conn_id}: {resp.status_code}")

    wf_id = state.get("workflow_definition_id")
    if wf_id:
        resp = requests.delete(f"{api_base}/workflows/definitions/{wf_id}", headers=headers, timeout=10)
        print(f"Deleted workflow definition {wf_id}: {resp.status_code}")

    print(f"Deleting {len(state.get('usernames', []))} load-test users...")
    all_users = requests.get(f"{api_base}/users", headers=headers, timeout=10).json()
    by_username = {u["username"]: u["id"] for u in all_users}
    deleted = 0
    for username in state.get("usernames", []):
        uid = by_username.get(username)
        if uid is None:
            continue
        resp = requests.delete(f"{api_base}/users/{uid}", headers=headers, timeout=10)
        if resp.status_code == 204:
            deleted += 1
    print(f"  {deleted} users deleted.")

    STATE_PATH.unlink()
    print("Removed seed_state.json. Cleanup complete.")


if __name__ == "__main__":
    main()
