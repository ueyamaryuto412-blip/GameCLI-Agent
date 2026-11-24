"""Research, try ranked candidates on the local machine, record and export one episode."""
import argparse
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
import sys
import uuid

from episode_preparation import NotPlayable
from produce import (DEFAULT_STUDIO, ROOT, adapter, exclusive, produce, proposal_inputs,
                     read_json, record_source, save_json, validate_studio)
from research import DEFAULT_BRIEF, markdown, research
from show_types import Episode
from voice_casting import studio_for_pick


def run_episode(job, report, studio, preparation, voice_pick=None):
    shortlist = job / "shortlist.json"
    save_json(shortlist, report)
    (job / "shortlist.md").write_text(markdown(report), encoding="utf-8")
    inspector = adapter(preparation)
    games = {c["id"]: c for c in report["games"]}
    attempts = []
    with exclusive(job / ".production.lock"), exclusive(job.parent / ".game-window.lock"):
        for rank, pick in enumerate(report["picks"], 1):
            game, persona, selection = proposal_inputs(shortlist, rank, None, live=False)
            folder = job / f"candidate-{rank}"
            folder.mkdir()
            attempt = {"pick": rank, "title": game.title, "state": "checking"}
            attempts.append(attempt)
            save_json(job / "selection.json", attempts)
            try:
                game, proof = inspector.prepare(Episode(game, persona), games[pick["game_id"]], folder)
            except NotPlayable as exc:
                attempt.update(state="unavailable", reason=str(exc))
                save_json(job / "selection.json", attempts)
                print(f"Skipped {game.title}: {exc}", flush=True)
                continue
            attempt.update(state="selected", proof=proof)
            save_json(job / "selection.json", attempts)
            source = job / "raw"
            if voice_pick is not None:
                if not selection.get("voice_cast"):
                    raise ValueError("選択された企画に声の候補がありません。")
                selection["voice_pick"] = voice_pick
            selected_studio = studio_for_pick(studio, selection)
            config = {"game": asdict(game), "persona": persona, "studio": selected_studio, "source": str(source),
                      "live": True, "minutes": 0, "proposal": selection, "preparation": preparation}
            save_json(job / "production.json", config)
            print(f"Recording {game.title} / {selection['name']}", flush=True)
            record_source(job, Episode(game, persona), selected_studio, source, 0)
            break
        else:
            raise NotPlayable(f"全候補を確認しましたが収録できませんでした。{job / 'selection.json'}")
    produce(job, config, resuming=True)
    return job / "episode.mp4"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--shortlist", type=Path, help="Use a saved shortlist instead of running research")
    parser.add_argument("--voice-pick", type=int, choices=(1, 2, 3), help="Choose a voice from the saved shortlist")
    parser.add_argument("--app-id", type=int, action="append", help="Limit fresh research to these Steam games")
    parser.add_argument("--brief", help="Editorial brief for fresh research")
    parser.add_argument("--count", type=int, choices=range(1, 9), help="Number of proposals")
    parser.add_argument("--settings", type=Path, default=ROOT / "studio/research.json")
    parser.add_argument("--studio", type=Path, default=DEFAULT_STUDIO)
    args = parser.parse_args()
    if args.voice_pick is not None and not args.shortlist:
        parser.error("--voice-pick requires --shortlist")
    if args.shortlist and any(x is not None for x in (args.app_id, args.brief, args.count)):
        parser.error("--shortlist uses previously researched candidates")
    settings, studio = read_json(args.settings), read_json(args.studio)
    validate_studio(studio, live=True)
    report = (read_json(args.shortlist) if args.shortlist else
              research(settings, args.brief or DEFAULT_BRIEF, args.app_id, args.count or 3))
    job = ROOT / "recordings" / ("show-" + datetime.now().strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:8])
    job.mkdir(parents=True)
    print(f"Production folder: {job}", flush=True)
    run_episode(job, report, studio, settings["preparation"], args.voice_pick)


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    main()
