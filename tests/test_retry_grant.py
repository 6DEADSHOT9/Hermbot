"""Retrying work never restores management authority the owner revoked or let lapse; it keeps the original grant."""
from uuid import uuid4

from fastapi import HTTPException
from fastapi.testclient import TestClient
import pytest

from backend import app as application, db, service
from backend.settings import settings

pytestmark = pytest.mark.integration


@pytest.fixture
def owner():
    client = TestClient(application.app, headers={"Authorization": "Bearer " + settings.owner_token})
    yield client
    client.close()


def conversation(conn, bot="researcher"):
    return conn.execute("SELECT id FROM conversations WHERE bot_id=%s", (bot,)).fetchone()["id"]


def manager_task(operations=("delegate", "inspect")):
    with db.connection() as conn:
        task = service.create_task(conn, bot_id="researcher", conversation_id=conversation(conn), prompt="Coordinate the writer on the report",
                                   request_key=str(uuid4()), managed_bot_ids=["writer"], management_operations=list(operations))
        return task, conn.execute("SELECT * FROM grants WHERE id=%s", (task["grant_id"],)).fetchone()


def settle(task_id, status="failed"):
    with db.connection() as conn:
        conn.execute("UPDATE tasks SET status=%s WHERE id=%s", (status, task_id))


def retry(owner, task):
    return owner.post(f"/api/v1/tasks/{task['id']}/retry", json={}, headers={"Idempotency-Key": str(uuid4())})


def grant_count():
    with db.connection() as conn:
        return conn.execute("SELECT count(*) AS n FROM grants").fetchone()["n"]


def task_row(task_id):
    with db.connection() as conn:
        return conn.execute("SELECT * FROM tasks WHERE id=%s", (task_id,)).fetchone()


def test_retrying_work_whose_grant_was_revoked_is_refused_with_the_reason(owner):
    task, grant = manager_task()
    assert owner.post(f"/api/v1/grants/{grant['id']}/revoke").status_code == 200
    assert task_row(task["id"])["status"] == "cancelled"
    before = grant_count()
    response = retry(owner, task)
    assert response.status_code == 409
    with db.connection() as conn:
        name = conn.execute("SELECT name FROM bots WHERE id='researcher'").fetchone()["name"]
    assert response.json()["detail"] == (f"{name}'s permission to manage teammates for this work was revoked. "
                                         "To grant it again, send the request again with Coordinate team.")
    assert grant_count() == before, "No new grant is issued"


def test_retrying_work_whose_grant_expired_is_refused(owner):
    task, grant = manager_task()
    with db.connection() as conn:
        conn.execute("UPDATE grants SET expires_at=now()-interval '1 hour' WHERE id=%s", (grant["id"],))
    settle(task["id"])
    before = grant_count()
    response = retry(owner, task)
    assert response.status_code == 409 and "has expired" in response.json()["detail"]
    assert grant_count() == before


def test_a_retry_keeps_the_original_grant_and_its_expiry(owner):
    task, grant = manager_task()
    settle(task["id"])
    before = grant_count()
    response = retry(owner, task)
    assert response.status_code == 202
    replacement = task_row(response.json()["id"])
    assert replacement["grant_id"] == grant["id"], "The retry holds the original grant, not a copy"
    assert grant_count() == before
    with db.connection() as conn:
        assert conn.execute("SELECT expires_at FROM grants WHERE id=%s", (grant["id"],)).fetchone()["expires_at"] == grant["expires_at"]


def test_revoking_after_a_retry_stops_the_original_and_the_retry(owner):
    task, grant = manager_task()
    settle(task["id"])
    replacement = retry(owner, task).json()
    assert owner.post(f"/api/v1/grants/{grant['id']}/revoke").status_code == 200
    assert task_row(replacement["id"])["status"] == "cancelled", "Revocation reaches every run of the work"
    assert retry(owner, task_row(replacement["id"])).status_code == 409


