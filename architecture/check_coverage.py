#!/usr/bin/env python3
"""Completeness gate for the Hermbot architecture explorer.

Writes architecture/coverage-report.md, stores the summary in model.json["coverage"], and re-renders index.html.
Exit code 0 only when every check passes. `.env` values are compared in memory (substring and sha256 of
tokens) and are never printed or written; only key names appear in the report.
"""
import hashlib
import json
import os
import re
import sys
from collections import Counter
from pathlib import Path

OUT = Path(__file__).resolve().parent
ROOT = OUT.parent
EXCLUDED_DIRS = {"node_modules", "__pycache__", ".pytest_cache", ".ruff_cache", ".git", "architecture"}
FEATURES = ["lifecycle", "conversations", "intake", "outcomes", "coordination", "execution", "attention", "artifacts",
            "knowledge", "tools", "computer", "connectors", "routines", "events", "management", "notifications", "auth",
            "runtime-gate", "ops", "testing"]
SECTIONS = ["purpose", "logic", "components", "data", "decisions", "invariants", "failures", "security", "reimplement",
            "tests", "limits"]
FLOWS = list("abcdefghijkl")
TAGS = {"documented", "inferred", "unknown"}
EV_RE = re.compile(r"^([\w./@+-]+?):(\d+)(?:-(\d+))?$")


def walk_files():
    found = []
    for dirpath, dirnames, filenames in os.walk(ROOT):
        dirnames[:] = [d for d in dirnames if d not in EXCLUDED_DIRS]
        for f in filenames:
            found.append(str((Path(dirpath) / f).relative_to(ROOT)))
    return sorted(found)


def line_counts():
    cache = {}

    def get(p):
        if p not in cache:
            fp = ROOT / p
            if p == ".env" or not fp.is_file():
                cache[p] = None
            else:
                try:
                    cache[p] = fp.read_text().count("\n") + 1
                except (UnicodeDecodeError, OSError):
                    cache[p] = -1  # binary: any line accepted
        return cache[p]
    return get


def nonempty(v):
    if v is None:
        return False
    if isinstance(v, str):
        return bool(v.strip())
    if isinstance(v, (list, dict)):
        return any(nonempty(x) for x in (v.values() if isinstance(v, dict) else v))
    return True


