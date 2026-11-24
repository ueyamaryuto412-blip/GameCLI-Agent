from pathlib import Path
import sys
import wave

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scene_narration import fit_scene_line
from show_adapters import SceneEdit
from sts2_chapter_player import chapter_result


def test_regular_fights_continue_and_boss_or_defeat_ends_the_episode():
    assert not chapter_result("Creating NCombatRoom encounter=VANTOM_BOSS.")
    assert not chapter_result("CHARACTER.REGENT fought ENCOUNTER.BOWLBUGS_WEAK for the first time and WON >:)")
    for report in ("has won against encounter ENCOUNTER.VANTOM_BOSS. That's 2 wins",
                   "fought ENCOUNTER.VANTOM_BOSS for the first time and WON >:)"):
        assert "ボス戦に勝利" in chapter_result("CHARACTER.REGENT " + report)
    assert "敗北" in chapter_result("CHARACTER.REGENT has died to a MONSTER.VANTOM.")


def test_fit_keeps_only_the_spoken_sentence_that_fits_before_input(tmp_path):
    def speak(text, path):
        duration = 3 if text == "王です。" else 7
        with wave.open(str(path), "wb") as wav:
            wav.setparams((1, 2, 24000, 0, "NONE", ""))
            wav.writeframes(bytes(2 * 24000 * duration))
    path = tmp_path / "line.wav"
    text = "王です。こちらが家臣です。"
    line, start, end = fit_scene_line(text, 10, 16, 8, speak, path)
    assert line == "王です。" and start >= 11 and end < 16
    assert end - start == 3
    assert fit_scene_line(text, 13, 16, 14, speak, path) == ("", 16, 16)


def test_observer_has_space_on_both_sides_across_a_silent_decision():
    rows = [
        dict(at=3, speech_finished_at=8, last_action=10, inputs=[{}], text="攻めます。"),
        dict(at=13, speech_finished_at=13, last_action=14, inputs=[{}], text=""),
        dict(at=28, speech_finished_at=30, last_action=32, inputs=[{}], text="間に合いました。"),
    ]
    start, end = SceneEdit().aside_window(rows)
    assert start >= 11.5 and end <= 25 and end - start >= 9
