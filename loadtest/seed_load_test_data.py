"""One-time setup for a load-testing run against a live C-ECM backend.

Creates a fixed pool of throwaway users, one dedicated Local Disk
connection (so load-test traffic never touches any real connection), a
handful of seed folders/files so browse/search/download have real content
to return, and one open-step workflow definition so any load-test user can
start/act on an approval without per-user assignment setup.

Writes everything it created to seed_state.json, next to this file — the
locustfile reads that to know which users/connection/files to drive
traffic against, and cleanup_load_test_data.py reads it to tear everything
back down afterward.

Usage:
    ..\\backend\\.venv\\Scripts\\python.exe seed_load_test_data.py [--users 60] [--files 30]
"""
import argparse
import json
import sys
from pathlib import Path

import requests

API_BASE = "http://127.0.0.1:8020"
STATE_PATH = Path(__file__).parent / "seed_state.json"
PASSWORD = "LoadTest#12345"


def _login(username: str, password: str) -> str:
    resp = requests.post(f"{API_BASE}/auth/login", json={"username": username, "password": password}, timeout=10)
    resp.raise_for_status()
    return resp.json()["access_token"]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--users", type=int, default=60, help="number of throwaway load-test user accounts to create")
    parser.add_argument("--files", type=int, default=30, help="number of seed files to upload for browse/search/download")
    parser.add_argument("--admin-username", default="admin")
    parser.add_argument("--admin-password", default="admin")
    args = parser.parse_args()

    print(f"Logging in as admin ({API_BASE})...")
    admin_token = _login(args.admin_username, args.admin_password)
    admin_headers = {"Authorization": f"Bearer {admin_token}"}

    print(f"Creating {args.users} load-test users...")
    usernames = []
    for i in range(args.users):
        username = f"loadtest_user_{i:03d}"
        resp = requests.post(
            f"{API_BASE}/users", headers=admin_headers,
            json={"username": username, "password": PASSWORD, "display_name": f"Load Test User {i:03d}", "is_superadmin": False},
            timeout=10,
        )
        if resp.status_code not in (201, 409):  # 409 = already exists from a previous run, fine
            print(f"  ! failed to create {username}: {resp.status_code} {resp.text}")
            continue
        usernames.append(username)
    print(f"  {len(usernames)} users ready.")

    print("Creating the dedicated Load Test Repository connection...")
    conn_resp = requests.post(
        f"{API_BASE}/connections", headers=admin_headers,
        json={
            "provider_key": "local", "display_name": "Load Test Repository",
            "username": "", "password": "",
            "config": {"storage_path": "data/loadtest_storage"},
        },
        timeout=10,
    )
    if conn_resp.status_code != 201:
        print(f"Connection creation failed ({conn_resp.status_code}): {conn_resp.text}")
        print("If 'Load Test Repository' already exists from a previous run, delete it first (see cleanup script) and re-run.")
        sys.exit(1)
    connection_id = conn_resp.json()["id"]
    print(f"  connection_id = {connection_id}")

    conn_headers = {**admin_headers, "X-Connection-Id": connection_id}

    print("Seeding folders...")
    folder_ids = []
    for i in range(5):
        resp = requests.post(f"{API_BASE}/folders", headers=conn_headers, json={"name": f"LoadTest Folder {i:02d}", "parent_id": None}, timeout=10)
        resp.raise_for_status()
        folder_ids.append(resp.json()["id"])

    print(f"Uploading {args.files} seed files...")
    file_ids = []
    body = ("Load test seed content. " * 200).encode()  # a few KB, representative small-document size
    for i in range(args.files):
        folder_id = folder_ids[i % len(folder_ids)]
        resp = requests.post(
            f"{API_BASE}/files", headers=conn_headers,
            files={"upload": (f"seed-doc-{i:03d}.txt", body, "text/plain")},
            data={"folder_id": folder_id},
            timeout=10,
        )
        resp.raise_for_status()
        file_ids.append(resp.json()["id"])
    print(f"  {len(file_ids)} files seeded.")

    print("Creating the Load Test Approval workflow definition (open step — any user can act)...")
    wf_resp = requests.post(
        f"{API_BASE}/workflows/definitions", headers=conn_headers,
        json={
            "name": "Load Test Approval", "description": "Seeded for load testing — open step, no fixed assignees.",
            "steps": [{"name": "Review", "assignees": [], "required_approvals": 1}],
        },
        timeout=10,
    )
    if wf_resp.status_code == 409:
        # Already exists from a previous run — look it up instead.
        defs = requests.get(f"{API_BASE}/workflows/definitions", headers=admin_headers, timeout=10).json()
        workflow_definition_id = next(d["id"] for d in defs if d["name"] == "Load Test Approval")
    else:
        wf_resp.raise_for_status()
        workflow_definition_id = wf_resp.json()["id"]
    print(f"  workflow_definition_id = {workflow_definition_id}")

    state = {
        "api_base": API_BASE,
        "password": PASSWORD,
        "usernames": usernames,
        "connection_id": connection_id,
        "folder_ids": folder_ids,
        "file_ids": file_ids,
        "workflow_definition_id": workflow_definition_id,
    }
    STATE_PATH.write_text(json.dumps(state, indent=2), encoding="utf-8")
    print(f"\nDone. Wrote {STATE_PATH}")
    print("Run the load test with:  locust -f locustfile.py --host", API_BASE)


if __name__ == "__main__":
    main()
