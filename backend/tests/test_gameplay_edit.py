from pathlib import Path
import json
import sys
import wave

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from gameplay_edit import FPS, HEIGHT, SAMPLE_FPS, VOICE_RATE, WIDTH, kept_ranges, write_voice_track
from edit_recorded_chapter import chapter_timeline
import scene_narration
from scene_narration import write_edited_voice


def test_speech_uses_observation_time_and_never_queues_into_next_scene(tmp_path, monkeypatch):
    folder = tmp_path / "player"
    folder.mkdir()
    events = [{"kind": "start", "step": 0, "at": "1970-01-01T00:00:00+00:00", "t": 0}]
    for step, t, phase, line in [(1, 0, "intro", "こんにちは。"), (2, 10, "play", "左に進みます。"),
                                 (3, 30, "play", "ここで攻撃です。"), (4, 40, "outro", "また遊びましょう。")]:
        filename = f"{step:04d}-0.wav"
        with wave.open(str(folder / filename), "wb") as wav:
            wav.setparams((1, 2, VOICE_RATE, 0, "NONE", ""))
            wav.writeframes(bytes(VOICE_RATE * 4))
        events.extend([dict(kind="capture", step=step, t=t, phase=phase),
                       dict(kind="cli", step=step, t=t+5, events=[dict(kind="say", value=line)]),
                       dict(kind="tts", step=step, t=t+8, text=line, file=filename)])
    events.extend([dict(kind="input", step=2, t=16, seconds=1),
                   dict(kind="game", step=3, t=30.2, message="Monster performing move"),
                   dict(kind="complete", step=4, t=50)])
    (folder / "trace.jsonl").write_text("\n".join(map(json.dumps, events)), encoding="utf8")
    (tmp_path / "recording-start.txt").write_text("0")
    monkeypatch.setattr(scene_narration, "Voicevox", object)
    cues = scene_narration.schedule(tmp_path, "player", tmp_path, {})
    assert [c["step"] for c in cues] == [1, 2, 4]
    assert cues[1]["at"] == pytest.approx(10.65)
    assert cues[1]["at"] + cues[1]["duration"] < 15


def test_scene_mix_preserves_full_phrases_and_audible_gap(tmp_path):
    (tmp_path / "player").mkdir()
    events = [{"kind": "start", "at": "1970-01-01T00:00:00+00:00", "t": 0},
              {"kind": "cli_start", "step": 1, "t": 1}, {"kind": "cli", "step": 1, "t": 25}]
    (tmp_path / "player/trace.jsonl").write_text("\n".join(map(json.dumps, events)), encoding="utf8")
    (tmp_path / "recording-start.txt").write_text("0")
    path = tmp_path / "spoken.wav"
    with wave.open(str(path), "wb") as wav:
        wav.setparams((1, 2, VOICE_RATE, 0, "NONE", ""))
        wav.writeframes(np.ones(VOICE_RATE * 2, dtype="<i2").tobytes())
    cues = [dict(step=i, at=at, duration=2, deadline=at+3, phase="play", text="実況", path=str(path))
            for i, at in enumerate([3, 20])]
    frames = np.zeros((27 * SAMPLE_FPS, HEIGHT, WIDTH), np.uint8)
    keep, cuts, _ = chapter_timeline(tmp_path, "player", frames, 27 * FPS, cues)
    assert cuts
    def edited(t):
        return sum(max(0, min(t*FPS, b) - a) for a, b in keep) / FPS
    write_edited_voice(tmp_path, cues, edited, edited(27))
    audio = np.fromfile(tmp_path / "voice.pcm", "<i2")
    active = np.flatnonzero(audio)
    assert len(active) == 4 * VOICE_RATE
    assert np.diff(active).max() - 1 >= 3 * VOICE_RATE
    with pytest.raises(ValueError, match="boundary"):
        write_edited_voice(tmp_path, [dict(cues[0], deadline=4)], edited, edited(27))


