import base64
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import llm


@pytest.mark.parametrize("json_reply", [True, False])
def test_codex_image_and_reply_contract(monkeypatch, json_reply):
    monkeypatch.setattr(llm, "find_cli_binary", lambda name: "codex.exe")
    integration = llm.LLMIntegration("codex_cli")
    integration._extra_image = base64.b64encode(b"later").decode()
    prompt = "画面を確認して一手を選ぶ"
    reply = '{"narration":"開けよう","actions":[]}' if json_reply else "実況レポート"
    paths = []

    def run(cmd, **kwargs):
        assert kwargs["input"] == prompt and cmd[-1] == "-"
        assert "forced_login_method=chatgpt" in cmd
        assert ("--model" in cmd) == (not json_reply)
        paths.extend(Path(cmd[i + 1]) for i, arg in enumerate(cmd) if arg == "--image")
        assert [path.read_bytes() for path in paths] == [b"first", b"later"]
        Path(cmd[cmd.index("--output-last-message") + 1]).write_text(reply, encoding="utf-8")
        return llm.subprocess.CompletedProcess(cmd, 0, stdout="diagnostic text")

    monkeypatch.setattr(llm.subprocess, "run", run)
    result = integration._call(prompt, base64.b64encode(b"first").decode(),
                               "default" if json_reply else "custom-model", expect_json=json_reply)
    assert result == ({"narration": "開けよう", "actions": []} if json_reply else reply)
    assert all(not path.exists() for path in paths)
