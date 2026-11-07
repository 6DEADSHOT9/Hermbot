"""Teaching privacy, persistence, task isolation and exact-version publication checks."""

import base64
import hashlib
import io
import struct
from types import SimpleNamespace
from uuid import UUID, uuid4

from fastapi.testclient import TestClient
from psycopg.types.json import Jsonb
import pytest

from backend import app as application, capabilities, computer, db, native, registry, service, teaching
from backend.secrets import decrypt
from backend.settings import settings
from docker.teaching_capture import AcceptedRFB, Capture


@pytest.fixture
def client():
    result = TestClient(application.app, headers={"Authorization": "Bearer " + settings.owner_token})
    yield result
    result.close()


def post(client, path, body, key=None):
    return client.post(path, json=body, headers={"Idempotency-Key": key or str(uuid4())})


@pytest.fixture
def capture_native(monkeypatch):
    capture = Capture()
    identity = computer.viewer("Bearer " + settings.owner_token)

    def rpc(bot, method, path, **kwargs):
        if path == "/computer":
            return {
                "lease": {
                    "holder": "human",
                    "epoch": 4,
                    "viewer_hash": hashlib.sha256(identity.encode()).hexdigest()[:12],
                },
                "input_ready": True,
            }
        if path == "/teaching/control":
            b = kwargs["json"]
            return capture.control(b["session"], b["action"], b["viewer"], b["epoch"], b["revision"])
        if path == "/teaching/batch":
            return capture.batch(kwargs["params"]["ack"])
        raise AssertionError(path)

    monkeypatch.setattr(native, "request", rpc)
    return capture


@pytest.fixture
def objects(monkeypatch):
    blobs = {}

    class Client:
        def put_object(self, **kw):
            blobs[kw["Key"]] = kw["Body"]

        def get_object(self, **kw):
            return {"Body": io.BytesIO(blobs[kw["Key"]])}

        def get_paginator(self, name):
            return SimpleNamespace(
                paginate=lambda **kw: [
                    {"Contents": [{"Key": k} for k in blobs if k.startswith(kw["Prefix"])]}
                ]
            )

        def delete_object(self, **kw):
            blobs.pop(kw["Key"], None)

    monkeypatch.setattr(teaching, "store", lambda: SimpleNamespace(client=Client()))
    return blobs


def session(bot="researcher", state="ready"):
    sid = uuid4()
    with db.connection() as conn:
        conn.execute(
            "INSERT INTO teaching_sessions(id,bot_id,viewer_hash,epoch,state,info_encrypted,evidence_encrypted,evidence_hash) VALUES(%s,%s,%s,1,%s,%s,%s,%s)",
            (
                sid,
                bot,
                "a" * 64,
                state,
                teaching.encode({"goal": "local test", "expected_result": "saved result"}),
                teaching.encode({"text": "Reviewed local procedure", "frames": []}),
                "b" * 64,
            ),
        )
    return sid


def test_accepted_rfb_fragmentation_and_clipboard_omission():
    key = struct.pack("!BBHI", 4, 1, 0, ord("a"))
    pointer = struct.pack("!BBHH", 5, 1, 30, 40)
    secret = b"CANARY_CLIPBOARD_SECRET"
    data = b"RFB 003.008\n\x01\x01" + key + pointer + struct.pack("!B3xi", 6, len(secret)) + secret
    for width in (1, 2, 7, 1000):
        parser = AcceptedRFB()
        events = []
        for n in range(0, len(data), width):
            events += parser.feed(data[n : n + width])
        assert events == [
            {"kind": "key", "down": True, "keysym": 97},
            {"kind": "pointer", "buttons": 1, "x": 30, "y": 40},
            {"kind": "omitted", "reason": "clipboard"},
        ]
        assert secret.decode() not in str(events)
    with pytest.raises(ValueError):
        AcceptedRFB().feed(b"x" * 14 + b"\x7f")


def test_capture_privacy_fences_late_frames_and_keys():
    c = Capture()
    c.control("id", "start", "viewer", 1, 1)
    generation = c.observe([{"kind": "key", "down": True, "keysym": 97}], "viewer", 1, "800x600")
    c.control("id", "private", "viewer", 1, 2)
    c.observe([{"kind": "key", "down": True, "keysym": 98}], "viewer", 1, "800x600")
    image = "data:image/jpeg;base64," + base64.b64encode(b"private frame").decode()
    c.frame(generation, image, "800x600")
    assert [e["kind"] for e in c.events] == ["key", "omitted"]
    c.control("id", "resume", "viewer", 1, 3)
    c.frame(generation, image, "800x600")
    assert len(c.events) == 2
    c.observe([{"kind": "key", "down": True, "keysym": 99}], "viewer", 2, "800x600")
    assert c.state == "paused" and len(c.events) == 2


def test_capture_idempotency_and_drag_boundaries():
    c = Capture()
    c.control("id", "start", "viewer", 1, 1)
    c.control("id", "start", "viewer", 1, 1)
    with pytest.raises(ValueError):
        c.control("id", "pause", "viewer", 1, 4)
    c.observe(
        [{"kind": "pointer", "buttons": b, "x": x, "y": 0} for x, b in enumerate([0, 0, 1, 1, 0, 0])],
        "viewer",
        1,
        "800x600",
    )
    assert [e["buttons"] for e in c.events] == [0, 1, 1, 0]
    c.bytes = 8 * 1024 * 1024
    c.append({"kind": "key", "down": True, "keysym": 97})
    assert c.state == "paused"


