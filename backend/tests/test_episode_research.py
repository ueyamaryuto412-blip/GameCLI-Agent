import json
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import produce
import research
import research_sources as sources


def test_youtube_sample_keeps_direct_title_matches_and_counts_as_displayed(monkeypatch):
    def video(key, title):
        return {"videoRenderer": {"videoId": key, "title": {"simpleText": title},
                                 "viewCountText": {"simpleText": "1.2万 回視聴"}}}
    a = video("1234567890a", "初見【ＳＴＳ２】")
    items = [video("1234567890b", "別のゲーム"), a, a]
    contents = {"sectionListRenderer": {"contents": [{"itemSectionRenderer": {"contents": items}}]}}
    page = {"contents": {"twoColumnSearchResultsRenderer": {"primaryContents": contents}}}
    response = type("Response", (), {"text": "var ytInitialData = " + json.dumps(page) + ";"})
    monkeypatch.setattr(sources, "fetch", lambda *args, **kwargs: response)
    result = sources.YouTubeExamples().collect({"title": "STS2"})
    assert result["status"] == "sampled"
    assert [v["views_display"] for v in result["videos"]] == ["1.2万 回視聴"]
    response.text = "<html>unavailable</html>"
    assert sources.YouTubeExamples().collect({"title": "STS2"})["status"] == "unavailable"


def test_selection_passes_persona_and_uses_local_recording_profile(tmp_path, monkeypatch):
    pick = dict(game_id="2868840", name="鑑定士", persona="初めて見る品を鑑定したい。",
                fit="試せる", risk="長考", decision="買うか温存するか", format="単発", unit="1ラン")
    report = {"schema": 1, "games": [{"id": "2868840", "title": "別名", "profile": "外部の設定"}],
              "picks": [pick]}
    path = tmp_path / "shortlist.json"
    produce.save_json(path, report)
    captured = []
    monkeypatch.setattr(sys, "argv", ["produce.py", "--proposal", str(path), "--minutes", "1"])
    monkeypatch.setattr(produce, "produce", lambda job, config: captured.append(config))
    monkeypatch.setattr(produce, "ROOT", tmp_path)
    games = tmp_path / "studio/games"
    games.mkdir(parents=True)
    existing = produce.DEFAULT_STUDIO.parent / "games/sts2.json"
    (games / "sts2.json").write_bytes(existing.read_bytes())
    produce.main()
    assert captured[0]["game"]["title"] == "Slay the Spire 2"
    assert captured[0]["persona"] == pick["persona"]
    assert captured[0]["proposal"]["format"] == "単発"
    with pytest.raises(ValueError):
        produce.proposal_inputs(path, 0, None, False)
    with pytest.raises(ValueError):
        research.check_picks([dict(pick, game_id="unobserved")], report["games"], 1)
    with pytest.raises(ValueError):
        research.check_picks([pick, pick], report["games"], 2)
