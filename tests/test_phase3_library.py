import base64
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import threading
import time
from urllib.parse import urlsplit
from uuid import UUID, uuid4

from fastapi import HTTPException
from fastapi.testclient import TestClient
import pytest

from backend import app as application, db, library, service, worker
from backend.artifacts import ArtifactStore
from backend.settings import settings

pytestmark = pytest.mark.integration


@pytest.fixture
def client(monkeypatch):
    store = ArtifactStore()
    store.initialize()
    monkeypatch.setattr(application, "artifacts", store)
    return TestClient(application.app, headers={"Authorization": "Bearer " + settings.owner_token})


def upload(client, content="Library input 42", **extra):
    body = {
        "name": "phase3.txt",
        "content_type": "text/plain",
        "content_base64": base64.b64encode(content.encode()).decode(),
        **extra,
    }
    return client.post("/api/v1/library", json=body, headers={"Idempotency-Key": str(uuid4())})


def conversation(bot="researcher"):
    with db.connection() as conn:
        return str(conn.execute("SELECT id FROM conversations WHERE bot_id=%s", (bot,)).fetchone()["id"])


def test_library_upload_versions_stale_edits_and_integrity(client):
    first = upload(client).json()
    second = upload(client, "Second version", previous_id=first["id"]).json()
    assert first["family_id"] == second["family_id"] and second["version"] == 2
    assert upload(client, "Stale version", previous_id=first["id"]).status_code == 409
    old = client.get("/api/v1/artifacts/" + first["id"])
    assert old.text == "Library input 42" and old.headers["x-content-type-options"] == "nosniff"
    assert (
        client.get("/api/v1/artifacts/" + second["id"] + "?download=true")
        .headers["content-disposition"]
        .startswith("attachment")
    )
    versions = client.get("/api/v1/library/" + first["id"]).json()["versions"]
    assert [v["version"] for v in versions] == [2, 1]
    with db.connection() as conn:
        conn.execute("UPDATE artifacts SET sha256=%s WHERE id=%s", ("0" * 64, UUID(first["id"])))
    assert client.get("/api/v1/artifacts/" + first["id"]).status_code == 502


def test_library_share_task_admission_and_delegation_are_explicit(client):
    file = upload(client).json()
    chat = conversation()
    body = {
        "bot_id": "researcher",
        "conversation_id": chat,
        "prompt": "Read this file",
        "artifact_ids": [file["id"]],
    }

    def submit():
        return client.post("/api/v1/tasks", json=body, headers={"Idempotency-Key": str(uuid4())})

    assert submit().status_code == 403
    assert (
        client.post(f"/api/v1/library/{file['id']}/shares", json={"conversation_id": chat}).status_code == 200
    )
    task = submit().json()
    attempt = worker.claim_new()[0]
    assert "Granted library file v1" in attempt["input"] and file["id"] in attempt["input"]
    with db.connection() as conn:
        admitted = service.locked_task(conn, UUID(task["id"]))
        assert service.artifact_visible(conn, UUID(file["id"]), admitted)
        child = service.delegate(
            conn, admitted, "writer", "Read shared input", artifact_ids=[UUID(file["id"])]
        )
        assert service.artifact_visible(
            conn, UUID(file["id"]), service.locked_task(conn, UUID(child["task_id"]))
        )
    assert client.delete(f"/api/v1/library/{file['id']}/shares/{chat}").status_code == 200
    assert submit().status_code == 403
    unrelated = {"bot_id": "reviewer", "conversation_id": conversation("reviewer"), "prompt": "Unrelated"}
    other = client.post("/api/v1/tasks", json=unrelated, headers={"Idempotency-Key": str(uuid4())}).json()
    with db.connection() as conn:
        assert (
            service.artifact_visible(conn, UUID(file["id"]), service.locked_task(conn, UUID(other["id"])))
            is None
        )


