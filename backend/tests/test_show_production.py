import json
from pathlib import Path
import sys
import types
import wave
from contextlib import contextmanager

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import produce
from show_adapters import ContinuousEdit


def row(at, text):
    return {"at": at, "speech_finished_at": at+1, "last_action": at+2, "spoken_file": "spoken/a.wav",
            "inputs": [{"at": at+2, "command": "click 8 9"}], "text": text,
            "next_observation": "visible at the following decision"}


def test_aside_only_receives_information_available_at_that_time():
    rows = [row(1, "first"), row(14, "second"), row(29, "ending")]
    start, end = ContinuousEdit().aside_window(rows)
    assert start < end <= rows[-1]["at"]
    past = produce.observed(rows, start, 9)
    assert past[-1]["observed_after"] == ""
    assert "ending" not in str(past)
    assert produce.observed(rows, float("inf"), 54)[-1]["speech"] == "ending"
    assert ContinuousEdit().aside_window([row(1, "first"), row(4, "next")]) is None


def test_aside_remains_on_gameplay_clock():
    mapping = {"fps": 24, "kept_ranges": [[0, 48], [96, 192]]}
    assert produce.map_aside(4.5, 2, mapping) == 2.5
    with pytest.raises(ValueError, match="cut through"):
        produce.map_aside(1.9, .3, mapping)


def test_live_job_reuses_capture_after_post_failure(tmp_path, monkeypatch):
    plugin = types.ModuleType("episode_test_plugin")
    captures = []
    class Voice:
        credit = "test character"
        def write(self, text, path):
            with wave.open(str(path), "wb") as wav:
                wav.setparams((1, 2, 24000, 0, "NONE", ""))
                wav.writeframes(bytes(480))
    class Recorder:
        def capture(self, episode, folder, player, voice, minutes):
            captures.append(episode.persona)
            (folder/"game.mkv").touch()
            (folder/"spoken").mkdir()
            voice.write("first", folder/"spoken/a.wav")
            produce.save_json(folder/"narration.json", [row(1, "first")])
            return "test completed"
    plugin.Recorder, plugin.Voice = Recorder, Voice
    monkeypatch.setitem(sys.modules, plugin.__name__, plugin)
    source = tmp_path / "raw"
    config = {"studio": {"recorder": {"factory": "episode_test_plugin:Recorder"},
                         "player": {"factory": "show_adapters:FirstPlay"},
                         "player_voice": {"factory": "episode_test_plugin:Voice"}},
              "persona": "探検家", "game": produce.asdict(produce.resolve_game("sts2", False)),
              "source": str(source), "minutes": 0, "live": True}
    def broken_post(*args):
        raise RuntimeError("export error")
    monkeypatch.setattr(produce, "post", broken_post)
    with pytest.raises(RuntimeError, match="export error"):
        produce.produce(tmp_path, config)
    assert produce.read_json(tmp_path/"capture.json")["credit"] == "test character"
    seen = []
    monkeypatch.setattr(produce, "post", lambda *args: seen.append(args[3:]))
    produce.produce(tmp_path, config, resuming=True)
    assert captures == ["探検家"]
    assert seen == [(source, "test character", "test completed")]


def test_unfinished_live_capture_is_not_replayed(tmp_path):
    config = {"studio": {}, "persona": "観察者", "game": produce.asdict(produce.resolve_game("sts2", False)),
              "source": str(tmp_path/"raw"), "live": True}
    with pytest.raises(RuntimeError, match="not finalized"):
        produce.produce(tmp_path, config, resuming=True)


def test_recording_uses_isolated_voice_and_preserves_its_settings(tmp_path, monkeypatch):
    import zundamon
    events = []

    @contextmanager
    def worker(parallel, **options):
        events.append((parallel, options))
        try:
            yield types.SimpleNamespace(credit="test worker")
        finally:
            events.append("closed")

    class Recorder:
        def capture(self, episode, folder, player, voice, minutes):
            assert voice.credit == "test worker"
            raise RuntimeError("capture failed")

    monkeypatch.setattr(zundamon, "recording_voice", worker)
    monkeypatch.setattr(produce, "adapter", lambda spec: Recorder())
    options = {"character": "四国めたん", "speed": 1.02}
    studio = {"recorder": {}, "player": {}, "player_voice": {
        "factory": "zundamon:Voicevox", "options": options}}
    previous = Path.cwd()
    with pytest.raises(RuntimeError, match="capture failed"):
        produce.record_source(tmp_path, None, studio, tmp_path / "raw", 0)
    assert events == [(True, options), "closed"]
    assert Path.cwd() == previous
