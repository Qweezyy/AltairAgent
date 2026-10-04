"""A logging proxy between Altair and the providers (OpenAI-compatible /chat/completions).

* Primary provider first; on a connection error or a non-200 answer before any byte was
  streamed, the second one. The model name is rewritten per provider.
* Every request is logged to requests.jsonl: shape of the request (messages, tools, sizes,
  hash of the tool list) and what the provider reported (prompt, cached, completion,
  reasoning tokens), latency. Optional: PROXY_EXTRA (JSON) is merged into every body.
Run: python proxy.py PORT LOGFILE
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
import time

import httpx
import uvicorn
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, Response, StreamingResponse
from starlette.routing import Route

from prov import PROVIDERS as _ALL
import transforms

ORDER = [int(x) for x in (os.environ.get("PROXY_ORDER") or "0,1").split(",")]
PROVIDERS = [_ALL[i] for i in ORDER]
TRANSFORMS = [t for t in (os.environ.get("PROXY_TRANSFORM") or "").split(",") if t]

LOG = sys.argv[2] if len(sys.argv) > 2 else "requests.jsonl"
EXTRA = json.loads(os.environ.get("PROXY_EXTRA") or "{}")
EXTRA_BY_PROV = json.loads(os.environ.get("PROXY_EXTRA_BY_PROV") or "{}")
client = httpx.AsyncClient(timeout=httpx.Timeout(300.0, connect=20.0))


def shape(body: dict) -> dict:
    msgs = body.get("messages") or []
    tools = body.get("tools") or []
    names = [t.get("function", {}).get("name") for t in tools]
    sys_chars = sum(len(json.dumps(m.get("content"), ensure_ascii=False)) for m in msgs if m.get("role") == "system")
    return {
        "n_msgs": len(msgs), "n_tools": len(tools),
        "tools_hash": hashlib.sha1(json.dumps(names).encode()).hexdigest()[:8],
        "tool_names": names,
        "sys_chars": sys_chars,
        "chars": len(json.dumps(msgs, ensure_ascii=False)),
        "tools_chars": len(json.dumps(tools, ensure_ascii=False)),
        "max_tokens": body.get("max_tokens") or body.get("max_completion_tokens"),
        "stream": bool(body.get("stream")),
        "mid_system": sum(1 for m in msgs[1:] if m.get("role") == "system"),
        "first_user": next((str(m.get("content"))[:60] for m in msgs if m.get("role") == "user"), ""),
    }


def write(rec: dict) -> None:
    with open(LOG, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(rec, ensure_ascii=False) + "\n")


def parse_usage(u: dict | None) -> dict:
    u = u or {}
    return {
        "in": u.get("prompt_tokens", 0), "out": u.get("completion_tokens", 0),
        "cached": (u.get("prompt_tokens_details") or {}).get("cached_tokens") or 0,
        "reasoning": (u.get("completion_tokens_details") or {}).get("reasoning_tokens") or 0,
        "cost": u.get("cost"),
    }


async def completions(request: Request) -> Response:
    body = await request.json()
    transforms.apply(body, TRANSFORMS)
    rec = {"t": time.time(), **shape(body)}
    errors = []
    for i, prov in enumerate(PROVIDERS):
        b = {**EXTRA, **EXTRA_BY_PROV.get(str(ORDER[i]), {}), **body, "model": prov["model"]}
        if b.get("stream"):
            b.setdefault("stream_options", {"include_usage": True})
        started = time.perf_counter()
        try:
            req = client.build_request("POST", f"{prov['base_url']}/chat/completions", json=b,
                                       headers={"Authorization": f"Bearer {prov['key']}"})
            resp = await client.send(req, stream=True)
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{prov['name']}: {exc!r}"[:200])
            continue
        if resp.status_code != 200:
            text = (await resp.aread()).decode("utf-8", "replace")[:300]
            await resp.aclose()
            errors.append(f"{prov['name']}: HTTP {resp.status_code} {text}")
            continue
        rec["provider"] = prov["name"]
        if not b.get("stream"):
            data = json.loads(await resp.aread())
            await resp.aclose()
            rec.update(parse_usage(data.get("usage")), seconds=time.perf_counter() - started, errors=errors)
            write(rec)
            return JSONResponse(data)

        async def relay(resp=resp, started=started):
            usage = None
            first = None
            buf = b""
            try:
                async for chunk in resp.aiter_bytes():
                    if first is None:
                        first = time.perf_counter() - started
                    buf += chunk
                    while b"\n" in buf:
                        line, buf = buf.split(b"\n", 1)
                        if line.startswith(b"data: ") and b'"usage"' in line:
                            try:
                                usage = json.loads(line[6:]).get("usage") or usage
                            except ValueError:
                                pass
                    yield chunk
            finally:
                await resp.aclose()
                rec.update(parse_usage(usage), seconds=time.perf_counter() - started, ttfb=first, errors=errors)
                write(rec)

        return StreamingResponse(relay(), media_type="text/event-stream")
    rec.update(failed=True, errors=errors)
    write(rec)
    return JSONResponse({"error": {"message": "all providers failed: " + " | ".join(errors)}}, status_code=502)


async def models(_: Request) -> Response:
    return JSONResponse({"data": [{"id": PROVIDERS[0]["model"]}]})


app = Starlette(routes=[Route("/v1/chat/completions", completions, methods=["POST"]),
                        Route("/v1/models", models)])

if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=int(sys.argv[1]), log_level="warning")
