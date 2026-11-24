from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import produce
import run_episode
from episode_preparation import NotPlayable


def report():
    games = []
    picks = []
    for name in ("uninstalled", "playable"):
        games.append({"id": name, "title": name, "description": "test game", "profile": None,
                      "audience": {"status": "unavailable", "videos": [], "search_url": "test"},
                      "store_url": "test"})
        picks.append({"name": name, "game_id": name, "persona": "慎重な挑戦者", "format": "シリーズ",
                      "risk": "時間", "fit": "初見", "decision": "試すか迷う", "unit": "一章の終了画面まで"})
    return {"schema": 1, "checked_at": "today", "brief": "test", "picks": picks, "games": games, "notes": []}


def test_skips_only_unplayable_then_hands_off_exact_selected_episode(tmp_path, monkeypatch):
    class Inspector:
        def prepare(self, episode, candidate, folder):
            if candidate["id"] == "uninstalled":
                raise NotPlayable("未インストール")
            return episode.game, {"evidence": "menu highlight moved"}

    monkeypatch.setattr(run_episode, "adapter", lambda _: Inspector())
    recorded, produced = [], []
    monkeypatch.setattr(run_episode, "record_source", lambda job, ep, *rest: recorded.append(ep))
    monkeypatch.setattr(run_episode, "produce", lambda job, config, **kw: produced.append(config))
    run_episode.run_episode(tmp_path, report(), {}, {"factory": "test"})
    assert [a["state"] for a in produce.read_json(tmp_path / "selection.json")] == ["unavailable", "selected"]
    assert len(recorded) == len(produced) == 1
    assert "一章の終了画面" in recorded[0].game.objective
    assert produced[0]["proposal"]["pick"] == 2
    assert produced[0]["persona"] == recorded[0].persona


def test_unconfirmed_end_keeps_source_and_does_not_export(tmp_path, monkeypatch):
    data = report()
    path = tmp_path / "shortlist.json"
    produce.save_json(path, data)
    game, persona, selection = produce.proposal_inputs(path, 2, None, False)
    config = dict(game=produce.asdict(game), persona=persona, studio={}, source=str(tmp_path / "raw"),
                  live=True, preparation={"factory": "test"})
    produce.save_json(tmp_path / "capture.json", {"stop": "STATUS: All goals", "credit": "test"})
    class Inspector:
        def ending(self, *args):
            return {"confirmed": False, "evidence": "メニュー画面", "resume_from": "メニュー"}
    monkeypatch.setattr(produce, "adapter", lambda _: Inspector())
    monkeypatch.setattr(produce, "post", lambda *args: pytest.fail("unconfirmed episode exported"))
    with pytest.raises(RuntimeError, match="終了条件"):
        produce.produce(tmp_path, config, resuming=True)
    assert not produce.read_json(tmp_path / "ending.json")["confirmed"]


def test_selected_person_uses_first_ranked_gemini_voice_for_edit(tmp_path, monkeypatch):
    data = report()
    cast = dict(engine="gemini", source_title="資料", source_url="https://example.org/source", evidence="本人の著作",
                interpretation="声の高さは推定", candidates=[
                    dict(style="Orus", speed=.96, reason="短い句が明瞭"),
                    dict(style="Algenib", speed=1.02, reason="抑揚が広い")])
    data["picks"][1]["voice_cast"] = cast
    class Inspector:
        def prepare(self, episode, candidate, folder):
            if candidate["id"] == "uninstalled":
                raise NotPlayable("未インストール")
            return episode.game, {}
    monkeypatch.setattr(run_episode, "adapter", lambda _: Inspector())
    recorded = []
    monkeypatch.setattr(run_episode, "record_source", lambda job, ep, studio, *_: recorded.append(studio))
    monkeypatch.setattr(run_episode, "produce", lambda *args, **kwargs: None)
    studio = produce.read_json(produce.DEFAULT_STUDIO)
    run_episode.run_episode(tmp_path, data, studio, {"factory": "test"})
    saved = produce.read_json(tmp_path / "production.json")
    assert saved["studio"] == recorded[0]
    assert recorded[0]["player_speech_stage"] == "post"
    assert recorded[0]["player_voice"]["options"]["voice"] == "Orus"
    assert cast["interpretation"] in recorded[0]["player_voice"]["options"]["style"]
    assert saved["proposal"]["voice_cast"] == cast
    assert recorded[0]["host_voice"] == studio["host_voice"]
