#!/usr/bin/env python3
"""Build the Hermbot architecture model.

  python3 architecture/build_model.py extract   -> architecture/_work/mech.json  (mechanical facts)
  python3 architecture/build_model.py merge     -> architecture/model.json       (mech + semantic agent output)

Everything is read-only toward the project. `.env` is never opened here.
"""
import ast
import json
import os
import re
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "architecture"
WORK = OUT / "_work"
EXCLUDED_DIRS = {"node_modules", "__pycache__", ".pytest_cache", ".ruff_cache", ".git", "architecture"}
NEVER_READ = {".env"}
TEXT_EXT = {".py", ".md", ".sql", ".yaml", ".yml", ".json", ".toml", ".ts", ".tsx", ".css", ".html", ".js",
            ".mjs", ".txt", ".lock", ".example", ".jsonl", ".svg", ".xml", "", ".dockerignore", ".gitignore"}


def rel(p):
    return str(Path(p).resolve().relative_to(ROOT))


# --------------------------------------------------------------------------- inventory
def inventory():
    files, excluded = [], defaultdict(int)
    for dirpath, dirnames, filenames in os.walk(ROOT):
        d = Path(dirpath)
        keep = []
        for name in dirnames:
            if name in EXCLUDED_DIRS:
                sub = d / name
                excluded[rel(sub)] += sum(len(fs) for _, _, fs in os.walk(sub))
            else:
                keep.append(name)
        dirnames[:] = sorted(keep)
        for name in sorted(filenames):
            p = d / name
            r = rel(p)
            info = {"path": r, "bytes": p.stat().st_size, "mtime": p.stat().st_mtime}
            if name not in NEVER_READ:
                import hashlib
                info["sha256"] = hashlib.sha256(p.read_bytes()).hexdigest()
            if name in NEVER_READ:
                info.update(lines=None, binary=False, secret=True)
            else:
                try:
                    text = p.read_text()
                    info.update(lines=text.count("\n") + (1 if text and not text.endswith("\n") else 0), binary=False)
                except (UnicodeDecodeError, OSError):
                    info.update(lines=None, binary=True)
            info["category"] = categorize(r)
            files.append(info)
    return files, dict(excluded)


def categorize(r):
    top = r.split("/")[0]
    if r == ".env":
        return "secret-config"
    if r.startswith("backend/migrations/"):
        return "migration"
    if r.endswith("Dockerfile"):
        return "dockerfile"
    if top == "backend":
        return "backend-module" if r.endswith(".py") else "backend-asset"
    if top == "docker":
        return "runtime-module"
    if top == "plugins":
        return "plugin"
    if top == "scripts":
        return "script"
    if top == "tests":
        if r.startswith("tests/browser/"):
            return "browser-test"
        return "test"
    if top == "test-results":
        return "evidence"
    if top == "plan":
        return "plan-archive" if "/archive/" in r else "doc"
    if top == "web":
        if r.startswith("web/dist/"):
            return "build-output"
        return "ui"
    if top == ".taste-skill":
        return "design-snapshot"
    if r in ("README.md",):
        return "doc"
    if r in ("compose.yaml", "compose.protocol.yaml"):
        return "compose"
    if r.endswith(".py"):
        return "root-module"
    return "config"


# --------------------------------------------------------------------------- python
def module_name_for(r):
    p = r[:-3].replace("/", ".")
    return p[:-9] if p.endswith(".__init__") else p


def resolve_import(r, node):
    """Return list of (target_relpath, names, line)."""
    pkg = r.rsplit("/", 1)[0].replace("/", ".") if "/" in r else ""
    targets = []
    if isinstance(node, ast.ImportFrom):
        if node.level:
            parts = pkg.split(".")
            base = parts[: len(parts) - (node.level - 1)] if node.level > 1 else parts
            mod = ".".join(base + ([node.module] if node.module else []))
        else:
            mod = node.module or ""
        names = [a.name for a in node.names]
        cands = [mod] + [f"{mod}.{n}" for n in names]
    else:
        names = [a.name for a in node.names]
        cands = names
    for c in cands:
        path = c.replace(".", "/")
        for t in (f"{path}.py", f"{path}/__init__.py"):
            if (ROOT / t).is_file():
                targets.append((t, names, node.lineno))
                break
    # runtime-image layout: plugin/gate are imported top-level inside the Hermes container
    if not targets and isinstance(node, ast.ImportFrom) and node.module == "hermbot_runtime_gate":
        targets.append(("hermbot_runtime_gate.py", names, node.lineno))
    return targets