def test_start_control_and_private_barrier(client, capture_native):
    base = "/api/v1/bots/researcher/teaching"
    key = str(uuid4())
    body = {"goal": "Save a local form", "expected_result": "A record exists"}
    first = post(client, base, body, key)
    assert first.status_code == 200, first.text
    assert post(client, base, body, key).json() == first.json()
    assert post(client, base, {**body, "goal": "Changed"}, key).status_code == 409
    sid = first.json()["id"]
    path = base + "/" + sid
    result = post(client, path + "/control", {"expected_revision": 1, "action": "start"})
    assert result.status_code == 200, result.text
    capture_native.observe([{"kind": "key", "down": True, "keysym": 97}], capture_native.viewer, 4, "800x600")
    barrier = client.post("/api/v1/bots/researcher/teaching-private-input")
    assert barrier.status_code == 200 and capture_native.state == "paused"
    assert post(client, path + "/control", {"expected_revision": 1, "action": "resume"}).status_code == 409
    with db.connection() as conn:
        r = conn.execute("SELECT * FROM teaching_sessions WHERE id=%s", (sid,)).fetchone()
        assert body["goal"] not in r["info_encrypted"]
    assert client.get("/api/v1/bots/writer/teaching/" + sid).status_code == 404
    assert TestClient(application.app).get(path).status_code == 401


def test_encrypted_persistence_duplicate_poll_and_review(client, capture_native, objects):
    result = post(
        client,
        "/api/v1/bots/researcher/teaching",
        {"goal": "Save a form", "expected_result": "Record created"},
    ).json()
    sid = UUID(result["id"])
    path = "/api/v1/bots/researcher/teaching/" + str(sid)
    assert post(client, path + "/control", {"expected_revision": 1, "action": "start"}).status_code == 200
    capture_native.observe([{"kind": "key", "down": True, "keysym": 97}], capture_native.viewer, 4, "800x600")
    teaching.collect(sid, "researcher")
    teaching.collect(sid, "researcher")
    assert len(teaching.timeline(sid)) == 1 and objects
    assert all(b"keysym" not in blob for blob in objects.values())
    assert post(client, path + "/control", {"expected_revision": 2, "action": "finish"}).status_code == 200
    teaching.collect(sid, "researcher")
    detail = client.get(path).json()
    assert detail["state"] == "ready"
    assert (
        client.put(
            path + "/evidence",
            json={
                "expected_revision": detail["revision"],
                "text": "Use {{record_name}} as the fresh form input",
                "frame_sequences": [99],
            },
        ).status_code
        == 422
    )
    assert (
        client.put(
            path + "/evidence",
            json={
                "expected_revision": detail["revision"],
                "text": "Use {{record_name}} as the fresh form input",
                "frame_sequences": [],
            },
        ).status_code
        == 200
    )
    frozen = client.get(path).json()
    assert frozen["evidence_hash"]
    assert client.post(path + "/generate", json={"expected_revision": detail["revision"]}).status_code == 409
    assert client.request("DELETE", path, json={"expected_revision": frozen["revision"]}).status_code == 200
    teaching.maintain()
    assert not objects
    assert client.get(path).status_code == 404


def test_restarted_collector_marks_interrupted(objects, capture_native):
    sid = session(state="recording")
    with db.connection() as conn:
        conn.execute("UPDATE teaching_sessions SET capture_revision=1 WHERE id=%s", (sid,))
    teaching.collect(sid, "researcher")
    with db.connection() as conn:
        assert (
            conn.execute("SELECT state FROM teaching_sessions WHERE id=%s", (sid,)).fetchone()["state"]
            == "interrupted"
        )


def test_candidate_is_exact_task_only_and_config_fenced(client, make_task):
    sid = session()
    name = "hermbot-local-test"
    content = f"---\nname: {name}\ndescription: Save a local form\n---\nUse fresh inputs and verify the saved record."
    path = "/api/v1/bots/researcher/teaching/" + str(sid)
    assert (
        client.put(
            path + "/draft", json={"expected_revision": 1, "name": name, "content": content}
        ).status_code
        == 200
    )
    with db.connection() as conn:
        inventory = {
            "version": 1,
            "skills": [],
            "toolsets": [{"name": "skills", "configured": True, "tools": ["skill_view", "skills_list"]}],
        }
        conn.execute(
            "UPDATE bots SET enabled_tools=%s,readiness=%s WHERE id=%s",
            (Jsonb(["skills"]), Jsonb({"compatible": True, "inventory": inventory}), "researcher"),
        )
    r = post(
        client,
        path + "/test",
        {
            "expected_revision": 2,
            "inputs": "Save record beta",
            "checks": "Read back beta and ensure alpha unchanged",
        },
    )
    assert r.status_code == 200, r.text
    task_id = UUID(r.json()["task_id"])
    other = make_task()
    with db.connection() as conn:
        task = service.locked_task(conn, task_id)
        view = capabilities.manifest(conn, "researcher", task)
        assert view["teaching_candidate"]["content"] == content
        assert name in {s["name"] for s in view["skills"]}
        assert teaching.candidate(conn, other) is None
        assert not capabilities.manifest(conn, "researcher", task, peer=True)["teaching_candidate"]
        assert name not in registry.require_bot(conn, "researcher")["enabled_skills"]
        assert conn.execute("SELECT count(*) n FROM knowledge_versions").fetchone()["n"] == 0
        t = conn.execute("SELECT * FROM teaching_tests WHERE task_id=%s", (task_id,)).fetchone()
        assert not t["consulted_at"]
        teaching.candidate(conn, task, consulted=True)
        assert conn.execute("SELECT consulted_at FROM teaching_tests WHERE id=%s", (t["id"],)).fetchone()[
            "consulted_at"
        ]
    report = client.post(
        path + "/test-report",
        json={
            "expected_revision": 3,
            "test_id": str(t["id"]),
            "passed": True,
            "observations": "I verified the saved result",
        },
    )
    assert report.status_code == 409
    with db.connection() as conn:
        conn.execute("UPDATE bots SET role=role||' changed' WHERE id='researcher'")
        with pytest.raises(Exception):
            teaching.candidate(conn, task)