def test_a_retry_does_not_hand_out_the_teammate_creation_allowance_again(owner):
    task, grant = manager_task(operations=("delegate", "create"))
    with db.connection() as conn:
        conn.execute("UPDATE grants SET created_bots=max_created_bots WHERE id=%s", (grant["id"],))
    settle(task["id"])
    replacement = retry(owner, task).json()
    with db.connection() as conn:
        held = conn.execute("SELECT g.created_bots,g.max_created_bots FROM grants g JOIN tasks t ON t.grant_id=g.id WHERE t.id=%s", (replacement["id"],)).fetchone()
    assert held["created_bots"] == held["max_created_bots"] == 1, "The allowance was already used"


def test_a_revoked_standing_grant_is_still_refused(owner):
    with db.connection() as conn:
        standing = conn.execute("INSERT INTO grants(id,manager_id,targets,operations,max_handoffs,expires_at,kind) "
                                "VALUES(%s,'researcher','[\"writer\"]','[\"delegate\"]',5,now()+interval '7 days','standing') RETURNING *", (uuid4(),)).fetchone()
        task = service.create_task(conn, bot_id="researcher", conversation_id=conversation(conn), prompt="Use the standing grant",
                                   request_key=str(uuid4()), grant_id=standing["id"])
    owner.post(f"/api/v1/grants/{standing['id']}/revoke")
    response = retry(owner, task)
    assert response.status_code == 409 and "an active permission or Coordinate team" in response.json()["detail"]


def test_an_active_standing_grant_is_kept_on_retry(owner):
    with db.connection() as conn:
        standing = conn.execute("INSERT INTO grants(id,manager_id,targets,operations,max_handoffs,expires_at,kind) "
                                "VALUES(%s,'researcher','[\"writer\"]','[\"delegate\"]',5,now()+interval '7 days','standing') RETURNING *", (uuid4(),)).fetchone()
        task = service.create_task(conn, bot_id="researcher", conversation_id=conversation(conn), prompt="Use the standing grant",
                                   request_key=str(uuid4()), grant_id=standing["id"])
    settle(task["id"])
    response = retry(owner, task)
    assert response.status_code == 202 and task_row(response.json()["id"])["grant_id"] == standing["id"]


def test_retrying_work_without_management_is_unchanged(owner, make_task):
    task = make_task(prompt="Summarize the notes")
    settle(task["id"])
    response = retry(owner, task)
    assert response.status_code == 202 and task_row(response.json()["id"])["grant_id"] is None


def test_a_delegated_teammate_retries_without_the_managers_authority(owner):
    root, grant = manager_task()
    with db.connection() as conn:
        child = service.create_task(conn, bot_id="writer", conversation_id=conversation(conn, "writer"), prompt="Draft the report section",
                                    request_key=str(uuid4()))
        conn.execute("UPDATE tasks SET grant_id=%s,status='failed' WHERE id=%s", (grant["id"], child["id"]))  # As a delegated child holds it.
    owner.post(f"/api/v1/grants/{grant['id']}/revoke")
    response = retry(owner, task_row(child["id"]))
    assert response.status_code == 202, response.text
    assert task_row(response.json()["id"])["grant_id"] is None, "The teammate gets no management authority from the manager's grant"


def test_only_a_retry_can_inherit_a_task_grant(owner):
    task, grant = manager_task()
    with db.connection() as conn:
        with pytest.raises(HTTPException) as borrowed:
            service.create_task(conn, bot_id="researcher", conversation_id=conversation(conn), prompt="Borrow it", request_key=str(uuid4()), grant_id=grant["id"])
        assert borrowed.value.status_code == 403, "A new message can choose standing grants only"
        with pytest.raises(HTTPException) as other_manager:
            service.create_task(conn, bot_id="writer", conversation_id=conversation(conn, "writer"), prompt="Not yours", request_key=str(uuid4()), inherited_grant_id=grant["id"])
        assert other_manager.value.status_code == 409