ENV_RE = re.compile(r"""(?:os\.environ\.get|os\.getenv|environ\.get)\(\s*['"]([A-Z][A-Z0-9_]+)['"]|os\.environ\[\s*['"]([A-Z][A-Z0-9_]+)['"]\s*\]""")
SQL_WRITE_RE = re.compile(r"\b(?:INSERT\s+INTO|UPDATE|DELETE\s+FROM)\s+([a-z_][a-z0-9_]*)", re.I)
SQL_READ_RE = re.compile(r"\b(?:FROM|JOIN)\s+([a-z_][a-z0-9_]*)", re.I)
ROUTE_DEC_RE = re.compile(r"^\s*@(\w+)\.(get|post|put|delete|patch|websocket|api_route)\(\s*(['\"])(.*?)\3(.*)$")
# `@router.get(PREFIX + "/{id}")` where PREFIX is a module-level string constant
ROUTE_CONST_RE = re.compile(r"^\s*@(\w+)\.(get|post|put|delete|patch|websocket|api_route)\(\s*([A-Z_][A-Z0-9_]*)\s*(?:\+\s*(['\"])(.*?)\4)?(.*)$")
STR_CONST_RE = re.compile(r"^([A-Z_][A-Z0-9_]*)\s*=\s*(['\"])([^'\"]*)\2\s*$")


def python_facts(r, tables):
    src = (ROOT / r).read_text()
    tree = ast.parse(src)
    lines = src.splitlines()
    doc = ast.get_docstring(tree)
    imports, symbols, env = [], [], set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            for t, names, line in resolve_import(r, node):
                if t != r:
                    imports.append({"target": t, "names": names, "line": line})
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            d = ast.get_docstring(node)
            symbols.append({"name": node.name, "kind": "class" if isinstance(node, ast.ClassDef) else "function",
                            "line": node.lineno, "end": getattr(node, "end_lineno", node.lineno),
                            "async": isinstance(node, ast.AsyncFunctionDef),
                            "doc": d.split("\n")[0] if d else None,
                            "args": [a.arg for a in node.args.args] if not isinstance(node, ast.ClassDef) else None})
    for i, line in enumerate(lines, 1):
        for m in ENV_RE.finditer(line):
            env.add((m.group(1) or m.group(2), i))
    # f-string env names like f"{bot_id.upper()}_TOKEN"
    for i, line in enumerate(lines, 1):
        if re.search(r'environ\.get\(f["\']\{', line):
            env.add(("<BOT>_TOKEN/_API_KEY (dynamic)", i))
    # routes
    routes = []
    router_auth = {}
    consts = {m.group(1): m.group(3) for m in map(STR_CONST_RE.match, lines) if m}
    for i, line in enumerate(lines, 1):
        m = re.search(r"(\w+)\s*=\s*APIRouter\((.*)\)", line)
        if m:
            router_auth[m.group(1)] = "owner" if "Depends(owner)" in m.group(2) else None
            continue
        m = ROUTE_DEC_RE.match(line)
        if m:
            obj, method, _, path, rest = m.groups()
        else:
            m = ROUTE_CONST_RE.match(line)
            if not m or m.group(3) not in consts:
                continue
            obj, method, const, _, suffix, rest = m.groups()
            path = consts[const] + (suffix or "")
        j = i
        while j < len(lines) and not re.match(r"\s*(async\s+)?def\s", lines[j]):
            j += 1
        fn = re.match(r"\s*(?:async\s+)?def\s+(\w+)", lines[j]).group(1) if j < len(lines) else None
        body = "\n".join(lines[j:j + 25])
        auth = "owner" if "Depends(owner)" in rest else router_auth.get(obj)
        if not auth:
            if path.startswith("/internal") or "bot_identity" in body or "internal_token" in body:
                auth = "bot/internal token"
            elif "supervisor_token" in body or "SUPERVISOR" in body or r.endswith("containers.py"):
                auth = "supervisor token"
            elif "owner(" in body or "session" in body:
                auth = "owner (in handler)"
            elif r.startswith("docker/"):
                auth = "runtime API key"
            else:
                auth = "none/handler-defined"
        routes.append({"method": method.upper(), "path": path, "handler": fn, "line": i, "auth_hint": auth,
                       "router": obj})
    # SQL table usage
    reads, writes = defaultdict(list), defaultdict(list)
    for i, line in enumerate(lines, 1):
        for m in SQL_WRITE_RE.finditer(line):
            if m.group(1).lower() in tables:
                writes[m.group(1).lower()].append(i)
        for m in SQL_READ_RE.finditer(line):
            if m.group(1).lower() in tables and ("SELECT" in line.upper() or "JOIN" in line.upper() or "FROM" in line):
                reads[m.group(1).lower()].append(i)
    return {"doc": doc.split("\n")[0] if doc else None, "imports": imports, "symbols": symbols,
            "env": sorted({e[0] for e in env}), "env_lines": sorted(env), "routes": routes,
            "reads": {k: v[:12] for k, v in reads.items()}, "writes": {k: v[:12] for k, v in writes.items()},
            "markers": sorted(set(re.findall(r"pytest\.mark\.(\w+)", src)))}


