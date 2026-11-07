"""Private adapter for pinned Hermes knowledge and Bot Desktop primitives.

Only the product backend holds this adapter's credential. No host ports or Docker socket are exposed.
Native RFB parsing and disk-backed control leases are reused; disconnect never releases a lease.
"""

import asyncio
import base64
import fcntl
import hashlib
import hmac
import json
import os
import re
import subprocess
import stat
import sys
from contextlib import contextmanager
from pathlib import Path
from pathlib import PurePosixPath
from typing import Literal
from uuid import uuid4

sys.path.insert(0, "/opt/hermes")
sys.path.insert(0, str(Path(__file__).parent))

from fastapi import Depends, FastAPI, Header, HTTPException, WebSocket
from pydantic import BaseModel, ConfigDict, Field

from teaching_capture import AcceptedRFB, Capture

from hermbot_runtime_gate import DrainTimeout, GateUnavailable, OwnershipChanged, drain

HOME = Path(os.environ.get("HERMES_HOME", "/opt/data"))
WORKSPACE = Path('/workspace')
app = FastAPI(docs_url=None, redoc_url=None)
capture = Capture()


def authorize(authorization: str = Header(default="")):
    supplied = authorization.removeprefix("Bearer ")
    expected = os.environ.get("HERMBOT_ADAPTER_KEY", "")
    if not expected or not hmac.compare_digest(supplied, expected):
        raise HTTPException(401, "Private runtime authentication required")


def sha(content):
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


