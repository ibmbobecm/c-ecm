def test_upload_creates_a_notification(client, conn_headers, uploaded_file, auth_headers):
    resp = client.get("/notifications", headers=auth_headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["unread_count"] >= 1
    assert any("hello.txt" in n["message"] for n in body["notifications"])


def test_mark_read_and_mark_all_read(client, conn_headers, uploaded_file, auth_headers):
    before = client.get("/notifications", headers=auth_headers).json()
    target = next(n for n in before["notifications"] if "hello.txt" in n["message"])
    assert target["read_at"] is None

    client.post(f"/notifications/{target['id']}/read", headers=auth_headers)
    after_one = client.get("/notifications", headers=auth_headers, params={"unread_only": True}).json()
    assert not any(n["id"] == target["id"] for n in after_one["notifications"])

    client.post("/notifications/read-all", headers=auth_headers)
    after_all = client.get("/notifications", headers=auth_headers, params={"unread_only": True}).json()
    assert after_all["unread_count"] == 0
    assert after_all["notifications"] == []


def test_notification_message_names_the_connection(client, conn_headers, uploaded_file, auth_headers, local_connection):
    # Multiple connections can exist at once, so a bare "admin created
    # hello.txt" doesn't say which backend it happened on -- the message
    # must name the connection.
    body = client.get("/notifications", headers=auth_headers).json()
    target = next(n for n in body["notifications"] if "hello.txt" in n["message"])
    assert local_connection["display_name"] in target["message"]


def test_routine_read_events_are_not_notifiable(client, conn_headers, uploaded_file, auth_headers):
    # Downloading/viewing a file logs no activity event at all today, so it
    # must not spam a notification either.
    before = client.get("/notifications", headers=auth_headers).json()["unread_count"]
    client.get(f"/files/{uploaded_file['id']}/download", headers=conn_headers)
    after = client.get("/notifications", headers=auth_headers).json()["unread_count"]
    assert after == before