def test_chapter_uses_mixed_speech_time_and_keeps_enemy_impact(tmp_path):
    (tmp_path / "player").mkdir()
    events = [
        {"kind": "start", "at": "1970-01-01T00:00:00+00:00", "t": 0},
        {"kind": "cli_start", "step": 2, "t": 1},
        {"kind": "tts_background", "step": 1, "t": 4, "audio_seconds": 3, "text": "前の発言"},
        {"kind": "game", "t": 20, "message": "Monster performing move"},
        {"kind": "cli", "step": 2, "t": 37},
    ]
    (tmp_path / "player/trace.jsonl").write_text("\n".join(map(json.dumps, events)), encoding="utf-8")
    (tmp_path / "recording-start.txt").write_text("0")
    (tmp_path / "transcript.csv").write_text("step,seconds,text\n1,11,前の発言\n", encoding="utf-8")
    frames = np.zeros((40 * SAMPLE_FPS, HEIGHT, WIDTH), np.uint8)
    keep, cuts, _ = chapter_timeline(tmp_path, "player", frames, 40 * FPS)
    assert cuts
    for start, end in [(0, 2), (11, 14), (19.35, 24), (38, 40)]:
        assert any(left <= start * FPS and right >= end * FPS for left, right in keep)


def decision(at, **fields):
    return {"at": at, "speech_finished_at": at + 1, "last_action": at + 2,
            "planned_actions": [{"command": "click 10 20", "reason": "カードを選ぶ"}],
            "inputs": [], **fields}


def test_cut_after_speech_input_and_visible_result():
    frames = np.zeros((35 * SAMPLE_FPS, HEIGHT, WIDTH), np.uint8)
    # A delayed impact changes just the enemy's health display.
    frames[16 * SAMPLE_FPS:, 27:36, 96:108] = 100
    lines = [decision(1, inputs=[{"at": 6}]), decision(28, thinking_at=12)]
    kept = kept_ranges(lines, frames, 35 * FPS)
    assert kept == [(0, 17 * FPS), (int(27.5 * FPS), 35 * FPS)]


@pytest.mark.parametrize("fields", [
    {"planned_actions": [{"command": "wait 2"}]},
    {"planned_actions": [{"command": "click 1200 500", "reason": "ターンを終える"}]},
    {"next_observation": "敵のターン。まだ着弾前"},
])
def test_enemy_turn_and_explicit_wait_play_in_full(fields):
    frames = np.zeros((30 * SAMPLE_FPS, HEIGHT, WIDTH), np.uint8)
    assert kept_ranges([decision(1, **fields), decision(24)], frames, 30 * FPS) == [(0, 30 * FPS)]


def test_external_gameplay_in_a_gap_is_protected():
    frames = np.zeros((30 * SAMPLE_FPS, HEIGHT, WIDTH), np.uint8)
    assert kept_ranges([decision(1), decision(24)], frames, 30 * FPS,
                       protected=[(12, 14)]) == [
                           (0, 9 * FPS), (12 * FPS, 14 * FPS), (int(22.75 * FPS), 30 * FPS)]


def test_logged_decision_wait_trims_looping_enemy_idle_but_keeps_new_hand():
    frames = np.zeros((30 * SAMPLE_FPS, HEIGHT, WIDTH), np.uint8)
    for i in range(len(frames)):
        x = 80 + i % 20
        frames[i, 32:44, x:x + 10] = 210
    frames[12 * SAMPLE_FPS:, 90:99, 84:96] = 155
    lines = [decision(1, next_observation="[actor: ぼくの第2ターン。]"), decision(25, thinking_at=5)]
    kept = kept_ranges(lines, frames, 30 * FPS)
    assert kept == [(0, 13 * FPS), (int(24.5 * FPS), 30 * FPS)]


def test_unresolved_enemy_attack_survives_logged_thinking():
    frames = np.zeros((30 * SAMPLE_FPS, HEIGHT, WIDTH), np.uint8)
    lines = [decision(1, next_observation="敵のターン。まだ着弾前"), decision(25, thinking_at=5)]
    assert kept_ranges(lines, frames, 30 * FPS) == [(0, 30 * FPS)]


def test_narration_moves_with_its_game_footage(tmp_path):
    frames = np.full(VOICE_RATE, 901, np.int16).tobytes()
    with wave.open(str(tmp_path / "voice.wav"), "wb") as voice:
        voice.setparams((1, 2, VOICE_RATE, 0, "NONE", ""))
        voice.writeframes(frames)
    row = decision(8, text="この一枚で決めるのだ", spoken_file="voice.wav")
    ranges = [(0, FPS), (7 * FPS, 10 * FPS)]
    write_voice_track(tmp_path, tmp_path, [row], ranges, "", "")
    audio = (tmp_path / "voice.pcm").read_bytes()
    assert audio == bytes(2 * VOICE_RATE * 2) + frames + bytes(VOICE_RATE * 2)
    assert "0:00:02.00,0:00:03.15" in (tmp_path / "voice.ass").read_text(encoding="utf-8-sig")
