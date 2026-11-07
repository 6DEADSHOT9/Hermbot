"""Observed execution activity; connection contact is deliberately separate from progress."""


def snapshots(conn, task_ids):
    if not task_ids:
        return {}
    rows = conn.execute("""
        SELECT t.id, a.started_at, a.last_contact_at, a.status AS execution_status,
               p.created_at AS last_progress_at,
               e.kind AS last_tool_event, e.created_at AS last_tool_at, e.data->>'tool' AS last_tool,
               m.waiting_model_requests
        FROM tasks t
        LEFT JOIN LATERAL (SELECT * FROM attempts WHERE task_id=t.id ORDER BY number DESC LIMIT 1) a ON true
        LEFT JOIN LATERAL (
            SELECT created_at FROM events WHERE task_id=t.id AND created_at>=COALESCE(a.created_at,t.created_at)
            AND kind IN ('tool.completed','artifact.published','work.outcome_recorded','message.delta')
            ORDER BY id DESC LIMIT 1
        ) p ON true
        LEFT JOIN LATERAL (
            SELECT kind,created_at,data FROM events WHERE task_id=t.id AND created_at>=COALESCE(a.created_at,t.created_at)
            AND kind IN ('tool.admitted','tool.started','tool.completed') ORDER BY id DESC LIMIT 1
        ) e ON true
        LEFT JOIN LATERAL (
            SELECT count(*) FILTER (WHERE kind='model.request.waiting') AS waiting_model_requests
            FROM (
                SELECT DISTINCT ON (data->>'request_id') kind FROM events
                WHERE task_id=t.id AND created_at>=COALESCE(a.created_at,t.created_at)
                AND kind IN ('model.request.waiting','model.request.active','model.request.finished')
                ORDER BY data->>'request_id',id DESC
            ) latest
        ) m ON true
        WHERE t.id=ANY(%s)
    """, (task_ids,)).fetchall()
    return {row['id']: {key: value for key, value in row.items() if key != 'id'} for row in rows}
