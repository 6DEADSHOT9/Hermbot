#!/usr/bin/env python3
"""Keep the architecture explorer in step with a changing repo.

  python3 architecture/refresh.py                 # re-extract, list new/changed/deleted files, merge, run the gate
  python3 architecture/refresh.py --mark-read PATH [PATH ...] --by NAME
                                                  # record that PATH's *current* content was read (after updating notes)
  python3 architecture/refresh.py --init --stale PATH ...   # seed the ledger from the current tree
  python3 architecture/refresh.py --apply NAME [NAME ...]    # apply NAME's citation_checks/corrections to earlier notes

Citations follow edits: every read file's text is snapshotted (_work/snapshots/<sha256>.txt) and _work/cite_base.json
records which version the `path:line` citations in _work/agents/*.json point at. When that file later changes, the
citations are rewritten to the new line numbers with difflib; any that land inside an edited hunk are queued in
_work/remap/needs_check.json for a human/agent to re-verify.

The read ledger (_work/read_ledger.json) stores, per file, the sha256 of the content that was actually read.
A file whose current hash differs from the ledger (or that is missing from it) is "unread" and fails the gate,
so new and edited files cannot slip into the published explorer without being read.
"""
import argparse
import difflib
import hashlib
import json
import re
import subprocess
import sys
import time
from pathlib import Path

OUT = Path(__file__).resolve().parent
WORK = OUT / "_work"
LEDGER = WORK / "read_ledger.json"
ROOT = OUT.parent
SNAP = WORK / "snapshots"            # sha256 -> text of a file version that notes cite
CITE_BASE = WORK / "cite_base.json"  # path -> sha256 of the version that `path:line` citations in _work/agents refer to
REMAP = WORK / "remap"
TEXT_EXT = {".py", ".sql", ".md", ".ts", ".tsx", ".js", ".mjs", ".css", ".html", ".yaml", ".yml", ".toml", ".json", ".txt",
            ".sh", ".cfg", ".ini", ".lock", "", ".example", ".Dockerfile", ".dockerignore", ".gitignore"}


def current():
    mech = json.loads((WORK / "mech.json").read_text())
    return {f["path"]: f for f in mech["files"] if f.get("sha256")}


def load():
    return json.loads(LEDGER.read_text()) if LEDGER.exists() else {}


def diff(ledger, files):
    new = sorted(p for p in files if p not in ledger)
    changed = sorted(p for p in files if p in ledger and ledger[p]["sha256"] != files[p]["sha256"])
    deleted = sorted(p for p in ledger if p not in files)
    return new, changed, deleted


def is_text(p):
    path = ROOT / p
    if p == ".env" or not path.is_file() or path.stat().st_size > 2_000_000:
        return False
    if Path(p).suffix not in TEXT_EXT and not Path(p).name.startswith("."):
        return False
    try:
        path.read_text()
        return True
    except (UnicodeDecodeError, OSError):
        return False


def snapshot(p, sha):
    """Keep the text of the version notes are written against, so later edits can be line-remapped."""
    SNAP.mkdir(exist_ok=True)
    dst = SNAP / f"{sha}.txt"
    if not dst.exists():
        dst.write_text((ROOT / p).read_text())


def line_map(old, new):
    """old line number -> (new line number, exact?) via difflib; lines inside edited hunks map to the nearest new line."""
    a, b = old.split("\n"), new.split("\n")
    out = {}
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(None, a, b, autojunk=False).get_opcodes():
        for i in range(i1, i2):
            if tag == "equal":
                out[i + 1] = (j1 + (i - i1) + 1, True)
            else:
                out[i + 1] = (min(j1 + (i - i1), max(j2 - 1, j1)) + 1, False)
    out[len(a) + 1] = (len(b) + 1, False)
    return out


