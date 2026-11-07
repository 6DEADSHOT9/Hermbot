#!/usr/bin/env python3
"""Merge mechanical facts (_work/mech.json) with semantic agent findings (_work/agents/*.json)
into architecture/model.json, then render architecture/index.html from explorer_template.html."""
import json
import re
from collections import defaultdict
from pathlib import Path

OUT = Path(__file__).resolve().parent
ROOT = OUT.parent
WORK = OUT / "_work"

KIND_OF_CATEGORY = {
    "backend-module": "module", "runtime-module": "runtime", "plugin": "plugin", "script": "script",
    "test": "test", "browser-test": "test", "doc": "doc", "plan-archive": "archive", "migration": "migration",
    "ui": "ui", "compose": "config", "config": "config", "dockerfile": "config", "root-module": "module",
    "backend-asset": "migration", "build-output": "misc", "secret-config": "config",
}
LAYER_FALLBACK = {
    "app.py": "api", "trigger_api.py": "api", "notification_api.py": "api", "supervisor.py": "runtime-control",
    "containers.py": "runtime-control", "provisioning.py": "runtime-control", "runtime.py": "runtime-control",
    "db.py": "persistence", "schema.sql": "persistence", "worker.py": "workers", "trigger_worker.py": "workers",
    "notification_worker.py": "workers",
}
SD_SECTIONS = ["topology", "control-vs-data-plane", "isolation", "durability", "concurrency", "workers", "recovery",
               "security", "observability", "scaling-spof", "production-path", "tradeoffs"]
FEATURE_SECTIONS = ["purpose", "logic", "components", "data", "state_machines", "decisions", "invariants", "failures",
                    "security", "reimplement", "tests", "limits"]


def load_agents():
    agents = {}
    # re-read passes (`*_refresh`) load last so their notes on edited files win over the earlier pass
    for p in sorted((WORK / "agents").glob("*.json"), key=lambda q: (q.stem.endswith("_refresh"), q.stem)):
        if p.stem.startswith("plain_"):
            continue
        try:
            agents[p.stem] = json.loads(p.read_text())
        except json.JSONDecodeError as exc:
            raise SystemExit(f"invalid JSON in {p}: {exc}")
    return agents


def load_plain():
    """Plain-language companion text (overview, glossary, per-feature/flow/state summaries, discrepancy write-ups)."""
    plain = {}
    for p in sorted((WORK / "agents").glob("plain_*.json")):
        d = json.loads(p.read_text())
        for k, v in d.items():
            if isinstance(v, dict) and isinstance(plain.get(k), dict):
                plain[k].update(v)
            elif isinstance(v, list) and isinstance(plain.get(k), list):
                plain[k].extend(v)
            else:
                plain[k] = v
    return plain


def ev_list(x):
    if not x:
        return []
    if isinstance(x, str):
        return [x]
    return [str(e) for e in x if e]


class Model:
    def __init__(self):
        self.nodes = {}
        self.edges = {}
        self.alias = {}

    def node(self, nid, kind, label, **kw):
        n = self.nodes.get(nid)
        if not n:
            n = self.nodes[nid] = {"id": nid, "kind": kind, "label": label, "evidence": [], "facts": {}}
        for k, v in kw.items():
            if v in (None, "", [], {}):
                continue
            if k == "evidence":
                for e in ev_list(v):
                    if e not in n["evidence"]:
                        n["evidence"].append(e)
            elif k == "facts":
                for fk, fv in v.items():
                    if fv not in (None, "", [], {}):
                        n["facts"][fk] = fv
            elif k == "purpose":
                if not n.get("purpose"):
                    n["purpose"] = v
            else:
                n[k] = v
        return n

    def edge(self, s, t, kind, label=None, evidence=None):
        s, t = self.alias.get(s, s), self.alias.get(t, t)
        if not s or not t or s == t:
            return
        key = (s, t, kind)
        e = self.edges.get(key)
        if not e:
            e = self.edges[key] = {"s": s, "t": t, "k": kind, "label": label or kind, "ev": []}
        elif label and e["label"] == kind:
            e["label"] = label
        for x in ev_list(evidence):
            if x not in e["ev"] and len(e["ev"]) < 8:
                e["ev"].append(x)


def route_id(path, method, rpath):
    return f"route:{path}:{method} {rpath}"


def clean_call_path(p):
    p = p.strip("`'\" ")
    for anchor in ("/api/", "/internal/", "/hooks/", "/health"):
        k = p.find(anchor)
        if k > 0:
            p = p[k:]
            break
    out, depth, k = "", 0, 0
    while k < len(p):
        if p.startswith("${", k):
            depth += 1
            out += "${"
            k += 2
            continue
        ch = p[k]
        if ch == "}" and depth:
            depth -= 1
        elif ch == "?" and not depth:
            break
        out += ch
        k += 1
    return out


def match_route(call_path, method, routes):
    """routes: list of (rid, METHOD, path). Return the list of best-matching route ids (empty when none)."""
    segs = []
    for seg in clean_call_path(call_path).split("/"):
        if not seg:
            continue
        if "${" in seg:
            segs.append(("*", seg.split("${", 1)[0]))
        else:
            segs.append((seg, None))
    best, score = [], -1.0
    for rid, rm, rp in routes:
        if method not in ("?", None) and rm != method:
            continue
        rsegs = [x for x in rp.strip("/").split("/") if x]
        if len(rsegs) != len(segs):
            continue
        sc, ok = 0.0, True
        for (cs, pre), rs in zip(segs, rsegs):
            if rs.startswith("{"):
                sc += 1
            elif cs == "*":
                if pre and rs.startswith(pre):
                    sc += 2
                elif not pre:
                    sc += 0.5
                else:
                    ok = False
                    break
            elif cs == rs:
                sc += 3
            else:
                ok = False
                break
        if ok:
            if sc > score:
                best, score = [rid], sc
            elif sc == score:
                best.append(rid)
    return best


