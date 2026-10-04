"""Providers for the experiments: read from the user's file at run time, never printed.

Primary first (cheaper, less stable), the second on any failure. Each call returns the
parsed JSON plus which provider answered and the wall time.
"""
from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path

import httpx

# A local text file with the providers (name, URL, key, model); never commit it.
SRC = Path(os.environ.get("ALTAIR_LAB_PROVIDERS", "providers.txt"))


def providers() -> list[dict]:
    text = SRC.read_text(encoding="utf-8", errors="replace")
    blocks = [b for b in re.split(r"\n\s*\n", text) if b.strip()]
    out = []
    for block in blocks:
        flat = " ".join(block.split())
        url = re.search(r"https?://[^\s,]+", flat).group(0).rstrip("/")
        after_url = flat[flat.index(url) + len(url):]
        parts = [p.strip() for p in after_url.split(",")]
        key = parts[1].split(" - ", 1)[1].strip()
        model = parts[2].split(" - ", 1)[1].strip()
        name = flat.split(",")[0].split(" - ", 1)[1].strip()
        out.append({"name": name, "base_url": url, "key": key, "model": model})
    return out


PROVIDERS = providers()


def tail(key: str) -> str:
    return "…" + key[-4:]


def chat(messages, *, tools=None, max_tokens=None, extra=None, timeout=180.0, only=None, retries=1) -> dict:
    """One chat completion with fallback. Returns {"json", "provider", "seconds"}."""
    errors = []
    order = PROVIDERS if only is None else [PROVIDERS[only]]
    for prov in order:
        for attempt in range(retries + 1):
            body = {"model": prov["model"], "messages": messages}
            if tools:
                body["tools"] = tools
            if max_tokens:
                body["max_tokens"] = max_tokens
            if extra:
                body.update(extra)
            started = time.perf_counter()
            try:
                r = httpx.post(f"{prov['base_url']}/chat/completions", json=body, timeout=timeout,
                               headers={"Authorization": f"Bearer {prov['key']}"})
                data = r.json() if r.content else {}
                if r.status_code != 200 or "choices" not in data:
                    raise RuntimeError(f"HTTP {r.status_code}: {str(data)[:200]}")
                return {"json": data, "provider": prov["name"], "seconds": time.perf_counter() - started}
            except Exception as exc:  # noqa: BLE001 - recorded and the next one tried
                errors.append(f"{prov['name']}#{attempt}: {str(exc)[:160]}")
                time.sleep(1.5)
    raise RuntimeError("all providers failed: " + " | ".join(errors))


def usage(res: dict) -> dict:
    u = res["json"].get("usage") or {}
    det = u.get("prompt_tokens_details") or {}
    cdet = u.get("completion_tokens_details") or {}
    return {
        "in": u.get("prompt_tokens", 0), "out": u.get("completion_tokens", 0),
        "cached": det.get("cached_tokens") or u.get("prompt_cache_hit_tokens") or 0,
        "reasoning": cdet.get("reasoning_tokens") or 0, "cost": u.get("cost"),
    }


def text_of(res: dict) -> str:
    return res["json"]["choices"][0]["message"].get("content") or ""


if __name__ == "__main__":
    for i, p in enumerate(PROVIDERS):
        print(p["name"], p["base_url"], tail(p["key"]), p["model"])
        try:
            res = chat([{"role": "user", "content": "Reply with the single word: pong"}], only=i, max_tokens=400)
            msg = res["json"]["choices"][0]["message"]
            print("  ok", round(res["seconds"], 1), "s", repr(text_of(res)[:40]), usage(res),
                  "reasoning field:", bool(msg.get("reasoning_content") or msg.get("reasoning")))
            print("  usage raw keys:", json.dumps(res["json"].get("usage"))[:300])
        except Exception as exc:  # noqa: BLE001
            print("  FAIL", exc)
