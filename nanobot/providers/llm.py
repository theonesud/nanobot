import asyncio
import json
import re
import uuid
from dataclasses import dataclass, field
from typing import Any

from loguru import logger


@dataclass
class ToolCallRequest:
    id: str
    name: str
    arguments: dict[str, Any]


@dataclass
class LLMResponse:
    content: str
    tool_calls: list[ToolCallRequest] = field(default_factory=list)
    usage: dict[str, int] = field(default_factory=dict)
    streamed: bool = False
    error: str | None = None

    @property
    def has_tool_calls(self):
        return bool(self.tool_calls)


_TOOL_CALL_RE = re.compile(r'(\{[\s\S]*?"nanobot_tool_call"[\s\S]*?\})')
_LLM_TIMEOUT = 300


class OpenCodeProvider:
    def __init__(self, bin_path="opencode", default_model="opencode-default", cwd=None):
        self.bin_path, self.default_model, self.cwd = bin_path, default_model, cwd

    async def chat(self, messages, tools=None, model=None, on_progress=None, **k):
        parts, files = [], []
        sys_parts = []
        for m in messages:
            r, c = m.get("role", "user"), m.get("content") or ""
            if r == "system":
                sys_parts.append(c if isinstance(c, str) else str(c))
                continue
            if isinstance(c, list):
                tx = "".join([i.get("text", "") for i in c if i.get("type") == "text"])
                for i in c:
                    if i.get("type") == "image_url":
                        files.append(i["image_url"]["url"])
                c = tx
            parts.append(f"<{r.upper()}>\n{c}\n</{r.upper()}>")

        sys = "\n\n".join(sys_parts)
        if tools:
            sys += (
                '\n\n## Tools\nRespond with `{ "nanobot_tool_call": { "name": "...", "arguments": {...} } }`.\n'
                + "\n".join(
                    [f"- {t['function']['name']}: {t['function']['description']}" for t in tools]
                )
            )

        p_text = "\n\n".join(parts)
        if sys:
            p_text = f"<SYSTEM>\n{sys.strip()}\n</SYSTEM>\n\n{p_text}"

        cmd = [self.bin_path, "run", "--message", p_text, "--format", "json"]
        for f in files:
            cmd.extend(["-f", f])

        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
                cwd=self.cwd,
            )
            out_txt, t_calls, usage, streamed = [], [], {}, False
            seen_calls: set[tuple[str, str]] = set()
            scan_offset = 0

            async def read_err():
                async for line in proc.stderr:
                    logger.error(f"🔴 OpenCode: {line.decode(errors='replace').strip()}")

            err_t = asyncio.create_task(read_err())

            async def _read_output():
                nonlocal scan_offset, streamed
                async for line in proc.stdout:
                    try:
                        ev = json.loads(line.decode(errors="replace").strip())
                        pt = ev.get("part", {})
                        if ev.get("type") == "text":
                            txt = pt.get("text", "")
                            out_txt.append(txt)
                            if on_progress and txt.strip():
                                await on_progress(txt)
                                streamed = True

                            curr = "".join(out_txt)
                            for m in _TOOL_CALL_RE.finditer(curr, scan_offset):
                                try:
                                    call_data = json.loads(m.group(1).strip())
                                    call = call_data.get("nanobot_tool_call")
                                    if call and "name" in call:
                                        dedup_key = (
                                            call["name"],
                                            json.dumps(call.get("arguments"), sort_keys=True),
                                        )
                                        if dedup_key not in seen_calls:
                                            seen_calls.add(dedup_key)
                                            t_calls.append(
                                                ToolCallRequest(
                                                    id=f"call_{uuid.uuid4().hex[:8]}",
                                                    name=call["name"],
                                                    arguments=call.get("arguments", {}),
                                                )
                                            )
                                except (json.JSONDecodeError, KeyError):
                                    continue
                            scan_offset = max(scan_offset, len(curr) - 200)
                        elif ev.get("type") == "step_finish":
                            usage.update(pt.get("tokens", {}))
                    except json.JSONDecodeError:
                        continue

            try:
                await asyncio.wait_for(_read_output(), timeout=_LLM_TIMEOUT)
            except asyncio.TimeoutError:
                proc.kill()
                await proc.wait()
                return LLMResponse(content="", error=f"OpenCode timed out after {_LLM_TIMEOUT}s")

            await proc.wait()
            await err_t
            if proc.returncode != 0:
                return LLMResponse(content="", error=f"OpenCode exit code {proc.returncode}")
            u = {
                "prompt_tokens": usage.get("prompt", 0),
                "completion_tokens": usage.get("completion", 0),
                "total_tokens": usage.get("total", 0),
            }
            return LLMResponse(
                content="".join(out_txt), tool_calls=t_calls, usage=u, streamed=streamed
            )
        except Exception as e:
            return LLMResponse(content="", error=str(e))

    def get_default_model(self):
        return self.default_model