def remap_citations(files):
    """When a cited file changed, rewrite `path:N` / `path:A-B` in every agent note to the new line numbers.

    Citations that fell inside an edited hunk are listed in _work/remap/needs_check.json for re-verification.
    A file with no stored snapshot of the cited version is listed whole (its citations cannot be remapped mechanically)."""
    base = json.loads(CITE_BASE.read_text()) if CITE_BASE.exists() else {}
    maps, unmappable = {}, []
    for p, sha in list(base.items()):
        if p not in files or files[p]["sha256"] == sha:
            continue
        src = SNAP / f"{sha}.txt"
        if src.exists() and is_text(p):
            maps[p] = line_map(src.read_text(), (ROOT / p).read_text())
        else:
            unmappable.append(p)
    if not maps and not unmappable:
        return
    REMAP.mkdir(exist_ok=True)
    nc_path = REMAP / "needs_check.json"
    needs = json.loads(nc_path.read_text()) if nc_path.exists() else []
    moved = 0
    if maps:
        pat = re.compile(r"(?<![\w./-])(" + "|".join(re.escape(p) for p in sorted(maps, key=len, reverse=True)) + r"):(\d+)(?:-(\d+))?(?!\d)")
        for f in sorted((WORK / "agents").glob("*.json")):
            text = f.read_text()

            def sub(m):
                nonlocal moved
                p, a_, b_ = m.group(1), int(m.group(2)), m.group(3)
                mp = maps[p]
                na, ea = mp.get(a_, (a_, False))
                if b_:
                    nb, eb = mp.get(int(b_), (int(b_), False))
                    nb = max(nb, na)
                    new = f"{p}:{na}-{nb}" if nb != na else f"{p}:{na}"
                    exact = ea and eb
                else:
                    new, exact = f"{p}:{na}", ea
                if not exact:
                    needs.append({"agent": f.stem, "was": m.group(0), "now": new})
                if new != m.group(0):
                    moved += 1
                return new
            out = pat.sub(sub, text)
            if out != text:
                f.write_text(out)
    for p in unmappable:
        needs.append({"agent": "*", "was": f"{p}:*", "now": f"{p}:* (no snapshot of the cited version; re-verify every citation)"})
    for p in list(maps) + unmappable:
        base[p] = files[p]["sha256"]
        if is_text(p):
            snapshot(p, files[p]["sha256"])
    CITE_BASE.write_text(json.dumps(base, indent=1, sort_keys=True))
    nc_path.write_text(json.dumps(needs, indent=1))
    print(f"remapped citations: files={len(maps)} moved={moved} need re-check={len(needs)} unmappable={unmappable}")


def walk_strings(obj, fn):
    if isinstance(obj, str):
        return fn(obj)
    if isinstance(obj, list):
        return [walk_strings(x, fn) for x in obj]
    if isinstance(obj, dict):
        return {k: walk_strings(v, fn) for k, v in obj.items()}
    return obj


def apply_fixes(names):
    """Apply a refresh agent's `citation_checks` and `corrections` to the earlier agents' notes, and log every change."""
    agents_dir = WORK / "agents"
    nc_path = REMAP / "needs_check.json"
    needs = json.loads(nc_path.read_text()) if nc_path.exists() else []
    log_path = REMAP / "applied.json"
    log = json.loads(log_path.read_text()) if log_path.exists() else []
    for name in names:
        src = json.loads((agents_dir / f"{name}.json").read_text())
        # all citation fixes for one target file are applied in a single pass, so a fix never re-hits another's output
        per_target = {}
        for c in src.get("citation_checks") or []:
            was, now, good = c.get("was"), c.get("now") or c.get("was"), c.get("correct")
            if not (now and good):
                continue
            targets = [c["agent"]] if c.get("agent") not in (None, "*") else [t.stem for t in agents_dir.glob("*.json")]
            for t in targets:
                if t != name and now != good:
                    per_target.setdefault(t, {})[now] = good
            needs = [x for x in needs if not (x.get("now") == now and (c.get("agent") in (None, "*") or x.get("agent") in (c.get("agent"), "*")))]
            log.append({"by": name, "type": "citation", "agent": c.get("agent"), "was": was, "now": now, "correct": good})
        for t, mapping in per_target.items():
            tf = agents_dir / f"{t}.json"
            if not tf.exists():
                continue
            pat = re.compile(r"(?<![\w./-])(" + "|".join(re.escape(k) for k in sorted(mapping, key=len, reverse=True)) + r")(?![\d-])")
            count = [0]

            def fix(sv):
                out, k = pat.subn(lambda m: mapping[m.group(1)], sv)
                count[0] += k
                return out
            data = walk_strings(json.loads(tf.read_text()), fix)
            if count[0]:
                tf.write_text(json.dumps(data, indent=1, ensure_ascii=False))
            log.append({"by": name, "type": "citation-pass", "agent": t, "replaced": count[0]})
        for c in src.get("corrections") or []:
            t = agents_dir / f"{c.get('agent')}.json"
            if not t.exists() or not c.get("find"):
                log.append({"by": name, "type": "correction", **c, "replaced": 0, "note": "target missing"})
                continue
            data = json.loads(t.read_text())
            count = [0]

            def corr(sv):
                if c["find"] in sv:
                    count[0] += 1
                    return sv.replace(c["find"], c.get("replace", ""))
                return sv
            data = walk_strings(data, corr)
            if count[0]:
                t.write_text(json.dumps(data, indent=1, ensure_ascii=False))
            log.append({"by": name, "type": "correction", **c, "replaced": count[0]})
    REMAP.mkdir(exist_ok=True)
    nc_path.write_text(json.dumps(needs, indent=1))
    log_path.write_text(json.dumps(log, indent=1, ensure_ascii=False))
    print(f"applied fixes from {names}; still needing a check: {len(needs)}")