# --------------------------------------------------------------------------- SQL
def split_top(s):
    out, depth, cur = [], 0, ""
    for ch in s:
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        if ch == "," and depth == 0:
            out.append(cur)
            cur = ""
        else:
            cur += ch
    if cur.strip():
        out.append(cur)
    return out


def sql_facts():
    files = ["backend/schema.sql"] + sorted(rel(p) for p in (ROOT / "backend/migrations").glob("*.sql"))
    tables, indexes, other = {}, [], []
    for f in files:
        text = (ROOT / f).read_text()
        clean = re.sub(r"--[^\n]*", "", text)
        for m in re.finditer(r"CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?([a-z_][a-z0-9_]*)\s*\(", clean, re.I):
            name = m.group(1).lower()
            start = m.end()
            depth, k = 1, start
            while depth and k < len(clean):
                depth += {"(": 1, ")": -1}.get(clean[k], 0)
                k += 1
            body = clean[start:k - 1]
            line = text[: text.find(m.group(0)) if m.group(0) in text else 0].count("\n") + 1
            t = tables.setdefault(name, {"name": name, "defined_in": f"{f}:{line}", "columns": [], "constraints": [],
                                         "altered_in": []})
            for part in split_top(body):
                p = " ".join(part.split())
                if not p:
                    continue
                head = p.split(" ")[0].upper()
                if head in ("PRIMARY", "UNIQUE", "CHECK", "FOREIGN", "CONSTRAINT", "EXCLUDE"):
                    t["constraints"].append(p)
                else:
                    col = p.split(" ")[0].strip('"')
                    t["columns"].append({"name": col, "def": p[len(col):].strip(), "source": f"{f}:{line}"})
        for m in re.finditer(r"ALTER\s+TABLE\s+(?:IF\s+EXISTS\s+)?([a-z_][a-z0-9_]*)\s+(.*?);", clean, re.I | re.S):
            name = m.group(1).lower()
            line = text.find(m.group(0)[:30])
            line = text[:line].count("\n") + 1 if line >= 0 else 1
            t = tables.setdefault(name, {"name": name, "defined_in": None, "columns": [], "constraints": [],
                                         "altered_in": []})
            t["altered_in"].append(f"{f}:{line}")
            for part in split_top(m.group(2)):
                p = " ".join(part.split())
                cm = re.match(r"ADD\s+COLUMN\s+(?:IF\s+NOT\s+EXISTS\s+)?(\w+)\s*(.*)", p, re.I)
                if cm:
                    t["columns"].append({"name": cm.group(1), "def": cm.group(2), "source": f"{f}:{line}"})
                elif p:
                    t["constraints"].append(p)
        for m in re.finditer(r"CREATE\s+(UNIQUE\s+)?INDEX\s+(?:IF\s+NOT\s+EXISTS\s+)?(\w+)\s+ON\s+(\w+)\s*(?:USING\s+\w+\s*)?\(([^;]*?)\)\s*(WHERE[^;]*)?;", clean, re.I | re.S):
            idx = text.find(m.group(2))
            indexes.append({"name": m.group(2), "table": m.group(3).lower(), "unique": bool(m.group(1)),
                            "columns": " ".join(m.group(4).split()), "where": " ".join((m.group(5) or "").split()),
                            "source": f"{f}:{text[:idx].count(chr(10)) + 1}"})
        for m in re.finditer(r"CREATE\s+(?:OR\s+REPLACE\s+)?(FUNCTION|TRIGGER|VIEW|TYPE)\s+(\w+)", clean, re.I):
            idx = text.find(m.group(0))
            other.append({"kind": m.group(1).lower(), "name": m.group(2), "source": f"{f}:{text[:idx].count(chr(10)) + 1}"})
    # enums from CHECK (... IN (...))
    for t in tables.values():
        enums = {}
        for c in t["columns"]:
            m = re.search(r"CHECK\s*\(\s*(\w+)\s+IN\s*\(([^)]*)\)", c["def"], re.I)
            if m:
                enums[c["name"]] = [v.strip(" '") for v in m.group(2).split(",")]
        for c in t["constraints"]:
            for m in re.finditer(r"(\w+)\s+IN\s*\(([^)]*)\)", c, re.I):
                enums.setdefault(m.group(1), [v.strip(" '") for v in m.group(2).split(",")])
        t["enums"] = enums
        t["indexes"] = [i for i in indexes if i["table"] == t["name"]]
        t["foreign_keys"] = [{"column": c["name"], "references": re.search(r"REFERENCES\s+(\w+)", c["def"], re.I).group(1).lower()}
                             for c in t["columns"] if re.search(r"REFERENCES\s+(\w+)", c["def"], re.I)]
        seen, cols = set(), []
        for c in t["columns"]:
            if c["name"] not in seen:
                seen.add(c["name"])
                cols.append(c)
        t["columns"] = cols
    return {"files": files, "tables": tables, "indexes": indexes, "other": other}


