# Refresh pass brief: phase 6.2 "Teaching" and edits to existing files

First read `architecture/_work/AGENT_BRIEF.md`. It has the hard rules, ID conventions, edge kinds and the output JSON shape, and all of it still applies. The most important rules:
- Read-only toward the project.
- Never open `.env`.
- No docker, no tests, no network.
- Every claim carries `path:line`, using the CURRENT file's line numbers.
- Rationale is tagged documented, inferred or unknown.

Valid node IDs are listed in `architecture/_work/ids.txt`. It was regenerated today and includes the 19 teaching routes, the five `teaching_*` tables and the teaching functions.

## What changed since the explorer was built
- A new feature, "Teaching" (plan/PHASE6-TEACHING.md, plan/PHASE6-TEACHING-REVIEW.md). Its new files:
  - `backend/teaching.py`
  - `backend/migrations/018_teaching.sql`
  - `docker/teaching_capture.py`
  - `web/src/Teaching.tsx`
  - `tests/test_teaching.py`
  - `test-results/phase62-*` (25 files)
- Small edits to existing files. Exact unified diffs (old to new) are in `architecture/_work/remap/diffs/`; read them to see precisely what changed.
- Existing notes are in `architecture/model.json`. Look at a node by id with `python3 -c "import json;m=json.load(open('architecture/model.json'));print(json.dumps([n for n in m['nodes'] if n['id']=='file:backend/app.py'][0],indent=1))"`. Features are in `m['features']`, flows in `m['flows']`, and state machines in `m['state_machines']`.
- Old citations were already shifted to the new line numbers mechanically. `_work/remap/needs_check.json` lists the few that fell inside edited hunks and still need human verification.

## Extra output keys for refresh agents (besides the AGENT_BRIEF shape)
- `module_notes[<path>]`, for every changed or new file you own:
  - give `purpose` and the COMPLETE `responsibilities` list for the file as it is NOW (a refresh note replaces the earlier pass's responsibilities);
  - add `changes_since_previous_pass`: a list of `{"text", "evidence"}` describing what the edit added or changed;
  - `functions` may list only new or changed functions (they are merged by name).
- `citation_checks`: for each item in needs_check.json that you own, give `{"was": "...", "now": "...", "correct": "<verified path:line or path:a-b>", "agent": "<agent field from needs_check>"}`.
- `corrections`: earlier statements that the edits made FALSE. Each is `{"agent": "<agent json stem, e.g. work>", "find": "<exact substring in that agent's JSON>", "replace": "<new text>", "evidence": ["path:line"]}`. Look for these by searching `architecture/_work/agents/<stem>.json` with grep. Use only exact substrings.

Write your JSON to `architecture/_work/agents/<your-name>.json`, check it with `python3 -m json.tool`, and reply with a short summary.
