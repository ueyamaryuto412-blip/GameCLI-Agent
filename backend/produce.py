"""Record a game/persona episode or produce the show from a finalized recording."""
import argparse
import base64
from bisect import bisect_right
from contextlib import contextmanager, nullcontext
from dataclasses import asdict, replace
from datetime import datetime
import importlib
import inspect
import json
import math
import os
from pathlib import Path
import re
import sys
import tempfile
import uuid
import wave

import cv2

from show_types import Design, Editor, Episode, Exporter, Game, Host, Player, Recorder, Speech, Voice
from recording_io import save_json
from voice_casting import studio_for_pick, validate_cast

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_STUDIO = ROOT / "studio/default.json"


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


@contextmanager
def exclusive(path):
    """Process-owned lock; a killed or interrupted process cannot leave it held."""
    with path.open("a+b") as stream:
        stream.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise RuntimeError(f"Another production is using {path}") from exc
        yield


def adapter_factory(spec):
    module, name = spec["factory"].split(":", 1)
    return getattr(importlib.import_module(module), name)


def adapter(spec):
    return adapter_factory(spec)(**spec.get("options", {}))


def validate_studio(settings, live):
    stages = ["host", "host_voice", "editor", "design", "exporter"]
    stage = settings.get("player_speech_stage", "capture")
    if stage not in ("post", "capture"):
        raise ValueError("player_speech_stage must be post or capture")
    if stage == "post":
        if not hasattr(adapter_factory(settings["editor"]), "render_post_speech"):
            raise ValueError("Post-production speech needs a compatible editor (e.g. SceneEdit)")
        stages.append("player_voice")
    if live:
        stages.extend(["recorder", "player", "player_voice"])
    for stage in stages:
        try:
            spec = settings[stage]
            inspect.signature(adapter_factory(spec)).bind(**spec.get("options", {}))
        except (KeyError, ValueError, TypeError, AttributeError, ImportError) as exc:
            raise ValueError(f"Invalid studio adapter '{stage}': {exc}") from exc


def speak(voice: Voice, text, work, name):
    """Keep phrase-level subtitle timings from the very WAVs used in the show."""
    phrases = re.findall(r"[^。！？\n]+[。！？]*|[。！？]+", text.strip())
    if not phrases:
        raise ValueError(f"Empty host speech: {name}")
    cues = []
    samples = 0
    target = work / f"{name}.wav"
    with wave.open(str(target), "wb") as output:
        output.setparams((1, 2, 24000, 0, "NONE", ""))
        for i, phrase in enumerate(phrases):
            path = work / f"{name}-{i}.wav"
            voice.write(phrase, path)
            with wave.open(str(path)) as wav:
                if (wav.getnchannels(), wav.getsampwidth(), wav.getframerate()) != (1, 2, 24000):
                    raise ValueError(f"{name}: voice adapter must write mono 24 kHz PCM16 WAV")
                start = samples/24000
                samples += wav.getnframes()
                if samples/24000 == start:
                    raise ValueError(f"No speech synthesized for {name}")
                output.writeframes(wav.readframes(wav.getnframes()))
                cues.append((start, samples/24000, phrase))
    seconds = samples / 24000
    if hasattr(voice, "finish_speech"):
        # Apply room effects after joining phrases so the tail continues across subtitles.
        voice.finish_speech(target)
        with wave.open(str(target), "rb") as rendered:
            seconds = rendered.getnframes() / rendered.getframerate()
    return Speech(target, seconds, cues)


def validate_source(source):
    rows = read_json(source / "narration.json")
    if not (source / "game.mkv").is_file() or not isinstance(rows, list) or not rows:
        raise ValueError("Expected game.mkv and a nonempty recorded narration log")
    required = {"at", "last_action", "speech_finished_at", "spoken_file", "text"}
    last = -1
    for row in rows:
        if not required.issubset(row):
            raise ValueError("This archive predates timestamped live speech")
        if not last <= row["at"] <= row["speech_finished_at"]:
            raise ValueError("Unfinished or unordered narration log")
        last = row["at"]
        if row.get("speech_pending") and (row["text"] or row["spoken_file"] or
                not isinstance(row.get("original_text"), str) or not 0 <= row["thinking_at"] <= row["decided_at"]):
            raise ValueError("Invalid deferred commentary in narration.json")
        if row["text"].strip() and not (source / row["spoken_file"]).is_file():
            raise FileNotFoundError(source / row["spoken_file"])
    return rows