def embedded_ddl(pyfiles):
    hits = []
    for r in pyfiles:
        for i, line in enumerate((ROOT / r).read_text().splitlines(), 1):
            if re.search(r"\b(CREATE\s+(TABLE|INDEX|UNIQUE)|ALTER\s+TABLE)\b", line, re.I):
                hits.append({"path": r, "line": i, "text": line.strip()[:200]})
    return hits


# --------------------------------------------------------------------------- compose
def compose_facts():
    import yaml
    out = {}
    for f in ("compose.yaml", "compose.protocol.yaml"):
        text = (ROOT / f).read_text()
        data = yaml.safe_load(text)
        lines = text.splitlines()

        def line_of(key, after=0):
            for i, l in enumerate(lines[after:], after + 1):
                if re.match(rf"^\s*{re.escape(key)}:", l):
                    return i
            return None

        svc_start = line_of("services") or 0
        services = {}
        for name, s in (data.get("services") or {}).items():
            ln = line_of(name, svc_start)
            env = s.get("environment") or {}
            env_names = sorted(env.keys()) if isinstance(env, dict) else sorted(e.split("=")[0] for e in env)
            services[name] = {
                "line": f"{f}:{ln}", "image": s.get("image"), "build": s.get("build"),
                "command": s.get("command"), "entrypoint": s.get("entrypoint"), "ports": s.get("ports", []),
                "networks": s.get("networks", []) if not isinstance(s.get("networks"), dict) else list(s["networks"]),
                "network_mode": s.get("network_mode"), "volumes": s.get("volumes", []),
                "depends_on": s.get("depends_on", {}), "profiles": s.get("profiles", []),
                "restart": s.get("restart"), "read_only": s.get("read_only"), "user": s.get("user"),
                "security_opt": s.get("security_opt"), "cap_drop": s.get("cap_drop"), "cap_add": s.get("cap_add"),
                "tmpfs": s.get("tmpfs"), "mem_limit": s.get("mem_limit"), "cpus": s.get("cpus"),
                "init": s.get("init"), "healthcheck": s.get("healthcheck"), "env_names": env_names,
            }
        env_refs = sorted(set(re.findall(r"\$\{([A-Z0-9_]+)", text)))
        out[f] = {"services": services, "networks": list((data.get("networks") or {}).keys()),
                  "volumes": list((data.get("volumes") or {}).keys()), "env_refs": env_refs,
                  "network_lines": {n: f"{f}:{line_of(n, line_of('networks', svc_start + 5) or 0)}" for n in (data.get('networks') or {})},
                  "volume_lines": {v: f"{f}:{line_of(v, line_of('volumes', len(lines) - 15) or 0)}" for v in (data.get('volumes') or {})}}
    return out


