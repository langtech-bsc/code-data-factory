import pytest

from swesmith.reconstruct.agent import (
    AgentError,
    clip,
    parse_json_object,
    run_agent,
)


def _scripted(replies):
    """Return a chat function that yields ``replies`` in order and records the transcript."""
    iterator = iter(replies)
    seen = []

    def chat(messages):
        seen.append([dict(m) for m in messages])
        return next(iterator)

    return chat, seen


def test_parse_json_object_handles_fences_and_prose():
    assert parse_json_object('{"action": "reject"}') == {"action": "reject"}
    fenced = 'Sure.\n```json\n{"action": "submit_tests", "content": "x"}\n```'
    assert parse_json_object(fenced)["action"] == "submit_tests"
    prose = 'I will now act: {"action": "exec", "command": "ls"} done.'
    assert parse_json_object(prose)["command"] == "ls"
    assert parse_json_object("no json here") is None


def test_exec_then_terminal_action_returns_terminal():
    chat, seen = _scripted(
        [
            '{"action": "exec", "command": "ls"}',
            '{"action": "submit", "selected_ids": ["a"]}',
        ]
    )
    outputs = []

    def execute(command):
        outputs.append(command)
        return "file.py"

    action = run_agent(chat, [{"role": "user", "content": "go"}], execute, {"submit"}, 5)
    assert action == {"action": "submit", "selected_ids": ["a"]}
    assert outputs == ["ls"]
    assert "file.py" in seen[1][-1]["content"]


def test_unparseable_and_unknown_actions_are_fed_back_not_executed():
    chat, seen = _scripted(
        [
            "not json",
            '{"action": "delete_repo"}',
            '{"action": "exec", "command": "   "}',
            '{"action": "reject", "reason": "no"}',
        ]
    )
    action = run_agent(
        chat, [{"role": "user", "content": "go"}], lambda c: "", {"reject"}, 10
    )
    assert action["reason"] == "no"
    feedback = [m["content"] for m in seen[-1] if m["role"] == "user"]
    assert any('"action" field' in text for text in feedback)
    assert any("Unknown action 'delete_repo'" in text for text in feedback)
    assert any('non-empty "command"' in text for text in feedback)


def test_turn_budget_exhaustion_raises():
    chat, _ = _scripted(['{"action": "exec", "command": "ls"}'] * 3)
    with pytest.raises(AgentError):
        run_agent(chat, [{"role": "user", "content": "go"}], lambda c: "", {"submit"}, 3)


def test_clip_keeps_the_tail_of_long_output():
    text = "x" * 100 + "END"
    clipped = clip(text, limit=10)
    assert clipped.endswith("END")
    assert clipped.startswith("...[truncated")
    assert clip("short", limit=10) == "short"
