"""Turn-capped JSON action loop over the single-shot PTIE ``complete()`` client.

The model answers each turn with one JSON object. ``exec`` runs a shell command
through the caller's executor and feeds the output back. Any action listed in
``terminal`` ends the loop and is returned to the caller.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Iterable

Message = dict[str, str]
ChatFn = Callable[[list[Message]], str]
ExecFn = Callable[[str], str]

OUTPUT_LIMIT = 4000
_FENCE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL)


class AgentError(RuntimeError):
    """Raised when the model does not reach a terminal action within its turn budget."""


def parse_json_object(text: str) -> dict | None:
    """Return the first JSON object found in ``text``, or None."""
    candidates = [text.strip(), *_FENCE.findall(text)]
    start, end = text.find("{"), text.rfind("}")
    if 0 <= start < end:
        candidates.append(text[start : end + 1])
    for candidate in candidates:
        try:
            value = json.loads(candidate)
        except (json.JSONDecodeError, TypeError):
            continue
        if isinstance(value, dict):
            return value
    return None


def clip(text: str, limit: int = OUTPUT_LIMIT) -> str:
    if len(text) <= limit:
        return text
    return f"...[truncated {len(text) - limit} chars]\n" + text[-limit:]


def run_agent(
    chat: ChatFn,
    messages: list[Message],
    execute: ExecFn,
    terminal: Iterable[str],
    max_turns: int,
) -> dict:
    """Drive the loop until a terminal action arrives. ``messages`` is extended in place."""
    terminal_set = set(terminal)
    allowed = ", ".join(sorted({"exec", *terminal_set}))
    for _ in range(max_turns):
        reply = chat(messages)
        messages.append({"role": "assistant", "content": reply})
        action = parse_json_object(reply)
        if action is None or not isinstance(action.get("action"), str):
            messages.append(
                {
                    "role": "user",
                    "content": 'Reply with exactly one JSON object with an "action" field.',
                }
            )
            continue
        name = action["action"]
        if name == "exec":
            command = action.get("command")
            if not isinstance(command, str) or not command.strip():
                messages.append(
                    {"role": "user", "content": '"exec" needs a non-empty "command" string.'}
                )
                continue
            output = execute(command)
            messages.append({"role": "user", "content": f"Command output:\n{clip(output)}"})
            continue
        if name in terminal_set:
            return action
        messages.append(
            {"role": "user", "content": f"Unknown action {name!r}. Allowed: {allowed}."}
        )
    raise AgentError(f"no terminal action within {max_turns} turns")