# --------------------------------------------------------------------------- web
def web_facts():
    out = {}
    files = sorted(rel(p) for p in (ROOT / "web/src").rglob("*") if p.is_file()) + ["web/public/sw.js", "web/index.html", "web/vite.config.ts"]
    for r in files:
        p = ROOT / r
        try:
            text = p.read_text()
        except UnicodeDecodeError:
            out[r] = {"binary": True}
            continue
        lines = text.splitlines()
        calls, imports, components = [], [], []
        for i, line in enumerate(lines, 1):
            for m in re.finditer(r"""\b(api|mutate|fetch)\s*(?:<[^>()]*(?:<[^>]*>)?[^>()]*>)?\(\s*([`'"])(.*?)\2(?:\s*,\s*['"](GET|POST|PUT|DELETE|PATCH)['"])?""", line):
                fn, _, path, meth = m.groups()
                method = meth
                if not method:
                    tail = line[m.end():m.end() + 200]
                    mm = re.search(r"method\s*:\s*['\"](\w+)['\"]", tail)
                    method = mm.group(1) if mm else ("GET" if fn != "mutate" else "?")
                calls.append({"fn": fn, "path": path, "method": method.upper(), "line": i})
            for m in re.finditer(r"new\s+(EventSource|WebSocket|RFB)\s*\(", line):
                lit = re.search(r"`([^`]*)`|'([^']*)'|\"([^\"]*)\"", line[m.end():])
                target = next((g for g in (lit.groups() if lit else ()) if g), "")
                calls.append({"fn": m.group(1), "path": target[:200], "method": m.group(1).upper(), "line": i})
            m = re.match(r"\s*import\s+.*?from\s+['\"](\.[^'\"]+)['\"]", line)
            if m:
                imports.append({"target": m.group(1), "line": i})
            m = re.match(r"\s*export\s+(?:default\s+)?function\s+(\w+)", line)
            if m:
                components.append({"name": m.group(1), "line": i})
            m = re.match(r"\s*(?:export\s+)?const\s+([A-Z]\w+)\s*=\s*\(", line)
            if m:
                components.append({"name": m.group(1), "line": i})
        # `const base=`/api/...`,path=`${base}/${id}``: expand a leading `${name}` from same-file template assignments
        tvars = {m.group(1): m.group(2) for m in re.finditer(r"\b(\w+)\s*=\s*`([^`]*)`", text)}
        for c in calls:
            raw = c["path"]
            for _ in range(4):
                m = re.match(r"\$\{(\w+)\}", c["path"])
                if not m or m.group(1) not in tvars:
                    break
                c["path"] = tvars[m.group(1)] + c["path"][m.end():]
            if c["path"] != raw:
                c["path_raw"] = raw
        resolved = []
        for imp in imports:
            base = (p.parent / imp["target"]).resolve()
            for ext in ("", ".tsx", ".ts", ".css", "/index.ts"):
                q = Path(str(base) + ext)
                if q.is_file():
                    resolved.append({"target": rel(q), "line": imp["line"]})
                    break
        out[r] = {"calls": calls, "imports": resolved, "components": components}
    return out