def observed(rows, stop, limit):
    # CHECK for row i arrives on the next decision. Never expose the last row's
    # eventual result while the aside is supposed to be watching that moment.
    past = [r for r in rows if r["speech_finished_at"] <= stop and r["last_action"] <= stop]
    evidence = []
    for i, row in enumerate(past):
        observation = row.get("next_observation", "") if i+1 < len(past) or math.isinf(stop) else ""
        evidence.append({"seconds": round(row["at"], 2), "speech": row["text"],
                         "inputs": [a["command"] for a in row.get("inputs", [])],
                         "observed_after": observation})
    if len(evidence) <= limit:
        return evidence
    # Keep the lead-in to the selected moment; retain representative earlier decisions for the recap.
    if not math.isinf(stop):
        return evidence[-limit:]
    tail = min(8, limit//2)
    head = evidence[:-tail]
    count = limit - tail
    return [head[round(i*(len(head)-1)/(count-1))] for i in range(count)] + evidence[-tail:]


def frame_at(source, at):
    cap = cv2.VideoCapture(str(source / "game.mkv"))
    try:
        cap.set(cv2.CAP_PROP_POS_FRAMES, math.floor(at*24))
        ok, image = cap.read()
    finally:
        cap.release()
    if not ok:
        raise ValueError(f"No recorded frame at {at:.2f}s")
    height, width = image.shape[:2]
    if width > 1280:
        image = cv2.resize(image, (1280, round(height*1280/width)), interpolation=cv2.INTER_AREA)
    ok, data = cv2.imencode(".jpg", image)
    if not ok:
        raise RuntimeError("Could not encode observation frame")
    return base64.b64encode(data).decode("ascii")


def map_aside(start, duration, manifest):
    spans = manifest["kept_ranges"]
    fps = manifest["fps"]
    starts = [a for a, _ in spans]
    i = bisect_right(starts, start*fps) - 1
    if i < 0 or (start+duration)*fps > spans[i][1]+.001:
        raise ValueError("Editor cut through the management AI's reserved interval")
    return (sum(b-a for a,b in spans[:i]) + start*fps - starts[i])/fps


def post(job, episode, settings, source, credit, stop_reason):
    rows = validate_source(source)
    host: Host = adapter(settings["host"])
    voice: Voice = adapter(settings["host_voice"])
    editor: Editor = adapter(settings["editor"])
    design: Design = adapter(settings["design"])
    exporter: Exporter = adapter(settings["exporter"])
    with tempfile.TemporaryDirectory(prefix="mix-", dir=job) as temp:
        work = Path(temp)
        if settings.get("player_speech_stage") == "post":
            player_voice = adapter(settings["player_voice"])
            rows = editor.render_post_speech(rows, job / "player-audio", player_voice, settings["player_voice"])
            credit = player_voice.credit
        elif any(row.get("speech_pending") for row in rows):
            raise ValueError("This recording requires player_speech_stage=post to generate its commentary.")
        elif hasattr(editor, "prepare_speech"):
            rows = editor.prepare_speech(rows, work, adapter(settings["player_voice"]))
        intro_text, traits = host.opening(episode)
        opening = speak(voice, intro_text, work, "opening")
        aside = None
        aside_text = ""
        aside_time = None
        protected = []
        slot = editor.aside_window(rows)
        if slot:
            start, end = slot
            if not 0 <= start < end:
                raise ValueError("Invalid aside window")
            past = observed(rows, start, 9)
            text = host.aside(episode, past, frame_at(source, start))
            candidate = speak(voice, text, work, "aside") if text.strip() else None
            if candidate and candidate.seconds <= end-start and all(
                not (r["text"].strip() and r["at"] < start+candidate.seconds+.15 and
                     r["speech_finished_at"] > start-.15) for r in rows
            ):
                aside, aside_text = candidate, text
                protected = [(max(0, start-.25), start+aside.seconds+.25)]
            else:
                print("Aside omitted: no clean space for the synthesized line.", flush=True)
        closing_text = host.closing(episode, observed(rows, math.inf, 54), stop_reason)
        closing = speak(voice, closing_text, work, "closing")
        body, manifest = editor.edit(source, rows, work, protected)
        if aside:
            aside_time = map_aside(slot[0], aside.seconds, manifest)
        report = {"opening": intro_text, "traits": traits, "aside": aside_text,
                  "aside_source_seconds": slot[0] if aside else None,
                  "aside_body_seconds": aside_time, "closing": closing_text, "stop": stop_reason}
        if hasattr(editor, "prepare_speech"):
            save_json(work / "selected-narration.json", [
                {"step": i, "source_start": r["at"], "source_end": r["speech_finished_at"],
                 "body_start": map_aside(r["at"], r["speech_finished_at"]-r["at"], manifest), "text": r["text"],
                 "delivery": r.get("delivery", "")}
                for i, r in enumerate(rows, 1) if r["text"]])
            save_json(work / "edit.json", manifest)
        # Install the script alongside the master only after all video stages succeed.
        exporter.export(work, episode, design, traits, body, manifest["duration_seconds"],
                        opening, closing, aside, aside_time,
                        f"管理AI　{voice.credit}　 ／　プレイAI　{credit}", work / "episode.mp4")
        save_json(work / "script.json", report)
        if (work / "selected-narration.json").exists():
            (work / "selected-narration.json").replace(job / "selected-narration.json")
            (work / "edit.json").replace(job / "edit.json")
        (work / "episode.mp4").replace(job / "episode.mp4")
        (work / "script.json").replace(job / "script.json")


def record_source(job, episode, settings, source, minutes):
    voice_spec = settings["player_voice"]
    open_voice = getattr(adapter_factory(voice_spec), "for_recording", None)
    player: Player = adapter(settings["player"])
    recorder: Recorder = adapter(settings["recorder"])
    source.mkdir()
    previous = Path.cwd()
    with (nullcontext(None) if settings.get("player_speech_stage") == "post"
          else open_voice(**voice_spec.get("options", {})) if open_voice
          else nullcontext(adapter(voice_spec))) as player_voice:
        try:
            os.chdir(source)
            stop = recorder.capture(episode, source, player, player_voice, minutes)
            credit = player_voice.credit if player_voice else "編集時に生成"
        finally:
            os.chdir(previous)
    validate_source(source)
    save_json(job / "capture.json", {"credit": credit, "stop": stop})


def produce(job, config, *, resuming=False):
    settings = config["studio"]
    episode = Episode(Game(**config["game"]), config["persona"])
    source = Path(config["source"])
    with exclusive(job / ".production.lock"):
        if config["live"] and not (job / "capture.json").exists():
            if resuming:
                raise RuntimeError("Capture was not finalized. Start a new live job or use a verified archive.")
            with exclusive(job.parent / ".game-window.lock"):
                record_source(job, episode, settings, source, config["minutes"])
        capture = read_json(job / "capture.json") if config["live"] else {
            "credit": config["recorded_credit"], "stop": "既存の収録。記録で確認できた範囲のみ。"}
        stop = capture["stop"]
        if config.get("preparation"):
            evidence_file = job / "ending.json"
            ending = read_json(evidence_file) if evidence_file.exists() else {}
            if not ending.get("confirmed"):
                ending = adapter(config["preparation"]).ending(episode, source, stop, job)
                save_json(evidence_file, ending)
            if not ending["confirmed"]:
                raise RuntimeError(f"終了条件を映像で確認できませんでした。元収録を保存しました。{ending['evidence']}")
            stop = ending["evidence"]
        post(job, episode, settings, source, capture["credit"], stop)
    print(f"Review video: {job / 'episode.mp4'}", flush=True)


def resolve_game(value, live):
    path = Path(value)
    if path.is_file():
        return Game(**read_json(path))
    for profile in (ROOT / "studio/games").glob("*.json"):
        game = Game(**read_json(profile))
        if value.casefold() in (profile.stem.casefold(), game.title.casefold()):
            return game
    if path.suffix.lower() == ".json":
        raise FileNotFoundError(path)
    process = ""
    if live:
        import capturekit
        matches = [w for w in capturekit.list_windows() if w.title == value]
        if len(matches) != 1:
            raise ValueError("For a new game, supply its unique open window title or a game profile JSON")
        process = matches[0].process_name
    return Game(title=value, description="初見のソロプレイ。", window=value, process=process,
                controls="画面の操作表示を確認し、一手ずつ結果を確かめる。",
                objective="タイトルからソロで一度挑む。敗北、クリア、またはゲーム内で明確な一区切りを確認し、実況を締める。")


def proposal_inputs(path, pick_number, override, live):
    report = read_json(path)
    if report.get("schema") != 1 or not 1 <= pick_number <= len(report.get("picks", [])):
        raise ValueError("企画ファイルまたは --pick の番号が無効です。")
    pick = report["picks"][pick_number-1]
    candidate = next((g for g in report["games"] if g["id"] == pick["game_id"]), None)
    if candidate is None or not pick["persona"].strip():
        raise ValueError("企画のゲームまたは人格が見つかりません。")
    app_id = candidate.get("steam_app_id")
    # The first shortlists used the decimal Steam ID as their only game identifier.
    if app_id is None and candidate["id"].isdecimal():
        app_id = int(candidate["id"])

    def matches(game):
        if app_id is not None:
            return game.steam_app_id == app_id
        return game.title.casefold() == candidate["title"].casefold()

    if override:
        game = resolve_game(override, live)
        if not matches(game):
            raise ValueError("指定したゲーム設定が採用企画の作品と一致しません。")
    else:
        for profile in (ROOT / "studio/games").glob("*.json"):
            registered = Game(**read_json(profile))
            if matches(registered):
                game = registered
                break
        else:
            # A research report is data. Never use its title as a filesystem path or launcher.
            process = ""
            if live:
                import capturekit
                windows = [w for w in capturekit.list_windows() if w.title == candidate["title"]]
                if len(windows) != 1:
                    raise ValueError("収録設定が未登録です。ゲームを開くか、同じ作品の設定JSONを --game で指定してください。")
                process = windows[0].process_name
            game = Game(title=candidate["title"], description=candidate["description"],
                        window=candidate["title"], process=process,
                        controls="画面の操作表示を読み、一手ずつ結果を確認する。",
                        objective=f"初見ソロで遊ぶ。今回の区切り：{pick['unit']}")
    game = replace(game, objective=f"今回の収録単位：{pick['unit']}。開始時に前回の結果が表示されていれば今回のプレイを始める。"
                        "今回の終了画面で止まり、結果画面を閉じない。")
    selection = {"file": str(path.resolve()), "pick": pick_number,
                 "name": pick["name"], "format": pick["format"], "unit": pick["unit"]}
    if "voice_cast" in pick:
        validate_cast(pick["voice_cast"])
        selection["voice_cast"] = pick["voice_cast"]
    return game, pick["persona"], selection


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--game", help="Game key, open game window title or game JSON path")
    parser.add_argument("--persona", help="Personality of the AI that actually plays")
    parser.add_argument("--proposal", type=Path, help="shortlist.json generated by research.py")
    parser.add_argument("--pick", type=int, help="Proposal number, starting at 1 (default 1)")
    parser.add_argument("--voice-pick", type=int, help="Use this ranked voice candidate from --proposal")
    parser.add_argument("--studio", type=Path, help="Adapter settings JSON")
    parser.add_argument("--from-recording", type=Path, help="Use an existing finalized raw recording")
    parser.add_argument("--recorded-credit", help="Original player voice credit for a legacy archive")
    parser.add_argument("--resume", type=Path, help="Rerun post-production from the saved job")
    parser.add_argument("--minutes", type=float, help="Optional live recording limit")
    args = parser.parse_args()
    if args.voice_pick is not None and not args.proposal:
        parser.error("--voice-pick requires --proposal")
    if args.resume:
        if any(x is not None for x in (args.game, args.persona, args.from_recording, args.minutes,
                                       args.recorded_credit, args.studio, args.proposal, args.pick)):
            parser.error("--resume uses the saved game, persona, source and studio settings")
        job = args.resume.resolve()
        produce(job, read_json(job / "production.json"), resuming=True)
        return
    if args.proposal and args.persona is not None:
        parser.error("--proposal supplies its own persona")
    if not args.proposal and (args.pick is not None or not args.game or not (args.persona or "").strip()):
        parser.error("Use --game and --persona, or --proposal with an optional --pick")
    if args.minutes is not None and (not math.isfinite(args.minutes) or args.minutes <= 0 or args.from_recording):
        parser.error("--minutes needs a positive duration and a live recording")
    if args.recorded_credit and not args.from_recording:
        parser.error("--recorded-credit applies to existing recordings")
    selection = None
    if args.proposal:
        game, persona, selection = proposal_inputs(args.proposal, args.pick if args.pick is not None else 1,
                                                  args.game, live=not args.from_recording)
    else:
        game, persona = resolve_game(args.game, live=not args.from_recording), args.persona.strip()
    if args.voice_pick is not None:
        if not selection.get("voice_cast"):
            parser.error("This proposal has no voice candidates")
        selection["voice_pick"] = args.voice_pick
    settings = read_json(args.studio or DEFAULT_STUDIO)
    if not args.from_recording or (selection and settings.get("player_speech_stage") == "post"):
        settings = studio_for_pick(settings, selection)
    else:
        # SceneEdit may resynthesize a shorter take; match the voice of the actual recording.
        source_job = args.from_recording.resolve().parent / "production.json"
        if source_job.is_file():
            original = read_json(source_job)
            if original.get("live") and Path(original["source"]).resolve() == args.from_recording.resolve():
                settings = studio_for_pick(settings, original.get("proposal"))
    validate_studio(settings, live=not args.from_recording)
    job = ROOT / "recordings" / ("show-" + datetime.now().strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:8])
    source = args.from_recording.resolve() if args.from_recording else job / "raw"
    if args.from_recording:
        validate_source(source)
    recorded_credit = args.recorded_credit or "音声未指定"
    if args.from_recording and not args.recorded_credit and (source.parent / "capture.json").is_file():
        recorded_credit = read_json(source.parent / "capture.json")["credit"]
    job.mkdir(parents=True)
    config = {"game": asdict(game), "persona": persona, "studio": settings,
              "source": str(source), "live": not args.from_recording, "minutes": args.minutes or 0,
              "recorded_credit": recorded_credit}
    if selection:
        config["proposal"] = selection
    save_json(job / "production.json", config)
    print(f"Production folder: {job}", flush=True)
    produce(job, config)


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