def merge():
    mech = json.loads((WORK / "mech.json").read_text())
    agents = load_agents()
    M = Model()
    files = {f["path"]: f for f in mech["files"]}
    py = mech["python"]

    # ------------------------------------------------------------------ evidence groups (test-results, .taste-skill)
    grouped = {}
    for a in agents.values():
        for x in a.get("extra_nodes", []) or []:
            if str(x.get("id", "")).startswith("group:"):
                for f in (x.get("facts") or {}).get("files", []) or []:
                    grouped.setdefault(f, x["id"])
    taste = [p for p in files if p.startswith(".taste-skill/")]
    for p in taste:
        sub = p.split("/")[1] if p.count("/") >= 2 else "root"
        gid = f"group:.taste-skill/{sub}"
        grouped.setdefault(p, gid)
        M.node(gid, "evidence-group", f".taste-skill/{sub}", group="design-snapshots",
               purpose=f"UI design-audit snapshots ({sub}) produced by the taste-skill design pass",
               evidence=[".taste-skill/direction.json:1"])
    leftovers = [p for p in files if p.startswith("test-results/") and p not in grouped]
    for p in leftovers:
        stem = Path(p).name
        prefix = re.split(r"[-_.]", stem)[0]
        gid = f"group:test-results/{prefix}-misc"
        grouped[p] = gid
        M.node(gid, "evidence-group", f"test-results/{prefix}* (ungrouped)", purpose="Evidence files not assigned by the tests agent",
               evidence=[f"{p}:1"])
    for p, g in grouped.items():
        M.alias[f"file:{p}"] = g
    group_files = defaultdict(list)
    for p, g in grouped.items():
        if p in files:
            group_files[g].append(p)

    # ------------------------------------------------------------------ file nodes
    for p, f in files.items():
        if p in grouped:
            continue
        kind = KIND_OF_CATEGORY.get(f["category"], "misc")
        if p.startswith("web/src/assets/") or p.startswith("web/public/fonts/"):
            kind = "misc"
        if p.startswith("tests/browser/") and not p.endswith(".mjs"):
            kind = "config"
        if p in ("diagnostic.py", "final_upgrade.py"):
            kind = "misc"
        layer = None
        if kind == "module" and p.startswith("backend/"):
            layer = LAYER_FALLBACK.get(Path(p).name)
        facts = {"bytes": f["bytes"], "category": f["category"]}
        if f.get("secret"):
            facts["note"] = "Secret configuration file. Never opened or rendered by this explorer; names come from .env.example."
        if f.get("binary"):
            facts["binary"] = True
        if f["bytes"] == 0:
            facts["empty"] = True
        if f.get("media"):
            facts["opened"] = f["media"]
        M.node(f"file:{p}", kind, Path(p).name if kind not in ("doc", "test") else p.split("/", 1)[-1],
               path=p, lines=f.get("lines"), layer=layer, facts=facts,
               evidence=[f"{p}:1"] if not f.get("secret") else [".env.example:1"])
    for g, fl in group_files.items():
        n = M.nodes.get(g) or M.node(g, "evidence-group", g.split(":", 1)[1])
        n["facts"]["files"] = sorted(fl)
        n["facts"]["file_count"] = len(fl)
        n["facts"]["file_details"] = [{"file": p, "bytes": files[p]["bytes"], **{k: v for k, v in (files[p].get("media") or {}).items()
                                       if k in ("format", "width", "height", "tests", "failures", "errors", "skipped", "status", "result",
                                                "pass", "passed", "items", "first_line")}} for p in sorted(fl)]
        n["lines"] = None

    # ------------------------------------------------------------------ python: imports, symbols, routes, env, tables
    all_routes = []
    for p, v in py.items():
        if "error" in v or p.startswith("test-results/"):
            continue
        fid = f"file:{p}"
        if fid not in M.nodes:
            continue
        n = M.nodes[fid]
        if v.get("doc"):
            n["facts"]["docstring"] = v["doc"]
        n["facts"]["symbols"] = [{"name": s["name"], "kind": s["kind"], "line": s["line"], "doc": s.get("doc")} for s in v["symbols"]]
        for imp in v["imports"]:
            M.edge(fid, f"file:{imp['target']}", "imports", "imports " + ", ".join(imp["names"][:4]), [f"{p}:{imp['line']}"])
        is_test = p.startswith("tests/")
        if not is_test:
            for s in v["symbols"]:
                sid = f"fn:{p}::{s['name']}"
                M.node(sid, "function", s["name"], path=p, lines=s["end"] - s["line"] + 1,
                       purpose=s.get("doc"), facts={"defined": f"{p}:{s['line']}-{s['end']}", "type": s["kind"],
                                                    "async": s.get("async") or None, "args": s.get("args")},
                       evidence=[f"{p}:{s['line']}"], module=fid)
                M.edge(fid, sid, "defines", "defines", [f"{p}:{s['line']}"])
        for r in v["routes"]:
            rid = route_id(p, r["method"], r["path"])
            outer = next((s for s in v["symbols"] if s["line"] <= r["line"] <= s["end"]), None)
            hname = r["handler"] if not outer or outer["line"] == r["line"] or outer["name"] == r["handler"] else f"{outer['name']}.{r['handler']}"
            hid = f"fn:{p}::{hname}"
            if hid not in M.nodes and not is_test:
                M.node(hid, "function", hname, path=p, facts={"defined": f"{p}:{r['line'] + 1}", "type": "route handler"},
                       evidence=[f"{p}:{r['line'] + 1}"], module=fid)
                M.edge(fid, hid, "defines", "defines", [f"{p}:{r['line']}"])
            M.node(rid, "route", f"{r['method']} {r['path']}", path=p, module=fid,
                   facts={"method": r["method"], "path": r["path"], "handler": hid, "auth": r["auth_hint"],
                          "defined": f"{p}:{r['line']}"}, evidence=[f"{p}:{r['line']}"])
            M.edge(fid, rid, "defines", "defines route", [f"{p}:{r['line']}"])
            if hid in M.nodes:
                M.edge(rid, hid, "calls", "handled by", [f"{p}:{r['line']}"])
            all_routes.append((rid, r["method"], r["path"]))
        for name, line in v.get("env_lines", []):
            if name.startswith("<"):
                continue
            M.edge(fid, f"env:{name}", "uses-secret", "reads env " + name, [f"{p}:{line}"])
        if not is_test:
            for t, lines in v.get("writes", {}).items():
                M.edge(fid, f"table:{t}", "writes", "writes", [f"{p}:{l}" for l in lines[:4]])
            for t, lines in v.get("reads", {}).items():
                M.edge(fid, f"table:{t}", "reads", "reads", [f"{p}:{l}" for l in lines[:4]])
        if is_test and v.get("markers"):
            n["facts"]["markers"] = v["markers"]

    # ------------------------------------------------------------------ SQL
    for t in mech["sql"]["tables"].values():
        tid = f"table:{t['name']}"
        src = t["defined_in"] or (t["altered_in"][0] if t["altered_in"] else None)
        M.node(tid, "table", t["name"], path=src.split(":")[0] if src else None,
               facts={"columns": t["columns"], "constraints": t["constraints"], "indexes": t["indexes"],
                      "enums": t["enums"], "foreign_keys": t["foreign_keys"], "defined": t["defined_in"],
                      "altered": t["altered_in"]}, evidence=[x for x in [t["defined_in"]] + t["altered_in"] if x])
        for fk in t["foreign_keys"]:
            col = next((c for c in t["columns"] if c["name"] == fk["column"]), {})
            M.edge(tid, f"table:{fk['references']}", "references", f"{fk['column']} → {fk['references']}", [col.get("source") or t["defined_in"]])
        for src_file in {x.split(":")[0] for x in [t["defined_in"]] + t["altered_in"] if x}:
            M.edge(f"file:{src_file}", tid, "defines", "defines table" if src_file == (t["defined_in"] or "").split(":")[0] else "alters table",
                   [x for x in [t["defined_in"]] + t["altered_in"] if x and x.startswith(src_file)])
    for o in mech["sql"]["other"]:
        oid = f"fn:{o['source'].split(':')[0]}::{o['name']}"
        M.node(oid, "function", o["name"], path=o["source"].split(":")[0], purpose=f"SQL {o['kind']} {o['name']}",
               facts={"type": f"sql {o['kind']}", "defined": o["source"]}, evidence=[o["source"]])
        M.edge(f"file:{o['source'].split(':')[0]}", oid, "defines", "defines", [o["source"]])

    # ------------------------------------------------------------------ compose
    for cf, c in mech["compose"].items():
        for name, s in c["services"].items():
            sid = f"svc:{name}"
            prof = s.get("profiles") or []
            label = name + (" (legacy)" if "legacy" in prof else "") + (" (protocol)" if cf == "compose.protocol.yaml" and name == "model-fixture" else "")
            facts = {k: s.get(k) for k in ("image", "build", "command", "entrypoint", "ports", "networks", "network_mode",
                                           "volumes", "depends_on", "profiles", "restart", "read_only", "user", "security_opt",
                                           "cap_drop", "cap_add", "tmpfs", "mem_limit", "cpus", "init", "healthcheck", "env_names")}
            if cf == "compose.protocol.yaml":
                n = M.nodes.get(sid)
                if n:
                    n["facts"]["protocol_override"] = {"environment": s.get("env_names"), "defined": s["line"]}
                    n["evidence"].append(s["line"])
                    continue
                facts["compose_file"] = cf
            else:
                facts["compose_file"] = cf
            M.node(sid, "service", label, path=cf, facts=facts, evidence=[s["line"]])
            M.edge(f"file:{cf}", sid, "defines", "defines service", [s["line"]])
            for net in s.get("networks") or []:
                M.edge(sid, f"net:{net}", "attached-to", "attached to", [s["line"]])
            for vol in s.get("volumes") or []:
                vname = str(vol).split(":")[0]
                vid = f"vol:{vname}" if not vname.startswith("/") else f"vol:host:{vname}"
                if vname.startswith("/"):
                    M.node(vid, "volume", vname, facts={"type": "host bind mount", "spec": vol}, evidence=[s["line"]])
                M.edge(sid, vid, "mounts", str(vol), [s["line"]])
            for p in s.get("ports") or []:
                pid = f"port:{p.rsplit(':', 1)[0] if p.count(':') == 2 else p}"
                M.node(pid, "port", f"published {p}", facts={"spec": p, "service": name}, evidence=[s["line"]])
                M.edge(sid, pid, "attached-to", "publishes " + p, [s["line"]])
            dep = s.get("depends_on") or {}
            for d, cond in (dep.items() if isinstance(dep, dict) else [(x, None) for x in dep]):
                M.edge(sid, f"svc:{d}", "depends-on", "depends on" + (f" ({cond.get('condition')})" if isinstance(cond, dict) else ""), [s["line"]])
            for e in s.get("env_names") or []:
                M.edge(sid, f"env:{e}", "uses-secret", "env " + e, [s["line"]])
            img = s.get("image")
            if img:
                M.edge(sid, f"img:{img.split('@')[0]}", "runs-in", "image", [s["line"]])
        for net in c["networks"]:
            M.node(f"net:{net}", "network", net, path=cf, facts={"driver": "bridge (compose default)"}, evidence=[c["network_lines"].get(net)])
        for vol in c["volumes"]:
            M.node(f"vol:{vol}", "volume", vol, path=cf, facts={"type": "named volume"}, evidence=[c["volume_lines"].get(vol)])
    # images & deps
    for im in mech["images"]:
        iid = f"img:{im['ref'].split('@')[0]}"
        M.node(iid, "dependency", im["ref"].split("@")[0], facts={"ref": im["ref"], "type": "container image",
               "pinned_digest": "@sha256:" in im["ref"]}, evidence=[im["source"]])
        M.edge(f"file:{im['source'].split(':')[0]}", iid, "uses", "FROM / image", [im["source"]])
    for d in mech["deps"]:
        did = f"dep:{d['name']}"
        M.node(did, "dependency", d["name"], facts={"version": d["version"], "ecosystem": d["ecosystem"]}, evidence=[d["source"]])
        M.edge(f"file:{d['source'].split(':')[0]}", did, "uses", "depends on " + d["version"], [d["source"]])
    for lf, info in (mech.get("locks") or {}).items():
        n = M.nodes.get(f"file:{lf}")
        if n:
            n["facts"]["locked_packages"] = info["packages"]
            n["facts"]["locked_package_count"] = len(info["packages"])
            n["facts"].update({k: v for k, v in info.items() if k != "packages"})
            n["purpose"] = n.get("purpose") or f"Lockfile pinning {len(info['packages'])} packages"
    # env vars
    env_names = {e["name"]: e["source"] for e in mech["env_example"]}
    for cf, c in mech["compose"].items():
        for e in c["env_refs"]:
            env_names.setdefault(e, f"{cf}:1")
    for p, v in py.items():
        for name, line in v.get("env_lines", []):
            if not name.startswith("<"):
                env_names.setdefault(name, f"{p}:{line}")
    for e in list(M.edges.values()):
        if e["t"].startswith("env:"):
            env_names.setdefault(e["t"][4:], (e["ev"] or [None])[0])
    for name, src in env_names.items():
        secretish = bool(re.search(r"TOKEN|KEY|PASSWORD|SECRET", name))
        M.node(f"env:{name}", "env", name, facts={"sensitive": secretish, "in_env_example": name in {x['name'] for x in mech['env_example']}},
               evidence=[src])
    # plugin tools
    for t in mech["plugin_tools"]:
        tid = f"tool:{t['name']}"
        M.node(tid, "tool", t["name"], path="plugins/hermbot/__init__.py", facts={"type": "plugin " + t["kind"], "declared": t["declared"], "implementation": t["impl"],
               "listed_in_plugin_yaml": t.get("in_plugin_yaml"), "handler": t.get("handler")},
               evidence=[t["declared"]] + t["impl"][:2])
        if t.get("in_plugin_yaml"):
            M.edge("file:plugins/hermbot/plugin.yaml", tid, "defines", "declares " + t["kind"], [t["declared"]])
        if t["impl"]:
            M.edge("file:plugins/hermbot/__init__.py", tid, "implements", "implements", t["impl"][:3])
    # web
    for p, w in mech["web"].items():
        fid = f"file:{p}"
        if w.get("binary"):
            continue
        if fid in M.nodes and w.get("components"):
            M.nodes[fid]["facts"]["components"] = w["components"]
        for imp in w.get("imports", []):
            M.edge(fid, f"file:{imp['target']}", "imports", "imports", [f"{p}:{imp['line']}"])
        calls = []
        for c in w.get("calls", []):
            if c["fn"] in ("EventSource", "WebSocket", "RFB"):
                rids = match_route(c["path"], "WEBSOCKET" if c["fn"] != "EventSource" else "GET", all_routes) or match_route(c["path"], "?", all_routes)
            else:
                rids = match_route(c["path"], c["method"], all_routes)
            calls.append({**c, "route": rids[0] if len(rids) == 1 else (rids or None)})
            for rid in rids:
                M.edge(fid, rid, "fetches", f"{c['method']} via {c['fn']}", [f"{p}:{c['line']}"])
        if fid in M.nodes:
            M.nodes[fid]["facts"]["api_calls"] = calls

    # ------------------------------------------------------------------ agent findings
    features, flows, sms = {}, {}, {}
    discrepancies, unknowns, sd = [], [], {k: [] for k in SD_SECTIONS}
    fragments = defaultdict(list)
    files_read, not_read = defaultdict(set), []
    for name, a in agents.items():
        for f in a.get("files_read", []) or []:
            files_read[f].add(name)
        for x in a.get("files_not_fully_read", []) or []:
            not_read.append({**x, "agent": name})
        for p, note in (a.get("module_notes") or {}).items():
            fid = p if p.startswith(("file:", "group:")) else f"file:{p}"
            if fid not in M.nodes:
                fid2 = grouped.get(p)
                if fid2 and fid2 in M.nodes:
                    g = M.nodes[fid2]
                    cap = {k: v for k, v in note.items() if k in ("purpose", "screen", "feature", "summary", "kind", "full_report", "visible_text", "components", "state", "viewport", "theme", "stage", "defects") and v}
                    g["facts"].setdefault("file_notes", {}).setdefault(p, {}).update(cap)
                    for det in g["facts"].get("file_details", []):
                        if det["file"] == p and note.get("purpose"):
                            det["caption"] = note["purpose"]
                    if note.get("feature", "").startswith("feature:"):
                        M.edge(fid2, note["feature"], "evidences", "screenshot of", [f"{p}:1"])
                continue
            n = M.nodes[fid]
            n.setdefault("notes_by", []).append(name)
            if name.endswith("_refresh"):
                # a re-read of edited content supersedes the earlier pass's notes for this file
                n["purpose"] = note.get("purpose") or n.get("purpose")
                n["facts"].pop("responsibilities", None)
                if note.get("changes_since_previous_pass"):
                    n["facts"]["changes_since_previous_pass"] = note["changes_since_previous_pass"]
            if note.get("purpose"):
                n["purpose"] = n.get("purpose") or note["purpose"]
            if note.get("layer") and (not n.get("layer") or n["kind"] != "module" or name in ("work", "automation", "runtime")):
                n["layer"] = note["layer"]
            for k in ("responsibilities", "kind", "markers", "modules", "emits", "covers", "summary", "changes_since_previous_pass"):
                if note.get(k):
                    n["facts"][k if k != "kind" else "test_kind"] = note[k]
            if note.get("functions"):
                if name.endswith("_refresh") and n["facts"].get("functions"):
                    # a refresh lists new/changed functions; merge by name instead of dropping the earlier list
                    byname = {f.get("name"): f for f in n["facts"]["functions"]}
                    byname.update({f.get("name"): f for f in note["functions"]})
                    n["facts"]["functions"] = sorted(byname.values(), key=lambda f: f.get("line") or 0)
                else:
                    n["facts"]["functions"] = note["functions"]
                for fn in note["functions"]:
                    sid = f"fn:{fid[5:]}::{fn.get('name')}"
                    if sid in M.nodes:
                        M.nodes[sid]["purpose"] = M.nodes[sid].get("purpose") or fn.get("desc")
                        if fn.get("desc"):
                            M.nodes[sid]["facts"]["description"] = fn.get("desc")
            if note.get("env"):
                n["facts"]["env"] = note["env"]
            if note.get("notes"):
                n["facts"].setdefault("notes", []).extend(note["notes"])
            for k, v in note.items():
                if k not in ("purpose", "layer", "responsibilities", "functions", "env", "notes", "kind", "markers", "modules",
                             "emits", "covers", "summary") and v:
                    n["facts"].setdefault(k, v)
        for rid, note in (a.get("route_notes") or {}).items():
            if rid not in M.nodes:
                M.node(rid, "route", rid.split(":", 2)[-1], purpose=note.get("purpose"), evidence=note.get("evidence"),
                       facts={"note": "Route named by agent; not found by mechanical extraction"})
            n = M.nodes[rid]
            if note.get("purpose") and not n.get("purpose"):
                n["purpose"] = note["purpose"]
            for k in ("auth", "request", "response"):
                if note.get(k):
                    n["facts"][k if k != "auth" else "auth_detail"] = note[k]
            for t in note.get("tables") or []:
                M.edge(rid, t if t.startswith(("table:", "bucket:")) else f"table:{t}", "reads", "touches", note.get("evidence"))
            for u in note.get("ui_callers") or []:
                M.edge(u if u.startswith("file:") else f"file:{u}", rid, "fetches", "called from UI", note.get("evidence"))
            n["evidence"] += [e for e in ev_list(note.get("evidence")) if e not in n["evidence"]]
        for tid, note in (a.get("table_notes") or {}).items():
            tid = tid if tid.startswith("table:") else f"table:{tid}"
            if tid not in M.nodes:
                continue
            n = M.nodes[tid]
            if note.get("purpose"):
                n["purpose"] = n.get("purpose") or note["purpose"]
            if note.get("state_machine"):
                n["facts"]["state_machine"] = note["state_machine"]
            for w in note.get("writers") or []:
                M.edge(w if w.startswith(("file:", "fn:", "route:")) else f"file:{w}", tid, "writes", "writes", note.get("evidence"))
            for r in note.get("readers") or []:
                M.edge(r if r.startswith(("file:", "fn:", "route:")) else f"file:{r}", tid, "reads", "reads", note.get("evidence"))
            n["evidence"] += [e for e in ev_list(note.get("evidence")) if e not in n["evidence"]]
        for x in a.get("extra_nodes", []) or []:
            if not x.get("id"):
                continue
            kind = x.get("kind") or "concept"
            if kind in ("evidence-group",):
                kind = "evidence-group"
            existing = M.nodes.get(x["id"])
            facts = dict(x.get("facts") or {})
            if x["id"].startswith("group:"):
                facts.pop("files", None)
            M.node(x["id"], existing["kind"] if existing else kind, x.get("label") or x["id"].split(":", 1)[-1],
                   purpose=x.get("purpose"), facts=facts, evidence=x.get("evidence"))
        for e in a.get("edges", []) or []:
            if e.get("source") and e.get("target"):
                M.edge(e["source"], e["target"], e.get("kind") or "calls", e.get("label"), e.get("evidence"))
        for f in a.get("features", []) or []:
            fid = f.get("id")
            if not fid:
                continue
            cur = features.setdefault(fid, {"id": fid, "owners": []})
            cur["owners"].append(name)
            for k, v in f.items():
                if k in ("id",):
                    continue
                if k in ("title", "purpose") and not cur.get(k):
                    cur[k] = v
                elif isinstance(v, list):
                    cur.setdefault(k, []).extend(v)
                elif isinstance(v, dict):
                    d = cur.setdefault(k, {})
                    for dk, dv in v.items():
                        if isinstance(dv, list):
                            d.setdefault(dk, []).extend(dv)
                        else:
                            d.setdefault(dk, dv)
                elif not cur.get(k):
                    cur[k] = v
        for f in a.get("feature_fragments", []) or []:
            if f.get("id"):
                fragments[f["id"]].append((name, f))
        for fl in a.get("flows", []) or []:
            fid = fl.get("id")
            if not fid:
                continue
            cur = flows.setdefault(fid, {"id": fid, "steps": [], "ui_steps": [], "owners": []})
            cur["owners"].append(name)
            if fl.get("title") and not cur.get("title"):
                cur["title"] = fl["title"]
            if fl.get("summary") and not cur.get("summary"):
                cur["summary"] = fl["summary"]
            steps = fl.get("steps") or []
            if name == "web" or all(s.get("side") == "ui" for s in steps):
                cur["ui_steps"].extend(steps)
            else:
                cur["steps"].extend(steps)
        for sm in a.get("state_machines", []) or []:
            if sm.get("id"):
                cur = sms.setdefault(sm["id"], {"id": sm["id"], "states": [], "transitions": [], "owners": []})
                cur["owners"].append(name)
                for k in ("entity", "table", "column", "notes"):
                    if sm.get(k) and not cur.get(k):
                        cur[k] = sm[k]
                names = {s["name"] for s in cur["states"]}
                cur["states"] += [s if isinstance(s, dict) else {"name": s} for s in sm.get("states") or []
                                  if (s["name"] if isinstance(s, dict) else s) not in names]
                cur["transitions"] += sm.get("transitions") or []
        for d in a.get("discrepancies", []) or []:
            discrepancies.append({**d, "agent": name})
        for u in a.get("unknowns", []) or []:
            unknowns.append({**u, "agent": name})
        for k, items in (a.get("system_design") or {}).items():
            key = k if k in sd else k.replace("_", "-")
            sd.setdefault(key, [])
            for it in items or []:
                sd[key].append({**(it if isinstance(it, dict) else {"text": str(it)}), "agent": name})

    # drop "not fully read" entries for files another pass read completely
    fully = set()
    for name, a in agents.items():
        partial = {x.get("path") for x in a.get("files_not_fully_read", []) or []}
        fully |= {f for f in a.get("files_read", []) or [] if f not in partial}
    not_read[:] = [x for x in not_read if x.get("path") not in fully]
    def wildcard_done(pat):
        prefix, exts = pat.split("*")[0], tuple(e.strip("*.|") for e in re.findall(r"\*\.(\w+)", pat))
        matched = [p for p in files if p.startswith(prefix) and (not exts or p.rsplit(".", 1)[-1] in exts)]
        return matched and all(p in fully for p in matched)
    not_read[:] = [x for x in not_read if not ("*" in (x.get("path") or "") and wildcard_done(x["path"]))]
    for x in not_read:
        p_ = x.get("path") or ""
        if p_ in files and files[p_].get("media"):
            md = files[p_]["media"]
            x["why"] = f"{x.get('why')} Mechanically opened by build_model.py: " + ", ".join(f"{k}={v}" for k, v in md.items() if k in ("format", "width", "height", "records", "decompressed_bytes", "num_tables", "top_level_keys"))
        elif "*" in p_:
            prefix = p_.split("*")[0]
            n_ = sum(1 for f in files.values() if f["path"].startswith(prefix) and (f.get("media") or {}).get("format") in ("png", "jpeg"))
            x["why"] = f"{x.get('why')} All {n_} image files here were opened by build_model.py and their dimensions recorded in their evidence-group nodes; pixels were not reviewed visually."

    # fragments into features
    for fid, frs in fragments.items():
        cur = features.setdefault(fid, {"id": fid, "owners": []})
        for name, f in frs:
            for k, v in f.items():
                if k == "id":
                    continue
                if isinstance(v, list):
                    cur.setdefault(k, []).extend(({**i, "source": name} if isinstance(i, dict) else i) for i in v)
                elif isinstance(v, dict):
                    d = cur.setdefault(k, {})
                    for dk, dv in v.items():
                        if isinstance(dv, list):
                            d.setdefault(dk, []).extend(dv)
                        elif dv:
                            d.setdefault(dk, dv)
                elif v and not cur.get(k):
                    cur[k] = v

    # feature nodes + implements edges
    for fid, f in features.items():
        M.node(fid, "feature", f.get("title") or fid.split(":", 1)[1], purpose=f.get("purpose"),
               evidence=[e for step in (f.get("logic") or [])[:3] for e in ev_list(step.get("evidence") if isinstance(step, dict) else None)][:4])
        comps = []
        for c in f.get("components") or []:
            cid = c if isinstance(c, str) else c.get("id")
            if cid and cid not in comps:
                comps.append(cid)
        f["components"] = comps
        for cid in comps:
            M.edge(cid, fid, "implements", "implements", M.nodes.get(cid, {}).get("evidence", [])[:1] or (M.nodes[fid]["evidence"][:1]))
        for smid in f.get("state_machines") or []:
            M.edge(smid, fid, "implements", "state of", M.nodes[fid]["evidence"][:1])
    # flows
    for fid, fl in flows.items():
        M.node(fid, "flow", f"Flow {fid[-1]}: {fl.get('title') or ''}".strip(), purpose=fl.get("summary"),
               evidence=[e for s in (fl["steps"] + fl["ui_steps"])[:3] for e in ev_list(s.get("evidence"))][:3])
        for s in fl["steps"] + fl["ui_steps"]:
            for nid in s.get("nodes") or []:
                M.edge(fid, nid, "involves", "step", ev_list(s.get("evidence"))[:2])
    # state machines
    for smid, sm in sms.items():
        tid = sm.get("table")
        M.node(smid, "sm", sm.get("entity") or smid.split(":", 1)[1], purpose=f"State machine of {sm.get('entity') or smid}" + (f" ({tid}.{sm.get('column')})" if tid else ""),
               evidence=[e for t in sm["transitions"][:2] for e in ev_list(t.get("evidence"))][:3], facts={"table": tid, "column": sm.get("column")})
        if tid:
            M.edge(smid, tid if tid.startswith("table:") else f"table:{tid}", "reads", "status column", M.nodes[smid]["evidence"][:1])
        slug = smid.split(":", 1)[1]
        for s in sm["states"]:
            sid = f"state:{slug}:{s['name']}"
            M.node(sid, "state", s["name"], purpose=s.get("desc"), facts={"terminal": s.get("terminal"), "machine": smid},
                   evidence=M.nodes[smid]["evidence"][:1])
            M.edge(smid, sid, "defines", "state", M.nodes[smid]["evidence"][:1])
        state_names = [st["name"] for st in sm["states"]]
        for t in sm["transitions"]:
            froms = [t.get("from")] if not isinstance(t.get("from"), list) else t["from"]
            if any(f in ("*", "any") for f in froms):
                froms = [x for x in state_names if x != t.get("to") and not next((st for st in sm["states"] if st["name"] == x), {}).get("terminal")]
            elif any(isinstance(f, str) and "|" in f for f in froms):
                froms = [y.strip() for f in froms for y in str(f).split("|")]
            for fr in froms:
                if not fr or not t.get("to"):
                    continue
                a_, b_ = f"state:{slug}:{fr}", f"state:{slug}:{t['to']}"
                for sid in (a_, b_):
                    if sid not in M.nodes and fr != "*":
                        M.node(sid, "state", sid.rsplit(":", 1)[1], facts={"machine": smid, "note": "state named only in a transition"},
                               evidence=ev_list(t.get("evidence"))[:1])
                        M.edge(smid, sid, "defines", "state", ev_list(t.get("evidence"))[:1])
                M.edge(a_, b_, "transitions", t.get("trigger") or "transition", ev_list(t.get("evidence")))
    # discrepancies: merge near-duplicates reported by several passes
    def words(t):
        return {w for w in re.findall(r"[a-z_][a-z0-9_.]{3,}", (t or "").lower())}
    sev_rank = {"high": 0, "medium": 1, "low": 2}
    merged = []
    for d in discrepancies:
        d.setdefault("agents", [d.get("agent")])
        w = words(d.get("text"))
        ev = set(ev_list(d.get("evidence")))
        hit = None
        for m_ in merged:
            mw = words(m_.get("text"))
            jac = len(w & mw) / max(1, len(w | mw))
            files_a = {e.split(":")[0] for e in ev}
            files_b = {e.split(":")[0] for e in ev_list(m_.get("evidence"))}
            if jac > 0.45 or (ev & set(ev_list(m_.get("evidence"))) and jac > 0.25) or (files_a & files_b and jac > 0.18):
                hit = m_
                break
        if hit:
            if sev_rank.get(d.get("severity"), 3) < sev_rank.get(hit.get("severity"), 3):
                hit["severity"] = d.get("severity")
            hit["nodes"] = list(dict.fromkeys((hit.get("nodes") or []) + (d.get("nodes") or [])))
            hit["evidence"] = list(dict.fromkeys(ev_list(hit.get("evidence")) + ev_list(d.get("evidence"))))
            hit["agents"] = list(dict.fromkeys(hit["agents"] + [d.get("agent")]))
            hit.setdefault("also_reported_as", []).append(d.get("text"))
        else:
            merged.append(d)
    discrepancies = merged
    for d in discrepancies:
        d["agent"] = ", ".join(a for a in d["agents"] if a)
    for i, d in enumerate(discrepancies, 1):
        did = f"issue:d{i}"
        d["id"] = did
        M.node(did, "discrepancy", (d.get("text") or "")[:70], purpose=d.get("text"), facts={"severity": d.get("severity"), "agent": d.get("agent")},
               evidence=d.get("evidence"))
        for nid in d.get("nodes") or []:
            M.edge(did, nid, "concerns", "concerns", ev_list(d.get("evidence"))[:2])
    for i, u in enumerate(unknowns, 1):
        u["id"] = f"unknown:u{i}"

    # system design node
    M.node("system-design", "system-design", "System design", purpose="Cross-cutting design: topology, isolation, durability, concurrency, recovery, security, scaling and trade-offs.",
           evidence=["compose.yaml:1", "backend/containers.py:1"])
    for fid in features:
        M.edge("system-design", fid, "involves", "feature", M.nodes[fid]["evidence"][:1] or ["README.md:1"])

    # runs-in edges for entry points (verified from compose commands)
    entry = {"svc:api": ["file:backend/app.py"], "svc:worker": ["file:backend/worker.py"], "svc:event-worker": ["file:backend/trigger_worker.py"],
             "svc:notification-worker": ["file:backend/notification_worker.py"], "svc:supervisor": ["file:backend/supervisor.py", "file:backend/containers.py"],
             "svc:model-fixture": ["file:tests/model_fixture.py"]}
    for svc, mods in entry.items():
        if svc in M.nodes:
            for mid in mods:
                M.edge(mid, svc, "runs-in", "entry point", [M.nodes[svc]["evidence"][0]])

    # ------------------------------------------------------------------ dangling edge targets -> placeholder nodes (flagged)
    dangling = []
    for e in list(M.edges.values()):
        for end in (e["s"], e["t"]):
            if end not in M.nodes:
                dangling.append(end)
                kind = end.split(":", 1)[0]
                kmap = {"file": "misc", "fn": "function", "route": "route", "table": "table", "svc": "service", "net": "network", "vol": "volume",
                        "env": "env", "tool": "tool", "actor": "actor", "bucket": "bucket", "feature": "feature", "flow": "flow", "sm": "sm",
                        "state": "state", "port": "port", "secret": "secret", "img": "dependency", "dep": "dependency", "group": "evidence-group",
                        "concept": "concept"}
                M.node(end, kmap.get(kind, "concept"), end.split(":", 1)[-1].split("::")[-1],
                       facts={"unresolved": True, "note": "Referenced by an edge but not found in the mechanical inventory"},
                       evidence=e["ev"][:1], tags=["unresolved"])
    # ------------------------------------------------------------------ adjacency-derived facts
    for n in M.nodes.values():
        n.setdefault("purpose", None)

    model = {
        "meta": {"title": "Hermbot architecture", "root": "hermbot", "generated_by": "architecture/build_model.py + merge_model.py",
                 "agents": sorted(agents), "counts": {}},
        "nodes": list(M.nodes.values()),
        "edges": list(M.edges.values()),
        "features": features, "flows": flows, "state_machines": sms,
        "discrepancies": discrepancies, "unknowns": unknowns, "system_design": sd,
        "excluded": mech["excluded"], "files_read": {k: sorted(v) for k, v in files_read.items()}, "not_fully_read": not_read,
        "file_index": {p: (grouped.get(p) or f"file:{p}") for p in files},
        "dangling": sorted(set(dangling)),
        "plain": load_plain(),
    }
    normalize_evidence(model)
    model = redact_env(model)
    kinds = defaultdict(int)
    for n in model["nodes"]:
        kinds[n["kind"]] += 1
    model["meta"]["counts"] = {"nodes": len(model["nodes"]), "edges": len(model["edges"]), "files": len(files), "by_kind": dict(kinds)}
    (OUT / "model.json").write_text(json.dumps(model, indent=1, ensure_ascii=False))
    print(json.dumps(model["meta"]["counts"]), "dangling:", len(model["dangling"]))
    render()


