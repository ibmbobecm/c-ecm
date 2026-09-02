"""Tests for AI Agents: folder/file-scoped chatbots with a public URL.

AI is disabled ("none" backend) by default in tests, same as every other
AI-router test in this suite — so most tests here exercise the CRUD/scope/
cleanup/rate-limit plumbing directly (which needs no LLM at all), and a
couple patch app.ai_service.llm_with_usage to exercise the actual answer
path without a real network call, matching test_ai_router.py's pattern of
flipping the backend via admin settings rather than hitting a live API.
"""
from unittest.mock import patch

from app import ai_service


def _create_agent(client, conn_headers, scope_type, resource_id, resource_name, name="Test Agent"):
    resp = client.post(
        "/ai-agents",
        headers=conn_headers,
        json={"name": name, "description": "A test agent", "scope_type": scope_type,
              "resource_id": resource_id, "resource_name": resource_name},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


def test_create_agent_for_file(client, conn_headers, uploaded_file):
    agent = _create_agent(client, conn_headers, "file", uploaded_file["id"], uploaded_file["name"])
    assert agent["scope_type"] == "file"
    assert agent["resource_id"] == uploaded_file["id"]
    assert agent["owner"] == "admin"
    assert agent["is_active"] is True
    assert "/public/chat/" in agent["chat_url"]


def test_list_for_resource_shows_created_agent(client, conn_headers, uploaded_file):
    agent = _create_agent(client, conn_headers, "file", uploaded_file["id"], uploaded_file["name"])
    resp = client.get("/ai-agents", headers=conn_headers, params={"resource_id": uploaded_file["id"]})
    assert resp.status_code == 200
    ids = [a["id"] for a in resp.json()]
    assert agent["id"] in ids


def test_chat_with_ai_disabled_returns_clear_message_not_an_error(client, conn_headers, uploaded_file):
    """AI is off by default in tests -- the chat endpoint should still
    respond 200 with an explanatory answer, not fail the request."""
    agent = _create_agent(client, conn_headers, "file", uploaded_file["id"], uploaded_file["name"])
    resp = client.post(f"/ai-agents/{agent['id']}/chat", headers=conn_headers, json={"question": "What is this?"})
    assert resp.status_code == 200
    assert "not configured" in resp.json()["answer"].lower()


def test_chat_answers_from_folder_scope_and_reports_tokens(client, conn_headers):
    """A folder-scoped agent should gather text from every file under the
    folder (not just one), and the chat response should carry real,
    non-estimated token usage when the backend reports it."""
    folder = client.post("/folders", headers=conn_headers, json={"name": "KB"}).json()
    client.post("/files", headers=conn_headers, files={"upload": ("a.txt", b"Sun is bright.", "text/plain")},
                data={"folder_id": folder["id"]})
    client.post("/files", headers=conn_headers, files={"upload": ("b.txt", b"Moon is calm.", "text/plain")},
                data={"folder_id": folder["id"]})
    agent = _create_agent(client, conn_headers, "folder", folder["id"], folder["name"])

    with patch.object(ai_service, "_BACKEND", "watsonx"), \
         patch.object(ai_service, "llm_with_usage", return_value=("The sun is bright and the moon is calm.", 42, False)):
        resp = client.post(f"/ai-agents/{agent['id']}/chat", headers=conn_headers, json={"question": "Describe the sun and moon."})
    assert resp.status_code == 200
    body = resp.json()
    assert body["answer"] == "The sun is bright and the moon is calm."
    assert body["tokens_used"] == 42
    assert body["tokens_estimated"] is False
    assert set(body["sources"]) == {"a.txt", "b.txt"}


def test_public_chat_works_without_auth_and_records_usage(client, conn_headers, uploaded_file):
    agent = _create_agent(client, conn_headers, "file", uploaded_file["id"], uploaded_file["name"])
    public_token = agent["chat_url"].rsplit("/", 1)[-1]

    with patch.object(ai_service, "_BACKEND", "watsonx"), \
         patch.object(ai_service, "llm_with_usage", return_value=("Hello world.", 10, False)):
        resp = client.post(f"/public/ai-agents/{public_token}/chat", json={"question": "Say hi"})
    assert resp.status_code == 200
    assert resp.json()["answer"] == "Hello world."

    report = client.get("/admin/ai-agents/report", headers=conn_headers).json()
    mine = next(a for a in report if a["id"] == agent["id"])
    assert mine["chat_count"] == 1
    assert mine["tokens_total"] == 10


def test_public_get_agent_info_never_leaks_owner_or_resource_id(client, conn_headers, uploaded_file):
    agent = _create_agent(client, conn_headers, "file", uploaded_file["id"], uploaded_file["name"])
    public_token = agent["chat_url"].rsplit("/", 1)[-1]
    resp = client.get(f"/public/ai-agents/{public_token}")
    assert resp.status_code == 200
    body = resp.json()
    assert set(body.keys()) == {"name", "description"}


def test_public_chat_404_for_unknown_or_deactivated_token(client, conn_headers, uploaded_file):
    resp = client.post("/public/ai-agents/not-a-real-token/chat", json={"question": "hi"})
    assert resp.status_code == 404

    agent = _create_agent(client, conn_headers, "file", uploaded_file["id"], uploaded_file["name"])
    public_token = agent["chat_url"].rsplit("/", 1)[-1]
    client.patch(f"/ai-agents/{agent['id']}", headers=conn_headers, json={"is_active": False})
    resp = client.post(f"/public/ai-agents/{public_token}/chat", json={"question": "hi"})
    assert resp.status_code == 404


def test_public_chat_rate_limited(client, conn_headers, uploaded_file):
    agent = _create_agent(client, conn_headers, "file", uploaded_file["id"], uploaded_file["name"])
    public_token = agent["chat_url"].rsplit("/", 1)[-1]
    with patch.object(ai_service, "_BACKEND", "watsonx"), \
         patch.object(ai_service, "llm_with_usage", return_value=("ok", 1, False)):
        statuses = [
            client.post(f"/public/ai-agents/{public_token}/chat", json={"question": "hi"}).status_code
            for _ in range(25)
        ]
    assert 429 in statuses


def test_only_owner_or_admin_can_manage_agent(client, auth_headers, conn_headers, uploaded_file):
    agent = _create_agent(client, conn_headers, "file", uploaded_file["id"], uploaded_file["name"])
    client.post("/users", headers=auth_headers, json={
        "username": "other_user_agents", "password": "otherpass123", "display_name": "Other", "roles": ["editor"],
    })
    try:
        login = client.post("/auth/login", json={"username": "other_user_agents", "password": "otherpass123"})
        other_headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
        resp = client.patch(f"/ai-agents/{agent['id']}", headers=other_headers, json={"name": "Hijacked"})
        assert resp.status_code == 403
        resp = client.delete(f"/ai-agents/{agent['id']}", headers=other_headers)
        assert resp.status_code == 403
    finally:
        for u in client.get("/users", headers=auth_headers).json():
            if u["username"] == "other_user_agents":
                client.delete(f"/users/{u['id']}", headers=auth_headers)


def test_permanent_delete_of_scoped_file_removes_its_agent(client, conn_headers, uploaded_file):
    agent = _create_agent(client, conn_headers, "file", uploaded_file["id"], uploaded_file["name"])
    client.delete(f"/files/{uploaded_file['id']}", headers=conn_headers)  # trash
    resp = client.delete(f"/files/{uploaded_file['id']}/permanent", headers=conn_headers)
    assert resp.status_code == 204
    resp = client.get(f"/ai-agents/{agent['id']}", headers=conn_headers)
    assert resp.status_code == 404


def test_admin_report_requires_admin_role(client, auth_headers, conn_headers):
    client.post("/users", headers=auth_headers, json={
        "username": "viewer_agents_report", "password": "viewerpass123", "display_name": "Viewer", "roles": ["viewer"],
    })
    try:
        login = client.post("/auth/login", json={"username": "viewer_agents_report", "password": "viewerpass123"})
        viewer_headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
        resp = client.get("/admin/ai-agents/report", headers=viewer_headers)
        assert resp.status_code == 403
    finally:
        for u in client.get("/users", headers=auth_headers).json():
            if u["username"] == "viewer_agents_report":
                client.delete(f"/users/{u['id']}", headers=auth_headers)


def test_agent_urls_include_demo_site(client, conn_headers, uploaded_file):
    agent = _create_agent(client, conn_headers, "file", uploaded_file["id"], uploaded_file["name"])
    assert "/public/demo/" in agent["demo_url"]
    assert agent["demo_download_url"] == agent["demo_url"] + "/download"


def test_public_demo_site_renders_and_reflects_customization(client, conn_headers, uploaded_file):
    agent = _create_agent(client, conn_headers, "file", uploaded_file["id"], uploaded_file["name"], name="Support Bot")
    public_token = agent["chat_url"].rsplit("/", 1)[-1]

    resp = client.get(f"/public/demo/{public_token}")
    assert resp.status_code == 200
    assert "Support Bot" in resp.text  # falls back to the agent's own name

    save = client.patch(f"/ai-agents/{agent['id']}/site", headers=conn_headers, json={"headline": "Need help fast?"})
    assert save.status_code == 200
    assert save.json()["headline"] == "Need help fast?"

    resp = client.get(f"/public/demo/{public_token}")
    assert "Need help fast?" in resp.text


def test_demo_site_download_sets_content_disposition(client, conn_headers, uploaded_file):
    agent = _create_agent(client, conn_headers, "file", uploaded_file["id"], uploaded_file["name"], name="Log Bot")
    public_token = agent["chat_url"].rsplit("/", 1)[-1]
    resp = client.get(f"/public/demo/{public_token}/download")
    assert resp.status_code == 200
    assert "attachment" in resp.headers["content-disposition"]
    assert "log-bot" in resp.headers["content-disposition"]


def test_site_partial_update_does_not_clear_other_fields(client, conn_headers, uploaded_file):
    agent = _create_agent(client, conn_headers, "file", uploaded_file["id"], uploaded_file["name"])
    client.patch(f"/ai-agents/{agent['id']}/site", headers=conn_headers,
                 json={"headline": "Hello", "subheadline": "World"})
    resp = client.patch(f"/ai-agents/{agent['id']}/site", headers=conn_headers, json={"accent_color": "#123456"})
    body = resp.json()
    assert body["headline"] == "Hello"
    assert body["subheadline"] == "World"
    assert body["accent_color"] == "#123456"


def test_site_editing_is_owner_or_admin_only(client, auth_headers, conn_headers, uploaded_file):
    agent = _create_agent(client, conn_headers, "file", uploaded_file["id"], uploaded_file["name"])
    client.post("/users", headers=auth_headers, json={
        "username": "other_user_site", "password": "otherpass123", "display_name": "Other", "roles": ["editor"],
    })
    try:
        login = client.post("/auth/login", json={"username": "other_user_site", "password": "otherpass123"})
        other_headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
        resp = client.patch(f"/ai-agents/{agent['id']}/site", headers=other_headers, json={"headline": "Hijacked"})
        assert resp.status_code == 403
    finally:
        for u in client.get("/users", headers=auth_headers).json():
            if u["username"] == "other_user_site":
                client.delete(f"/users/{u['id']}", headers=auth_headers)


def test_deleting_agent_removes_its_site_customization(client, conn_headers, uploaded_file):
    agent = _create_agent(client, conn_headers, "file", uploaded_file["id"], uploaded_file["name"])
    client.patch(f"/ai-agents/{agent['id']}/site", headers=conn_headers, json={"headline": "Custom"})
    client.delete(f"/ai-agents/{agent['id']}", headers=conn_headers)

    from app import ai_agents_store
    assert ai_agents_store.get_site(agent["id"]) is None


def test_edit_token_shows_admin_bar_on_public_demo_and_lets_it_save(client, conn_headers, uploaded_file):
    """The whole point of the edit token: the owner opens the public demo
    page from the authenticated app and gets a WordPress-style admin bar
    they can edit and save from — with no C-ECM login needed on that page
    itself, and without a real session JWT ever appearing in the URL."""
    agent = _create_agent(client, conn_headers, "file", uploaded_file["id"], uploaded_file["name"], name="Bar Bot")
    public_token = agent["chat_url"].rsplit("/", 1)[-1]

    minted = client.post(f"/ai-agents/{agent['id']}/edit-token", headers=conn_headers)
    assert minted.status_code == 200
    edit_token = minted.json()["edit_token"]
    assert edit_token

    # Without the token: plain page, no admin bar.
    plain = client.get(f"/public/demo/{public_token}")
    assert "cecm-admin-bar" not in plain.text

    # With the token: admin bar present.
    with_bar = client.get(f"/public/demo/{public_token}", params={"edit_token": edit_token})
    assert "cecm-admin-bar" in with_bar.text

    # The admin bar's own save call works with just the edit token, no auth header at all.
    save = client.patch(f"/public/ai-agents/{public_token}/site", json={
        "edit_token": edit_token, "headline": "Saved from the bar",
    })
    assert save.status_code == 200
    assert save.json()["headline"] == "Saved from the bar"

    resp = client.get(f"/public/demo/{public_token}")
    assert "Saved from the bar" in resp.text


def test_edit_token_never_appears_in_a_download(client, conn_headers, uploaded_file):
    """The dedicated /download endpoint doesn't even accept edit_token as a
    parameter — the admin bar is unconditionally absent from a download,
    by construction, not merely by omitting the query param."""
    agent = _create_agent(client, conn_headers, "file", uploaded_file["id"], uploaded_file["name"])
    public_token = agent["chat_url"].rsplit("/", 1)[-1]
    client.post(f"/ai-agents/{agent['id']}/edit-token", headers=conn_headers)

    resp = client.get(f"/public/demo/{public_token}/download")
    import zipfile
    import io
    zf = zipfile.ZipFile(io.BytesIO(resp.content))
    assert "cecm-admin-bar" not in zf.read("index.html").decode()


def test_invalid_or_wrong_agent_edit_token_rejected(client, conn_headers, uploaded_file):
    agent_a = _create_agent(client, conn_headers, "file", uploaded_file["id"], uploaded_file["name"], name="Agent A")
    resp = client.post("/files", headers=conn_headers, files={"upload": ("b.txt", b"b", "text/plain")})
    file_b = resp.json()
    agent_b = _create_agent(client, conn_headers, "file", file_b["id"], file_b["name"], name="Agent B")

    token_for_a = client.post(f"/ai-agents/{agent_a['id']}/edit-token", headers=conn_headers).json()["edit_token"]
    public_token_b = agent_b["chat_url"].rsplit("/", 1)[-1]

    # A token minted for agent A must not work against agent B.
    save = client.patch(f"/public/ai-agents/{public_token_b}/site", json={
        "edit_token": token_for_a, "headline": "Hijacked",
    })
    assert save.status_code == 403

    # A garbage token is rejected the same way.
    save = client.patch(f"/public/ai-agents/{public_token_b}/site", json={
        "edit_token": "not-a-real-token", "headline": "Hijacked",
    })
    assert save.status_code == 403


def test_edit_token_minting_is_owner_or_admin_only(client, auth_headers, conn_headers, uploaded_file):
    agent = _create_agent(client, conn_headers, "file", uploaded_file["id"], uploaded_file["name"])
    client.post("/users", headers=auth_headers, json={
        "username": "other_user_edit_token", "password": "otherpass123", "display_name": "Other", "roles": ["editor"],
    })
    try:
        login = client.post("/auth/login", json={"username": "other_user_edit_token", "password": "otherpass123"})
        other_headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
        resp = client.post(f"/ai-agents/{agent['id']}/edit-token", headers=other_headers)
        assert resp.status_code == 403
    finally:
        for u in client.get("/users", headers=auth_headers).json():
            if u["username"] == "other_user_edit_token":
                client.delete(f"/users/{u['id']}", headers=auth_headers)


# --- pages / posts / contact / AI site generation / static export -------

def test_page_crud_and_slug_uniqueness(client, conn_headers, uploaded_file):
    agent = _create_agent(client, conn_headers, "file", uploaded_file["id"], uploaded_file["name"])
    p1 = client.post(f"/ai-agents/{agent['id']}/site/pages", headers=conn_headers, json={"title": "Services"}).json()
    p2 = client.post(f"/ai-agents/{agent['id']}/site/pages", headers=conn_headers, json={"title": "Services"}).json()
    assert p1["slug"] == "services"
    assert p2["slug"] == "services-2"  # same title twice must not collide

    listed = client.get(f"/ai-agents/{agent['id']}/site/pages", headers=conn_headers).json()
    assert {p["id"] for p in listed} == {p1["id"], p2["id"]}

    updated = client.patch(f"/ai-agents/{agent['id']}/site/pages/{p1['id']}", headers=conn_headers,
                            json={"content": "New content"}).json()
    assert updated["content"] == "New content"
    assert updated["title"] == "Services"  # untouched field preserved

    assert client.delete(f"/ai-agents/{agent['id']}/site/pages/{p1['id']}", headers=conn_headers).status_code == 204
    listed = client.get(f"/ai-agents/{agent['id']}/site/pages", headers=conn_headers).json()
    assert [p["id"] for p in listed] == [p2["id"]]


def test_post_crud(client, conn_headers, uploaded_file):
    agent = _create_agent(client, conn_headers, "file", uploaded_file["id"], uploaded_file["name"])
    post = client.post(f"/ai-agents/{agent['id']}/site/posts", headers=conn_headers,
                        json={"title": "Hello world", "excerpt": "intro", "content": "full text"}).json()
    assert post["slug"] == "hello-world"

    updated = client.patch(f"/ai-agents/{agent['id']}/site/posts/{post['id']}", headers=conn_headers,
                            json={"excerpt": "updated intro"}).json()
    assert updated["excerpt"] == "updated intro"
    assert updated["content"] == "full text"

    assert client.delete(f"/ai-agents/{agent['id']}/site/posts/{post['id']}", headers=conn_headers).status_code == 204
    assert client.get(f"/ai-agents/{agent['id']}/site/posts", headers=conn_headers).json() == []


def test_pages_and_posts_are_owner_or_admin_only(client, auth_headers, conn_headers, uploaded_file):
    agent = _create_agent(client, conn_headers, "file", uploaded_file["id"], uploaded_file["name"])
    client.post("/users", headers=auth_headers, json={
        "username": "other_user_pages", "password": "otherpass123", "display_name": "Other", "roles": ["editor"],
    })
    try:
        login = client.post("/auth/login", json={"username": "other_user_pages", "password": "otherpass123"})
        other_headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
        resp = client.post(f"/ai-agents/{agent['id']}/site/pages", headers=other_headers, json={"title": "Hijack"})
        assert resp.status_code == 403
        resp = client.post(f"/ai-agents/{agent['id']}/site/posts", headers=other_headers, json={"title": "Hijack"})
        assert resp.status_code == 403
    finally:
        for u in client.get("/users", headers=auth_headers).json():
            if u["username"] == "other_user_pages":
                client.delete(f"/users/{u['id']}", headers=auth_headers)


def test_contact_fields_survive_headline_only_update(client, conn_headers, uploaded_file):
    """Regression guard for the exact bug the merge_site_update helper
    exists to prevent: adding contact_* fields must not let an old-style
    partial update (just headline) wipe them back to null."""
    agent = _create_agent(client, conn_headers, "file", uploaded_file["id"], uploaded_file["name"])
    client.patch(f"/ai-agents/{agent['id']}/site", headers=conn_headers, json={
        "contact_email": "hi@example.com", "contact_note": "Reach out",
    })
    resp = client.patch(f"/ai-agents/{agent['id']}/site", headers=conn_headers, json={"headline": "New headline"})
    body = resp.json()
    assert body["headline"] == "New headline"
    assert body["contact_email"] == "hi@example.com"
    assert body["contact_note"] == "Reach out"


def test_multi_page_site_renders_nav_and_all_pages(client, conn_headers, uploaded_file):
    agent = _create_agent(client, conn_headers, "file", uploaded_file["id"], uploaded_file["name"], name="Nav Bot")
    public_token = agent["chat_url"].rsplit("/", 1)[-1]
    client.post(f"/ai-agents/{agent['id']}/site/pages", headers=conn_headers, json={"title": "Services", "content": "We do X."})
    client.post(f"/ai-agents/{agent['id']}/site/posts", headers=conn_headers, json={"title": "First post", "content": "Body."})
    client.patch(f"/ai-agents/{agent['id']}/site", headers=conn_headers, json={"contact_email": "hi@example.com"})

    home = client.get(f"/public/demo/{public_token}")
    assert home.status_code == 200
    assert "Services" in home.text and "Blog" in home.text and "Contact" in home.text

    assert client.get(f"/public/demo/{public_token}/page/services").status_code == 200
    assert client.get(f"/public/demo/{public_token}/blog").status_code == 200
    assert client.get(f"/public/demo/{public_token}/blog/first-post").status_code == 200
    assert client.get(f"/public/demo/{public_token}/contact").status_code == 200
    assert "hi@example.com" in client.get(f"/public/demo/{public_token}/contact").text
    assert client.get(f"/public/demo/{public_token}/page/does-not-exist").status_code == 404


def test_static_site_download_is_a_zip_with_flat_relative_links(client, conn_headers, uploaded_file):
    agent = _create_agent(client, conn_headers, "file", uploaded_file["id"], uploaded_file["name"])
    public_token = agent["chat_url"].rsplit("/", 1)[-1]
    client.post(f"/ai-agents/{agent['id']}/site/pages", headers=conn_headers, json={"title": "Services", "content": "X"})
    client.post(f"/ai-agents/{agent['id']}/site/posts", headers=conn_headers, json={"title": "A post", "content": "Y"})
    client.patch(f"/ai-agents/{agent['id']}/site", headers=conn_headers, json={"contact_phone": "555-1234"})

    resp = client.get(f"/public/demo/{public_token}/download")
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "application/zip"
    assert ".zip" in resp.headers["content-disposition"]

    import io
    import zipfile
    zf = zipfile.ZipFile(io.BytesIO(resp.content))
    names = set(zf.namelist())
    assert names == {"index.html", "page-services.html", "blog.html", "blog-a-post.html", "contact.html"}
    index_html = zf.read("index.html").decode()
    assert 'href="page-services.html"' in index_html
    assert 'href="blog.html"' in index_html
    assert "cecm-admin-bar" not in index_html  # never shipped in a download


def test_generate_site_draft_from_knowledge_base(client, conn_headers, uploaded_file):
    agent = _create_agent(client, conn_headers, "file", uploaded_file["id"], uploaded_file["name"])
    fake_json = (
        '{"headline": "Great Headline", "subheadline": "Sub", "body": "About us.", '
        '"contact_note": "Reach out", '
        '"pages": [{"title": "Services", "content": "We do things."}], '
        '"posts": [{"title": "A Post", "excerpt": "Ex", "content": "Full content."}]}'
    )
    with patch.object(ai_service, "_BACKEND", "watsonx"), \
         patch.object(ai_service, "llm_with_usage", return_value=(fake_json, 900, False)):
        resp = client.post(f"/ai-agents/{agent['id']}/site/generate", headers=conn_headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["headline"] == "Great Headline"
    assert body["pages"] == [{"title": "Services", "content": "We do things."}]
    assert body["posts"] == [{"title": "A Post", "excerpt": "Ex", "content": "Full content."}]
    assert body["tokens_used"] == 900
    assert body["sources"] == ["hello.txt"]

    # The draft is never auto-saved — the agent's own site config is untouched.
    site = client.get(f"/ai-agents/{agent['id']}/site", headers=conn_headers).json()
    assert site["headline"] is None


def test_generate_site_draft_handles_truncated_json_gracefully(client, conn_headers, uploaded_file):
    """Regression test: a real live call once truncated mid-JSON because
    max_tokens defaulted to 512 for this much-larger-than-chat response.
    Whatever the cause, invalid JSON must surface as a clear 422, not a
    500 or a silently-wrong draft."""
    agent = _create_agent(client, conn_headers, "file", uploaded_file["id"], uploaded_file["name"])
    truncated = '{"headline": "Cut off mid-way", "pages": [{"title": "Serv'
    with patch.object(ai_service, "_BACKEND", "watsonx"), \
         patch.object(ai_service, "llm_with_usage", return_value=(truncated, 512, False)):
        resp = client.post(f"/ai-agents/{agent['id']}/site/generate", headers=conn_headers)
    assert resp.status_code == 422


def test_generate_site_draft_retries_once_when_model_responds_conversationally(client, conn_headers, uploaded_file):
    """Regression test: a real live run against a technical installation-
    guide PDF had the model ignore "respond with only JSON" entirely and
    answer with a conversational summary instead ("The document you
    provided appears to be an excerpt from..."), with no '{' or '}'
    anywhere in the response -- silently failing both the automatic
    first-open generation and the manual "Regenerate with AI" button.
    One automatic retry with a more forceful correction must recover a
    valid draft instead of surfacing an error immediately."""
    agent = _create_agent(client, conn_headers, "file", uploaded_file["id"], uploaded_file["name"])
    conversational = "This document appears to be an installation guide. Here is a summary of its contents..."
    fake_json = '{"headline": "Recovered", "subheadline": "S", "body": "B", "contact_note": "C", "pages": [], "posts": []}'
    with patch.object(ai_service, "_BACKEND", "watsonx"), \
         patch.object(ai_service, "llm_with_usage", side_effect=[(conversational, 300, False), (fake_json, 400, False)]) as mock_llm:
        resp = client.post(f"/ai-agents/{agent['id']}/site/generate", headers=conn_headers)
    assert resp.status_code == 200
    assert resp.json()["headline"] == "Recovered"
    assert mock_llm.call_count == 2
    assert resp.json()["tokens_used"] == 700  # both attempts' usage combined


def test_generate_site_draft_requests_a_larger_max_tokens_than_chat(client, conn_headers, uploaded_file):
    """A full site draft needs much more completion room than a short chat
    answer — assert the call site actually asks for it, so this can't
    silently regress back to the 512-token default that truncated the
    very first live response."""
    agent = _create_agent(client, conn_headers, "file", uploaded_file["id"], uploaded_file["name"])
    with patch.object(ai_service, "_BACKEND", "watsonx"), \
         patch.object(ai_service, "llm_with_usage", return_value=("{}", 10, False)) as mock_llm:
        client.post(f"/ai-agents/{agent['id']}/site/generate", headers=conn_headers)
    assert mock_llm.call_args.kwargs.get("max_tokens", 512) > 512


def test_generate_site_draft_is_owner_or_admin_only(client, auth_headers, conn_headers, uploaded_file):
    agent = _create_agent(client, conn_headers, "file", uploaded_file["id"], uploaded_file["name"])
    client.post("/users", headers=auth_headers, json={
        "username": "other_user_generate", "password": "otherpass123", "display_name": "Other", "roles": ["editor"],
    })
    try:
        login = client.post("/auth/login", json={"username": "other_user_generate", "password": "otherpass123"})
        other_headers = {**{"Authorization": f"Bearer {login.json()['access_token']}"}, "X-Connection-Id": conn_headers["X-Connection-Id"]}
        resp = client.post(f"/ai-agents/{agent['id']}/site/generate", headers=other_headers)
        assert resp.status_code == 403
    finally:
        for u in client.get("/users", headers=auth_headers).json():
            if u["username"] == "other_user_generate":
                client.delete(f"/users/{u['id']}", headers=auth_headers)


def test_deleting_agent_removes_its_pages_and_posts(client, conn_headers, uploaded_file):
    agent = _create_agent(client, conn_headers, "file", uploaded_file["id"], uploaded_file["name"])
    page = client.post(f"/ai-agents/{agent['id']}/site/pages", headers=conn_headers, json={"title": "P"}).json()
    post = client.post(f"/ai-agents/{agent['id']}/site/posts", headers=conn_headers, json={"title": "Q"}).json()
    client.delete(f"/ai-agents/{agent['id']}", headers=conn_headers)

    from app import ai_agents_store
    assert ai_agents_store.get_page(agent["id"], page["id"]) is None
    assert ai_agents_store.get_post(agent["id"], post["id"]) is None


# --- the live admin bar: full CRUD via edit_token, no C-ECM login at all ---

def _mint_edit_token(client, conn_headers, agent_id):
    return client.post(f"/ai-agents/{agent_id}/edit-token", headers=conn_headers).json()["edit_token"]


def test_admin_bar_shows_all_four_panels_and_embeds_pages_and_posts(client, conn_headers, uploaded_file):
    agent = _create_agent(client, conn_headers, "file", uploaded_file["id"], uploaded_file["name"])
    public_token = agent["chat_url"].rsplit("/", 1)[-1]
    client.post(f"/ai-agents/{agent['id']}/site/pages", headers=conn_headers, json={"title": "Services"})
    edit_token = _mint_edit_token(client, conn_headers, agent["id"])

    resp = client.get(f"/public/demo/{public_token}", params={"edit_token": edit_token})
    assert resp.status_code == 200
    for panel in ('data-panel="customize"', 'data-panel="pages"', 'data-panel="blog"', 'data-panel="generate"'):
        assert panel in resp.text
    assert '"title": "Services"' in resp.text  # pages baked in as data for the Pages panel


def test_admin_bar_panel_query_param_sets_initial_panel(client, conn_headers, uploaded_file):
    agent = _create_agent(client, conn_headers, "file", uploaded_file["id"], uploaded_file["name"])
    public_token = agent["chat_url"].rsplit("/", 1)[-1]
    edit_token = _mint_edit_token(client, conn_headers, agent["id"])

    resp = client.get(f"/public/demo/{public_token}", params={"edit_token": edit_token, "panel": "pages"})
    assert 'initialPanel = "pages"' in resp.text

    # An unrecognized panel name must not be reflected into the page unescaped.
    resp = client.get(f"/public/demo/{public_token}", params={"edit_token": edit_token, "panel": "not-a-real-panel"})
    assert 'initialPanel = ""' in resp.text


def test_public_pages_crud_via_edit_token_no_auth_header(client, conn_headers, uploaded_file):
    agent = _create_agent(client, conn_headers, "file", uploaded_file["id"], uploaded_file["name"])
    public_token = agent["chat_url"].rsplit("/", 1)[-1]
    edit_token = _mint_edit_token(client, conn_headers, agent["id"])

    created = client.post(f"/public/ai-agents/{public_token}/site/pages",
                           json={"edit_token": edit_token, "title": "Services", "content": "We do X."})
    assert created.status_code == 201
    page = created.json()
    assert page["slug"] == "services"

    updated = client.patch(f"/public/ai-agents/{public_token}/site/pages/{page['id']}",
                            json={"edit_token": edit_token, "content": "Updated."})
    assert updated.status_code == 200
    assert updated.json()["content"] == "Updated."

    deleted = client.delete(f"/public/ai-agents/{public_token}/site/pages/{page['id']}",
                             params={"edit_token": edit_token})
    assert deleted.status_code == 204
    from app import ai_agents_store
    assert ai_agents_store.get_page(agent["id"], page["id"]) is None


def test_public_posts_crud_via_edit_token_no_auth_header(client, conn_headers, uploaded_file):
    agent = _create_agent(client, conn_headers, "file", uploaded_file["id"], uploaded_file["name"])
    public_token = agent["chat_url"].rsplit("/", 1)[-1]
    edit_token = _mint_edit_token(client, conn_headers, agent["id"])

    created = client.post(f"/public/ai-agents/{public_token}/site/posts",
                           json={"edit_token": edit_token, "title": "Hello", "excerpt": "Hi", "content": "Body"})
    assert created.status_code == 201
    post = created.json()

    updated = client.patch(f"/public/ai-agents/{public_token}/site/posts/{post['id']}",
                            json={"edit_token": edit_token, "excerpt": "Updated excerpt"})
    assert updated.json()["excerpt"] == "Updated excerpt"

    deleted = client.delete(f"/public/ai-agents/{public_token}/site/posts/{post['id']}",
                             params={"edit_token": edit_token})
    assert deleted.status_code == 204


def test_public_pages_and_posts_require_a_valid_edit_token(client, conn_headers, uploaded_file):
    agent = _create_agent(client, conn_headers, "file", uploaded_file["id"], uploaded_file["name"])
    public_token = agent["chat_url"].rsplit("/", 1)[-1]

    resp = client.post(f"/public/ai-agents/{public_token}/site/pages",
                        json={"edit_token": "not-a-real-token", "title": "Hijack"})
    assert resp.status_code == 403
    resp = client.post(f"/public/ai-agents/{public_token}/site/posts",
                        json={"edit_token": "not-a-real-token", "title": "Hijack"})
    assert resp.status_code == 403


def test_public_generate_and_publish_creates_everything_in_one_call(client, conn_headers, uploaded_file):
    """The live admin bar's "Generate & publish" button — unlike the
    authenticated draft-then-apply endpoint, this one applies the entire
    draft immediately (there's nowhere on the live page to build a
    per-item review UI), reachable with only the edit_token, no login."""
    agent = _create_agent(client, conn_headers, "file", uploaded_file["id"], uploaded_file["name"])
    public_token = agent["chat_url"].rsplit("/", 1)[-1]
    edit_token = _mint_edit_token(client, conn_headers, agent["id"])

    fake_json = (
        '{"headline": "H", "subheadline": "S", "body": "B", "contact_note": "C", '
        '"pages": [{"title": "Services", "content": "X"}], '
        '"posts": [{"title": "Post One", "excerpt": "E", "content": "Y"}, '
        '{"title": "Post Two", "excerpt": "E2", "content": "Z"}]}'
    )
    with patch.object(ai_service, "_BACKEND", "watsonx"), \
         patch.object(ai_service, "llm_with_usage", return_value=(fake_json, 777, False)):
        resp = client.post(f"/public/ai-agents/{public_token}/site/generate", params={"edit_token": edit_token})
    assert resp.status_code == 200
    body = resp.json()
    assert body == {"pages_created": 1, "posts_created": 2, "tokens_used": 777, "tokens_estimated": False}

    site = client.get(f"/ai-agents/{agent['id']}/site", headers=conn_headers).json()
    assert site["headline"] == "H"
    pages = client.get(f"/ai-agents/{agent['id']}/site/pages", headers=conn_headers).json()
    assert [p["title"] for p in pages] == ["Services"]
    posts = client.get(f"/ai-agents/{agent['id']}/site/posts", headers=conn_headers).json()
    assert {p["title"] for p in posts} == {"Post One", "Post Two"}

    # The freshly-published content shows up in the live nav immediately.
    home = client.get(f"/public/demo/{public_token}")
    assert "Services" in home.text and "Blog" in home.text


def test_public_generate_and_publish_requires_a_valid_edit_token(client, conn_headers, uploaded_file):
    agent = _create_agent(client, conn_headers, "file", uploaded_file["id"], uploaded_file["name"])
    public_token = agent["chat_url"].rsplit("/", 1)[-1]
    resp = client.post(f"/public/ai-agents/{public_token}/site/generate", params={"edit_token": "not-a-real-token"})
    assert resp.status_code == 403


def test_first_open_with_edit_token_auto_generates_the_site(client, conn_headers, uploaded_file):
    """The admin's very first "Open test site" click should populate the
    whole site automatically from the knowledge base -- no separate
    manual "Generate" click required."""
    agent = _create_agent(client, conn_headers, "file", uploaded_file["id"], uploaded_file["name"])
    public_token = agent["chat_url"].rsplit("/", 1)[-1]
    edit_token = _mint_edit_token(client, conn_headers, agent["id"])

    fake_json = (
        '{"headline": "Auto Headline", "subheadline": "Auto Sub", "body": "Auto body.", '
        '"contact_note": "Reach out", "seo_description": "A distinct SEO line.", '
        '"footer_tagline": "Auto tagline.", '
        '"pages": [{"title": "Services", "content": "X"}], "posts": []}'
    )
    with patch.object(ai_service, "_BACKEND", "watsonx"), \
         patch.object(ai_service, "llm_with_usage", return_value=(fake_json, 500, False)) as mock_llm:
        resp = client.get(f"/public/demo/{public_token}", params={"edit_token": edit_token})
    assert resp.status_code == 200
    assert mock_llm.call_count == 1
    assert "Auto Headline" in resp.text

    site = client.get(f"/ai-agents/{agent['id']}/site", headers=conn_headers).json()
    assert site["headline"] == "Auto Headline"
    assert site["seo_description"] == "A distinct SEO line."
    assert site["footer_tagline"] == "Auto tagline."
    pages = client.get(f"/ai-agents/{agent['id']}/site/pages", headers=conn_headers).json()
    assert [p["title"] for p in pages] == ["Services"]


def test_second_open_does_not_regenerate(client, conn_headers, uploaded_file):
    """Auto-generation must fire at most once -- any saved content already
    makes the site no longer "empty", so a second visit (or a page
    refresh) can never silently overwrite real admin edits."""
    agent = _create_agent(client, conn_headers, "file", uploaded_file["id"], uploaded_file["name"])
    public_token = agent["chat_url"].rsplit("/", 1)[-1]
    edit_token = _mint_edit_token(client, conn_headers, agent["id"])

    fake_json = '{"headline": "Once", "subheadline": "S", "body": "B", "contact_note": "C", "pages": [], "posts": []}'
    with patch.object(ai_service, "_BACKEND", "watsonx"), \
         patch.object(ai_service, "llm_with_usage", return_value=(fake_json, 500, False)) as mock_llm:
        client.get(f"/public/demo/{public_token}", params={"edit_token": edit_token})
        client.get(f"/public/demo/{public_token}", params={"edit_token": edit_token})
    assert mock_llm.call_count == 1


def test_visitor_without_edit_token_never_triggers_auto_generate(client, conn_headers, uploaded_file):
    """A random visitor to the public site (no edit_token at all) must
    never trigger an LLM call or a write -- only the admin's own edit link
    can kick off generation."""
    agent = _create_agent(client, conn_headers, "file", uploaded_file["id"], uploaded_file["name"])
    public_token = agent["chat_url"].rsplit("/", 1)[-1]

    with patch.object(ai_service, "_BACKEND", "watsonx"), \
         patch.object(ai_service, "llm_with_usage") as mock_llm:
        resp = client.get(f"/public/demo/{public_token}")
    assert resp.status_code == 200
    mock_llm.assert_not_called()


def test_seo_description_and_footer_tagline_round_trip_and_render(client, conn_headers, uploaded_file):
    agent = _create_agent(client, conn_headers, "file", uploaded_file["id"], uploaded_file["name"])
    public_token = agent["chat_url"].rsplit("/", 1)[-1]
    updated = client.patch(f"/ai-agents/{agent['id']}/site", headers=conn_headers, json={
        "seo_description": "Distinct search-result copy.",
        "footer_tagline": "Short footer line.",
    })
    assert updated.json()["seo_description"] == "Distinct search-result copy."
    assert updated.json()["footer_tagline"] == "Short footer line."

    home = client.get(f"/public/demo/{public_token}")
    assert 'content="Distinct search-result copy."' in home.text
    assert "Short footer line." in home.text


# --- the floating widget's "contact us" lead-capture flow ------------------

def test_home_page_embeds_the_contact_pill_and_leads_endpoint(client, conn_headers, uploaded_file):
    agent = _create_agent(client, conn_headers, "file", uploaded_file["id"], uploaded_file["name"])
    public_token = agent["chat_url"].rsplit("/", 1)[-1]
    home = client.get(f"/public/demo/{public_token}")
    assert 'id="cecm-contact-pill"' in home.text
    assert f"/public/ai-agents/{public_token}/leads" in home.text


def test_public_lead_capture_no_auth_required(client, conn_headers, uploaded_file):
    agent = _create_agent(client, conn_headers, "file", uploaded_file["id"], uploaded_file["name"])
    public_token = agent["chat_url"].rsplit("/", 1)[-1]

    resp = client.post(f"/public/ai-agents/{public_token}/leads", json={
        "email": "visitor@example.com", "phone": "555-1234", "message": "Tell me more about pricing.",
    })
    assert resp.status_code == 201
    body = resp.json()
    assert body["email"] == "visitor@example.com"
    assert body["phone"] == "555-1234"
    assert body["message"] == "Tell me more about pricing."
    assert body["agent_id"] == agent["id"]

    leads = client.get(f"/ai-agents/{agent['id']}/leads", headers=conn_headers).json()
    assert len(leads) == 1
    assert leads[0]["email"] == "visitor@example.com"


def test_public_lead_capture_allows_missing_email_and_phone(client, conn_headers, uploaded_file):
    agent = _create_agent(client, conn_headers, "file", uploaded_file["id"], uploaded_file["name"])
    public_token = agent["chat_url"].rsplit("/", 1)[-1]
    resp = client.post(f"/public/ai-agents/{public_token}/leads", json={"message": "Just a question, no contact info."})
    assert resp.status_code == 201
    assert resp.json()["email"] is None
    assert resp.json()["phone"] is None


def test_listing_leads_requires_authentication(client, conn_headers, uploaded_file):
    agent = _create_agent(client, conn_headers, "file", uploaded_file["id"], uploaded_file["name"])
    resp = client.get(f"/ai-agents/{agent['id']}/leads")
    assert resp.status_code == 401


def test_lead_capture_is_rate_limited_separately_from_chat(client, conn_headers, uploaded_file):
    """The lead-capture endpoint has its own, stricter limiter -- exhausting
    it must not be affected by (or affect) the chat endpoint's own limit."""
    agent = _create_agent(client, conn_headers, "file", uploaded_file["id"], uploaded_file["name"])
    public_token = agent["chat_url"].rsplit("/", 1)[-1]

    from app.routers import public_ai_agents
    with patch.object(public_ai_agents, "_MAX_LEAD_REQUESTS", 2):
        ok1 = client.post(f"/public/ai-agents/{public_token}/leads", json={"message": "one"})
        ok2 = client.post(f"/public/ai-agents/{public_token}/leads", json={"message": "two"})
        blocked = client.post(f"/public/ai-agents/{public_token}/leads", json={"message": "three"})
    assert ok1.status_code == 201
    assert ok2.status_code == 201
    assert blocked.status_code == 429

    # Chat is a separate limiter entirely -- still available right after.
    chat = client.post(f"/public/ai-agents/{public_token}/chat", json={"question": "hello"})
    assert chat.status_code == 200


def test_report_and_stats_include_lead_count(client, auth_headers, conn_headers, uploaded_file):
    agent = _create_agent(client, conn_headers, "file", uploaded_file["id"], uploaded_file["name"])
    public_token = agent["chat_url"].rsplit("/", 1)[-1]
    client.post(f"/public/ai-agents/{public_token}/leads", json={"message": "hi"})

    stats = client.get(f"/ai-agents/{agent['id']}", headers=conn_headers).json()
    assert stats["lead_count"] == 1

    report = client.get("/admin/ai-agents/report", headers=auth_headers).json()
    row = next(a for a in report if a["id"] == agent["id"])
    assert row["lead_count"] == 1


def test_deleting_agent_removes_its_leads(client, conn_headers, uploaded_file):
    agent = _create_agent(client, conn_headers, "file", uploaded_file["id"], uploaded_file["name"])
    public_token = agent["chat_url"].rsplit("/", 1)[-1]
    client.post(f"/public/ai-agents/{public_token}/leads", json={"message": "hi"})
    client.delete(f"/ai-agents/{agent['id']}", headers=conn_headers)

    from app import ai_agents_store
    assert ai_agents_store.list_leads(agent["id"]) == []


# --- the admin bar's "describe a specific change" targeted-edit box --------

def test_targeted_edit_updates_only_the_named_site_field(client, conn_headers, uploaded_file):
    agent = _create_agent(client, conn_headers, "file", uploaded_file["id"], uploaded_file["name"])
    public_token = agent["chat_url"].rsplit("/", 1)[-1]
    edit_token = _mint_edit_token(client, conn_headers, agent["id"])
    client.patch(f"/ai-agents/{agent['id']}/site", headers=conn_headers, json={"headline": "Original Headline"})

    fake_json = (
        '{"summary": "Updated the footer tagline.", '
        '"site_updates": {"footer_tagline": "24/7 support, always."}, '
        '"page_updates": [], "post_updates": []}'
    )
    with patch.object(ai_service, "_BACKEND", "watsonx"), \
         patch.object(ai_service, "llm_with_usage", return_value=(fake_json, 300, False)):
        resp = client.post(f"/public/ai-agents/{public_token}/site/edit",
                            json={"edit_token": edit_token, "instruction": "Make the footer mention 24/7 support"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["summary"] == "Updated the footer tagline."
    assert body["site_updated"] is True
    assert body["pages_updated"] == 0
    assert body["posts_updated"] == 0

    site = client.get(f"/ai-agents/{agent['id']}/site", headers=conn_headers).json()
    assert site["footer_tagline"] == "24/7 support, always."
    assert site["headline"] == "Original Headline"  # untouched


def test_targeted_edit_updates_an_existing_page_by_id(client, conn_headers, uploaded_file):
    agent = _create_agent(client, conn_headers, "file", uploaded_file["id"], uploaded_file["name"])
    public_token = agent["chat_url"].rsplit("/", 1)[-1]
    edit_token = _mint_edit_token(client, conn_headers, agent["id"])
    page = client.post(f"/ai-agents/{agent['id']}/site/pages", headers=conn_headers,
                        json={"title": "Security", "content": "Old content.", "nav_order": 5}).json()

    fake_json = (
        '{"summary": "Moved Security first in the menu.", "site_updates": {}, '
        f'"page_updates": [{{"id": "{page["id"]}", "nav_order": 0}}], "post_updates": []}}'
    )
    with patch.object(ai_service, "_BACKEND", "watsonx"), \
         patch.object(ai_service, "llm_with_usage", return_value=(fake_json, 300, False)):
        resp = client.post(f"/public/ai-agents/{public_token}/site/edit",
                            json={"edit_token": edit_token, "instruction": "Move Security first"})
    assert resp.status_code == 200
    assert resp.json()["pages_updated"] == 1

    pages = client.get(f"/ai-agents/{agent['id']}/site/pages", headers=conn_headers).json()
    updated = next(p for p in pages if p["id"] == page["id"])
    assert updated["nav_order"] == 0
    assert updated["content"] == "Old content."  # untouched, wasn't named in the patch


def test_targeted_edit_ignores_an_unknown_page_id(client, conn_headers, uploaded_file):
    """The model must never be trusted to invent a page id -- only ids
    that already exist in this agent's own pages can be touched."""
    agent = _create_agent(client, conn_headers, "file", uploaded_file["id"], uploaded_file["name"])
    public_token = agent["chat_url"].rsplit("/", 1)[-1]
    edit_token = _mint_edit_token(client, conn_headers, agent["id"])

    fake_json = (
        '{"summary": "Updated a page.", "site_updates": {}, '
        '"page_updates": [{"id": "not-a-real-id", "title": "Hijacked"}], "post_updates": []}'
    )
    with patch.object(ai_service, "_BACKEND", "watsonx"), \
         patch.object(ai_service, "llm_with_usage", return_value=(fake_json, 300, False)):
        resp = client.post(f"/public/ai-agents/{public_token}/site/edit",
                            json={"edit_token": edit_token, "instruction": "Change some page"})
    assert resp.status_code == 200
    assert resp.json()["pages_updated"] == 0


def test_targeted_edit_rejects_invalid_accent_color(client, conn_headers, uploaded_file):
    agent = _create_agent(client, conn_headers, "file", uploaded_file["id"], uploaded_file["name"])
    public_token = agent["chat_url"].rsplit("/", 1)[-1]
    edit_token = _mint_edit_token(client, conn_headers, agent["id"])

    fake_json = '{"summary": "Changed color.", "site_updates": {"accent_color": "not-a-color"}, "page_updates": [], "post_updates": []}'
    with patch.object(ai_service, "_BACKEND", "watsonx"), \
         patch.object(ai_service, "llm_with_usage", return_value=(fake_json, 300, False)):
        resp = client.post(f"/public/ai-agents/{public_token}/site/edit",
                            json={"edit_token": edit_token, "instruction": "Make it green"})
    assert resp.status_code == 200
    assert resp.json()["site_updated"] is False

    site = client.get(f"/ai-agents/{agent['id']}/site", headers=conn_headers).json()
    assert site["accent_color"] is None


def test_targeted_edit_requires_a_valid_edit_token(client, conn_headers, uploaded_file):
    agent = _create_agent(client, conn_headers, "file", uploaded_file["id"], uploaded_file["name"])
    public_token = agent["chat_url"].rsplit("/", 1)[-1]
    resp = client.post(f"/public/ai-agents/{public_token}/site/edit",
                        json={"edit_token": "not-a-real-token", "instruction": "Change something"})
    assert resp.status_code == 403


def test_targeted_edit_requires_a_non_blank_instruction(client, conn_headers, uploaded_file):
    agent = _create_agent(client, conn_headers, "file", uploaded_file["id"], uploaded_file["name"])
    public_token = agent["chat_url"].rsplit("/", 1)[-1]
    edit_token = _mint_edit_token(client, conn_headers, agent["id"])
    with patch.object(ai_service, "_BACKEND", "watsonx"):
        resp = client.post(f"/public/ai-agents/{public_token}/site/edit",
                            json={"edit_token": edit_token, "instruction": "   "})
    assert resp.status_code == 422


def test_admin_bar_generate_panel_has_the_instruction_box(client, conn_headers, uploaded_file):
    agent = _create_agent(client, conn_headers, "file", uploaded_file["id"], uploaded_file["name"])
    public_token = agent["chat_url"].rsplit("/", 1)[-1]
    edit_token = _mint_edit_token(client, conn_headers, agent["id"])
    resp = client.get(f"/public/demo/{public_token}", params={"edit_token": edit_token})
    assert 'id="cecm-edit-instruction"' in resp.text
    assert 'id="cecm-edit-btn"' in resp.text
    assert "/site/edit" in resp.text


# --- the pencil editor's rich-text (Quill) content: sanitize + render ------

def test_page_content_strips_script_tags_but_keeps_safe_formatting(client, conn_headers, uploaded_file):
    agent = _create_agent(client, conn_headers, "file", uploaded_file["id"], uploaded_file["name"])
    page = client.post(f"/ai-agents/{agent['id']}/site/pages", headers=conn_headers, json={
        "title": "Services", "is_rich_html": True,
        "content": '<p>Hello <strong>world</strong></p><script>alert(1)</script>',
    }).json()
    # bleach strips the disallowed *tag* but keeps its harmless inner text
    # (there's no script left to execute, just the word "alert(1)" as
    # plain text) -- the property that actually matters is no <script>
    # element survives, not that the substring never appears anywhere.
    assert "<script" not in page["content"]
    assert "<strong>world</strong>" in page["content"]


def test_page_content_strips_javascript_link_protocol(client, conn_headers, uploaded_file):
    agent = _create_agent(client, conn_headers, "file", uploaded_file["id"], uploaded_file["name"])
    page = client.post(f"/ai-agents/{agent['id']}/site/pages", headers=conn_headers, json={
        "title": "Services", "is_rich_html": True, "content": '<p><a href="javascript:alert(1)">click</a></p>',
    }).json()
    assert "javascript:" not in page["content"]


def test_page_content_allows_image_by_url_but_strips_data_uri(client, conn_headers, uploaded_file):
    """The pencil editor's image button inserts a URL the admin typed, not
    a base64-encoded local file (which would blow past this field's
    length limit almost immediately) -- a real http(s) image src must
    survive sanitization, but a data: URI must not."""
    agent = _create_agent(client, conn_headers, "file", uploaded_file["id"], uploaded_file["name"])
    page = client.post(f"/ai-agents/{agent['id']}/site/pages", headers=conn_headers, json={
        "title": "Services", "is_rich_html": True,
        "content": '<p><img src="https://example.com/diagram.png" alt="Diagram"></p>'
                    '<p><img src="data:image/png;base64,AAAA"></p>',
    }).json()
    assert '<img src="https://example.com/diagram.png" alt="Diagram">' in page["content"]
    assert "data:image" not in page["content"]


def test_page_content_not_sanitized_unless_is_rich_html_is_set(client, conn_headers, uploaded_file):
    """bleach parses EVERY input as HTML -- so sanitization is opt-in per
    request (the pencil editor's Quill save sets is_rich_html: true).
    Plain text written some other way (the site's older plain-textarea
    forms, the API directly, ...) must never be silently mangled just
    because it happens to contain a "<word>"-shaped substring."""
    agent = _create_agent(client, conn_headers, "file", uploaded_file["id"], uploaded_file["name"])
    page = client.post(f"/ai-agents/{agent['id']}/site/pages", headers=conn_headers, json={
        "title": "Legacy", "content": "Plain text with a literal <tag> in it.",
    }).json()
    assert page["content"] == "Plain text with a literal <tag> in it."


def test_public_page_update_also_sanitizes_content(client, conn_headers, uploaded_file):
    """The same sanitization must apply on the public, edit_token-gated
    path -- not just the authenticated one -- since that's what the
    pencil editor's Save button actually calls."""
    agent = _create_agent(client, conn_headers, "file", uploaded_file["id"], uploaded_file["name"])
    public_token = agent["chat_url"].rsplit("/", 1)[-1]
    edit_token = _mint_edit_token(client, conn_headers, agent["id"])
    page = client.post(f"/ai-agents/{agent['id']}/site/pages", headers=conn_headers,
                        json={"title": "Services", "content": "placeholder"}).json()

    updated = client.patch(f"/public/ai-agents/{public_token}/site/pages/{page['id']}", json={
        "edit_token": edit_token, "is_rich_html": True, "content": '<p onclick="alert(1)">Hi</p>',
    }).json()
    assert "onclick" not in updated["content"]
    assert "<p>Hi</p>" in updated["content"]


def test_rich_html_page_content_renders_unescaped_while_legacy_plain_text_still_escapes(client, conn_headers, uploaded_file):
    agent = _create_agent(client, conn_headers, "file", uploaded_file["id"], uploaded_file["name"])
    public_token = agent["chat_url"].rsplit("/", 1)[-1]

    rich_page = client.post(f"/ai-agents/{agent['id']}/site/pages", headers=conn_headers, json={
        "title": "Rich", "is_rich_html": True, "content": "<p>This has <em>real</em> formatting.</p>",
    }).json()
    legacy_page = client.post(f"/ai-agents/{agent['id']}/site/pages", headers=conn_headers, json={
        "title": "Legacy", "content": "Plain text with a literal <tag> in it.",
    }).json()

    rich_html = client.get(f"/public/demo/{public_token}/page/{rich_page['slug']}").text
    assert "<em>real</em>" in rich_html

    legacy_html = client.get(f"/public/demo/{public_token}/page/{legacy_page['slug']}").text
    assert "&lt;tag&gt;" in legacy_html
    assert "<tag>" not in legacy_html


def test_excerpt_strips_html_tags_for_preview_cards(client, conn_headers, uploaded_file):
    agent = _create_agent(client, conn_headers, "file", uploaded_file["id"], uploaded_file["name"])
    public_token = agent["chat_url"].rsplit("/", 1)[-1]
    client.post(f"/ai-agents/{agent['id']}/site/posts", headers=conn_headers, json={
        "title": "A Post", "is_rich_html": True,
        "content": "<p>Some <strong>rich</strong> content for the preview card.</p>",
    })
    home = client.get(f"/public/demo/{public_token}").text
    assert "<strong>" not in home
    assert "Some rich content" in home


def test_admin_bar_embeds_quill_editor_and_ai_generate_button(client, conn_headers, uploaded_file):
    agent = _create_agent(client, conn_headers, "file", uploaded_file["id"], uploaded_file["name"])
    public_token = agent["chat_url"].rsplit("/", 1)[-1]
    edit_token = _mint_edit_token(client, conn_headers, agent["id"])
    resp = client.get(f"/public/demo/{public_token}", params={"edit_token": edit_token})
    assert "quill.min.js" in resp.text
    assert "cecm-quill-mount" in resp.text
    assert "Generate with AI" in resp.text
    assert 'data-cecm-label="the homepage headline and subheadline"' in resp.text


def test_admin_bar_embeds_the_image_modal(client, conn_headers, uploaded_file):
    agent = _create_agent(client, conn_headers, "file", uploaded_file["id"], uploaded_file["name"])
    public_token = agent["chat_url"].rsplit("/", 1)[-1]
    edit_token = _mint_edit_token(client, conn_headers, agent["id"])
    resp = client.get(f"/public/demo/{public_token}", params={"edit_token": edit_token})
    assert 'id="cecm-image-modal"' in resp.text
    assert 'id="cecm-image-upload-btn"' in resp.text
    assert "openImageModal(quill)" in resp.text


# --- the pencil editor's image-upload modal ---------------------------

def _fake_png_bytes() -> bytes:
    import io as _io
    from PIL import Image
    buf = _io.BytesIO()
    Image.new("RGB", (4, 4), color=(200, 50, 50)).save(buf, format="PNG")
    return buf.getvalue()


def test_image_upload_succeeds_and_is_servable(client, conn_headers, uploaded_file):
    agent = _create_agent(client, conn_headers, "file", uploaded_file["id"], uploaded_file["name"])
    public_token = agent["chat_url"].rsplit("/", 1)[-1]
    edit_token = _mint_edit_token(client, conn_headers, agent["id"])

    resp = client.post(
        f"/public/ai-agents/{public_token}/site/images",
        data={"edit_token": edit_token},
        files={"file": ("photo.png", _fake_png_bytes(), "image/png")},
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["content_type"] == "image/png"
    assert f"/public/ai-agents/{public_token}/site/images/{body['id']}" in body["url"]

    served = client.get(f"/public/ai-agents/{public_token}/site/images/{body['id']}")
    assert served.status_code == 200
    assert served.headers["content-type"] == "image/png"
    assert served.content == _fake_png_bytes()


def test_image_upload_serving_requires_no_edit_token(client, conn_headers, uploaded_file):
    """Ordinary site visitors need to load these images too, not just the
    admin -- unlike every write endpoint, serving one is fully public."""
    agent = _create_agent(client, conn_headers, "file", uploaded_file["id"], uploaded_file["name"])
    public_token = agent["chat_url"].rsplit("/", 1)[-1]
    edit_token = _mint_edit_token(client, conn_headers, agent["id"])
    uploaded = client.post(
        f"/public/ai-agents/{public_token}/site/images",
        data={"edit_token": edit_token},
        files={"file": ("photo.png", _fake_png_bytes(), "image/png")},
    ).json()

    served = client.get(f"/public/ai-agents/{public_token}/site/images/{uploaded['id']}")
    assert served.status_code == 200


def test_image_upload_rejects_a_non_image_file_regardless_of_claimed_type(client, conn_headers, uploaded_file):
    """The browser-supplied Content-Type/filename is never trusted -- the
    actual file signature is verified instead."""
    agent = _create_agent(client, conn_headers, "file", uploaded_file["id"], uploaded_file["name"])
    public_token = agent["chat_url"].rsplit("/", 1)[-1]
    edit_token = _mint_edit_token(client, conn_headers, agent["id"])

    resp = client.post(
        f"/public/ai-agents/{public_token}/site/images",
        data={"edit_token": edit_token},
        files={"file": ("fake.png", b"<script>alert(1)</script>", "image/png")},
    )
    assert resp.status_code == 422


def test_image_upload_rejects_oversized_file(client, conn_headers, uploaded_file):
    agent = _create_agent(client, conn_headers, "file", uploaded_file["id"], uploaded_file["name"])
    public_token = agent["chat_url"].rsplit("/", 1)[-1]
    edit_token = _mint_edit_token(client, conn_headers, agent["id"])

    oversized = b"0" * (5 * 1024 * 1024 + 1)
    resp = client.post(
        f"/public/ai-agents/{public_token}/site/images",
        data={"edit_token": edit_token},
        files={"file": ("big.png", oversized, "image/png")},
    )
    assert resp.status_code == 413


def test_image_upload_requires_a_valid_edit_token(client, conn_headers, uploaded_file):
    agent = _create_agent(client, conn_headers, "file", uploaded_file["id"], uploaded_file["name"])
    public_token = agent["chat_url"].rsplit("/", 1)[-1]
    resp = client.post(
        f"/public/ai-agents/{public_token}/site/images",
        data={"edit_token": "not-a-real-token"},
        files={"file": ("photo.png", _fake_png_bytes(), "image/png")},
    )
    assert resp.status_code == 403


def test_serving_an_unknown_image_id_returns_404(client, conn_headers, uploaded_file):
    agent = _create_agent(client, conn_headers, "file", uploaded_file["id"], uploaded_file["name"])
    public_token = agent["chat_url"].rsplit("/", 1)[-1]
    resp = client.get(f"/public/ai-agents/{public_token}/site/images/not-a-real-id")
    assert resp.status_code == 404


def test_deleting_agent_removes_its_uploaded_images(client, conn_headers, uploaded_file):
    agent = _create_agent(client, conn_headers, "file", uploaded_file["id"], uploaded_file["name"])
    public_token = agent["chat_url"].rsplit("/", 1)[-1]
    edit_token = _mint_edit_token(client, conn_headers, agent["id"])
    uploaded = client.post(
        f"/public/ai-agents/{public_token}/site/images",
        data={"edit_token": edit_token},
        files={"file": ("photo.png", _fake_png_bytes(), "image/png")},
    ).json()

    client.delete(f"/ai-agents/{agent['id']}", headers=conn_headers)

    from app import ai_agents_store
    assert ai_agents_store.get_image(agent["id"], uploaded["id"]) is None
    assert ai_agents_store.get_image_bytes(uploaded["id"]) is None


# --- the sidebar layout (panels no longer push the page down) --------------

def test_admin_bar_panels_are_a_fixed_sidebar_not_inline_content(client, conn_headers, uploaded_file):
    agent = _create_agent(client, conn_headers, "file", uploaded_file["id"], uploaded_file["name"])
    public_token = agent["chat_url"].rsplit("/", 1)[-1]
    edit_token = _mint_edit_token(client, conn_headers, agent["id"])
    resp = client.get(f"/public/demo/{public_token}", params={"edit_token": edit_token})
    assert 'id="cecm-page-content"' in resp.text
    assert ".cecm-panel {" in resp.text
    assert "position: fixed" in resp.text
    assert "cecm-shifted" in resp.text


# --- the "Add a page"/"Add a post" forms' own AI-draft-from-topic ----------

def test_draft_item_fills_a_new_page_from_a_topic(client, conn_headers, uploaded_file):
    agent = _create_agent(client, conn_headers, "file", uploaded_file["id"], uploaded_file["name"])
    public_token = agent["chat_url"].rsplit("/", 1)[-1]
    edit_token = _mint_edit_token(client, conn_headers, agent["id"])

    fake_json = '{"title": "Security Overview", "content": "We take security seriously across every layer."}'
    with patch.object(ai_service, "_BACKEND", "watsonx"), \
         patch.object(ai_service, "llm_with_usage", return_value=(fake_json, 300, False)):
        resp = client.post(f"/public/ai-agents/{public_token}/site/draft-item",
                            json={"edit_token": edit_token, "kind": "page", "topic": "Security"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["title"] == "Security Overview"
    assert "security" in body["content"].lower()
    assert body["excerpt"] is None

    # Nothing was actually saved -- this only drafts, the admin still
    # has to click "Add page" themselves.
    pages = client.get(f"/ai-agents/{agent['id']}/site/pages", headers=conn_headers).json()
    assert pages == []


def test_draft_item_fills_a_new_post_including_excerpt(client, conn_headers, uploaded_file):
    agent = _create_agent(client, conn_headers, "file", uploaded_file["id"], uploaded_file["name"])
    public_token = agent["chat_url"].rsplit("/", 1)[-1]
    edit_token = _mint_edit_token(client, conn_headers, agent["id"])

    fake_json = '{"title": "5 Tips", "excerpt": "A quick teaser.", "content": "Full post body here."}'
    with patch.object(ai_service, "_BACKEND", "watsonx"), \
         patch.object(ai_service, "llm_with_usage", return_value=(fake_json, 300, False)):
        resp = client.post(f"/public/ai-agents/{public_token}/site/draft-item",
                            json={"edit_token": edit_token, "kind": "post", "topic": ""})
    assert resp.status_code == 200
    body = resp.json()
    assert body["title"] == "5 Tips"
    assert body["excerpt"] == "A quick teaser."
    assert body["content"] == "Full post body here."


def test_draft_item_rejects_an_invalid_kind(client, conn_headers, uploaded_file):
    agent = _create_agent(client, conn_headers, "file", uploaded_file["id"], uploaded_file["name"])
    public_token = agent["chat_url"].rsplit("/", 1)[-1]
    edit_token = _mint_edit_token(client, conn_headers, agent["id"])
    resp = client.post(f"/public/ai-agents/{public_token}/site/draft-item",
                        json={"edit_token": edit_token, "kind": "banana", "topic": ""})
    assert resp.status_code == 422  # pydantic pattern validation, before it ever reaches the service


def test_draft_item_requires_a_valid_edit_token(client, conn_headers, uploaded_file):
    agent = _create_agent(client, conn_headers, "file", uploaded_file["id"], uploaded_file["name"])
    public_token = agent["chat_url"].rsplit("/", 1)[-1]
    resp = client.post(f"/public/ai-agents/{public_token}/site/draft-item",
                        json={"edit_token": "not-a-real-token", "kind": "page", "topic": ""})
    assert resp.status_code == 403


def test_admin_bar_add_forms_embed_quill_and_generate_buttons(client, conn_headers, uploaded_file):
    agent = _create_agent(client, conn_headers, "file", uploaded_file["id"], uploaded_file["name"])
    public_token = agent["chat_url"].rsplit("/", 1)[-1]
    edit_token = _mint_edit_token(client, conn_headers, agent["id"])
    resp = client.get(f"/public/demo/{public_token}", params={"edit_token": edit_token})
    assert 'id="cecm-new-page-content-mount"' in resp.text
    assert 'id="cecm-new-post-content-mount"' in resp.text
    assert 'id="cecm-generate-page-btn"' in resp.text
    assert 'id="cecm-generate-post-btn"' in resp.text
    assert "/site/draft-item" in resp.text
