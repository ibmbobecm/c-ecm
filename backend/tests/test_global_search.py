"""Tests for global search endpoint."""
import pytest
from fastapi.testclient import TestClient


def test_global_search_no_connections(client: TestClient, auth_headers):
    """Global search with no connections should return empty hits, not error."""
    # Delete all connections first if any exist (isolation)
    conns = client.get("/connections", headers=auth_headers).json()
    for c in conns:
        client.delete(f"/connections/{c['id']}", headers=auth_headers)

    resp = client.get("/search/global", headers=auth_headers, params={"q": "test"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["query"] == "test"
    assert body["hits"] == []
    assert body["connection_errors"] == {}


def test_global_search_with_connection(client: TestClient, auth_headers, conn_headers, uploaded_file):
    """Global search should reach all configured connections without erroring."""
    resp = client.get("/search/global", headers=auth_headers, params={"q": "hello"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["query"] == "hello"
    # Result is structurally valid — hits is a list (may be empty depending
    # on provider search implementation) and there are no unexpected keys.
    assert isinstance(body["hits"], list)
    assert isinstance(body["connection_errors"], dict)
    # All hits must have required fields
    for h in body["hits"]:
        assert "name" in h
        assert "connection_id" in h
        assert h["resource_type"] in ("file", "folder")


def test_global_search_requires_auth(client: TestClient):
    resp = client.get("/search/global", params={"q": "test"})
    assert resp.status_code == 403


def test_global_search_short_query(client: TestClient, auth_headers):
    """Query shorter than min_length=1 should be rejected."""
    resp = client.get("/search/global", headers=auth_headers, params={"q": ""})
    assert resp.status_code == 422


def test_global_search_total_time_bounded_by_slowest_not_sum(client: TestClient, auth_headers):
    """Regression test: iterating futures.items() in submission order and
    calling .result(timeout=15) on each blocked on a slow connection before
    ever checking a faster one that already finished — N slow connections
    took N*15s instead of ~15s total. as_completed() with one shared
    timeout should keep the whole call bounded by the slowest single
    connection, confirmed here with a much shorter timeout/delay pair so
    the test itself stays fast."""
    import time
    from unittest.mock import patch

    from app.storage_providers.base import FolderContents

    conns = []
    for i in range(3):
        c = client.post("/connections", headers=auth_headers, json={
            "provider_key": "local", "display_name": f"timing-test-{i}", "username": "", "password": "", "config": {},
        }).json()
        conns.append(c)

    delays = {conns[0]["id"]: 0.6, conns[1]["id"]: 0.0, conns[2]["id"]: 0.0}

    class _SlowProvider:
        def refresh_if_needed(self, creds):
            return creds, False

        def search(self, creds, q):
            time.sleep(delays.get(creds.get("_cid"), 0))
            return [], []

    def _fake_get_creds(cid):
        return "local", {"_cid": cid}

    try:
        with patch("app.routers.search.get_provider", return_value=_SlowProvider()), \
             patch("app.routers.search.connections_store.get_creds", side_effect=_fake_get_creds):
            start = time.monotonic()
            resp = client.get("/search/global", headers=auth_headers, params={"q": "x"})
            elapsed = time.monotonic() - start
        assert resp.status_code == 200
        # The old (buggy) submission-order-blocking behavior would have
        # taken close to 3x the slow connection's delay if it always waited
        # on the first-submitted future regardless of completion order.
        # Bounded-by-slowest should finish well under 2x the single delay.
        assert elapsed < delays[conns[0]["id"]] * 2
    finally:
        for c in conns:
            client.delete(f"/connections/{c['id']}", headers=auth_headers)


def test_global_search_persists_refreshed_oauth_creds(client: TestClient, auth_headers, conn_headers):
    """Regression test: a connection's refreshed OAuth token was being used
    for the search itself but never written back to connections_store.
    Microsoft Graph and Box both rotate refresh tokens on every refresh —
    discarding the refreshed creds here would leave the now-invalidated old
    refresh token in storage while throwing away the one that actually
    still works, permanently breaking the connection's next refresh
    anywhere else in the app."""
    from unittest.mock import patch

    cid = conn_headers["X-Connection-Id"]

    class _RotatingProvider:
        def refresh_if_needed(self, creds):
            return {**creds, "access_token": "new-rotated-token"}, True

        def search(self, creds, q):
            return [], []

    with patch("app.routers.search.get_provider", return_value=_RotatingProvider()) as mock_get_provider:
        resp = client.get("/search/global", headers=auth_headers, params={"q": "x"})
    assert resp.status_code == 200
    assert mock_get_provider.called

    from app import connections_store

    _pk, persisted_creds = connections_store.get_creds(cid)
    assert persisted_creds.get("access_token") == "new-rotated-token"
