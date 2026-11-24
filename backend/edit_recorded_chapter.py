"""Edit a recorded chapter, optionally rebuilding its commentary from the decision log."""
import argparse
import csv
from datetime import datetime
import json
from pathlib import Path
import subprocess
import tempfile

import cv2
import numpy as np

from gameplay_edit import FPS, FFMPEG, HEIGHT, SAMPLE_FPS, WIDTH, frame_selector, sample_motion, thinking_pause
from scene_narration import BREATH, schedule, write_edited_voice


def chapter_timeline(source, player, frames, total, narration=None):
    events = [json.loads(row) for row in (source / player / "trace.jsonl").read_text("utf-8").splitlines()]
    epoch = float((source / "recording-start.txt").read_text("ascii"))
    offset = datetime.fromisoformat(events[0]["at"]).timestamp() - epoch - events[0]["t"]
    duration = total / FPS
    protected = [(0, 2), (duration - 2, duration)]
    if narration is None:
        with (source / "transcript.csv").open(encoding="utf-8-sig", newline="") as file:
            cues = list(csv.DictReader(file))
        speech = [e for e in events if e["kind"] in ("tts", "tts_background")]
        for cue, event in zip(cues, speech, strict=True):
            if cue["text"] != event["text"] or int(cue["step"]) != event["step"]:
                raise ValueError("Soundtrack and decision log do not match")
            start = float(cue["seconds"])
            protected.append((start - .28, start + event["audio_seconds"] + .35))
    else:
        for cue in narration:
            protected.append((cue["at"] - BREATH / 2, cue["at"] + cue["duration"] + BREATH / 2))

    requests = {}
    windows = []
    markers = [(0, "自己紹介")]
    fight = 0
    greeting_ends = {e["step"]: e["t"] + offset for e in events
                     if narration is not None and e["kind"] in ("intro_end", "complete")}
    for e in events:
        t = e["t"] + offset
        kind = e["kind"]
        if kind == "input":
            protected.append((t - e["seconds"] - .32, t + 1.6))
        elif kind == "game":
            protected.append((t - .65, t + (4 if "performing move" in e["message"] else 2.25)))
        elif kind == "fight_start":
            protected.append((t - 1, t + 5))
            fight += 1
            markers.append((t, f"第{fight}戦"))
        elif kind == "fight_end":
            protected.append((t - 1.5, t + 3))
        elif kind == "chapter_end":
            markers.append((t, "決着と締め"))
        elif kind == "cli_start":
            requests[e["step"]] = t
        elif kind == "cli" and e["step"] in requests:
            windows.append((requests.pop(e["step"]) + .30, greeting_ends.get(e["step"], t) - .55))

    cuts = []
    intro_end = next((e["t"] + offset for e in events if e["kind"] == "intro_end"), 0)
    intro_voice_end = max((q["at"] + q["duration"] for q in narration or []
                          if q["phase"] == "intro"), default=float("inf"))
    for start, end in windows:
        parts = [(max(0, start), min(duration, end))]
        for left, right in protected:
            remaining = []
            for lo, hi in parts:
                if right <= lo or left >= hi:
                    remaining.append((lo, hi))
                else:
                    if lo < left:
                        remaining.append((lo, left))
                    if hi > right:
                        remaining.append((right, hi))
            parts = remaining
        for lo, hi in parts:
            cut = thinking_pause(frames, lo, hi)
            if intro_voice_end <= lo < hi <= intro_end and hi - lo > 5:
                # The title's moving clouds can mask an otherwise unchanged menu.
                a, b = int(np.ceil((lo + .5) * SAMPLE_FPS)), int(np.floor(hi * SAMPLE_FPS))
                samples = frames[a:b + 1].astype(np.int16)
                delta = np.abs(samples - samples[0])
                if delta.mean(axis=(1, 2)).max() < 4 and (delta > 32).mean(axis=(1, 2)).max() < .008:
                    cut = a * (FPS // SAMPLE_FPS), b * (FPS // SAMPLE_FPS)
            if cut and cut[1] - cut[0] >= 1.5 * FPS:
                cuts.append(cut)

    if any(left < b/FPS and right > a/FPS for a, b in cuts for left, right in protected):
        raise ValueError("A proposed cut overlaps protected footage")
    keep = list(zip([0] + [b for a, b in cuts], [a for a, b in cuts] + [total]))
    if any(a >= b for a, b in keep):
        raise ValueError("Invalid edit timeline")
    return keep, cuts, markers


def cut_audio(source, work, keep, total):
    pcm = work / (source.name + ".pcm")
    if not pcm.exists() or pcm.stat().st_size != total * (48000 // FPS) * 4:
        subprocess.run([FFMPEG, "-v", "error", "-nostdin", "-y", "-i", str(source),
                        "-map", "0:a:0", "-af", "aresample=48000:async=1:first_pts=0,apad",
                        "-t", str(total / FPS), "-ac", "2", "-f", "s16le", str(pcm)], check=True)
    samples = np.memmap(pcm, dtype="<i2", mode="r").reshape(-1, 2)
    per_frame = 48000 // FPS
    if len(samples) != total * per_frame:
        raise ValueError("Source audio and video lengths differ")
    fade = np.linspace(0, 1, 576, dtype=np.float32)[:, None]  # 12 ms on each side of a join
    with (work / "trim.pcm").open("wb") as output:
        for i, (lo, hi) in enumerate(keep):
            part = samples[lo * per_frame:hi * per_frame].copy()
            if i:
                part[:len(fade)] = (part[:len(fade)] * fade).round()
            if i + 1 < len(keep):
                part[-len(fade):] = (part[-len(fade):] * fade[::-1]).round()
            output.write(part.tobytes())


def detail_overlay(movie, work, narration, edited, detail):
    """Repeat a briefly displayed item beside the game while its recorded line is spoken."""
    cue = next(q for q in narration if q["step"] == detail["step"])
    x0, y0, x1, y1 = detail["crop"]
    if not (0 <= x0 < x1 <= 1 and 0 <= y0 < y1 <= 1):
        raise ValueError("Detail crop must be inside the captured frame")
    cap = cv2.VideoCapture(str(movie))
    cap.set(cv2.CAP_PROP_POS_MSEC, detail["source_seconds"] * 1000)
    ok, frame = cap.read()
    cap.release()
    if not ok:
        raise ValueError("Cannot read the item detail frame")
    height, width = frame.shape[:2]
    item = frame[round(y0*height):round(y1*height), round(x0*width):round(x1*width)]
    panel = cv2.resize(item, (328, round(328*item.shape[0]/item.shape[1])), interpolation=cv2.INTER_AREA)
    panel = cv2.copyMakeBorder(panel, 3, 3, 3, 3, cv2.BORDER_CONSTANT, value=(35, 43, 49))
    path = work / "detail.png"
    path.write_bytes(cv2.imencode(".png", panel)[1].tobytes())
    start, end = edited(cue["at"]), edited(cue["at"] + cue["duration"])
    return path, f"overlay=W-w-84:(H-h)/2:enable='between(t,{start:.6f},{end:.6f})'"


def edit(source, player, output_dir, work, narration=None, detail=None):
    movie = source / "chapter.mp4"
    cap = cv2.VideoCapture(str(movie))
    total = round(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    fps = cap.get(cv2.CAP_PROP_FPS)
    cap.release()
    if not total or abs(fps - FPS) > .01:
        raise ValueError("Expected a finalized 24 fps chapter.mp4")
    motion = work / "motion.gray"
    if not motion.exists():
        sample_motion(movie, motion)
    frames = np.memmap(motion, dtype=np.uint8, mode="r").reshape(-1, HEIGHT, WIDTH)
    if abs(len(frames) / SAMPLE_FPS - total / FPS) > .5:
        raise ValueError("Motion analysis length differs from video")
    keep, cuts, markers = chapter_timeline(source, player, frames, total, narration)
    cut_audio(source / "game.mkv" if narration is not None else movie, work, keep, total)
    length = sum(b - a for a, b in keep) / FPS
    def edited(t):
        return sum(max(0, min(t * FPS, b) - a) for a, b in keep) / FPS

    extra_inputs = []
    filters = []
    audio_map = "1:a:0"
    outputs = ["chapter-edited.mp4", "cuts.csv"]
    if narration is not None:
        write_edited_voice(work, narration, edited, length)
        extra_inputs = ["-f", "s16le", "-ar", "24000", "-ac", "1", "-i", str(work / "voice.pcm")]
        filters.append("[1:a]volume=0.65[game];[game][3:a]amix=inputs=2:duration=first:"
                       "normalize=0,alimiter=limit=0.95:latency=1[audio]")
        audio_map = "[audio]"
        outputs.append("narration.csv")

    with (work / "cuts.csv").open("w", newline="", encoding="utf-8-sig") as file:
        writer = csv.writer(file)
        writer.writerow(["元動画の開始秒", "元動画の終了秒", "カット秒数", "編集版のつなぎ位置（秒）"])
        for a, b in cuts:
            writer.writerow([a/FPS, b/FPS, (b-a)/FPS, edited(a/FPS)])
    marks = [(edited(t), name) for t, name in markers]
    meta = [";FFMETADATA1"]
    for (t, name), (end, _) in zip(marks, marks[1:] + [(length, "")]):
        meta += ["[CHAPTER]", "TIMEBASE=1/1000", f"START={round(t*1000)}",
                 f"END={round(end*1000)}", f"title={name}"]
    (work / "chapters.txt").write_text("\n".join(meta) + "\n", encoding="utf-8")
    vf = f"select='{frame_selector(keep)}',settb=1/{FPS},setpts=N,scale=1920:1080:flags=lanczos,setsar=1"
    video_map = "0:v:0"
    video_filter = ["-vf", vf]
    if detail:
        path, overlay = detail_overlay(movie, work, narration, edited, detail)
        extra_inputs += ["-i", str(path)]
        filters += [f"[0:v]{vf}[base]", f"[base][4:v]{overlay}[video]"]
        video_map = "[video]"
        video_filter = []
    print(f"{len(cuts)} cuts, {total/FPS:.2f}s -> {length:.2f}s. Encoding.", flush=True)
    if filters:
        extra_inputs += ["-filter_complex", ";".join(filters)]
    subprocess.run([FFMPEG, "-v", "error", "-nostdin", "-y", "-threads", "8", "-i", str(movie),
                    "-f", "s16le", "-ar", "48000", "-ac", "2", "-i", str(work / "trim.pcm"),
                    "-f", "ffmetadata", "-i", str(work / "chapters.txt"), *extra_inputs,
                    "-map", video_map, "-map", audio_map, "-metadata", "comment=実況音声 VOICEVOX:ずんだもん",
                    "-map_chapters", "2", *video_filter, "-r", str(FPS), "-c:v", "libx264", "-threads", "8",
                    "-preset", "veryfast", "-crf", "19", "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k",
                    "-movflags", "+faststart", "-progress", str(work / "progress"), str(work / "chapter-edited.mp4")],
                   check=True)
    for filename in outputs:
        (work / filename).replace(output_dir / filename)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("--player", default="astra")
    parser.add_argument("--scene-speech", action="store_true", help="Align excerpts to the observed game state")
    parser.add_argument("--selections", type=Path, help="Optional JSON of sentence selections per recorded WAV")
    parser.add_argument("--detail", type=Path, help="Optional JSON identifying a briefly displayed item and its crop")
    args = parser.parse_args()
    if (args.selections or args.detail) and not args.scene_speech:
        parser.error("--selections and --detail require --scene-speech")
    destination = args.source / "edits" / ("scene-sync" if args.scene_speech else "chapter")
    destination.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=destination) as temp:
        selections = json.loads(args.selections.read_text("utf-8")) if args.selections else {}
        cues = schedule(args.source, args.player, Path(temp), selections) if args.scene_speech else None
        detail = json.loads(args.detail.read_text("utf-8")) if args.detail else None
        edit(args.source, args.player, destination, Path(temp), cues, detail)