def test_library_listing_search_table_preview_and_idempotency(client):
    chat = conversation()
    key = str(uuid4())
    body = {
        "name": "figures.csv",
        "content_type": "text/csv",
        "conversation_id": chat,
        "content_base64": base64.b64encode(b"Item,Count\nApples,42").decode(),
    }
    first = client.post("/api/v1/library", json=body, headers={"Idempotency-Key": key})
    again = client.post("/api/v1/library", json=body, headers={"Idempotency-Key": key})
    assert first.status_code == 201 and first.json()["id"] == again.json()["id"]
    assert (
        client.post(
            "/api/v1/library", json={**body, "name": "other.csv"}, headers={"Idempotency-Key": key}
        ).status_code
        == 409
    )
    result = client.get("/api/v1/library?q=Apples").json()
    assert len(result["items"]) == 1
    assert client.get("/api/v1/library/" + first.json()["id"]).json()["table"] == [
        ["Item", "Count"],
        ["Apples", "42"],
    ]
    assert client.get("/api/v1/library?conversation_id=" + conversation("writer")).json()["items"] == []
    assert TestClient(application.app).get("/api/v1/library").status_code == 401


def test_library_concurrent_versions_preserve_every_output(client, make_task):
    task = make_task()

    def publish(i):
        with db.connection() as conn:
            return application.artifacts.publish(
                conn, task, "concurrent.txt", f"Output {i}", "text/plain", "version-test:" + str(i)
            )

    with ThreadPoolExecutor(max_workers=4) as pool:
        replies = list(pool.map(publish, range(4)))
    assert sorted(r["version"] for r in replies) == [1, 2, 3, 4]
    assert len({r["family_id"] for r in replies}) == 1


@pytest.mark.parametrize(
    "name,mime,data",
    [
        ("../escape.txt", "text/plain", b"ok"),
        ("x.png", "image/png", b"<script>oops</script>"),
        ("x.txt", "text/plain", b"\xff"),
        ("x.svg", "image/svg+xml", b"<svg/>"),
    ],
)
def test_library_rejects_unsafe_names_and_mismatched_formats(client, name, mime, data):
    assert (
        client.post(
            "/api/v1/library",
            json={"name": name, "content_type": mime, "content_base64": base64.b64encode(data).decode()},
            headers={"Idempotency-Key": str(uuid4())},
        ).status_code
        == 422
    )


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1/admin",
        "http://[::1]/",
        "http://169.254.169.254/latest",
        "file:///etc/passwd",
        "https://user:pass@example.com/",
        "http://example.com:8000/",
    ],
)
def test_link_import_blocks_local_metadata_and_credential_urls(client, url):
    assert (
        client.post(
            "/api/v1/library/links", json={"url": url}, headers={"Idempotency-Key": str(uuid4())}
        ).status_code
        == 422
    )


def test_link_redirect_rechecks_address_and_never_connects_to_private_target(client, monkeypatch):
    addresses = {"public.example": "93.184.216.34", "internal.example": "127.0.0.1"}
    monkeypatch.setattr(
        library.socket, "getaddrinfo", lambda host, *a, **k: [(2, 1, 6, "", (addresses[host], 80))]
    )
    connections = []

    class FakeSocket:
        def close(self):
            pass

    def connect(address, **kwargs):
        connections.append(address)
        return FakeSocket()

    monkeypatch.setattr(library.socket, "create_connection", connect)

    class FakeConnection:
        def __init__(self, *a, **k):
            self.sock = None

        def request(self, *a, **k):
            pass

        def getresponse(self):
            return type(
                "Reply", (), {"status": 302, "getheader": lambda self, k: "http://internal.example/private"}
            )()

        def close(self):
            pass

    monkeypatch.setattr(library.http.client, "HTTPConnection", FakeConnection)
    response = client.post(
        "/api/v1/library/links",
        json={"url": "http://public.example/"},
        headers={"Idempotency-Key": str(uuid4())},
    )
    assert response.status_code == 422 and connections == [("93.184.216.34", 80)]


