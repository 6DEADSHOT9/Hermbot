"""Current owner-action eligibility, shared by inbox and conversation readers."""

from fastapi import HTTPException

from . import service


def valid(conn, request, task, *, review=False):
    if not task or request["status"] != "pending" or request["expires_at"] <= service.now():
        return False
    if task["status"] in service.TERMINAL | service.STOPPING or (
        task["deadline"] and task["deadline"] <= service.now()
    ):
        return False
    chat = conn.execute(
        "SELECT archived_at FROM conversations WHERE id=%s", (task["conversation_id"],)
    ).fetchone()
    if not chat or chat["archived_at"]:
        return False
    try:
        service.check_grant(conn, task, lock_scope=False)
    except HTTPException:
        return False
    if review and request["kind"] == "runtime":
        return bool(
            conn.execute(
                "SELECT 1 FROM attempts WHERE id=%s AND status=ANY(%s)",
                (request["attempt_id"], list(service.ACTIVE_ATTEMPTS) + ["reserved"]),
            ).fetchone()
        )
    if not review and request.get("outcome_id"):
        return bool(
            conn.execute(
                "SELECT 1 FROM work_outcomes WHERE id=%s AND revision=%s AND status<>'cancelled'",
                (request["outcome_id"], request["outcome_revision"]),
            ).fetchone()
        )
    return True