def test_draft_validation_and_cancel_fences_generation(client):
    sid = session()
    path = "/api/v1/bots/researcher/teaching/" + str(sid)
    assert (
        client.put(
            path + "/draft",
            json={"expected_revision": 1, "name": "hermbot-" + ("a" * 60), "content": "not a skill"},
        ).status_code
        == 422
    )
    assert (
        client.put(
            path + "/draft",
            json={
                "expected_revision": 1,
                "name": "hermbot-test",
                "content": "---\nname: hermbot-other\ndescription: Wrong name\n---\nDo something",
            },
        ).status_code
        == 422
    )
    with db.connection() as conn:
        conn.execute(
            "UPDATE teaching_sessions SET job_state='running',job_token=%s WHERE id=%s", (uuid4(), sid)
        )
    assert client.post(path + "/cancel-generation", json={"expected_revision": 1}).status_code == 200
    with db.connection() as conn:
        assert (
            conn.execute("SELECT job_token FROM teaching_sessions WHERE id=%s", (sid,)).fetchone()[
                "job_token"
            ]
            is None
        )


def test_generate_uses_only_reviewed_input_without_colour_gate(client, monkeypatch):
    sid = session()
    path = "/api/v1/bots/researcher/teaching/" + str(sid)


    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def raise_for_status(self):
            pass

        def iter_bytes(self):
            import json

            yield json.dumps(
                {
                    "choices": [
                        {
                            "message": {
                                "content": json.dumps(
                                    {
                                        "vision_check": ["incorrect", "colours"],
                                        "summary": "A local procedure", "changes": [], "questions": [], "suggested_inputs": "Fresh record", "suggested_checks": "Read back the record",
                                        "name": "hermbot-generated",
                                        "content": "---\nname: hermbot-generated\ndescription: Save local test records\n---\nUse fresh input and read back the result.",
                                    }
                                )
                            }
                        }
                    ]
                }
            ).encode()

    class HTTP:
        def __init__(self, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def stream(self, *args, **kwargs):
            assert "Reviewed local procedure" in kwargs["json"]["messages"][0]["content"][0]["text"]
            return Response()

    monkeypatch.setattr(teaching.httpx, "Client", HTTP)
    assert client.post(path + "/generate", json={"expected_revision": 1}).status_code == 200
    teaching.generate_one()
    detail = client.get(path).json()
    assert detail["job_state"] == "completed" and detail["draft_hash"]
    with db.connection() as conn:
        row = conn.execute("SELECT draft_encrypted FROM teaching_sessions WHERE id=%s", (sid,)).fetchone()
        assert "Use fresh input" not in row["draft_encrypted"]
        assert "Use fresh input" in decrypt(row["draft_encrypted"])


def test_publish_rejects_unverified_or_stale_candidate(client):
    sid = session()
    path = "/api/v1/bots/researcher/teaching/" + str(sid)
    r = post(
        client,
        path + "/publish",
        {"expected_revision": 1, "test_id": str(uuid4()), "expected_native_hash": "0" * 64, "enable": True},
    )
    assert r.status_code == 409


def test_prepared_session_waits_for_owner_start(client, capture_native, objects):
    first = post(
        client,
        "/api/v1/bots/researcher/teaching",
        {"goal": "Save local form", "expected_result": "Record exists"},
    ).json()
    teaching.collect(UUID(first["id"]), "researcher")
    assert client.get("/api/v1/bots/researcher/teaching/" + first["id"]).json()["state"] == "preparing"
    assert (
        post(
            client,
            "/api/v1/bots/researcher/teaching/" + first["id"] + "/control",
            {"expected_revision": 1, "action": "discard"},
        ).status_code
        == 200
    )
    teaching.maintain()
    assert client.get("/api/v1/bots/researcher/teaching/" + first["id"]).status_code == 404


def test_failed_persistence_does_not_refresh_heartbeat(monkeypatch):
    clock = [100.0]
    monkeypatch.setattr("docker.teaching_capture.time.monotonic", lambda: clock[0])
    c = Capture()
    c.control("id", "start", "viewer", 1, 1)
    c.observe([{"kind": "key", "down": True, "keysym": 97}], "viewer", 1, "800x600")
    clock[0] = 110
    c.batch(0)
    clock[0] = 116
    c.observe([{"kind": "key", "down": True, "keysym": 98}], "viewer", 1, "800x600")
    assert c.state == "paused"
    assert len(c.events) == 1
    with pytest.raises(ValueError):
        c.control("id", "finish", "viewer", 1, 1)


def test_normalized_typing_preserves_unknown_shortcuts():
    values = [{"kind": "key", "down": True, "keysym": ord(c)} for c in "test"]
    values.extend(
        [
            {"kind": "key", "down": True, "keysym": 0xFFE3},
            {"kind": "key", "down": True, "keysym": ord("s")},
            {"kind": "omitted", "reason": "private step"},
        ]
    )
    summary = teaching.summarize(values)
    assert summary[0].endswith("test")
    assert "modifiers" in summary[1] and "private step" in summary[2]


def test_exact_candidate_publication_versions_and_stale_evidence(client, monkeypatch):
    sid = session()
    path = "/api/v1/bots/researcher/teaching/" + str(sid)
    content = "---\nname: hermbot-local-test\ndescription: Save local records\n---\nSave the fresh record and read it back."
    assert (
        client.put(
            path + "/draft", json={"expected_revision": 1, "name": "hermbot-local-test", "content": content}
        ).status_code
        == 200
    )
    result = post(
        client,
        path + "/test",
        {
            "expected_revision": 2,
            "inputs": "Save beta",
            "auto_refine": False,
            "checks": "Read back beta and ensure alpha is unchanged",
        },
    )
    assert result.status_code == 200, result.text
    task_id = UUID(result.json()["task_id"])
    with db.connection() as conn:
        conn.execute(
            "UPDATE tasks SET status='completed',output='Verified local beta record' WHERE id=%s", (task_id,)
        )
        t = conn.execute(
            "UPDATE teaching_tests SET consulted_at=now() WHERE task_id=%s RETURNING *", (task_id,)
        ).fetchone()
    assert (
        client.post(
            path + "/test-report",
            json={
                "expected_revision": 3,
                "test_id": str(t["id"]),
                "passed": True,
                "observations": "I read the saved beta record and checked alpha stayed unchanged",
            },
        ).status_code
        == 200
    )
    files = {}

    def rpc(bot, method, url, **kw):
        if method == "GET":
            text = files.get(url, "")
            return {
                "content": text,
                "sha256": hashlib.sha256(text.encode()).hexdigest(),
                "exists": bool(text),
            }
        body = kw["json"]
        assert body["expected_hash"] == hashlib.sha256(files.get(url, "").encode()).hexdigest()
        files[url] = body["content"]
        return {"sha256": hashlib.sha256(body["content"].encode()).hexdigest()}

    monkeypatch.setattr(native, "request", rpc)
    # Changing reviewed evidence invalidates an otherwise accepted result.
    with db.connection() as conn:
        conn.execute("UPDATE teaching_sessions SET evidence_hash=%s WHERE id=%s", ("c" * 64, sid))
    body = {
        "expected_revision": 4,
        "test_id": str(t["id"]),
        "expected_native_hash": hashlib.sha256(b"").hexdigest(),
        "enable": False,
    }
    assert post(client, path + "/publish", body).status_code == 409
    assert not files
    with db.connection() as conn:
        conn.execute("UPDATE teaching_sessions SET evidence_hash=%s WHERE id=%s", (t["evidence_hash"], sid))
    key = str(uuid4())
    published = post(client, path + "/publish", body, key)
    assert published.status_code == 200, published.text
    assert post(client, path + "/publish", body, key).json() == published.json()
    assert files["/skills/hermbot-local-test"] == content
    assert published.json()["status"] == "refresh_pending"
    with db.connection() as conn:
        assert conn.execute("SELECT count(*) n FROM teaching_publications").fetchone()["n"] == 1
        assert conn.execute("SELECT count(*) n FROM knowledge_versions").fetchone()["n"] == 1
        assert not registry.require_bot(conn, "researcher")["enabled_skills"]


def test_deleting_source_stops_active_candidate_test(client):
    sid = session()
    path = "/api/v1/bots/researcher/teaching/" + str(sid)
    content = "---\nname: hermbot-delete-test\ndescription: Verify local deletion\n---\nRead the test fixture and report."
    assert (
        client.put(
            path + "/draft", json={"expected_revision": 1, "name": "hermbot-delete-test", "content": content}
        ).status_code
        == 200
    )
    result = post(
        client,
        path + "/test",
        {
            "expected_revision": 2,
            "inputs": "Read the fixture",
            "checks": "Verify the source exists and report",
        },
    )
    assert result.status_code == 200, result.text
    task_id = UUID(result.json()["task_id"])
    assert client.request("DELETE", path, json={"expected_revision": 3}).status_code == 200
    with db.connection() as conn:
        assert service.locked_task(conn, task_id)["status"] == "cancelled"
        assert teaching.candidate(conn, service.locked_task(conn, task_id)) is None


def test_readonly_viewer_updates_and_disconnect_do_not_pause_owner():
    capture = Capture()
    capture.control("session", "start", "owner-viewer", 1, 1)
    capture.observe([], "readonly-viewer", None, "")
    capture.disconnect("readonly-viewer")
    assert capture.state == "recording"
    capture.disconnect("owner-viewer")
    assert capture.state == "paused"


@pytest.fixture
def model_response(monkeypatch):
    import copy
    from contextlib import contextmanager
    import json
    import httpx

    def install(output, *, during=None, responses=None):
        sent = []
        pending = list(responses or [])

        class HTTP:
            def __init__(self, **kwargs):
                pass

            def __enter__(self):
                return self

            def __exit__(self, *args):
                pass

            @contextmanager
            def stream(self, method, url, **kwargs):
                sent.append(copy.deepcopy(kwargs['json']))
                if during:
                    during()
                response = pending.pop(0) if pending else httpx.Response(
                    200, json={'choices': [{'message': {'content': json.dumps(output)}}]})
                response.request = httpx.Request(method, url)
                yield response

        monkeypatch.setattr(teaching.httpx, 'Client', HTTP)
        return sent

    return install


def teaching_draft(client):
    sid = session()
    path = '/api/v1/bots/researcher/teaching/' + str(sid)
    content = '---\nname: hermbot-feedback\ndescription: "Write local notes"\n---\nUse the note textbox; verify displayed text.'
    assert client.put(path + '/draft', json={'expected_revision': 1, 'name': 'hermbot-feedback', 'content': content}).status_code == 200
    return sid, path, content


def settled_test(client, sid, path, *, output='Observed old text remained. Owner clarification is needed.'):
    revision = client.get(path).json()['revision']
    result = post(client, path + '/test', {'expected_revision': revision, 'inputs': 'Write a fresh test note', 'checks': 'Read back the text; preserve existing notes'} )
    assert result.status_code == 200, result.text
    task_id = result.json()['task_id']
    with db.connection() as conn:
        conn.execute("UPDATE tasks SET status='completed',output=%s WHERE id=%s", (output, task_id))
        test = conn.execute('UPDATE teaching_tests SET consulted_at=now() WHERE task_id=%s RETURNING *', (task_id,)).fetchone()
    return task_id, test


def generated(content, *, changes=None, questions=None):
    return {'name': 'hermbot-feedback', 'content': content, 'summary': 'Updated from confirmed feedback.',
            'changes': changes or [], 'questions': questions or [],
            'suggested_inputs': 'Write a fresh local note', 'suggested_checks': 'Read back exact text; ask if existing text is present'}


def test_feedback_revises_exact_candidate_and_keeps_sources_scoped(client, model_response, make_task):
    import json
    sid, path, original = teaching_draft(client)
    task_id, test = settled_test(client, sid, path)
    other = make_task(bot='writer')
    with db.connection() as conn:
        for task, answer in ((task_id, 'Use the sole textbox; no separate title.'), (other['id'], 'UNRELATED_PRIVATE_ANSWER')):
            conn.execute("INSERT INTO input_requests(id,task_id,prompt,answer_type,question_hash,status,answer,answer_source,answered_at,expires_at) VALUES(%s,%s,'Which control?','text',%s,'answered',%s,'owner',now(),now()+interval '1 hour')", (uuid4(), task, 'a'*64, answer))
    teaching.auto_refine_one()
    teaching.auto_refine_one()
    with db.connection() as conn:
        refs = conn.execute('SELECT * FROM teaching_refinements WHERE session_id=%s', (sid,)).fetchall()
        assert len(refs) == 1
        frozen = teaching.decode(refs[0]['sources_encrypted'])
    serialized = json.dumps(frozen)
    assert 'Use the sole textbox' in serialized and 'UNRELATED_PRIVATE_ANSWER' not in serialized
    assert 'test_report_not_independent_proof' in serialized
    revised = original + '\nOwner-confirmed: use the sole textbox without a separate title.'
    sent = model_response(generated(revised, changes=[{'text': 'Use the confirmed textbox.', 'source_ids': [s['id'] for s in frozen['sources'] if s['kind'].startswith('owner_answer')]}]))
    teaching.generate_one()
    detail = client.get(path).json()
    assert detail['draft'] == revised and detail['job_state'] == 'completed'
    assert not detail['tests'][0]['current'] and not detail['tests'][0]['owner_passed']
    assert detail['publications'] == []
    assert 'Use the sole textbox' in json.dumps(sent)
    with db.connection() as conn:
        saved = conn.execute('SELECT candidate_encrypted FROM teaching_tests WHERE id=%s', (test['id'],)).fetchone()
        assert decrypt(saved['candidate_encrypted']) == original
    result = post(client, path+'/test', {'expected_revision': detail['revision'], 'inputs': 'Write another note', 'checks': 'Exact readback and no overwrite'})
    assert result.status_code == 200
    with db.connection() as conn:
        task = conn.execute('SELECT * FROM tasks WHERE id=%s', (result.json()['task_id'],)).fetchone()
        assert teaching.candidate(conn, task)['content'] == revised


def test_teaching_questions_continue_after_completed_test_and_bind_answers(client, model_response):
    sid, path, original = teaching_draft(client)
    _, test = settled_test(client, sid, path)
    teaching.auto_refine_one()
    model_response(generated(original, questions=[{'prompt': 'Append to existing text or replace it?', 'source_ids': ['test:'+str(test['id'])]}]))
    teaching.generate_one()
    detail = client.get(path).json()
    question = detail['learning']['questions'][0]
    blocked = post(client, path+'/test', {'expected_revision': detail['revision'], 'inputs':'New note', 'checks':'Read back note'})
    assert blocked.status_code == 409
    body = {'expected_revision': detail['revision'], 'question_id': question['id'], 'answer':'Ask before replacing existing notes.', 'scope':'workflow'}
    assert client.post(path+'/answer', json=body).status_code == 200
    assert client.post(path+'/answer', json=body).status_code == 409
    latest = client.get(path).json()
    assert latest['job_state'] == 'queued'
    with db.connection() as conn:
        ref = conn.execute("SELECT * FROM teaching_refinements WHERE session_id=%s ORDER BY created_at DESC LIMIT 1", (sid,)).fetchone()
        sources = teaching.decode(ref['sources_encrypted'])['sources']
        answer = next(s for s in sources if s['id'].startswith('teaching-answer:'))
        assert answer['scope'] == 'workflow' and 'Ask before replacing' in answer['answer']
    revised = original+'\nAsk before replacing existing notes.'
    sent = model_response(generated(revised, changes=[{'text':'Preserve notes unless replacement is approved.', 'source_ids':[answer['id']]}]))
    teaching.generate_one()
    final = client.get(path).json()
    assert final['learning']['questions'] == [] and final['draft'] == revised
    assert 'scope=this_test apply only to that execution' in sent[0]['messages'][0]['content'][0]['text']


def test_one_run_answer_is_scoped_and_old_question_cannot_edit_new_draft(client, model_response):
    sid, path, original = teaching_draft(client)
    _, test = settled_test(client, sid, path)
    teaching.auto_refine_one()
    model_response(generated(original, questions=[{'prompt':'How should this test handle existing text?', 'source_ids':['test:'+str(test['id'])]}]))
    teaching.generate_one()
    detail=client.get(path).json()
    q=detail['learning']['questions'][0]
    response=client.post(path+'/answer',json={'expected_revision':detail['revision'],'question_id':q['id'],'answer':'Leave this note unchanged and stop.','scope':'this_test'})
    assert response.status_code==200
    with db.connection() as conn:
        ref=conn.execute('SELECT * FROM teaching_refinements WHERE session_id=%s ORDER BY created_at DESC LIMIT 1',(sid,)).fetchone()
        sources=teaching.decode(ref['sources_encrypted'])['sources']
        assert next(s for s in sources if s['id'].startswith('teaching-answer:'))['scope']=='this_test'
    model_response(generated(original))
    teaching.generate_one()
    current=client.get(path).json()
    assert client.post(path+'/answer',json={'expected_revision':current['revision'],'question_id':q['id'],'answer':'Replace everything','scope':'workflow'}).status_code==409


def test_refinement_waits_for_live_attempts_and_fences_manual_edits(client, model_response):
    sid,path,original=teaching_draft(client)
    task_id,test=settled_test(client,sid,path)
    with db.connection() as conn:
        conn.execute("INSERT INTO attempts(id,task_id,bot_id,number,session_id,status,idempotency_key,input) VALUES(%s,%s,'researcher',1,'test','running',%s,'test')",(uuid4(),task_id,str(uuid4())))
    teaching.auto_refine_one()
    assert client.get(path).json()['job_state']=='idle'
    with db.connection() as conn:
        conn.execute("UPDATE attempts SET status='completed' WHERE task_id=%s",(task_id,))
    teaching.auto_refine_one()
    edited=original+'\nOwner manual edit wins.'
    def edit():
        detail=client.get(path).json()
        assert client.put(path+'/draft',json={'expected_revision':detail['revision'],'name':'hermbot-feedback','content':edited}).status_code==200
    model_response(generated(original+'\nLate model result.'),during=edit)
    teaching.generate_one()
    assert client.get(path).json()['draft']==edited
    with db.connection() as conn:
        assert conn.execute('SELECT state FROM teaching_refinements WHERE session_id=%s',(sid,)).fetchone()['state']=='stale'


def test_feedback_cancellation_and_untrusted_source_ids(client, model_response):
    sid,path,original=teaching_draft(client)
    settled_test(client,sid,path)
    teaching.auto_refine_one()
    model_response(generated(original,changes=[{'text':'Unproven change','source_ids':['answer:invented']}]))
    teaching.generate_one()
    detail=client.get(path).json()
    assert detail['job_state']=='failed' and detail['draft']==original
    assert 'reads images' not in detail['error']
    teaching.auto_refine_one()
    assert client.get(path).json()['job_state']=='failed'
    assert client.post(path+'/generate',json={'expected_revision':detail['revision']}).status_code==200
    def cancel():
        now=client.get(path).json()
        assert client.post(path+'/cancel-generation',json={'expected_revision':now['revision']}).status_code==200
    model_response(generated(original+'\nCancelled output.'),during=cancel)
    teaching.generate_one()
    assert client.get(path).json()['draft']==original


def test_provider_parameter_fallback_and_specific_errors(client,model_response):
    import httpx
    sid,path,original=teaching_draft(client)
    response=httpx.Response(400,json={'error':{'code':'unsupported_parameter','param':'max_tokens'}})
    sent=model_response(generated(original),responses=[response])
    assert client.post(path+'/generate',json={'expected_revision':2}).status_code==200
    teaching.generate_one()
    assert client.get(path).json()['job_state']=='completed'
    assert 'max_completion_tokens' in sent[1] and 'max_tokens' not in sent[1]
    for status,expected,code in ((401,'authentication',None),(401,'expired','expired_secret_key'),(429,'limit',None),(400,'HTTP 400',None)):
        detail=client.get(path).json()
        model_response(generated(original),responses=[httpx.Response(status,json={'error':{'message':'PRIVATE_PROVIDER_BODY','code':code}})])
        assert client.post(path+'/generate',json={'expected_revision':detail['revision']}).status_code==200
        teaching.generate_one()
        detail=client.get(path).json()
        assert detail['job_state']=='failed' and expected in detail['error']
        assert 'PRIVATE_PROVIDER_BODY' not in detail['error']


def test_dismiss_pauses_and_reports_only_durable_prefix(client,capture_native):
    result=post(client,'/api/v1/bots/researcher/teaching',{'goal':'Local notes','expected_result':'Readback'})
    assert result.status_code==200
    sid=result.json()['id']
    path='/api/v1/bots/researcher/teaching/'+sid
    assert post(client,path+'/control',{'expected_revision':1,'action':'start'}).status_code==200
    response=client.post('/api/v1/bots/researcher/teaching-dismiss')
    assert response.status_code==200 and response.json()['capture']['state']=='paused'
    assert response.json()['capture']['last_seq']==0
    assert capture_native.state=='paused' and capture_native.reason!='private input'
    assert client.post('/api/v1/bots/researcher/teaching-dismiss').status_code==200


def test_owner_chat_corrections_retained_without_unrelated_chat(client,model_response,make_task):
    import json
    sid,path,original=teaching_draft(client)
    task_id,test=settled_test(client,sid,path)
    other=make_task(bot='researcher')
    correction='Keep existing notes unless I approve replacing them. '+('detail '*700)+'Do not erase.'
    with db.connection() as conn:
        for task,text in ((task_id,correction),(other['id'],'UNRELATED_SAME_BOT_CHAT')):
            t=conn.execute('SELECT * FROM tasks WHERE id=%s',(task,)).fetchone()
            db.message(conn,t,'owner',text,'feedback-test:'+str(uuid4()),role='user')
    teaching.auto_refine_one()
    with db.connection() as conn:
        row=conn.execute('SELECT * FROM teaching_refinements WHERE session_id=%s',(sid,)).fetchone()
        data=teaching.decode(row['sources_encrypted'])
    assert correction in json.dumps(data) and 'UNRELATED_SAME_BOT_CHAT' not in json.dumps(data)
    message=next(s for s in data['sources'] if s.get('text')==correction)
    assert message['text'].endswith('Do not erase.')
    model_response(generated(original))
    teaching.generate_one()
    assert client.get(path).json()['job_state']=='completed'


def test_saved_manual_draft_invalidates_previous_teaching_question(client,model_response):
    sid,path,original=teaching_draft(client)
    _,test=settled_test(client,sid,path)
    teaching.auto_refine_one()
    model_response(generated(original,questions=[{'prompt':'Which note field?', 'source_ids':['test:'+str(test['id'])]}]))
    teaching.generate_one()
    row=client.get(path).json()
    q=row['learning']['questions'][0]
    assert client.put(path+'/draft',json={'expected_revision':row['revision'],'name':'hermbot-feedback','content':original+'\nManually specified field.'}).status_code==200
    current=client.get(path).json()
    assert current['learning']['questions']==[]
    assert client.post(path+'/answer',json={'expected_revision':current['revision'],'question_id':q['id'],'answer':'Old answer','scope':'workflow'}).status_code==409


def test_pending_learning_cannot_be_published_and_failure_is_not_a_pass(client,model_response):
    sid,path,original=teaching_draft(client)
    _,test=settled_test(client,sid,path)
    row=client.get(path).json()
    assert client.post(path+'/test-report',json={'expected_revision':row['revision'],'test_id':str(test['id']),'passed':True,'observations':'Owner inspected the saved note result.'}).status_code==200
    row=client.get(path).json()
    result=post(client,path+'/publish',{'expected_revision':row['revision'],'test_id':str(test['id']),'expected_native_hash':'0'*64})
    assert result.status_code==409 and 'feedback' in result.json()['detail']
    teaching.auto_refine_one()
    model_response(generated(original+'\nClarified procedure.'))
    teaching.generate_one()
    current=client.get(path).json()
    assert current['tests'][0]['owner_passed'] and not current['tests'][0]['current']
    assert post(client,path+'/publish',{'expected_revision':current['revision'],'test_id':str(test['id']),'expected_native_hash':'0'*64}).status_code==409


def test_owner_message_answers_and_explicit_steering_are_learning_sources(client,model_response):
    import json
    sid,path,original=teaching_draft(client)
    task_id,_=settled_test(client,sid,path)
    with db.connection() as conn:
        for source,text in (('owner_message','Confirmed note field'),('manager','UNTRUSTED_MANAGER_ANSWER')):
            conn.execute("INSERT INTO input_requests(id,task_id,prompt,answer_type,question_hash,status,answer,answer_source,answered_at,expires_at) VALUES(%s,%s,'Which field?','text',%s,'answered',%s,%s,now(),now()+interval '1 hour')",(uuid4(),task_id,'a'*64,text,source))
        conn.execute("INSERT INTO controls(id,task_id,input,source) VALUES(%s,%s,'Stop this run; leave the current note unchanged.','owner')",(uuid4(),task_id))
    teaching.auto_refine_one()
    with db.connection() as conn:
        frozen=teaching.decode(conn.execute('SELECT sources_encrypted FROM teaching_refinements WHERE session_id=%s',(sid,)).fetchone()['sources_encrypted'])
    text=json.dumps(frozen)
    assert 'Confirmed note field' in text and 'leave the current note unchanged' in text
    assert 'UNTRUSTED_MANAGER_ANSWER' not in text
    sent=model_response(generated(original))
    teaching.generate_one()
    assert 'one-off stop/leave-as-is instructions' in sent[0]['messages'][0]['content'][0]['text']


def test_deletion_removes_learning_sources_and_expiry_blocks_processing(client,model_response,objects,monkeypatch):
    sid,path,original=teaching_draft(client)
    settled_test(client,sid,path)
    teaching.auto_refine_one()
    sent=model_response(generated(original))
    with db.connection() as conn:
        conn.execute("UPDATE teaching_sessions SET expires_at=now()-interval '1 hour' WHERE id=%s",(sid,))
    teaching.generate_one()
    assert not sent
    monkeypatch.setattr(native,'request',lambda *args,**kwargs:{'session':'another-session'})
    teaching.maintain()
    with db.connection() as conn:
        row=conn.execute('SELECT * FROM teaching_sessions WHERE id=%s',(sid,)).fetchone()
        ref=conn.execute('SELECT * FROM teaching_refinements WHERE session_id=%s',(sid,)).fetchone()
        assert row['state']=='deleted' and row['learning_encrypted'] is None
        assert teaching.decode(ref['sources_encrypted'])=={} and ref['result_encrypted'] is None
        assert ref['state']=='cancelled'


def test_multiple_questions_wait_for_all_answers_and_reject_other_bot(client,model_response):
    sid,path,original=teaching_draft(client)
    _,test=settled_test(client,sid,path)
    teaching.auto_refine_one()
    questions=[{'prompt':prompt,'source_ids':['test:'+str(test['id'])]} for prompt in ('Which field receives notes?','How should old notes be handled?')]
    model_response(generated(original,questions=questions))
    teaching.generate_one()
    row=client.get(path).json()
    first,second=row['learning']['questions']
    body={'expected_revision':row['revision'],'question_id':first['id'],'answer':'Use the note textbox.','scope':'workflow'}
    assert client.post(path.replace('/researcher/','/writer/')+'/answer',json=body).status_code==404
    assert client.post(path+'/answer',json=body).status_code==200
    row=client.get(path).json()
    assert row['job_state']=='completed'
    assert row['learning']['questions'][0]['answer']=='Use the note textbox.'
    assert client.post(path+'/answer',json={'expected_revision':row['revision'],'question_id':second['id'],'answer':'Ask before replacing.','scope':'workflow'}).status_code==200
    assert client.get(path).json()['job_state']=='queued'


def test_oversized_feedback_is_visible_and_never_silently_truncated(client):
    sid,path,_=teaching_draft(client)
    task_id,_=settled_test(client,sid,path)
    with db.connection() as conn:
        for i in range(4):
            task=conn.execute('SELECT * FROM tasks WHERE id=%s',(task_id,)).fetchone()
            db.message(conn,task,'owner','Owner correction '+str(i)+': '+('x'*28000)+' Do not erase.',str(uuid4()),role='user')
    teaching.auto_refine_one()
    row=client.get(path).json()
    assert row['job_state']=='failed' and 'too large' in row['error']
    with db.connection() as conn:
        assert not conn.execute('SELECT 1 FROM teaching_refinements WHERE session_id=%s',(sid,)).fetchone()


def test_provider_change_during_generation_fences_late_draft(client,model_response):
    sid,path,original=teaching_draft(client)
    settled_test(client,sid,path)
    teaching.auto_refine_one()
    def change_provider():
        with db.connection() as conn:
            provider=conn.execute("SELECT provider_id FROM bots WHERE id='researcher'").fetchone()['provider_id']
            conn.execute('UPDATE providers SET revision=revision+1 WHERE id=%s',(provider,))
    model_response(generated(original+'\nStale provider output.'),during=change_provider)
    teaching.generate_one()
    current=client.get(path).json()
    assert current['draft']==original and current['job_state']=='cancelled'
    assert 'late result was not applied' in current['error']


def test_new_owner_failure_observation_reopens_failed_refinement_once(client,model_response):
    sid,path,original=teaching_draft(client)
    _,test=settled_test(client,sid,path)
    teaching.auto_refine_one()
    model_response(generated(original,changes=[{'text':'Invalid source','source_ids':['unrelated:source']}]))
    teaching.generate_one()
    row=client.get(path).json()
    assert row['job_state']=='failed'
    assert client.post(path+'/test-report',json={'expected_revision':row['revision'],'test_id':str(test['id']),'passed':False,'observations':'Owner readback found unexpected old note text.'}).status_code==200
    assert client.get(path).json()['job_state']=='idle'
    teaching.auto_refine_one()
    with db.connection() as conn:
        refs=conn.execute('SELECT * FROM teaching_refinements WHERE session_id=%s ORDER BY created_at',(sid,)).fetchall()
        assert len(refs)==2
        sources=teaching.decode(refs[-1]['sources_encrypted'])['sources']
        assert any(s.get('kind')=='owner_observation' and s['passed'] is False for s in sources)
    teaching.auto_refine_one()
    with db.connection() as conn:
        assert conn.execute('SELECT count(*) AS n FROM teaching_refinements WHERE session_id=%s',(sid,)).fetchone()['n']==2

    model_response(generated(original))
    teaching.generate_one()
    row=client.get(path).json()
    assert client.post(path+'/test-report',json={'expected_revision':row['revision'],'test_id':str(test['id']),'passed':False,'observations':'Owner readback found unexpected old note text.'}).status_code==200
    teaching.auto_refine_one()
    with db.connection() as conn:
        assert conn.execute('SELECT count(*) AS n FROM teaching_refinements WHERE session_id=%s',(sid,)).fetchone()['n']==2
        assert conn.execute('SELECT refinement_id FROM teaching_tests WHERE id=%s',(test['id'],)).fetchone()['refinement_id'] is not None
    assert client.get(path).json()['job_state']!='queued'
