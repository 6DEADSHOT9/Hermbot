from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4

from fastapi.encoders import jsonable_encoder

from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from psycopg_pool import ConnectionPool

from .settings import settings

pool = ConnectionPool(settings.database_url, min_size=1, max_size=12, open=False, kwargs={"row_factory": dict_row})


@contextmanager
def connection():
    with pool.connection() as conn:
        with conn.transaction():
            yield conn
            from .timeline import publish
            publish(conn)


def initialize():
    pool.open(wait=True, timeout=30)
    with connection() as conn:
        conn.execute("SELECT pg_advisory_xact_lock(887832101)")
        conn.execute(Path(__file__).with_name("schema.sql").read_text())
        for migration in sorted(Path(__file__).with_name("migrations").glob("*.sql")):
            conn.execute(migration.read_text())
        for bot_id, name, role, color, capabilities in (
            ("researcher", "Scout", "Research, evidence, and data analysis", "#cf8d42", ["research", "data", "code"]),
            ("writer", "Quill", "Writing, synthesis, and clear deliverables", "#728eca", ["writing", "data", "code"]),
            ("reviewer", "Sage", "Review, code validation, and coordination", "#829c77", ["review", "code", "research"]),
        ):
            conn.execute(
                "INSERT INTO bots(id,name,role,color,capabilities,runtime_url) VALUES (%s,%s,%s,%s,%s,%s) "
                "ON CONFLICT(id) DO NOTHING",
                (bot_id, name, role, color, Jsonb(capabilities), f"http://hermes-{bot_id}:8642"),
            )
            if not conn.execute("SELECT id FROM conversations WHERE kind='dm' AND bot_id=%s", (bot_id,)).fetchone():
                conversation = uuid4()
                conn.execute("INSERT INTO conversations(id,title,kind,bot_id) VALUES (%s,%s,'dm',%s)", (conversation, name, bot_id))
                conn.execute("INSERT INTO participants VALUES (%s,%s)", (conversation, bot_id))
        from .registry import bootstrap
        bootstrap(conn)


def event(conn, kind, task=None, *, conversation_id=None, bot_id=None, data=None, source_id=None):
    task = task or {}
    return conn.execute(
        "INSERT INTO events(kind,conversation_id,task_id,bot_id,data,source_id) VALUES (%s,%s,%s,%s,%s,%s) "
        "ON CONFLICT(source_id) DO NOTHING RETURNING id",
        (kind, conversation_id or task.get("conversation_id"), task.get("id"), bot_id or task.get("bot_id"), Jsonb(jsonable_encoder(data or {})), source_id),
    ).fetchone()


def message(conn, task, author, content, source_id, role="assistant"):
    conn.execute(
        "INSERT INTO messages(conversation_id,author,role,content,task_id,source_id,reply_to_message_id,attachment_ids) VALUES (%s,%s,%s,%s,%s,%s,%s,%s) "
        "ON CONFLICT(source_id) DO NOTHING",
        (task["conversation_id"], author, role, content, task["id"], source_id, task.get("reply_to_message_id"), Jsonb(task.get("attachment_ids", []) if role == "user" else [])),
    )