# --------------------------------------------------------------------------- deps / tools / env
def gather_deps():
    deps = []
    py = (ROOT / "pyproject.toml").read_text().splitlines()
    for i, line in enumerate(py, 1):
        m = re.match(r'\s*"([A-Za-z0-9_.\-]+)(\[[^\]]*\])?==([^"]+)"', line)
        if m:
            deps.append({"name": m.group(1), "version": m.group(3), "ecosystem": "python", "source": f"pyproject.toml:{i}"})
        for m in re.finditer(r'"(pytest|ruff)==([^"]+)"', line):
            deps.append({"name": m.group(1), "version": m.group(2), "ecosystem": "python-dev", "source": f"pyproject.toml:{i}"})
    for pj in ("web/package.json", "tests/browser/package.json"):
        text = (ROOT / pj).read_text()
        data = json.loads(text)
        for sec in ("dependencies", "devDependencies"):
            for name, ver in (data.get(sec) or {}).items():
                ln = next((i for i, l in enumerate(text.splitlines(), 1) if f'"{name}"' in l), 1)
                deps.append({"name": name, "version": ver, "ecosystem": f"npm ({pj})", "source": f"{pj}:{ln}"})
    images = []
    for f in ("backend/Dockerfile", "backend/Tools.Dockerfile", "docker/hermes.Dockerfile"):
        for i, line in enumerate((ROOT / f).read_text().splitlines(), 1):
            m = re.match(r"\s*FROM\s+(\S+)(?:\s+AS\s+(\S+))?", line, re.I)
            if m:
                images.append({"ref": m.group(1), "stage": m.group(2), "source": f"{f}:{i}"})
    for f in ("compose.yaml",):
        for i, line in enumerate((ROOT / f).read_text().splitlines(), 1):
            m = re.match(r"\s*image:\s*(\S+)", line)
            if m:
                images.append({"ref": m.group(1), "stage": None, "source": f"{f}:{i}"})
    return deps, images


def plugin_tools():
    y = (ROOT / "plugins/hermbot/plugin.yaml").read_text().splitlines()
    init = (ROOT / "plugins/hermbot/__init__.py").read_text().splitlines()
    tools = []
    section = None
    for i, line in enumerate(y, 1):
        if re.match(r"^provides_tools:", line):
            section = "tool"
        elif re.match(r"^provides_hooks:", line):
            section = "hook"
        elif re.match(r"^\S", line):
            section = None
        m = re.match(r"\s*-\s*(\w+)", line)
        if m and section:
            name = m.group(1)
            impl = [f"plugins/hermbot/__init__.py:{j}" for j, l in enumerate(init, 1) if re.search(rf"['\"]{name}['\"]", l)]
            tools.append({"name": name, "kind": section, "declared": f"plugins/hermbot/plugin.yaml:{i}", "impl": impl[:6],
                          "in_plugin_yaml": True})
    # Tools actually registered in code: the TOOLS table keys become hermbot_<key> (register() loop).
    start = next((j for j, l in enumerate(init) if re.match(r"^TOOLS\s*=", l)), None)
    known = {t["name"] for t in tools}
    if start is not None:
        for j in range(start, len(init)):
            if j > start and re.match(r"^\S", init[j]) and not init[j].startswith("}"):
                break
            m = re.match(r"""^\s{4}['"](\w+)['"]\s*:\s*\(""", init[j])
            if m:
                name = "hermbot_" + m.group(1)
                ev = f"plugins/hermbot/__init__.py:{j + 1}"
                if name in known:
                    next(t for t in tools if t["name"] == name)["impl"].insert(0, ev)
                else:
                    tools.append({"name": name, "kind": "tool", "declared": ev, "impl": [ev], "in_plugin_yaml": False})
                    known.add(name)
    for j, l in enumerate(init):
        m = re.search(r"register_middleware\(['\"](\w+)['\"],\s*(\w+)", l)
        if m:
            tools.append({"name": f"middleware:{m.group(1)}", "kind": "middleware", "declared": f"plugins/hermbot/__init__.py:{j + 1}",
                          "impl": [f"plugins/hermbot/__init__.py:{j + 1}"], "handler": m.group(2), "in_plugin_yaml": False})
    return tools


