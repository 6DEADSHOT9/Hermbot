"""Deterministic protocol fixture, never a substitute for live-model acceptance."""
import asyncio
import json
import time
from uuid import uuid4

from fastapi import FastAPI, Request
from fastapi.responses import StreamingResponse
from fastapi.responses import JSONResponse

app = FastAPI()


def choose(messages):
    last_user = max((i for i, m in enumerate(messages) if m.get("role") == "user"), default=0)
    prompt = str(messages[last_user].get("content", ""))
    tail = messages[last_user:]
    calls = [call for m in tail for call in (m.get("tool_calls") or [])]
    names = [c["function"]["name"] for c in calls]
    results = [m for m in tail if m.get("role") == "tool"]
    def tool(name, args):
        return {"role": "assistant", "content": None, "tool_calls": [{"id": "call_" + uuid4().hex, "type": "function", "function": {"name": name, "arguments": json.dumps(args)}}]}
    if "[POC:review]" in prompt and "hermbot_review" not in names:
        return tool("hermbot_review", {"summary": "Approve the fixture report?", "payload": {"action": "return report"}})
    if "hermbot_review" in names:
        return {"role": "assistant", "content": "[Protocol fixture] Waiting for owner review."}
    if "[POC:slow]" in prompt:
        return {"role": "assistant", "content": "[Protocol fixture] Slow response finished."}
    if "[POC:manager]" in prompt or "[POC:research]" in prompt:
        if "hermbot_teammates" not in names:
            return tool("hermbot_teammates", {})
        delegated = [c for c in calls if c["function"]["name"] == "hermbot_delegate"]
        discovery = next((json.loads(m["content"]) for m in results if "teammates" in str(m.get("content"))), {})
        peers = [p["id"] for p in discovery.get("teammates", [])]
        first_peer = "researcher" if "researcher" in peers else "reviewer"
        second_peer = next((p for p in peers if p != first_peer), "writer")
        if not delegated:
            manager = "[POC:manager]" in prompt
            return tool("hermbot_delegate", {"bot_id": first_peer if manager else "writer", "prompt": "[POC:evidence] Produce a fixture evidence artifact." if manager else "[POC:write] Write a fixture report."})
        if "[POC:manager]" in prompt and len(delegated) == 1:
            receipt = next(json.loads(m["content"]) for m in results if m.get("tool_call_id") == delegated[0]["id"])
            return tool("hermbot_delegate", {"bot_id": second_peer, "prompt": "[POC:write] Write from your granted dependency artifact.", "depends_on": [receipt["task_id"]]})
        return {"role": "assistant", "content": "[Protocol fixture] Assignments accepted; yielding to the worker."}
    if "hermbot_finish" in names:
        return {"role": "assistant", "content": "[Protocol fixture] Complete."}
    if "[POC:write]" in prompt or "[POC:evidence]" in prompt:
        if "hermbot_publish" not in names:
            return tool("hermbot_publish", {"name": "fixture-report.md", "content": "# Protocol fixture report\n\nThis deterministic artifact verifies transport and persistence, not model quality.", "content_type": "text/markdown"})
        asset = next(json.loads(m["content"]) for m in results if "artifact_id" in str(m.get("content")))
        return tool("hermbot_finish", {"result": "[Protocol fixture] Artifact " + str(asset.get("artifact_id", asset.get("id"))) + " is ready."})
    return tool("hermbot_finish", {"result": "[Protocol fixture] Completed and combined the supplied task updates. " + prompt[-1800:]})


@app.post("/v1/chat/completions")
async def complete(request: Request):
    body = await request.json()
    if "[POC:fail-on-researcher]" in str(body.get("messages")) and "You are Scout" in str(body.get("messages", [{}])[0].get("content", "")):
        return JSONResponse({"error": {"message": "Injected fixture provider rejection", "type": "invalid_request_error", "code": "fixture_injected_failure"}}, status_code=401)
    if "[POC:slow]" in str(body.get("messages")):
        await asyncio.sleep(15)
    visible = {t.get('function', {}).get('name') for t in body.get('tools', [])}
    if visible == {'hermbot_route_message'}:
        message = {"role": "assistant", "content": None, "tool_calls": [{"id": "call_" + uuid4().hex, "type": "function", "function": {"name": "hermbot_route_message", "arguments": json.dumps({'intent': 'assignment'})}}]}
    elif 'hermbot_prepare' in visible and 'hermbot_publish' not in visible:
        message = {"role": "assistant", "content": None, "tool_calls": [{"id": "call_" + uuid4().hex, "type": "function", "function": {"name": "hermbot_prepare", "arguments": json.dumps({'skills': [], 'tools': []})}}]}
    else:
        message = choose(body.get("messages", []))
    ident = "chatcmpl_" + uuid4().hex
    reason = "tool_calls" if message.get("tool_calls") else "stop"
    def envelope(choice):
        return {"id": ident, "object": "chat.completion.chunk" if body.get("stream") else "chat.completion", "created": int(time.time()), "model": "hermbot-protocol-fixture", "choices": [choice]}
    if body.get("stream"):
        async def chunks():
            delta = {"role": "assistant"}
            if message.get("tool_calls"):
                delta["tool_calls"] = [{"index": i, **call} for i, call in enumerate(message["tool_calls"])]
            else:
                delta["content"] = message["content"]
            yield "data: " + json.dumps(envelope({"index": 0, "delta": delta, "finish_reason": None})) + "\n\n"
            yield "data: " + json.dumps(envelope({"index": 0, "delta": {}, "finish_reason": reason})) + "\n\n"
            yield "data: [DONE]\n\n"
        return StreamingResponse(chunks(), media_type="text/event-stream")
    result = envelope({"index": 0, "message": message, "finish_reason": reason})
    result["usage"] = {"prompt_tokens": 100, "completion_tokens": 30, "total_tokens": 130}
    return result


@app.get("/v1/models")
def models():
    return {"object": "list", "data": [{"id": "hermbot-protocol-fixture", "object": "model", "owned_by": "test-fixture"}]}
