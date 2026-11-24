import base64
import io
import wave
from types import SimpleNamespace

import pytest

from gemini_tts import GeminiVoice
from scene_narration import render_post_speech


def wav_bytes():
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as wav:
        wav.setparams((1, 2, 24000, 0, "NONE", ""))
        wav.writeframes(bytes(48000))
    return buffer.getvalue()


@pytest.mark.parametrize("project", [None, "credits-project"])
def test_response_audio_and_selected_text_are_cached(tmp_path, project):
    voice = GeminiVoice.__new__(GeminiVoice)
    voice.project, voice.model, voice.voice, voice.style = project, "gemini-3.8-flash-tts", "Algenib", "低い声"
    requests = []
    encoded = base64.b64encode(wav_bytes()).decode()

    def post(url, json, **kwargs):
        requests.append(json)
        expected_style = "低い声" if len(requests) == 1 else "低い声\n同じ人物・声色のまま、この一言の口調：声をひそめて"
        if project:
            assert "projects/credits-project/" in url
            assert json["generationConfig"]["speechConfig"]["voiceConfig"]["voice"] == "Algenib"
            assert json["contents"][0]["parts"][0] == {"text": "剣が出た。", "speechMetadata": {"style": expected_style}}
            data = {"candidates": [{"content": {"parts": [{"inlineData": {"data": encoded}}]}}]}
        else:
            assert json["generation_config"]["speech_config"] == [{"voice": "Algenib"}]
            assert json["input"][0]["content"][0]["text"] == "剣が出た。"
            assert json["input"][0]["content"][0]["annotations"][0]["style"] == expected_style
            data = {"steps": [{"type": "model_output", "content": [{"type": "audio", "data": encoded}]}]}
        return SimpleNamespace(raise_for_status=lambda: None, json=lambda: data)

    voice.session = SimpleNamespace(post=post)
    rows = [dict(at=0, thinking_at=0, decided_at=5, original_text="剣が出た。", text="", speech_pending=True),
            dict(at=6, thinking_at=6, decided_at=6.1, original_text="遅すぎる。", text="", speech_pending=True),
            dict(at=10, thinking_at=9, decided_at=14, speech_finished_at=11,
                 original_text="驚いた。剣が出た。", text="剣が出た。", spoken_file="spoken/old.wav", delivery="声をひそめて")]
    for _ in range(2):
        result = render_post_speech(rows, tmp_path, voice, {"voice": "Algenib"}, lambda _: {})
        assert [r["text"] for r in result] == ["剣が出た。", "", "剣が出た。"]
        assert result[0]["speech_finished_at"] < 5 and result[2]["spoken_file"] != "spoken/old.wav"
        assert result[0]["spoken_file"] != result[2]["spoken_file"]
    assert len(requests) == 2
    assert rows[0]["text"] == ""
