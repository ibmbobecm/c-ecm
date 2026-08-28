import pytest


@pytest.fixture(autouse=True)
def _reset_login_attempts():
    from app import auth

    auth._login_attempts.clear()
    yield
    auth._login_attempts.clear()


def test_login_lockout_after_repeated_failures(client):
    for _ in range(5):
        resp = client.post("/auth/login", json={"username": "admin", "password": "wrong"})
        assert resp.status_code == 401

    locked = client.post("/auth/login", json={"username": "admin", "password": "wrong"})
    assert locked.status_code == 429

    # Even the correct password is blocked during the lockout window.
    still_locked = client.post("/auth/login", json={"username": "admin", "password": "admin"})
    assert still_locked.status_code == 429


def test_successful_login_works_with_a_clean_attempt_counter(client):
    resp = client.post("/auth/login", json={"username": "admin", "password": "admin"})
    assert resp.status_code == 200


def test_failed_attempts_below_threshold_do_not_lock_out(client):
    for _ in range(4):
        resp = client.post("/auth/login", json={"username": "admin", "password": "wrong"})
        assert resp.status_code == 401
    ok = client.post("/auth/login", json={"username": "admin", "password": "admin"})
    assert ok.status_code == 200


def test_successful_login_clears_the_attempt_counter(client):
    for _ in range(4):
        client.post("/auth/login", json={"username": "admin", "password": "wrong"})
    ok = client.post("/auth/login", json={"username": "admin", "password": "admin"})
    assert ok.status_code == 200
    # The counter reset on success, so 4 more failures still shouldn't lock out.
    for _ in range(4):
        resp = client.post("/auth/login", json={"username": "admin", "password": "wrong"})
        assert resp.status_code == 401
    still_ok = client.post("/auth/login", json={"username": "admin", "password": "admin"})
    assert still_ok.status_code == 200
