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


class OpenCodeProvider:
    def __init__(self, bin_path="opencode", default_model="opencode-default"):
        self.bin_path, self.default_model = bin_path, default_model

    async def chat(self, messages, tools=None, model=None, on_progress=None, **k):
        parts, files = [], []
        for m in messages:
            r, c = m.get("role", "user"), m.get("content") or ""
            if r == "system":
                continue
            if isinstance(c, list):
                tx = "".join([i.get("text", "") for i in c if i.get("type") == "text"])
                for i in c:
                    if i.get("type") == "image_url":
                        files.append(i["image_url"]["url"])
                c = tx
            parts.append(f"<{r.upper()}>\n{c}\n</{r.upper()}>")

        sys = next((m.get("content") for m in messages if m.get("role") == "system"), "")
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
        p_text += "\n\n<SYSTEM>\nAlways end with summary.</SYSTEM>"

        cmd = [self.bin_path, "run", "--message", p_text, "--format", "json"]
        for f in files:
            cmd.extend(["-f", f])

        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
            )
            out_txt, t_calls, usage, streamed = [], [], {}, False

            async def read_err():
                async for line in proc.stderr:
                    logger.error(f"🔴 OpenCode: {line.decode().strip()}")

            err_t = asyncio.create_task(read_err())
            async for line in proc.stdout:
                try:
                    ev = json.loads(line.decode().strip())
                    pt = ev.get("part", {})
                    if ev.get("type") == "text":
                        txt = pt.get("text", "")
                        out_txt.append(txt)
                        if on_progress and txt.strip():
                            await on_progress(txt)
                            streamed = True

                        # Hardened Tool Call Parsing
                        curr = "".join(out_txt)
                        for m in re.finditer(r"(\{[\s\S]*?\"nanobot_tool_call\"[\s\S]*?\})", curr):
                            try:
                                raw_json = m.group(1).strip()
                                call_data = json.loads(raw_json)
                                call = call_data.get("nanobot_tool_call")
                                if call and "name" in call:
                                    cid = f"call_{uuid.uuid4().hex[:8]}"
                                    if not any(
                                        c.name == call["name"]
                                        and c.arguments == call.get("arguments")
                                        for c in t_calls
                                    ):
                                        t_calls.append(
                                            ToolCallRequest(
                                                id=cid,
                                                name=call["name"],
                                                arguments=call.get("arguments", {}),
                                            )
                                        )
                            except (json.JSONDecodeError, KeyError):
                                continue
                    elif ev.get("type") == "step_finish":
                        usage = pt.get("tokens", {})
                except Exception:
                    pass
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
