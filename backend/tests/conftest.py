"""Test fixtures. The env vars below MUST be set before anything under
`app` is imported anywhere — app.config reads FD_DATA_DIR once, at import
time, to compute DATA_DIR — so this has to happen at module scope here,
not inside a fixture, since conftest.py is always collected before any
test module's imports run.
"""

import os
import shutil
import tempfile
import uuid
from pathlib import Path

_TEST_DATA_DIR = Path(tempfile.gettempdir()) / f"filedrive-test-{uuid.uuid4().hex}"
os.environ["FD_DATA_DIR"] = str(_TEST_DATA_DIR)
os.environ["FD_APP_USERNAME"] = "admin"
os.environ["FD_APP_PASSWORD"] = "admin"
os.environ["FD_LOCAL_STORAGE_DIR"] = str(_TEST_DATA_DIR / "local_storage")

import pytest
from fastapi.testclient import TestClient

from app.main import app


@pytest.fixture(scope="session", autouse=True)
def _cleanup_test_data_dir():
    yield
    shutil.rmtree(_TEST_DATA_DIR, ignore_errors=True)


@pytest.fixture()
def client():
    with TestClient(app) as c:
        yield c


@pytest.fixture()
def auth_headers(client):
    resp = client.post("/auth/login", json={"username": "admin", "password": "admin"})
    assert resp.status_code == 200, resp.text
    token = resp.json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture()
def local_connection(client, auth_headers):
    """A fresh Local Disk connection, isolated to its own subfolder per
    test (via a unique display_name/storage_path) so tests never see each
    other's files."""
    name = f"test-local-{uuid.uuid4().hex[:8]}"
    resp = client.post(
        "/connections",
        headers=auth_headers,
        json={
            "provider_key": "local",
            "display_name": name,
            "username": "",
            "password": "",
            "config": {"storage_path": str(_TEST_DATA_DIR / name)},
        },
    )
    assert resp.status_code == 201, resp.text
    conn = resp.json()
    yield conn
    client.delete(f"/connections/{conn['id']}", headers=auth_headers)


@pytest.fixture()
def conn_headers(auth_headers, local_connection):
    return {**auth_headers, "X-Connection-Id": local_connection["id"]}


@pytest.fixture()
def uploaded_file(client, conn_headers):
    resp = client.post(
        "/files",
        headers=conn_headers,
        files={"upload": ("hello.txt", b"hello world", "text/plain")},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()
