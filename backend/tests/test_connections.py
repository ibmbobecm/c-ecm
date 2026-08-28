def test_duplicate_connection_name_rejected(client, auth_headers):
    body = {"provider_key": "local", "display_name": "dup-test", "username": "", "password": "", "config": {}}
    first = client.post("/connections", headers=auth_headers, json=body)
    assert first.status_code == 201
    try:
        second = client.post("/connections", headers=auth_headers, json=body)
        assert second.status_code == 409
        assert "already exists" in second.json()["detail"]
    finally:
        client.delete(f"/connections/{first.json()['id']}", headers=auth_headers)


def test_duplicate_connection_name_case_insensitive(client, auth_headers):
    body = {"provider_key": "local", "display_name": "CaseTest", "username": "", "password": "", "config": {}}
    first = client.post("/connections", headers=auth_headers, json=body)
    assert first.status_code == 201
    try:
        body2 = {**body, "display_name": "casetest"}
        second = client.post("/connections", headers=auth_headers, json=body2)
        assert second.status_code == 409
    finally:
        client.delete(f"/connections/{first.json()['id']}", headers=auth_headers)


def test_distinct_names_both_succeed(client, auth_headers):
    r1 = client.post("/connections", headers=auth_headers, json={"provider_key": "local", "display_name": "distinct-a", "username": "", "password": "", "config": {}})
    r2 = client.post("/connections", headers=auth_headers, json={"provider_key": "local", "display_name": "distinct-b", "username": "", "password": "", "config": {}})
    assert r1.status_code == 201
    assert r2.status_code == 201
    client.delete(f"/connections/{r1.json()['id']}", headers=auth_headers)
    client.delete(f"/connections/{r2.json()['id']}", headers=auth_headers)
