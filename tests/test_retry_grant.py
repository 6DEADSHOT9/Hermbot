"""Retrying work never restores management authority the owner revoked. A retry keeps its work's own grant, and
renewing a task grant for 24 hours happens on every team retry, only after the owner confirms it."""
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


def retry(owner, task, **body):
    return owner.post(f"/api/v1/tasks/{task['id']}/retry", json=body, headers={"Idempotency-Key": str(uuid4())})


def confirmed(owner, task):
    """A team retry as the owner makes it: asked first, then confirmed."""
    asked = retry(owner, task)
    assert asked.status_code == 409 and asked.json()["detail"]["code"] == "renewal_required", asked.text
    return retry(owner, task, renew_management=True)


def grant_count():
    with db.connection() as conn:
        return conn.execute("SELECT count(*) AS n FROM grants").fetchone()["n"]


def task_row(task_id):
    with db.connection() as conn:
        return conn.execute("SELECT * FROM tasks WHERE id=%s", (task_id,)).fetchone()


def grant_row(grant_id):
    with db.connection() as conn:
        return conn.execute("SELECT * FROM grants WHERE id=%s", (grant_id,)).fetchone()


def names():
    with db.connection() as conn:
        rows = conn.execute("SELECT id,name FROM bots WHERE id IN ('researcher','writer')").fetchall()
    return {row["id"]: row["name"] for row in rows}


def test_every_team_retry_asks_in_the_servers_words_and_renews_the_same_grant_for_24_hours(owner):
    task, grant = manager_task()
    settle(task["id"])
    before = grant_count()
    asked = retry(owner, task)
    assert asked.status_code == 409
    assert asked.json()["detail"] == {"code": "renewal_required",
                                      "message": f"Retry and let {names()['researcher']} manage {names()['writer']} for another 24 hours?"}
    response = retry(owner, task, renew_management=True)
    assert response.status_code == 202, response.text
    replacement = task_row(response.json()["id"])
    current = grant_row(grant["id"])
    assert replacement["grant_id"] == grant["id"] and grant_count() == before, "The same grant is renewed, never copied"
    assert 23.9 < (current["expires_at"] - service.now()).total_seconds() / 3600 <= 24
    with db.connection() as conn:
        event = conn.execute("SELECT data FROM events WHERE kind='grant.renewed' AND task_id=%s", (task["id"],)).fetchone()
    assert event and event["data"]["grant_id"] == str(grant["id"]) and event["data"]["targets"] == ["writer"]


def test_an_expired_grant_is_renewed_the_same_way(owner):
    task, grant = manager_task()
    with db.connection() as conn:
        conn.execute("UPDATE grants SET expires_at=now()-interval '1 day' WHERE id=%s", (grant["id"],))
    settle(task["id"])
    response = confirmed(owner, task)
    assert response.status_code == 202 and grant_row(grant["id"])["expires_at"] > service.now()


def test_a_revoked_grant_is_refused_and_never_renewed(owner):
    task, grant = manager_task()
    assert owner.post(f"/api/v1/grants/{grant['id']}/revoke").status_code == 200
    assert task_row(task["id"])["status"] == "cancelled"
    before = grant_count()
    for body in ({}, {"renew_management": True}):
        response = retry(owner, task, **body)
        assert response.status_code == 409
        assert response.json()["detail"] == (f"{names()['researcher']}'s permission to manage teammates for this work was revoked. "
                                             "To grant it again, send the request again with Coordinate team.")
    assert grant_count() == before and grant_row(grant["id"])["revoked_at"]


def test_a_retry_records_the_work_it_came_from(owner):
    task, grant = manager_task()
    settle(task["id"])
    first = task_row(confirmed(owner, task).json()["id"])
    assert first["retried_from"] == task["id"]
    settle(first["id"])
    second = task_row(confirmed(owner, first).json()["id"])
    assert second["retried_from"] == task["id"], "Every retry points at the work's first run"


def test_a_second_retry_is_refused_while_the_first_is_running(owner):
    task, grant = manager_task()
    settle(task["id"])
    assert confirmed(owner, task).status_code == 202
    second = retry(owner, task, renew_management=True)
    assert second.status_code == 409 and second.json()["detail"] == "A retry of this work is already running. Use Redirect on it instead."


def test_a_retry_stopped_at_its_budget_points_to_continue(owner):
    task, grant = manager_task()
    settle(task["id"])
    first = confirmed(owner, task).json()
    settle(first["id"], "paused_budget")
    second = retry(owner, task, renew_management=True)
    assert second.status_code == 409 and second.json()["detail"] == "A retry of this work is stopped at its budget. Continue it instead."


def test_other_pieces_of_one_message_sharing_the_grant_do_not_block_a_retry(owner):
    task, grant = manager_task()
    with db.connection() as conn:
        sibling = service.create_task(conn, bot_id="researcher", conversation_id=conversation(conn), prompt="The other piece of the message",
                                      request_key=str(uuid4()))
        conn.execute("UPDATE tasks SET grant_id=%s WHERE id=%s", (grant["id"], sibling["id"]))  # As intake shares it.
    settle(task["id"])
    assert task_row(sibling["id"])["status"] == "queued"
    assert confirmed(owner, task).status_code == 202


