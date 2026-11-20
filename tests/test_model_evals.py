import json
from pathlib import Path

import httpx
from openai import AsyncOpenAI
import pytest

from src.minibot.core import client
from src.minibot.skills.loader import SkillLoader
from src.minibot import eval_model


def completion(message, usage=None):
    data = {
        "id": "offline-test-completion",
        "object": "chat.completion",
        "created": 0,
        "model": "stub",
        "choices": [{
            "index": 0,
            "message": {"role": "assistant", **message},
            "finish_reason": "tool_calls" if "tool_calls" in message else "stop",
        }],
    }
    if usage is not None:
        data["usage"] = usage
    return httpx.Response(200, json=data)


def write_files(contents):
    return {
        "tool_calls": [{
            "id": f"write-{index}",
            "type": "function",
            "function": {"name": "write_file", "arguments": json.dumps({"path": path, "content": content})},
        } for index, (path, content) in enumerate(contents.items())],
    }


@pytest.fixture
def stub_provider(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "stub-key-only")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://stub.invalid/v1")

    def install(handler):
        def open_client(**kwargs):
            return AsyncOpenAI(
                **kwargs,
                max_retries=0,
                http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
            )

        monkeypatch.setattr(client, "AsyncOpenAI", open_client)

    return install


def test_model_eval_compares_file_set_and_aggregates_provider_usage(tmp_path, monkeypatch, stub_provider, capsys):
    private_skill = tmp_path / "skills" / "private"
    private_skill.mkdir(parents=True)
    (private_skill / "SKILL.md").write_text(
        "---\nname: local-skill\ndescription: PRIVATE LOCAL SKILL DESCRIPTION\n---\nBody\n", encoding="utf-8",
    )
    monkeypatch.setattr(SkillLoader, "COMMON_SKILL_DIRS", (private_skill.parent,))

    usage = {"prompt_tokens": 14, "completion_tokens": 3, "total_tokens": 17}
    workspaces = set()
    requests = []

    def provider(request):
        payload = json.loads(request.content)
        requests.append(payload["model"])
        system = payload["messages"][0]["content"]
        assert "PRIVATE LOCAL SKILL DESCRIPTION" not in system
        assert {tool["function"]["name"] for tool in payload["tools"]} == eval_model.FILE_TOOLS
        if payload["messages"][-1]["role"] == "tool":
            complete_usage = None if payload["model"] == "wrong" else usage
            return completion({"content": "Done."}, complete_usage)

        workspace = Path(system.split("Working Directory: ", 1)[1].splitlines()[0])
        workspaces.add(workspace)
        prompt = payload["messages"][1]["content"]
        scenario = next(task for task in eval_model.SCENARIOS.values() if task.prompt == prompt)
        for name, contents in scenario.initial_files.items():
            assert (workspace / name).read_text(encoding="utf-8") == contents
        contents = dict(scenario.expected_files)
        if payload["model"] == "wrong":
            contents["unexpected.txt"] = "Extra file despite claiming success."
        return completion(write_files(contents), usage)

    stub_provider(provider)
    assert eval_model.main(["--model", "correct", "--model", "wrong", "--repetitions", "2"]) == 1

    stdout = capsys.readouterr().out
    report = json.loads(stdout)
    assert "stub-key-only" not in stdout
    assert "stub.invalid" not in stdout
    correct, wrong = report["models"]
    assert correct["summary"] == {
        "trials": 4, "passed": 4, "pass_rate": 1.0, "model_invocations": 8,
        "tool_invocations": 6, "input_tokens": 112, "output_tokens": 24,
    }
    assert wrong["summary"]["pass_rate"] == 0.0
    assert wrong["summary"]["input_tokens"] is None
    assert wrong["summary"]["output_tokens"] is None
    wrong_trial = wrong["scenarios"][0]["trials"][0]
    assert wrong_trial["status"] == "output_mismatch"
    assert wrong_trial["input_tokens"] is None
    assert [item["task"] for item in correct["scenarios"]] == list(eval_model.SCENARIOS)
    assert len(requests) == 16
    assert len(workspaces) == 8
    assert all(not workspace.exists() for workspace in workspaces)


def test_model_eval_fails_runtime_even_when_file_matches(stub_provider, capsys):
    def provider(request):
        payload = json.loads(request.content)
        if payload["messages"][-1]["role"] == "tool":
            return httpx.Response(401, json={"error": {"message": "secret provider diagnostic"}})
        scenario = eval_model.SCENARIOS["create-note"]
        return completion(write_files(scenario.expected_files))

    stub_provider(provider)
    assert eval_model.main(["--model", "stub", "--task", "create-note"]) == 1
    stdout = capsys.readouterr().out
    assert "secret provider diagnostic" not in stdout
    model = json.loads(stdout)["models"][0]
    assert len(model["scenarios"]) == 1
    assert model["scenarios"][0]["trials"] == [{
        "repetition": 1, "passed": False, "status": "provider_authentication",
        "model_invocations": 2, "tool_invocations": 1, "input_tokens": None, "output_tokens": None,
    }]


def test_model_eval_requires_explicit_opt_in(monkeypatch, capsys):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with pytest.raises(SystemExit, match="2"):
        eval_model.main(["--model", "stub"])
    assert "set OPENAI_API_KEY" in capsys.readouterr().err