def test_link_response_deadline_stops_continuously_dripping_body(monkeypatch):
    class SlowResponse(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.send_header("Content-Length", "200")
            self.end_headers()
            try:
                for _ in range(200):
                    self.wfile.write(b"x")
                    self.wfile.flush()
                    time.sleep(0.02)
            except (BrokenPipeError, ConnectionResetError):
                pass

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), SlowResponse)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    monkeypatch.setattr(library, "MAX_FETCH_SECONDS", 0.15)
    monkeypatch.setattr(
        library, "public_address", lambda url: (urlsplit(url), "127.0.0.1", server.server_port)
    )
    try:
        started = time.monotonic()
        with pytest.raises(HTTPException) as exc:
            library.fetch_link("http://public.example/")
        assert exc.value.status_code == 502
        assert time.monotonic() - started < 1
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_link_import_html_removes_scripts_and_searches_only_saved_text(client, monkeypatch):
    monkeypatch.setattr(
        library, "fetch_link", lambda url: ("page.txt", b"Imported public page 123", "text/plain", url)
    )
    result = client.post(
        "/api/v1/library/links",
        json={"url": "https://example.com/"},
        headers={"Idempotency-Key": str(uuid4())},
    )
    assert result.status_code == 201 and result.json()["source"] == "link"
    assert (
        library.extract(b"<h1>Title</h1><script>secret()</script><p>Visible</p>", "text/html")
        == "Title\nVisible"
    )
    assert client.get("/api/v1/library?q=123").json()["items"][0]["id"] == result.json()["id"]


def test_library_new_version_requires_a_new_share(client):
    first = upload(client).json()
    chat = conversation()
    client.post(f"/api/v1/library/{first['id']}/shares", json={"conversation_id": chat})
    second = upload(client, "Changed content", previous_id=first["id"]).json()
    assert (
        client.get("/api/v1/library?conversation_id=" + chat + "&all_versions=true").json()["items"][0]["id"]
        == first["id"]
    )
    assert client.get("/api/v1/library?conversation_id=" + chat).json()["items"][0]["id"] == first["id"]
    with db.connection() as conn:
        assert library.visible_in_conversation(conn, UUID(second["id"]), UUID(chat)) is None


def test_office_preview_rejects_expanding_archives():
    import io
    import zipfile

    archive = io.BytesIO()
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as output:
        output.writestr("word/document.xml", "<document>" + "x" * 2_000_001 + "</document>")
    with pytest.raises(HTTPException) as exc:
        library.extract(archive.getvalue(), library.DOCX)
    assert exc.value.status_code == 413


def test_library_plain_unicode_names_transfer_and_dot_rejection(client, tmp_path):
    from docker.write_attachment import install
    import hashlib
    import json
    import io
    import zipfile
    from backend.containers import AttachmentSpec

    name = "Résumé notes 42.txt"
    uploaded = upload(client, "Safe bytes", name=name)
    assert uploaded.status_code == 201
    spec = AttachmentSpec(
        bot_id="researcher",
        attachment_id=uploaded.json()["id"],
        name=name,
        sha256=hashlib.sha256(b"Safe bytes").hexdigest(),
        content_base64=base64.b64encode(b"Safe bytes").decode(),
    )
    install(tmp_path, spec.model_dump())
    assert (tmp_path / "attachments" / spec.attachment_id / name).read_bytes() == b"Safe bytes"
    assert upload(client, name="..").status_code == 422
    archive = io.BytesIO()
    with zipfile.ZipFile(archive, "w") as z:
        z.writestr(
            "word/document.xml",
            '<?xml version="1.0" encoding="UTF-16"?><!DOCTYPE a [<!ENTITY x "bad">]><a>&x;</a>'.encode(
                "utf-16"
            ),
        )
    with pytest.raises(HTTPException) as exc:
        library.extract(archive.getvalue(), library.DOCX)
    assert exc.value.status_code == 422
    assert "encrypted" not in json.dumps(uploaded.json())