def owner_hint(p):
    for prefix, who in (("backend/migrations/", "runtime"), ("backend/", "work/automation/runtime by module"), ("docker/", "runtime"),
                        ("plugins/", "runtime"), ("web/", "web"), ("scripts/", "web"), ("tests/", "tests"), ("test-results/", "tests + image review"),
                        ("plan/", "docs"), (".taste-skill/", "image review")):
        if p.startswith(prefix):
            return who
    return "lead"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mark-read", nargs="*")
    ap.add_argument("--by", default="lead")
    ap.add_argument("--init", action="store_true")
    ap.add_argument("--stale", nargs="*", default=[])
    ap.add_argument("--apply", nargs="*", help="agent names whose citation_checks/corrections to apply to earlier notes")
    a = ap.parse_args()
    if a.apply:
        apply_fixes(a.apply)
    subprocess.run([sys.executable, str(OUT / "build_model.py"), "extract"], check=True)
    files, ledger = current(), load()
    remap_citations(files)
    if a.init:
        ledger = {p: {"sha256": f["sha256"], "read_by": ["initial mapping run"], "read_at": time.strftime("%Y-%m-%dT%H:%M:%S")}
                  for p, f in files.items() if p not in a.stale}
    if a.mark_read:
        for p in a.mark_read:
            if p in files:
                ledger[p] = {"sha256": files[p]["sha256"], "read_by": [a.by], "read_at": time.strftime("%Y-%m-%dT%H:%M:%S")}
                if is_text(p):
                    snapshot(p, files[p]["sha256"])
                    base = json.loads(CITE_BASE.read_text()) if CITE_BASE.exists() else {}
                    base[p] = files[p]["sha256"]
                    CITE_BASE.write_text(json.dumps(base, indent=1, sort_keys=True))
            else:
                print(f"not in inventory: {p}")
    if a.init or a.mark_read:
        LEDGER.write_text(json.dumps(ledger, indent=1, sort_keys=True))
    new, changed, deleted = diff(ledger, files)
    queue = {"generated": time.strftime("%Y-%m-%dT%H:%M:%S"), "new": new, "changed": changed, "deleted": deleted,
             "work": [{"path": p, "reason": "new" if p in new else "changed", "suggested_pass": owner_hint(p)} for p in new + changed]}
    (WORK / "refresh-queue.json").write_text(json.dumps(queue, indent=1))
    print(f"ledger={len(ledger)} files={len(files)} new={len(new)} changed={len(changed)} deleted={len(deleted)}")
    for w in queue["work"]:
        print(f"  {w['reason']:8} {w['path']}  -> {w['suggested_pass']}")
    if not (a.init or a.mark_read):
        subprocess.run([sys.executable, str(OUT / "merge_model.py")], check=True)
        sys.exit(subprocess.run([sys.executable, str(OUT / "check_coverage.py")]).returncode)


if __name__ == "__main__":
    main()