def redact_env(model):
    """Replace any .env value (>= 8 chars, not also a public default in tracked files) with a placeholder.
    Values are compared in memory only and never printed."""
    envp = ROOT / ".env"
    if not envp.is_file():
        return model
    public = "\n".join((ROOT / f).read_text() for f in ("compose.yaml", ".env.example", "README.md"))
    text = json.dumps(model, ensure_ascii=False)
    hits = 0
    for line in envp.read_text().splitlines():
        m = re.match(r"\s*([A-Z][A-Z0-9_]*)\s*=\s*(.*)$", line)
        if not m:
            continue
        v = m.group(2).strip().strip('"').strip("'")
        if len(v) >= 8 and v not in public and v in text:
            hits += text.count(v)
            text = text.replace(json.dumps(v)[1:-1], f"[redacted .env value: {m.group(1)}]")
    if hits:
        print(f"redacted {hits} occurrence(s) of .env values")
    return json.loads(text)


_FILE_CACHE = {}


def _exists(path):
    if path not in _FILE_CACHE:
        _FILE_CACHE[path] = (ROOT / path).is_file() and path != ".env"
    return _FILE_CACHE[path]


def norm_ev(e):
    """Normalise one citation into a list of `path:line` / `path:start-end` strings."""
    e = str(e).strip().strip("`").strip()
    if not e:
        return []
    if e.lower() in ("[unknown]", "unknown"):
        return ["[unknown]"]
    if e.startswith("architecture/"):
        return []
    m = re.match(r"^([\w./@+-]+?):\s*L?(\d+(?:-\d+)?(?:\s*[,;]\s*L?\d+(?:-\d+)?)*)$", e)
    if m:
        return [f"{m.group(1)}:{part.strip().lstrip('L')}" for part in re.split(r"[,;]", m.group(2)) if part.strip()]
    m = re.match(r"^([\w./@+-]+?)(?:#L?(\d+))?$", e)
    if m and _exists(m.group(1)):
        return [f"{m.group(1)}:{m.group(2) or 1}"]
    m = re.match(r"^([\w./@+-]+\.\w+)[: ]", e)
    if m and _exists(m.group(1)):
        nums = re.findall(r"\d+", e[len(m.group(1)):])
        return [f"{m.group(1)}:{nums[0] if nums else 1}"]
    return [e]


def normalize_evidence(obj):
    if isinstance(obj, dict):
        for k, v in list(obj.items()):
            if k in ("evidence", "ev"):
                items = v if isinstance(v, list) else [v]
                out = []
                for x in items:
                    for y in norm_ev(x):
                        if y not in out:
                            out.append(y)
                obj[k] = out
            else:
                normalize_evidence(v)
    elif isinstance(obj, list):
        for v in obj:
            normalize_evidence(v)


def render():
    tpl = (OUT / "explorer_template.html").read_text()
    data = (OUT / "model.json").read_text()
    data = json.dumps(json.loads(data), separators=(",", ":"), ensure_ascii=False).replace("</", "<\\/")
    html = tpl.replace("/*__MODEL__*/null", data)
    (OUT / "index.html").write_text(html)
    print("index.html bytes:", len(html.encode()))


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "render":
        render()
    else:
        merge()