def test_the_old_run_must_be_settled_before_its_grant_is_renewed(owner):
    task, grant = manager_task()
    with db.connection() as conn:
        child = service.create_task(conn, bot_id="writer", conversation_id=conversation(conn, "writer"), prompt="A queued handoff", request_key=str(uuid4()))
        conn.execute("UPDATE tasks SET root_id=%s,parent_id=%s,grant_id=%s,depth=1 WHERE id=%s", (task["id"], task["id"], grant["id"], child["id"]))
    settle(task["id"])
    response = retry(owner, task, renew_management=True)
    assert response.status_code == 409 and response.json()["detail"] == "Wait for the entire old operation to settle before retrying"


def test_a_spent_handoff_budget_is_refused_instead_of_renewed(owner):
    task, grant = manager_task()
    with db.connection() as conn:
        conn.execute("UPDATE tasks SET handoffs=%s,status='failed' WHERE id=%s", (grant["max_handoffs"], task["id"]))
    response = retry(owner, task, renew_management=True)
    assert response.status_code == 409 and "used all its handoffs" in response.json()["detail"]
    assert grant_row(grant["id"])["expires_at"] == grant["expires_at"], "Nothing was renewed"


def test_a_task_grant_shares_its_handoff_budget_across_retries(owner):
    task, grant = manager_task()
    with db.connection() as conn:
        conn.execute("UPDATE tasks SET handoffs=%s,status='failed' WHERE id=%s", (grant["max_handoffs"] - 1, task["id"]))
    replacement = task_row(confirmed(owner, task).json()["id"])
    with db.connection() as conn:
        conn.execute("UPDATE tasks SET handoffs=1 WHERE id=%s", (replacement["id"],))  # The retry used the last one.
        with pytest.raises(HTTPException) as exhausted:
            service.delegate(conn, service.locked_task(conn, replacement["id"]), "writer", "Draft the report section")
    assert exhausted.value.status_code == 409 and exhausted.value.detail == "Task handoff budget exhausted"


def test_a_retry_notices_a_managed_teammate_was_deleted(owner):
    task, grant = manager_task()
    settle(task["id"])
    with db.connection() as conn:
        conn.execute("UPDATE grants SET targets=targets||'[\"gone-teammate\"]'::jsonb WHERE id=%s", (grant["id"],))
    response = retry(owner, task)
    assert response.status_code == 409 and "no longer exists" in response.json()["detail"], "Refused before asking to renew"


def test_a_retried_manager_task_is_recorded_as_managed(owner):
    task, grant = manager_task()
    settle(task["id"])
    replacement = confirmed(owner, task).json()
    with db.connection() as conn:
        event = conn.execute("SELECT data FROM events WHERE kind='task.queued' AND task_id=%s", (replacement["id"],)).fetchone()
    assert event["data"]["manager"] is True


def test_revoking_after_a_retry_stops_the_original_and_the_retry(owner):
    task, grant = manager_task()
    settle(task["id"])
    replacement = confirmed(owner, task).json()
    assert owner.post(f"/api/v1/grants/{grant['id']}/revoke").status_code == 200
    assert task_row(replacement["id"])["status"] == "cancelled", "Revocation reaches every run of the work"
    assert retry(owner, task_row(replacement["id"]), renew_management=True).status_code == 409


def test_a_retry_does_not_hand_out_the_teammate_creation_allowance_again(owner):
    task, grant = manager_task(operations=("delegate", "create"))
    with db.connection() as conn:
        conn.execute("UPDATE grants SET created_bots=max_created_bots WHERE id=%s", (grant["id"],))
    settle(task["id"])
    replacement = confirmed(owner, task).json()
    held = grant_row(task_row(replacement["id"])["grant_id"])
    assert held["created_bots"] == held["max_created_bots"] == 1, "The allowance was already used"


def standing(expiry="7 days"):
    with db.connection() as conn:
        grant = conn.execute("INSERT INTO grants(id,manager_id,targets,operations,max_handoffs,expires_at,kind) "
                             "VALUES(%s,'researcher','[\"writer\"]','[\"delegate\"]',5,now()+%s::interval,'standing') RETURNING *",
                             (uuid4(), expiry)).fetchone()
        task = service.create_task(conn, bot_id="researcher", conversation_id=conversation(conn), prompt="Use the standing grant",
                                   request_key=str(uuid4()), grant_id=grant["id"])
    return task, grant


def test_a_revoked_standing_grant_is_refused(owner):
    task, grant = standing()
    owner.post(f"/api/v1/grants/{grant['id']}/revoke")
    response = retry(owner, task)
    assert response.status_code == 409 and "choose an active permission or send the request again with Coordinate team" in response.json()["detail"]


def test_an_active_standing_grant_is_kept_without_asking(owner):
    task, grant = standing()
    settle(task["id"])
    response = retry(owner, task)
    assert response.status_code == 202 and task_row(response.json()["id"])["grant_id"] == grant["id"]
    assert grant_row(grant["id"])["expires_at"] == grant["expires_at"], "A standing grant keeps the duration the owner chose"


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
        assert other_manager.value.status_code == 403
