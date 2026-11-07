"""Read-only deployment snapshot; execute inside the API, never initialize/migrate its DB."""
import hashlib
import json

from backend import db


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str).encode()).hexdigest()


db.pool.open(wait=True)
try:
    with db.connection() as conn:
        conn.execute('SET TRANSACTION READ ONLY')
        groups = conn.execute('SELECT id FROM conversations ORDER BY id').fetchall()
        bots = conn.execute('SELECT id,name,role,color,avatar,provider_id,model_name,enabled_tools,enabled_skills,memory_mb,cpus,revision FROM bots ORDER BY id').fetchall()
        evidence = conn.execute('SELECT id,evidence_hash,retained_bytes,last_seq FROM teaching_sessions ORDER BY id').fetchall()
        active = conn.execute("SELECT count(*) AS n FROM tasks WHERE status IN ('running','cancelling','pausing_budget')").fetchone()['n']
    print(json.dumps({'conversations': {'count': len(groups), 'sha256': digest(groups)}, 'bots': {'count': len(bots), 'sha256': digest(bots)},
                      'teaching_evidence': {'count': len(evidence), 'sha256': digest(evidence)}, 'active_executions': active}))
finally:
    db.pool.close()