def env_example_names():
    names = []
    for i, line in enumerate((ROOT / ".env.example").read_text().splitlines(), 1):
        m = re.match(r"\s*([A-Z][A-Z0-9_]*)\s*=", line)
        if m:
            names.append({"name": m.group(1), "source": f".env.example:{i}"})
    return names


def tests_facts(files):
    out = {}
    for f in files:
        r = f["path"]
        if not r.startswith("tests/") or f.get("binary") or r.startswith("tests/browser/node_modules"):
            continue
        if not (r.endswith(".py") or r.endswith(".mjs") or r.endswith(".json")):
            continue
        text = (ROOT / r).read_text()
        imports = sorted(set(re.findall(r"from\s+(backend|docker|plugins|tests|scripts)[.\w]*\s+import|import\s+(backend)\b", text)))
        mods = sorted(set(m for m in re.findall(r"(?:from|import)\s+((?:backend|docker|plugins|scripts)(?:\.\w+)?)", text)))
        out[r] = {"markers": sorted(set(re.findall(r"pytest\.mark\.(\w+)", text))),
                  "pytestmark": bool(re.search(r"^pytestmark", text, re.M)),
                  "modules": mods, "tests": len(re.findall(r"^\s*(?:async\s+)?def\s+test_", text, re.M)),
                  "urls": sorted(set(re.findall(r"['\"`](/api/v1/[^'\"`?\s]*)", text)))[:40]}
    return out


def media_facts(r):
    """Mechanically open every binary/evidence file and summarise it (untrusted data: parsed, never executed)."""
    import gzip
    import struct
    import xml.etree.ElementTree as ET
    p = ROOT / r
    out = {}
    try:
        head = p.open("rb").read(64 * 1024)
        if head[:8] == b"\x89PNG\r\n\x1a\n":
            w, h = struct.unpack(">II", head[16:24])
            out.update(format="png", width=w, height=h)
        elif head[:2] == b"\xff\xd8":
            data, i = p.read_bytes(), 2
            while i < len(data) - 9:
                if data[i] != 0xFF:
                    i += 1
                    continue
                marker, size = data[i + 1], struct.unpack(">H", data[i + 2:i + 4])[0]
                if marker in (0xC0, 0xC1, 0xC2):
                    h, w = struct.unpack(">HH", data[i + 5:i + 9])
                    out.update(format="jpeg", width=w, height=h)
                    break
                i += 2 + size
        elif head[:4] == b"wOF2":
            out.update(format="woff2", num_tables=struct.unpack(">H", head[12:14])[0])
        elif head[:2] == b"\x1f\x8b":
            n, size = 0, 0
            with gzip.open(p, "rb") as fh:
                for line in fh:
                    n += 1
                    size += len(line)
            out.update(format="gzip", decompressed_bytes=size, records=n)
        elif r.endswith(".xml"):
            root = ET.fromstring(p.read_text())
            suites = [root] if root.tag == "testsuite" else root.findall("testsuite")
            out.update(format="junit", tests=sum(int(x.get("tests", 0)) for x in suites),
                       failures=sum(int(x.get("failures", 0)) for x in suites), errors=sum(int(x.get("errors", 0)) for x in suites),
                       skipped=sum(int(x.get("skipped", 0)) for x in suites), timestamp=suites[0].get("timestamp") if suites else None)
        elif r.endswith(".json"):
            data = json.loads(p.read_text())
            out["format"] = "json"
            if isinstance(data, dict):
                out["top_level_keys"] = list(data.keys())[:15]
                for k in ("status", "result", "pass", "passed", "ok", "success", "verdict", "failures", "summary"):
                    if k in data and isinstance(data[k], (str, int, float, bool)):
                        out[k] = data[k]
            elif isinstance(data, list):
                out["items"] = len(data)
        elif r.endswith(".py"):
            text = p.read_text()
            out.update(format="python", lines=text.count("\n") + 1, defs=re.findall(r"^def (\w+)", text, re.M)[:20])
        elif not head:
            out["format"] = "empty marker file"
        else:
            out["format"] = "text"
            out["first_line"] = head.split(b"\n", 1)[0][:120].decode("utf-8", "replace")
    except Exception as exc:  # noqa: BLE001 - record, never fail extraction
        out["error"] = f"{type(exc).__name__}"
    return out


