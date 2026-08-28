def test_permissions_not_supported_returns_501(client, conn_headers, uploaded_file):
    # Local Disk is one of the providers that hasn't opted into real
    # permissions yet — the base StorageProvider default should apply.
    resp = client.get(f"/resources/{uploaded_file['id']}/permissions", headers=conn_headers, params={"resource_type": "file"})
    assert resp.status_code == 501
    assert "supported" in resp.json()["detail"].lower()
