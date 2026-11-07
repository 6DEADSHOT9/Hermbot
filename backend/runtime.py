"""Versioned public Hermes API adapter; never execute replayed tool output in the client."""
import json

import httpx

from .settings import settings
from . import db
from .secrets import decrypt
from .models import ARGUMENT_MODELS

REQUIRED_FEATURES = ("run_submission", "run_status", "run_events_sse", "run_stop")
HERMES_REVISION = "749220ef0007f8d87bd1531f1c24b0fe93816385"


class HermesClient:
    def __init__(self, bot_id, base_url=None):
        self.bot_id = bot_id
        with db.connection() as conn:
            bot = conn.execute("SELECT runtime_url,runtime_key_encrypted,provider_id,model_name FROM bots WHERE id=%s", (bot_id,)).fetchone()
        self.base_url = base_url or (bot["runtime_url"] if bot else f"http://hermes-{bot_id}:8642")
        self.provider_configured = bool(bot and bot["provider_id"] and bot["model_name"])
        self.protocol_fixture = bool(bot and bot['model_name'] == 'hermbot-protocol-fixture')
        self.headers = {"Authorization": f"Bearer {decrypt(bot['runtime_key_encrypted']) if bot and bot['runtime_key_encrypted'] else settings.runtime_key(bot_id)}"}

    async def request(self, method, path, **kwargs):
        headers = {**self.headers, **kwargs.pop("headers", {})}
        async with httpx.AsyncClient(timeout=httpx.Timeout(15, connect=4)) as client:
            response = await client.request(method, self.base_url + path, headers=headers, **kwargs)
            response.raise_for_status()
            return response.json()

    async def capabilities(self):
        capabilities = await self.request("GET", "/v1/capabilities")
        missing = [feature for feature in REQUIRED_FEATURES if not capabilities.get("features", {}).get(feature)]
        toolsets = await self.request("GET", "/v1/toolsets")
        plugin = next((row for row in toolsets.get("data", []) if row.get("name") == "hermbot"), {})
        required_tools = {'hermbot_' + name for name in ARGUMENT_MODELS}
        plugin_ready = plugin.get("enabled") and plugin.get("configured") and required_tools.issubset(plugin.get("tools", []))
        if not plugin_ready:
            missing.append("hermbot_plugin")
        adapter_ready = False
        installed_skills = []
        if not self.protocol_fixture:
            from .native import target
            url, headers = target(self.bot_id)
            try:
                async with httpx.AsyncClient(timeout=4) as client:
                    adapter = await client.get(url + '/health', headers=headers)
                    adapter_ready = adapter.status_code == 200 and adapter.json().get('adapter') == 4 and adapter.json().get('execution_capabilities') == 1
                    if adapter_ready:
                        catalog = await client.get(url + '/skills', headers=headers)
                        catalog.raise_for_status()
                        installed_skills = catalog.json().get('data', [])
            except (httpx.HTTPError, ValueError):
                adapter_ready = False
            if not adapter_ready:
                missing.append('execution_capability_adapter')
        return {"compatible": not missing, "missing_features": missing, "features": capabilities.get("features", {}),
                "revision": HERMES_REVISION, "provider_configured": self.provider_configured, "plugin_ready": bool(plugin_ready), 'adapter_ready': adapter_ready,
                'inventory': {'version': 1, 'toolsets': [{'name': row['name'], 'configured': bool(row.get('configured')), 'tools': row.get('tools', [])} for row in toolsets.get('data', []) if row['name'] in {'file', 'terminal', 'skills', 'memory', 'browser', 'computer_use', 'hermbot'}],
                              'outcomes_version': 1 if plugin_ready else 0,
                              'skills': [{key: row[key] for key in ('name', 'description', 'category', 'required_tools', 'sha256', 'metadata_error') if key in row} for row in installed_skills]}}

    async def ensure_session(self, session_id):
        try:
            await self.request("GET", f"/api/sessions/{session_id}")
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code != 404:
                raise
            try:
                await self.request("POST", "/api/sessions", json={"id": session_id, "title": f"Hermbot {session_id}"})
            except httpx.HTTPStatusError as create_exc:
                if create_exc.response.status_code != 409:
                    raise

    async def start(self, body, key):
        return await self.request("POST", "/v1/runs", json=body, headers={"Idempotency-Key": key})

    async def status(self, run_id):
        return await self.request("GET", f"/v1/runs/{run_id}")

    async def stop(self, run_id):
        return await self.request("POST", f"/v1/runs/{run_id}/stop", json={})

    async def approve(self, run_id, choice, request_id):
        return await self.request("POST", f"/v1/runs/{run_id}/approval", json={"choice": choice, "request_id": request_id})

    async def steer(self, run_id, text):
        return await self.request("POST", f"/v1/runs/{run_id}/steer", json={"input": text})

    async def events(self, run_id):
        # Transport reconnection does not imply replay safety; polling and durable product events reconcile state.
        async with httpx.AsyncClient(timeout=httpx.Timeout(25, connect=4)) as client:
            async with client.stream("GET", f"{self.base_url}/v1/runs/{run_id}/events", headers=self.headers) as response:
                response.raise_for_status()
                data, name = [], "message"
                async for line in response.aiter_lines():
                    if not line:
                        if data:
                            payload = json.loads("\n".join(data))
                            payload.setdefault("event", name)
                            yield payload
                        data, name = [], "message"
                    elif line.startswith("data:"):
                        data.append(line[5:].lstrip())
                    elif line.startswith("event:"):
                        name = line[6:].strip()