def lock_facts():
    """Every locked package in every lockfile, with what pulled it in (pip) or whether it is dev/optional (npm)."""
    out = {}
    lines = (ROOT / "requirements.lock").read_text().splitlines()
    pkgs, cur = [], None
    for i, line in enumerate(lines, 1):
        m = re.match(r"^([A-Za-z0-9_.\-\[\]]+)==([^\s\\;]+)", line)
        if m:
            cur = {"name": m.group(1), "version": m.group(2), "line": i, "via": []}
            pkgs.append(cur)
            continue
        m = re.match(r"^\s+#\s+(?:via\s+)?([A-Za-z0-9_.\-]+(?:\s*\(.*\))?)\s*$", line)
        if cur and m and "hash" not in line:
            cur["via"].append(m.group(1).strip())
    out["requirements.lock"] = {"tool": "uv pip compile (hashed, universal, python 3.12)", "packages": pkgs,
                                "hash_lines": sum(1 for l in lines if "--hash=" in l)}
    for lf in ("web/package-lock.json", "tests/browser/package-lock.json"):
        d = json.loads((ROOT / lf).read_text())
        pk = []
        for key, v in (d.get("packages") or {}).items():
            if not key:
                continue
            pk.append({"name": key.split("node_modules/")[-1], "version": v.get("version"), "dev": bool(v.get("dev")),
                       "optional": bool(v.get("optional")), "license": v.get("license")})
        out[lf] = {"lockfileVersion": d.get("lockfileVersion"), "packages": pk}
    return out


def extract():
    WORK.mkdir(parents=True, exist_ok=True)
    files, excluded = inventory()
    for f in files:
        if f["path"] != ".env" and (f.get("binary") or f["path"].startswith(("test-results/", ".taste-skill/", "plan/archive/"))
                                    or f["path"].endswith((".woff2", ".png", ".jpg", ".gz"))):
            f["media"] = media_facts(f["path"])
    sql = sql_facts()
    tables = set(sql["tables"])
    pyfiles = [f["path"] for f in files if f["path"].endswith(".py") and not f["path"].startswith("test-results/")]
    py = {}
    for r in pyfiles:
        try:
            py[r] = python_facts(r, tables)
        except SyntaxError as exc:
            py[r] = {"error": str(exc)}
    deps, images = gather_deps()
    mech = {"root": str(ROOT), "files": files, "excluded": excluded, "python": py, "sql": sql,
            "embedded_ddl": embedded_ddl([p for p in pyfiles if not p.startswith("tests/")]),
            "compose": compose_facts(), "web": web_facts(), "deps": deps, "images": images,
            "plugin_tools": plugin_tools(), "env_example": env_example_names(), "tests": tests_facts(files),
            "locks": lock_facts()}
    (WORK / "mech.json").write_text(json.dumps(mech, indent=1, default=str))
    n_routes = sum(len(v.get("routes", [])) for v in py.values())
    print(f"files={len(files)} excluded_dirs={len(excluded)} excluded_files={sum(excluded.values())} "
          f"py={len(py)} routes={n_routes} tables={len(tables)} tools={len(mech['plugin_tools'])}")


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "extract"
    if cmd == "extract":
        extract()
    elif cmd == "merge":
        from merge_model import merge  # noqa: E402
        merge()