@contextmanager
def directory(parts):
    fd = os.open(HOME, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        for name in parts:
            try:
                os.mkdir(name, 0o700, dir_fd=fd)
            except FileExistsError:
                pass
            next_fd = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = next_fd
        yield fd
    except OSError:
        raise HTTPException(409, "The private knowledge directory is unsafe or inaccessible")
    finally:
        os.close(fd)


def read_file(parts, name):
    with directory(parts) as fd:
        try:
            file_fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
        except FileNotFoundError:
            return ""
        except OSError:
            raise HTTPException(409, "Knowledge files cannot be symlinks or inaccessible")
        with os.fdopen(file_fd, "r", encoding="utf-8") as file:
            if not stat.S_ISREG(os.fstat(file.fileno()).st_mode):
                raise HTTPException(409, "Knowledge must be a regular file")
            try:
                value = file.read(100_001)
            except UnicodeError:
                raise HTTPException(422, "Knowledge files must contain UTF-8 text")
            if len(value) > 100_000:
                raise HTTPException(413, "Knowledge file exceeds the editable size limit")
            return value


def _write_file(parts, name, content, expected_hash):
    current = read_file(parts, name)
    if sha(current) == sha(content):
        return {"sha256": sha(content), "replayed": True}
    if sha(current) != expected_hash:
        raise HTTPException(409, "Knowledge changed since this draft was opened; reload before saving")
    with directory(parts) as fd:
        temporary = "." + uuid4().hex + ".tmp"
        try:
            file_fd = os.open(
                temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=fd
            )
            with os.fdopen(file_fd, "w", encoding="utf-8") as file:
                file.write(content)
                file.flush()
                os.fsync(file.fileno())
            os.replace(temporary, name, src_dir_fd=fd, dst_dir_fd=fd)
            os.fsync(fd)
        finally:
            try:
                os.unlink(temporary, dir_fd=fd)
            except FileNotFoundError:
                pass
    return {"sha256": sha(content), "replayed": False}


def write_file(parts, name, content, expected_hash):
    with directory([]) as fd:
        lock_fd = os.open("hermbot-knowledge.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600, dir_fd=fd)
    try:
        fcntl.flock(lock_fd, fcntl.LOCK_EX)
        result = _write_file(parts, name, content, expected_hash)
        if name == "SKILL.md" and not content:
            with directory(parts) as fd:
                try:
                    os.unlink(name, dir_fd=fd)
                    os.fsync(fd)
                except FileNotFoundError:
                    pass
        return result
    finally:
        os.close(lock_fd)


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class KnowledgeWrite(Strict):
    content: str = Field(max_length=100_000)
    expected_hash: str = Field(pattern=r"^[0-9a-f]{64}$")


class WorkspaceExport(Strict):
    path: str = Field(min_length=1, max_length=2000)


def workspace_bytes(path):
    supplied = PurePosixPath(path)
    if not supplied.is_absolute() or not path.startswith('/workspace/') or any(part in ('.', '..') for part in path.split('/')):
        raise HTTPException(422, 'Publish a file below /workspace using an absolute path without traversal')
    parts = supplied.parts[2:]
    if not parts or any(part.startswith('.') for part in parts):
        raise HTTPException(422, 'Hidden workspace files cannot be published through this interface')
    fd = os.open(WORKSPACE, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        for part in parts[:-1]:
            following = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = following
        file_fd = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
        with os.fdopen(file_fd, 'rb') as file:
            info = os.fstat(file.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                raise HTTPException(422, 'Publish a regular workspace file without symlinks or hard links')
            data = file.read(10_000_001)
            if len(data) > 10_000_000:
                raise HTTPException(413, 'Published files are limited to 10 MB')
            after = os.fstat(file.fileno())
            if len(data) != info.st_size or after.st_size != info.st_size or after.st_mtime_ns != info.st_mtime_ns:
                raise HTTPException(409, 'The file changed during publication; finish writing before publishing')
            return data
    except OSError:
        raise HTTPException(422, 'The workspace file is missing, inaccessible, or contains a symbolic link') from None
    finally:
        os.close(fd)


@app.post('/workspace/export', dependencies=[Depends(authorize)])
def export_workspace(body: WorkspaceExport):
    data = workspace_bytes(body.path)
    return {'name': PurePosixPath(body.path).name, 'size': len(data), 'sha256': hashlib.sha256(data).hexdigest(),
            'content_base64': base64.b64encode(data).decode('ascii')}


class Control(Strict):
    action: Literal["takeover", "handback"]
    viewer_id: str = Field(min_length=32, max_length=128)
    force: bool = False


def skill_name(name):
    if not re.fullmatch(r"[a-z][a-z0-9-]{0,79}", name):
        raise HTTPException(422, "Use a lowercase skill name with letters, numbers and hyphens")
    return name


@app.get("/health")
def health():
    return {"status": "ok", "adapter": 4, 'execution_capabilities': 1}


@app.get("/memory", dependencies=[Depends(authorize)])
def memory():
    from tools.memory_tool import load_on_disk_store

    store = load_on_disk_store()
    return {
        "targets": [
            {
                "name": name,
                "content": value,
                "sha256": sha(value),
                "limit": store.user_char_limit if name == "user" else store.memory_char_limit,
            }
            for name, value in [
                (name, read_file(["memories"], "USER.md" if name == "user" else "MEMORY.md"))
                for name in ("memory", "user")
            ]
        ]
    }


@app.put("/memory/{target}", dependencies=[Depends(authorize)])
def update_memory(target: str, body: KnowledgeWrite):
    if target not in ("memory", "user"):
        raise HTTPException(422, "Choose memory or user profile")
    from tools.memory_tool import load_on_disk_store, _scan_memory_content

    store = load_on_disk_store()
    limit = store.user_char_limit if target == "user" else store.memory_char_limit
    if len(body.content) > limit:
        raise HTTPException(413, f"This native memory target has a {limit}-character limit")
    if body.content and _scan_memory_content(body.content):
        raise HTTPException(422, "The native memory safety check rejected this content")
    return write_file(
        ["memories"], "USER.md" if target == "user" else "MEMORY.md", body.content, body.expected_hash
    )


@app.get("/skills", dependencies=[Depends(authorize)])
def catalog():
    from tools.skills_tool import _find_all_skills
    import yaml
    enabled = set(json.loads(os.environ.get('HERMES_ENABLED_SKILLS', '[]')))
    items = _find_all_skills(skip_disabled=True)
    for item in items:
        item['required_tools'] = []
        if item['name'] not in enabled:
            continue
        try:
            raw = skill(item['name'])
            content = raw['content']
            item['sha256'] = raw['sha256']
            frontmatter = yaml.safe_load(content.split('---', 2)[1]) if content.startswith('---\n') else {}
            metadata = (frontmatter or {}).get('metadata', {})
            requirements = metadata.get('hermbot', {}).get('required_tools', []) if isinstance(metadata, dict) else []
            if not isinstance(requirements, list) or len(requirements) > 30 or any(not isinstance(t, str) or not re.fullmatch(r'[a-z][a-z0-9_]{0,119}', t) for t in requirements):
                item['metadata_error'] = 'Skill required_tools must be a bounded list of exact tool names.'
            else:
                item['required_tools'] = sorted(set(requirements))
        except (HTTPException, ValueError, IndexError, AttributeError, yaml.YAMLError):
            item['metadata_error'] = 'The enabled skill could not be read or its requirement metadata is invalid.'
    return {"data": items}


@app.get("/skills/{name}", dependencies=[Depends(authorize)])
def skill(name: str):
    skill_name(name)
    from tools.skills_tool import _skill_search_dirs, _locate_skill

    project, all_dirs, _ = _skill_search_dirs()
    error, _, skill_md = _locate_skill(name, None, project, all_dirs)
    if error is not None:
        return {
            "name": name,
            "content": "",
            "sha256": sha(""),
            "exists": False,
            "editable": name.startswith("hermbot-"),
        }
    path = Path(skill_md)
    if not path.is_relative_to(HOME / "skills"):
        # Bundled sources are immutable image files. Do not preprocess templates or shell expansions.
        if not path.is_relative_to(Path("/opt/hermes/skills")) or path.is_symlink():
            raise HTTPException(409, "Unsupported native skill location")
        value = path.read_text(encoding="utf-8")[:100_000]
    else:
        relative = path.relative_to(HOME)
        value = read_file(list(relative.parts[:-1]), relative.name)
    return {
        "name": name,
        "content": value,
        "sha256": sha(value),
        "exists": True,
        "editable": name.startswith("hermbot-"),
    }


@app.put("/skills/{name}", dependencies=[Depends(authorize)])
def update_skill(name: str, body: KnowledgeWrite):
    skill_name(name)
    if not name.startswith("hermbot-"):
        raise HTTPException(
            422, "Owner-managed skills use the hermbot- prefix; import a copy to edit a bundled skill"
        )
    if body.content:
        import yaml

        if not body.content.startswith("---\n") or "\n---\n" not in body.content[4:]:
            raise HTTPException(422, "A skill needs YAML frontmatter with its name and description")
        try:
            metadata = yaml.safe_load(body.content.split("---", 2)[1])
        except yaml.YAMLError:
            raise HTTPException(422, "Invalid skill frontmatter")
        if (
            not isinstance(metadata, dict)
            or metadata.get("name") != name
            or not isinstance(metadata.get("description"), str)
            or not metadata["description"].strip()
        ):
            raise HTTPException(
                422, "Skill frontmatter must match the selected name and include a description"
            )
    return write_file(["skills", "hermbot", name], "SKILL.md", body.content, body.expected_hash)


def ready_for_input(viewer_id):
    from tools.bot_desktop import lease

    state = lease.get()
    try:
        marker = json.loads(read_file([], "hermbot-control-ready.json"))
    except ValueError:
        return False
    return (
        state.holder == "human"
        and state.viewer_id == viewer_id
        and marker.get("epoch") == state.epoch
        and marker.get("viewer_id") == viewer_id
    )


@app.get("/computer", dependencies=[Depends(authorize)])
def computer():
    from tools.bot_desktop import lease, runtime

    state = lease.get()
    return {
        "desktop": runtime.status().as_dict(),
        "lease": lease.public_view(state),
        "input_ready": ready_for_input(state.viewer_id or ""),
    }


@app.post("/computer/start", dependencies=[Depends(authorize)])
def start():
    from tools.bot_desktop import runtime

    try:
        runtime.start()
    except RuntimeError:
        raise HTTPException(
            409, "The desktop cannot start; check its installed packages and available memory"
        )
    return computer()


@app.post("/computer/stop", dependencies=[Depends(authorize)])
def stop():
    from tools.bot_desktop import lease, runtime

    if lease.human_holds():
        raise HTTPException(409, "Hand back control before stopping the desktop")
    runtime.stop()
    return computer()


@app.get("/computer/thumbnail", dependencies=[Depends(authorize)])
def thumbnail():
    from tools.bot_desktop.thumbnail import thumbnail_data_url

    return {"image": thumbnail_data_url()}


@app.post("/computer/control", dependencies=[Depends(authorize)])
def control(body: Control):
    from tools.bot_desktop import lease

    if body.action == "handback":
        capture.pause('computer handed back')
        state = lease.release(None if body.force else body.viewer_id)
        if state.holder == "human":
            raise HTTPException(
                409, "Another session holds control; explicitly reclaim it before handing back"
            )
        return computer()
    # Close admission first; concurrent admitted callbacks retain shared activity locks.
    # Human input becomes ready only after all of them drain and this exact lease still owns control.
    capture.pause('computer ownership changed')
    state = lease.acquire(body.viewer_id, reason="Owner takeover in Hermbot")
    def owns_control():
        current = lease.get()
        return current.holder == 'human' and current.viewer_id == body.viewer_id and current.epoch == state.epoch
    try:
        with drain(HOME, owns_control):
            marker = json.dumps({"epoch": state.epoch, "viewer_id": body.viewer_id})
            write_file([], "hermbot-control-ready.json", marker, sha(read_file([], "hermbot-control-ready.json")))
    except DrainTimeout:
        raise HTTPException(409, "In-flight tools have not settled. Bot admission stays paused; retry takeover when they settle") from None
    except OwnershipChanged:
        raise HTTPException(409, "Computer ownership changed during handoff; reload its current control state") from None
    except GateUnavailable:
        raise HTTPException(409, "Computer handover cannot verify tool admission; human input stays disabled") from None
    return computer()


@app.post("/computer/browser", dependencies=[Depends(authorize)])
def open_browser(body: dict):
    if not ready_for_input(body.get("viewer_id", "")):
        raise HTTPException(
            409, "Take control and wait for in-flight work to settle before opening a browser"
        )
    from tools.bot_desktop import browser, runtime

    launch = browser.dock_launch()
    if not launch or not runtime.status().running:
        raise HTTPException(409, "Start the desktop before opening its browser")
    exe, profile = launch
    subprocess.Popen(
        browser.dock_argv(exe, profile) + ["about:blank"],
        env=runtime.desktop_env(),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    return {"opened": True}


class TeachingControl(Strict):
    session: str = Field(pattern=r'^[0-9a-f-]{36}$')
    action: Literal['start', 'pause', 'private', 'resume', 'finish', 'discard']
    viewer: str = Field(pattern=r'^[0-9a-f]{64}$')
    epoch: int = Field(ge=0)
    revision: int = Field(ge=1)


@app.post('/teaching/control', dependencies=[Depends(authorize)])
async def teaching_control(body: TeachingControl):
    from tools.bot_desktop import lease
    current = lease.get()
    if body.action not in ('finish','discard') and (not ready_for_input(body.viewer) or current.epoch != body.epoch):
        raise HTTPException(409, 'Take over the computer in this session before recording')
    try:
        if body.action == 'finish' and capture.session == body.session and capture.state == 'recording':
            await teaching_frame(capture.generation, capture.geometry)
        result = capture.control(body.session, body.action, body.viewer, body.epoch, body.revision)
        if body.action in ('start','resume'):
            from tools.bot_desktop import runtime
            await teaching_frame(result['generation'], runtime.status().as_dict().get('geometry',''))
        return result
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from None


@app.get('/teaching/batch', dependencies=[Depends(authorize)])
def teaching_batch(ack: int = 0):
    return capture.batch(ack)


async def teaching_frame(generation, geometry):
    from tools.bot_desktop.thumbnail import thumbnail_data_url
    try:
        await asyncio.sleep(.2)
        from tools.bot_desktop import runtime
        geometry = runtime.status().as_dict().get('geometry','')
        image = await asyncio.to_thread(thumbnail_data_url, (1440, 1000))
        capture.frame(generation, image, geometry)
        with capture.lock:
            if capture.generation == generation and capture.state == 'recording':
                capture.geometry = geometry
    except Exception:
        capture.pause('Screen capture unavailable; retained prefix only')


@app.websocket("/computer/ws")
async def screen(ws: WebSocket):
    try:
        authorize(ws.headers.get("authorization", ""))
    except HTTPException:
        await ws.close(code=4401)
        return
    viewer = ws.headers.get("x-hermbot-viewer", "")
    if not re.fullmatch(r"[0-9a-f]{64}", viewer):
        await ws.close(code=4401)
        return
    from tools.bot_desktop import runtime
    from tools.bot_desktop.rfb_filter import RfbClientFilter

    sock = runtime.rfb_socket_path()
    if not sock:
        await ws.close(code=4404)
        return
    try:
        reader, writer = await asyncio.open_unix_connection(str(sock))
    except OSError:
        await ws.close(code=4404)
        return
    await ws.accept()
    observer = AcceptedRFB()
    admitted = [None]
    def allowed():
        from tools.bot_desktop import lease
        current = lease.get()
        admitted[0] = current.epoch if ready_for_input(viewer) else None
        return admitted[0] is not None
    gate = RfbClientFilter(allowed)

    async def receive():
        while True:
            packet = await ws.receive()
            if packet["type"] == "websocket.disconnect":
                return
            data = packet.get("bytes")
            if data is None or len(data) > 1_000_000:
                return
            with capture.lock:
                try:
                    data = gate.feed(data)
                except ValueError:
                    return
                generation = None
                try:
                    events = observer.feed(data)
                    geometry = capture.geometry
                    generation = capture.observe(events, viewer, admitted[0], geometry)
                except ValueError:
                    capture.pause('unsupported observation; retained prefix only')
            if data:
                writer.write(data)
                await writer.drain()
            if generation is not None:
                asyncio.create_task(teaching_frame(generation, geometry))

    async def send():
        while True:
            data = await reader.read(65536)
            if not data:
                return
            runtime.touch_activity()
            await ws.send_bytes(data)

    tasks = [asyncio.create_task(receive()), asyncio.create_task(send())]
    try:
        _, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        for task in pending:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
    finally:
        capture.disconnect(viewer)
        writer.close()
        await writer.wait_closed()
        # Only the explicit handback endpoint releases control, including after browser reload.
        try:
            await ws.close()
        except RuntimeError:
            pass


class RoutineScheduler(Strict):
    generation: str = Field(pattern=r'^[0-9a-f-]{36}$')
    token: str = Field(min_length=32, max_length=100)
    enabled: bool


@app.put('/routine-scheduler', dependencies=[Depends(authorize)])
def routine_scheduler(body: RoutineScheduler):
    """Manage only our fixed no-agent heartbeat through the supported CLI/API."""
    from urllib import request
    def jobs():
        req = request.Request('http://127.0.0.1:8642/api/jobs?include_disabled=true', headers={'Authorization': 'Bearer ' + os.environ['API_SERVER_KEY']})
        with request.urlopen(req, timeout=5) as response:
            return json.loads(response.read(1_000_000))['jobs']
    with directory([]) as fd:
        lock_fd = os.open('hermbot-scheduler.lock', os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600, dir_fd=fd)
    try:
        fcntl.flock(lock_fd, fcntl.LOCK_EX)
        source = Path('/opt/hermbot/routine_wakeup.py').read_text()
        _write_file(['scripts'], 'hermbot_routine_wakeup.py', source, sha(read_file(['scripts'], 'hermbot_routine_wakeup.py')))
        url = os.environ.get('HERMBOT_API_URL', 'http://api:8000').rstrip('/') + '/internal/routine-wake/' + os.environ['BOT_ID'] + '/' + body.generation
        content = json.dumps({'url': url, 'token': body.token})
        _write_file([], 'hermbot-routine-wakeup.json', content, sha(read_file([], 'hermbot-routine-wakeup.json')))
        name = 'Hermbot wake ' + body.generation
        matching = [job for job in jobs() if job.get('name') == name]
        if len(matching) > 1:
            raise HTTPException(409, 'Multiple native wake jobs need reconciliation')
        if not matching and body.enabled:
            # Creation has no idempotency header; list under the same lock after any lost response.
            subprocess.run(['/opt/hermes/.venv/bin/hermes', 'cron', 'create', 'every 1m', '--name', name,
                '--script', 'hermbot_routine_wakeup.py', '--no-agent', '--deliver', 'local', '--paused'],
                capture_output=True, timeout=15, check=True)
            matching = [job for job in jobs() if job.get('name') == name]
        if not matching:
            return {'job_id': None, 'enabled': False}
        job = matching[0]
        schedule = job.get('schedule') or {}
        if not job.get('no_agent') or job.get('script') != 'hermbot_routine_wakeup.py' or schedule.get('kind') != 'interval' or schedule.get('minutes') != 1:
            raise HTTPException(409, 'Native wake definition differs; reconcile before enabling')
        if bool(job.get('enabled')) != body.enabled:
            action = 'resume' if body.enabled else 'pause'
            req = request.Request('http://127.0.0.1:8642/api/jobs/' + job['id'] + '/' + action, data=b'{}', method='POST', headers={'Authorization': 'Bearer ' + os.environ['API_SERVER_KEY'], 'Content-Type': 'application/json'})
            with request.urlopen(req, timeout=5) as response:
                response.read(10000)
        return {'job_id': job['id'], 'enabled': body.enabled}
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(503, 'Native routine scheduler needs reconciliation; no task was submitted') from None
    finally:
        os.close(lock_fd)
