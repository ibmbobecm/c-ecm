def test_upload_records_created_event(client, conn_headers, uploaded_file, auth_headers, local_connection):
    resp = client.get("/activity", headers=auth_headers, params={"connection_id": local_connection["id"]})
    assert resp.status_code == 200
    events = resp.json()
    created = [e for e in events if e["event_type"] == "created" and e["resource_id"] == uploaded_file["id"]]
    assert len(created) == 1
    assert created[0]["resource_name"] == "hello.txt"
    assert created[0]["actor"] == "admin"
    assert created[0]["provider_key"] == "local"


def test_rename_and_delete_record_events_with_names(client, conn_headers, uploaded_file, auth_headers, local_connection):
    client.patch(f"/files/{uploaded_file['id']}", headers=conn_headers, json={"name": "renamed.txt"})
    client.delete(f"/files/{uploaded_file['id']}", headers=conn_headers)

    resp = client.get("/activity", headers=auth_headers, params={"connection_id": local_connection["id"], "resource_id": uploaded_file["id"]})
    events = {e["event_type"]: e for e in resp.json()}
    assert "renamed" in events
    assert events["renamed"]["resource_name"] == "renamed.txt"
    assert "deleted" in events
    # deleted event must carry the real name, not None — this was a bug
    # fixed in this session (name looked up before the delete call).
    assert events["deleted"]["resource_name"] == "renamed.txt"


def test_activity_filters_by_event_type(client, conn_headers, uploaded_file, auth_headers, local_connection):
    client.delete(f"/files/{uploaded_file['id']}", headers=conn_headers)
    resp = client.get("/activity", headers=auth_headers, params={"connection_id": local_connection["id"], "event_type": "deleted"})
    events = resp.json()
    assert all(e["event_type"] == "deleted" for e in events)
    assert any(e["resource_id"] == uploaded_file["id"] for e in events)


def test_activity_requires_app_session_not_connection(client, uploaded_file, local_connection):
    # No Authorization header at all -> 401/403, not 400 "no connection".
    resp = client.get("/activity")
    assert resp.status_code in (401, 403)
