# Shared brief for semantic reading agents

Project: Hermbot (paths below are relative to the repository root). It's a private local workspace where persistent "Hermes" AI teammates (bots) collaborate. The pieces:
- a FastAPI backend (`backend/`), backed by PostgreSQL and MinIO
- worker processes
- a supervisor that owns Docker
- per-bot Hermes runtime containers (`docker/`), plus the Hermes plugin `plugins/hermbot`
- a React/Vite UI (`web/`)

We are building one interactive architecture explorer. Your job is to read your assigned files completely and line by line, then write structured JSON findings.

## Hard rules
- Read-only toward the project. Never create, modify or delete any project file. The ONLY file you write is your output JSON: `architecture/_work/agents/<your-agent-name>.json`. You may also write scratch files under `architecture/_work/agents/scratch-<agent>/` if needed.
- NEVER open, cat, grep or print `.env`. Use `.env.example` and `compose.yaml` for config names. Never copy secret values anywhere.
- No docker commands, no running tests, no network calls.
- Code is the source of truth. If README or plan docs say something the code contradicts, record a discrepancy.
- EVERY claim needs evidence as `path:line` (repo-relative, e.g. `backend/worker.py:212`, or a range like `backend/worker.py:212-240`). Verify the line numbers by reading; don't guess.
- Every design rationale must carry a tag:
  - `documented`: stated in plan/*.md, README, ISSUES or a code comment; cite it.
  - `inferred`: deduced from code structure; give the reasoning.
  - `unknown`
- Be exhaustive within your scope. Completeness matters more than brevity. The final product must miss nothing.

## Node ID conventions (use EXACTLY these; the valid IDs are listed in `architecture/_work/ids.txt`)
- Files and modules: `file:<relpath>`, e.g. `file:backend/worker.py`
- Functions and classes (top-level): `fn:<relpath>::<name>`. For nested functions, such as route handlers inside a `routes()` factory, use `fn:<relpath>::<outer>.<inner>`.
- Routes: `route:<relpath>:<METHOD> <path>`, e.g. `route:backend/app.py:POST /api/v1/messages`
- Tables: `table:<name>`. Services: `svc:<compose service>`; dynamic bot containers: `svc:hermbot-bot-*`
- Networks: `net:<name>`, with dynamic per-bot networks as `net:hermbot-bot-*`. Volumes: `vol:<name>`, with dynamic per-bot volumes as `vol:hermbot-bot-*-<suffix>`.
- Env vars: `env:<NAME>`. Plugin tools: `tool:<name>`. Plugin hooks: `tool:pre_llm_call`, `tool:on_session_end`.
- Actors: `actor:owner`, `actor:model-provider`, `actor:<provider-slug>` (e.g. `actor:github`, `actor:slack`, `actor:google-gmail`), `actor:browser-push-service`, `actor:docker-engine`
- Object store: `bucket:<name>` (find the real bucket names in code)
- Features: `feature:<slug>`, from this FIXED list (add new slugs only if you find a real feature that isn't covered; keep slugs short):
  - `feature:lifecycle` (teammates create/edit/clone/archive/restore/start/stop/delete/provisioning)
  - `feature:conversations` (chat, groups, threads, reactions, mentions, attachments)
  - `feature:intake` (work intake/routing, work items, revisions, remove/restore)
  - `feature:outcomes` (required results, outcomes, result acceptance, evidence, receipts, hermbot_finish)
  - `feature:coordination` (hermbot_message/delegate/wait, dependencies, replacement, teammate messages)
  - `feature:execution` (execution policy, monitoring, budgets, stop/redirect/steer/retry, uncertain blocking)
  - `feature:attention` (owner inputs, approvals, decisions, attention)
  - `feature:artifacts` (artifacts, file library, MinIO, checksums, shares/grants)
  - `feature:knowledge` (memory and skills)
  - `feature:tools` (capabilities, tools, tool references, native tools, tool admission)
  - `feature:computer` (private bot computers, noVNC, CUA)
  - `feature:connectors` (connections, catalog, OAuth, HTTP, MCP, provider reviews, action modes)
  - `feature:routines` (schedules, overlap/missed policy, drafts)
  - `feature:events` (event triggers, providers, own-write suppression)
  - `feature:management` (recurring management, standing grants, manager authority, lifecycle proposals)
  - `feature:notifications` (web push)
  - `feature:auth` (auth, sessions, secrets, encryption, workspace settings, providers)
  - `feature:runtime-gate` (runtime gate, restart/crash recovery)
  - `feature:ops` (scripts: configure, upgrade_config, check, stop, audits)
  - `feature:testing` (test architecture, protocol fixture)
- Flows: `flow:a` … `flow:l`:
  - a: owner message > intake > work item > dispatch > native run > events > outcome > acceptance > UI
  - b: teammate coordination (message/delegate/wait/finish/read_artifact)
  - c: connector action > review/approval > provider call > receipt/reconciliation
  - d: routine tick > admission > run > history
  - e: event trigger (poll/webhook) > trigger_worker > work
  - f: manager lifecycle proposal > owner approval > supervisor outbox > provisioning > manager resumes
  - g: notification fan-out > web push > service worker
  - h: stop / retry / recovery after worker or container restart
  - i: login/session and owner auth
  - j: bot create/clone/delete (provisioning path)
  - k: attachment upload > MinIO > install into bot workspace
  - l: startup sequence of the stack (compose depends_on/healthchecks, db initialize, migrations, bootstrap)
- State machines: `sm:<slug>`; states: `state:<sm-slug>:<state>`

## Edge kinds (use only these)
`imports, calls, http, reads, writes, stores-in, runs-in, attached-to, mounts, uses-secret, spawns, polls, webhooks, pushes, fetches, renders, tests, evidences, documents, implements, transitions`
(The import graph is already extracted mechanically. Only add `imports` edges for things the AST can't see, e.g. dynamic imports or the plugin importing runtime modules.)

## Output JSON shape (all top-level keys optional; include what applies to your scope)
```json
{
  "agent": "<name>",
  "files_read": ["<relpath>", "..."],
  "files_not_fully_read": [{"path": "...", "why": "..."}],
  "module_notes": {
    "<relpath>": {
      "purpose": "one line",
      "layer": "api|domain|persistence|runtime-control|connectors|workers|notifications|runtime|plugin|ui|ops|test|config|doc",
      "responsibilities": ["..."],
      "functions": [{"name": "fn name (or outer.inner)", "line": 123, "desc": "one line"}],
      "env": ["NAME"],
      "notes": [{"text": "...", "evidence": ["path:line"]}]
    }
  },
  "route_notes": {
    "<route id>": {"purpose": "...", "auth": "...", "request": "...", "response": "...", "tables": ["table:x"], "ui_callers": ["file:web/src/X.tsx"], "evidence": ["path:line"]}
  },
  "table_notes": {"table:x": {"purpose": "...", "writers": ["file:..."], "readers": ["file:..."], "state_machine": "sm:...", "evidence": ["path:line"]}},
  "extra_nodes": [{"id": "...", "kind": "actor|service|network|volume|bucket|port|secret|function|concept", "label": "...", "purpose": "...", "facts": {}, "evidence": ["path:line"]}],
  "edges": [{"source": "<id>", "target": "<id>", "kind": "<edge kind>", "label": "short", "evidence": ["path:line"]}],
  "features": [{
    "id": "feature:<slug>", "title": "...",
    "purpose": "problem solved + user-visible behaviour",
    "logic": [{"step": "...", "evidence": ["path:line"]}],
    "components": ["<node ids: files, fns, routes, tables, tools, ui>"],
    "data": [{"text": "tables/fields owned or touched", "evidence": ["..."]}],
    "state_machines": ["sm:..."],
    "decisions": [{"decision": "...", "why": "...", "alternatives": "...", "tradeoffs": "...", "tag": "documented|inferred|unknown", "evidence": ["..."]}],
    "invariants": [{"text": "...", "enforcement": "how the code enforces it", "evidence": ["..."]}],
    "failures": [{"text": "failure mode + recovery", "evidence": ["..."]}],
    "security": [{"text": "...", "evidence": ["..."]}],
    "reimplement": {"order": ["step 1", "..."], "model": ["minimal tables/fields"], "interfaces": ["key APIs/functions"], "pitfalls": [{"text": "...", "evidence": ["plan/ISSUES.md:NN or code"]}]},
    "tests": {"covered": [{"text": "...", "evidence": ["tests/...:N"]}], "gaps": ["..."]},
    "limits": [{"text": "...", "evidence": ["..."]}]
  }],
  "feature_fragments": [{"id": "feature:<slug>", "<any section above>": ["items to append"]}],
  "flows": [{"id": "flow:a", "title": "...", "summary": "...", "steps": [{"text": "what happens", "nodes": ["ids involved"], "evidence": ["path:line"]}]}],
  "state_machines": [{"id": "sm:<slug>", "entity": "...", "table": "table:x", "column": "status", "states": [{"name": "...", "desc": "...", "terminal": false}], "transitions": [{"from": "...", "to": "...", "trigger": "...", "evidence": ["path:line"]}]}],
  "discrepancies": [{"text": "doc says X, code does Y (or dead/legacy path, empty file, inconsistency)", "severity": "high|medium|low", "nodes": ["ids"], "evidence": ["path:line", "..."]}],
  "system_design": {"<section>": [{"text": "...", "tag": "documented|inferred|unknown", "evidence": ["..."]}]},
  "unknowns": [{"text": "...", "nodes": ["..."]}]
}
```
System design section keys: `topology`, `control-vs-data-plane`, `isolation`, `durability`, `concurrency`, `workers`, `recovery`, `security`, `observability`, `scaling-spof`, `production-path`, `tradeoffs`. For `tradeoffs`, each item is `{"decision", "benefit", "cost", "tag", "evidence"}`.

Write valid JSON (check it with `python3 -m json.tool`). If your output is large, that's expected. When you're done, reply with a short summary: files read, counts of each section, and the top discrepancies.