def main():
    model = json.loads((OUT / "model.json").read_text())
    mech = json.loads((OUT / "_work" / "mech.json").read_text())
    nodes = {n["id"]: n for n in model["nodes"]}
    edges = model["edges"]
    checks = []

    def check(name, missing, detail):
        checks.append({"name": name, "pass": not missing, "detail": detail, "missing": sorted(set(missing))[:200],
                       "missing_count": len(set(missing))})

    # 1. files
    files = walk_files()
    idx = model["file_index"]
    miss = []
    for f in files:
        nid = idx.get(f)
        if not nid or nid not in nodes:
            miss.append(f)
        elif nid.startswith("group:") and f not in nodes[nid]["facts"].get("files", []):
            miss.append(f)
    represented = len(files) - len(set(miss))
    check("Every non-excluded file maps to a node (count matches a fresh walk of the repo)",
          miss + ([f"count mismatch: walk={len(files)} model={len(idx)}"] if len(files) != len(idx) else []),
          f"{represented}/{len(files)} files represented; inventory={len(idx)}")

    # 2. inventories
    py_mods = [p for p in mech["python"] if not p.startswith(("tests/", "test-results/"))]
    check("Every Python module has a node", [p for p in py_mods if f"file:{p}" not in nodes], f"{len(py_mods)} modules")
    routes = [f"route:{p}:{r['method']} {r['path']}" for p, v in mech["python"].items() for r in v.get("routes", [])]
    check("Every HTTP route has a node", [r for r in routes if r not in nodes], f"{len(routes)} routes")
    colmiss = []
    for t in mech["sql"]["tables"].values():
        n = nodes.get(f"table:{t['name']}")
        if not n:
            colmiss.append(f"table:{t['name']}")
            continue
        have = {c["name"] for c in n["facts"].get("columns", [])}
        colmiss += [f"{t['name']}.{c['name']}" for c in t["columns"] if c["name"] not in have]
    ncols = sum(len(t["columns"]) for t in mech["sql"]["tables"].values())
    check("Every table and column is present", colmiss, f"{len(mech['sql']['tables'])} tables, {ncols} columns")
    comp = []
    for cf, c in mech["compose"].items():
        comp += [f"svc:{s}" for s in c["services"]] + [f"net:{n}" for n in c["networks"]] + [f"vol:{v}" for v in c["volumes"]]
        for s in c["services"].values():
            for p in s.get("ports") or []:
                comp.append(f"port:{p.rsplit(':', 1)[0] if p.count(':') == 2 else p}")
    check("Every compose service, network, volume and port is present", [c for c in comp if c not in nodes], f"{len(set(comp))} compose objects")
    envs = {e["name"] for e in mech["env_example"]}
    for c in mech["compose"].values():
        envs |= set(c["env_refs"])
        for s in c["services"].values():
            envs |= set(s.get("env_names") or [])
    for v in mech["python"].values():
        envs |= {e for e in v.get("env", []) if not e.startswith("<")}
    check("Every env var name is present", [f"env:{e}" for e in envs if f"env:{e}" not in nodes], f"{len(envs)} env var names")
    tools = [f"tool:{t['name']}" for t in mech["plugin_tools"]]
    check("Every plugin tool and hook is present", [t for t in tools if t not in nodes], f"{len(tools)} tools/hooks")
    webfiles = [f for f in files if f.startswith("web/") and not f.startswith("web/dist/")]
    check("Every web file is present", [f for f in webfiles if f"file:{f}" not in nodes], f"{len(webfiles)} web files")
    calls, callmiss, unmatched = 0, [], []
    disc_text = " ".join(d.get("text", "") for d in model["discrepancies"])
    for p, w in mech["web"].items():
        for c in w.get("calls", []):
            calls += 1
            rec = [x for x in nodes.get(f"file:{p}", {"facts": {}})["facts"].get("api_calls", []) if x["line"] == c["line"] and x["path"] == c["path"]]
            if not rec:
                callmiss.append(f"{p}:{c['line']}")
            elif not rec[0].get("route"):
                unmatched.append(f"{p}:{c['line']}")
    unexplained = [u for u in unmatched if u not in disc_text]
    check("Every UI API call is recorded and either mapped to a route or explained as a discrepancy", callmiss + unexplained,
          f"{calls} calls; {calls - len(unmatched)} mapped to routes; {len(unmatched)} explained as discrepancies")

    opened_miss = [f["path"] for f in mech["files"] if f["path"] != ".env"
                   and (f.get("binary") or f["path"].startswith(("test-results/", ".taste-skill/", "plan/archive/")))
                   and (not f.get("media") or f["media"].get("error"))]
    n_opened = sum(1 for f in mech["files"] if f.get("media") and not f["media"].get("error"))
    check("Every binary, evidence and archive file was opened and summarised (image size, JUnit counts, JSON keys, gzip records)",
          opened_miss, f"{n_opened} files opened mechanically")

    # 3. features
    fmiss, tagmiss = [], []
    # the required list plus any feature found later (e.g. phase 6.2 teaching)
    slugs = FEATURES + sorted(k.split(":", 1)[1] for k in model["features"] if k.split(":", 1)[1] not in FEATURES)
    for slug in slugs:
        f = model["features"].get(f"feature:{slug}")
        if not f:
            fmiss.append(f"feature:{slug} (missing)")
            continue
        for s in SECTIONS:
            if not nonempty(f.get(s)):
                fmiss.append(f"feature:{slug}.{s}")
        for d in f.get("decisions") or []:
            if isinstance(d, dict) and d.get("tag") not in TAGS:
                tagmiss.append(f"feature:{slug}: {str(d.get('decision'))[:60]}")
    check("Every feature has every panel section filled", fmiss, f"{len(slugs)} features x {len(SECTIONS)} sections")
    check("Every design rationale is labelled documented/inferred/unknown", tagmiss,
          f"{sum(len(f.get('decisions') or []) for f in model['features'].values())} decisions")

    # 4. flows
    flmiss = []
    for x in FLOWS + sorted(k.split(":", 1)[1] for k in model["flows"] if k.split(":", 1)[1] not in FLOWS):
        fl = model["flows"].get(f"flow:{x}")
        if not fl or not fl.get("steps"):
            flmiss.append(f"flow:{x} (no steps)")
            continue
        for i, s in enumerate(fl["steps"], 1):
            if not s.get("evidence"):
                flmiss.append(f"flow:{x} step {i} without evidence")
    check("Every flow (a–l and any added later) has steps and every step is evidenced", flmiss,
          f"{sum(len(f.get('steps', [])) for f in model['flows'].values())} steps across {len(model['flows'])} flows")

    # 5. evidence on nodes and edges, and validity of citations
    nev = [n["id"] for n in nodes.values() if not n.get("evidence") and "unknown" not in json.dumps(n.get("facts", {})).lower()]
    eev = [f"{e['s']} -{e['k']}-> {e['t']}" for e in edges if not e.get("ev")]
    check("Every node is evidenced or marked unknown", nev, f"{len(nodes)} nodes")
    check("Every edge is evidenced or marked unknown", eev, f"{len(edges)} edges")
    get = line_counts()
    bad, total = [], 0

    def scan(obj):
        nonlocal total
        if isinstance(obj, dict):
            for k, v in obj.items():
                if k in ("evidence", "ev"):
                    for e in (v if isinstance(v, list) else [v]):
                        if not isinstance(e, str):
                            continue
                        total += 1
                        m = EV_RE.match(e.strip())
                        if not m:
                            if e.strip().lower() not in ("[unknown]", "unknown"):
                                bad.append(f"malformed: {e[:80]}")
                            continue
                        n = get(m.group(1))
                        if n is None:
                            bad.append(f"no such file: {e}")
                        elif n != -1 and int(m.group(3) or m.group(2)) > n:
                            bad.append(f"line beyond end ({n}): {e}")
                else:
                    scan(v)
        elif isinstance(obj, list):
            for v in obj:
                scan(v)
    scan(model)
    check("Every evidence citation points at an existing file and line", bad, f"{total} citations checked")

    # dangling references
    check("No edge points at an unresolved node", model.get("dangling", []), f"{len(model.get('dangling', []))} unresolved references")

    # 6. secrets
    env_vals = {}
    envp = ROOT / ".env"
    if envp.is_file():
        for line in envp.read_text().splitlines():
            m = re.match(r"\s*([A-Z][A-Z0-9_]*)\s*=\s*(.*)$", line)
            if m:
                v = m.group(2).strip().strip('"').strip("'")
                if len(v) >= 8:
                    env_vals[m.group(1)] = v
    public_text = "\n".join((ROOT / p).read_text() for p in ("compose.yaml", ".env.example", "README.md"))
    outputs = {p.name: p.read_text() for p in (OUT / "model.json", OUT / "index.html") if p.exists()}
    token_hashes = set()
    for text in outputs.values():
        for tok in re.findall(r"[A-Za-z0-9_\-+/=.:@]{8,}", text):
            token_hashes.add(hashlib.sha256(tok.encode()).hexdigest())
    leaks, public = [], []
    for k, v in env_vals.items():
        h = hashlib.sha256(v.encode()).hexdigest()
        hit = h in token_hashes or any(v in t for t in outputs.values())
        if hit:
            sensitive = bool(re.search(r"TOKEN|KEY|PASSWORD|SECRET", k))
            if not sensitive and v in public_text:
                public.append(k)
            else:
                leaks.append(k)
    check("No .env value appears in model.json or index.html (sha256 token match + substring, values never printed)", leaks,
          f"{len(env_vals)} .env values checked; {len(public)} non-secret values equal public defaults ({', '.join(public) or 'none'})")

    # 7. freshness: every file's current content was read (read ledger) - catches files added/changed after the last pass
    import time as _t
    ledger_p = OUT / "_work" / "read_ledger.json"
    ledger = json.loads(ledger_p.read_text()) if ledger_p.exists() else {}
    hashed = {f["path"]: f["sha256"] for f in mech["files"] if f.get("sha256")}
    unread_new = sorted(p for p in hashed if p not in ledger)
    unread_changed = sorted(p for p in hashed if p in ledger and ledger[p]["sha256"] != hashed[p])
    removed = sorted(p for p in ledger if p not in hashed)
    check("Every file's current content has been read (sha256 matches the read ledger; new or edited files must be re-read)",
          [f"new: {p}" for p in unread_new] + [f"changed: {p}" for p in unread_changed] + [f"deleted, still in ledger: {p}" for p in removed],
          f"{len(hashed)} files hashed; {len(ledger)} in read ledger")
    freshness = {"built_at": _t.strftime("%Y-%m-%d %H:%M"), "files_hashed": len(hashed), "ledger_entries": len(ledger),
                 "unread_new": unread_new, "unread_changed": unread_changed, "deleted": removed,
                 "recent_reads": sorted(({"path": p, **v} for p, v in ledger.items() if v.get("read_by") != ["initial mapping run"]),
                                        key=lambda x: x.get("read_at", ""), reverse=True)[:40]}

    cats = Counter(f["category"] for f in mech["files"])
    passed = all(c["pass"] for c in checks)
    coverage = {"pass": passed, "files_total": len(files), "files_represented": represented,
                "summary": f"{sum(c['pass'] for c in checks)}/{len(checks)} checks pass. {represented}/{len(files)} files represented; "
                           f"{len(nodes)} nodes, {len(edges)} edges; {sum(model['excluded'].values())} files in excluded directories are listed, not modelled.",
                "checks": checks, "by_category": dict(sorted(cats.items())), "freshness": freshness}
    model["coverage"] = coverage
    (OUT / "model.json").write_text(json.dumps(model, indent=1, ensure_ascii=False))
    sys.path.insert(0, str(OUT))
    from merge_model import render
    render()
    md = ["# Coverage report", "", f"**Result: {'PASS' if passed else 'FAIL'}** — {coverage['summary']}", "",
          "| Check | Result | Detail |", "|---|---|---|"]
    for c in checks:
        md.append(f"| {c['name']} | {'pass' if c['pass'] else 'FAIL'} | {c['detail']}{' — missing: ' + str(c['missing_count']) if c['missing'] else ''} |")
    for c in checks:
        if c["missing"]:
            md += ["", f"## Missing: {c['name']}", ""] + [f"- `{m}`" for m in c["missing"]]
    md += ["", "## Files by category", ""] + [f"- {k}: {v}" for k, v in coverage["by_category"].items()]
    md += ["", "## Excluded directories (listed, not modelled)", ""] + [f"- `{k}/`: {v} files" for k, v in model["excluded"].items()]
    if model.get("not_fully_read"):
        md += ["", "## Files not fully read", ""] + [f"- `{x.get('path')}` — {x.get('why')} ({x.get('agent')})" for x in model["not_fully_read"]]
    (OUT / "coverage-report.md").write_text("\n".join(md) + "\n")
    for c in checks:
        print(("PASS " if c["pass"] else "FAIL ") + c["name"] + " :: " + c["detail"] + (f" :: missing {c['missing_count']}" if c["missing"] else ""))
    sys.exit(0 if passed else 1)


if __name__ == "__main__":
    main()
