"""Locust scenario for C-ECM — simulates a realistic mix of what a real
user actually does (mostly browsing/reading, occasionally writing, rarely
acting on a workflow) rather than hammering one endpoint.

Reads the pool of throwaway users/connection/seed content that
seed_load_test_data.py already created (seed_state.json, next to this
file) — run that first. Deliberately excludes AI (/ai/*) and e-signature
(/files/*/esignature) endpoints: AI hits a real, possibly-billed external
API (watsonx), and DocuSign isn't configured with real credentials in this
environment anyway.

Usage (interactive, with the live web dashboard):
    locust -f locustfile.py --host http://127.0.0.1:8020
    # then open http://localhost:8089 and set concurrency/spawn-rate there

Usage (headless, scripted):
    locust -f locustfile.py --host http://127.0.0.1:8020 \\
        --headless --users 75 --spawn-rate 5 --run-time 10m \\
        --csv=results/run1 --html=results/run1.html
"""
import json
import random
from pathlib import Path

from locust import HttpUser, between, task

STATE_PATH = Path(__file__).parent / "seed_state.json"
_state = json.loads(STATE_PATH.read_text(encoding="utf-8")) if STATE_PATH.exists() else None

_SEARCH_TERMS = ["load", "test", "seed", "doc", "content"]


class ECMUser(HttpUser):
    wait_time = between(1, 4)  # think-time between actions, so this reads as real usage, not a hammer test

    def on_start(self):
        if _state is None:
            raise RuntimeError(
                "seed_state.json not found — run seed_load_test_data.py first."
            )
        self.username = random.choice(_state["usernames"])
        resp = self.client.post(
            "/auth/login",
            json={"username": self.username, "password": _state["password"]},
            name="/auth/login",
        )
        token = resp.json()["access_token"]
        self.client.headers.update({
            "Authorization": f"Bearer {token}",
            "X-Connection-Id": _state["connection_id"],
        })
        self.folder_ids = _state["folder_ids"]
        self.file_ids = _state["file_ids"]
        self.workflow_definition_id = _state["workflow_definition_id"]

    # ---- reads (the bulk of real traffic) ---------------------------------

    @task(10)
    def browse_folder(self):
        folder_id = random.choice([None, *self.folder_ids])
        params = {"folder_id": folder_id} if folder_id else {}
        self.client.get("/folders/contents", params=params, name="/folders/contents")

    @task(6)
    def download_file(self):
        file_id = random.choice(self.file_ids)
        self.client.get(f"/files/{file_id}/download", name="/files/[id]/download")

    @task(5)
    def search(self):
        q = random.choice(_SEARCH_TERMS)
        self.client.get("/search", params={"q": q}, name="/search")

    @task(3)
    def list_notifications(self):
        self.client.get("/notifications", name="/notifications")

    @task(2)
    def global_search(self):
        q = random.choice(_SEARCH_TERMS)
        self.client.get("/search/global", params={"q": q}, name="/search/global")

    # ---- writes (less frequent, matching real usage) -----------------------

    @task(3)
    def upload_file(self):
        folder_id = random.choice(self.folder_ids)
        content = f"Load test upload from {self.username}. ".encode() * 50
        self.client.post(
            "/files",
            files={"upload": ("upload.txt", content, "text/plain")},
            data={"folder_id": folder_id},
            name="/files [upload]",
        )

    @task(2)
    def tag_a_file(self):
        file_id = random.choice(self.file_ids)
        tag = self.client.post(
            "/tags", json={"name": f"loadtest-{random.randint(1, 20)}", "color": "#5B8DEF"}, name="/tags [create]",
        )
        if tag.status_code == 201:
            self.client.post(
                f"/resources/{file_id}/tags",
                json={"resource_type": "file", "tag_id": tag.json()["id"]},
                name="/resources/[id]/tags [attach]",
            )

    @task(2)
    def comment_on_a_file(self):
        file_id = random.choice(self.file_ids)
        self.client.post(
            f"/resources/{file_id}/comments",
            json={"resource_type": "file", "body": f"Load test comment from {self.username}"},
            name="/resources/[id]/comments [create]",
        )

    # ---- workflow actions (rare, matching real usage) -----------------------

    @task(1)
    def start_a_workflow(self):
        file_id = random.choice(self.file_ids)
        self.client.post(
            "/workflows/instances",
            json={
                "definition_id": self.workflow_definition_id,
                "resources": [{"resource_id": file_id, "resource_type": "file"}],
                "comment": "Started by load test",
            },
            name="/workflows/instances [start]",
        )

    @task(1)
    def act_on_a_pending_workflow(self):
        pending = self.client.get(
            "/workflows/instances", params={"status": "in_review"}, name="/workflows/instances [list]",
        )
        if pending.status_code != 200:
            return
        instances = pending.json()
        if not instances:
            return
        instance = random.choice(instances)
        self.client.post(
            f"/workflows/instances/{instance['id']}/action",
            json={"action": "approved", "comment": "Approved by load test"},
            name="/workflows/instances/[id]/action",
        )
