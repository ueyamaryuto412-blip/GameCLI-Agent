"""Keep gameplay in source order and trim only the quiet tail of a decision wait."""
from bisect import bisect_right
import json
import math
from pathlib import Path
import re
import subprocess
import tempfile
import wave

import cv2
import imageio_ffmpeg
import numpy as np

import gameplay_seams
from gameplay_subtitles import ASS_HEADER, label_event, voice_event

FPS = 24  # capturekit recording rate in sts2_pilot
SAMPLE_FPS = 4
WIDTH, HEIGHT = 192, 108
VOICE_RATE = 24000
FFMPEG = imageio_ffmpeg.get_ffmpeg_exe()


def sample_motion(source, dest):
    subprocess.run([FFMPEG, "-nostdin", "-loglevel", "error", "-y", "-threads", "8",
                    "-i", str(source), "-an", "-vf",
                    f"fps={SAMPLE_FPS},scale={WIDTH}:{HEIGHT}:flags=area,format=gray",
                    "-f", "rawvideo", str(dest)], check=True)


def idle_tail(frames, earliest, latest):
    """Return a removable interval in 24 fps frames, or None if nothing is clearly idle."""
    left = max(0, math.ceil(earliest * SAMPLE_FPS))
    right = min(len(frames) - 1, math.floor(latest * SAMPLE_FPS))
    if right - left < 3 * SAMPLE_FPS:
        return None
    reference = frames[right]
    for i in range(right - 1, left - 1, -1):
        delta = cv2.absdiff(frames[i], reference)
        # Both a small moving UI element and a whole-screen change matter.
        tiles = delta.reshape(12, 9, 16, 12).mean(axis=(1, 3))
        if tiles.max() > 12 or np.count_nonzero(delta > 16) > delta.size * .015:
            left = i + 1
            break
    # Leave a beat on the settled result and another before the next decision.
    left += SAMPLE_FPS
    if right - left < 2 * SAMPLE_FPS:
        return None
    return left * (FPS // SAMPLE_FPS), right * (FPS // SAMPLE_FPS)


def turn_in_progress(line):
    actions = line.get("planned_actions", [])
    if any(a.get("command", "").startswith("wait ") for a in actions):
        return True
    description = " ".join(a.get("reason", "") for a in actions)
    if re.search(r"ターン終了|ターンを(?:終|渡)|end turn", description, re.I):
        return True
    return bool(re.search(r"敵のターン|敵ターン|演出中|着弾前|配札待ち",
                          line.get("next_observation", "")))


def thinking_pause(frames, earliest, latest, player_has_hand=False):
    """Find settled footage during a logged model call, allowing looping idle animation."""
    left = max(0, math.ceil(earliest * SAMPLE_FPS))
    right = min(len(frames) - 1, math.floor(latest * SAMPLE_FPS))
    if right - left < 2 * SAMPLE_FPS:
        return None
    # Characters breathe and the background moves even when the hand and HUD are still.
    # Compare against the range of appearances in the last second at this decision.
    settled = frames[right - SAMPLE_FPS:right + 1]
    low, high = settled.min(axis=0), settled.max(axis=0)
    variation = cv2.subtract(high, low).reshape(12, 9, 16, 12).mean(axis=(1, 3))
    if variation[0].max() > 14 or variation[8:].max() > 30:
        return idle_tail(frames, earliest, latest)
    for i in range(right - 1, left - 1, -1):
        difference = cv2.add(cv2.subtract(low, frames[i]), cv2.subtract(frames[i], high))
        tiles = difference.reshape(12, 9, 16, 12).mean(axis=(1, 3))
        if (tiles[0].max() > 8 or tiles[8:].max() > 12 or (not player_has_hand and tiles.max() > 38)
                or np.count_nonzero(difference > 18) > difference.size * .04):
            left = i + 1
            break
    left += SAMPLE_FPS  # let the result register before cutting forward
    if right - left < SAMPLE_FPS:
        return None
    return left * (FPS // SAMPLE_FPS), right * (FPS // SAMPLE_FPS)


def kept_ranges(lines, frames, total_frames, protected=()):
    if not lines:
        raise ValueError("No recorded decisions to edit")
    # Keep the beginning and end as recorded. Never turn a combat defeat into an early cut.
    ranges = []
    start = 0
    intro = idle_tail(frames, 0, lines[0]["at"] - 1.25)
    if intro:
        ranges.append((start, intro[0]))
        start = intro[1]
    for prev, nxt in zip(lines, lines[1:]):
        last_input = max([prev["last_action"], *(x["at"] for x in prev.get("inputs", []))])
        if "thinking_at" in nxt:
            # The model has already captured the screen and hasn't issued its next input.
            # Allow enemy sequences more time to finish and retain explicit pending attacks.
            if re.search(r"着弾前|攻撃はまだ着弾|敵の攻撃を待", prev.get("next_observation", "")):
                continue
            hold = 6 if turn_in_progress(prev) else 1.5
            earliest = max(nxt["thinking_at"] + .25, last_input + hold, prev["speech_finished_at"] + .25)
            latest = min(nxt["at"] - .45, total_frames / FPS)
            actor = prev.get("next_observation", "").partition("]")[0]
            player_has_hand = bool(re.search(r"(?:ぼく|自分|アイアンクラッド|プレイヤー)の第?\d*ターン", actor))
            cut = thinking_pause(frames, earliest, latest, player_has_hand=player_has_hand)
        else:
            if turn_in_progress(prev):
                continue
            earliest = max(last_input + 5, prev["speech_finished_at"] + 1)
            latest = min(nxt["at"] - 1.25, total_frames / FPS)
            cut = idle_tail(frames, earliest, latest)
        if cut:
            left, right = cut
            for a, b in sorted(protected):
                if b * FPS <= left or a * FPS >= right:
                    continue
                if math.floor(a * FPS) - left >= FPS:
                    ranges.append((start, left))
                    start = math.floor(a * FPS)
                left = max(left, math.ceil(b * FPS))
            if right - left >= FPS:
                ranges.append((start, left))
                start = right
    last = lines[-1]
    end = total_frames
    if not last.get("planned_actions") and not last.get("inputs"):
        tail = idle_tail(frames, last["speech_finished_at"] + 2, total_frames / FPS)
        if tail:
            end = min(end, tail[0])
    ranges.append((start, end))
    return ranges


def edited_time(seconds, ranges, starts, offsets):
    frame = seconds * FPS
    i = max(0, bisect_right(starts, frame) - 1)
    return (offsets[i] + min(frame, ranges[i][1]) - starts[i]) / FPS


def write_voice_track(work, folder, lines, ranges, credit, title):
    starts = [a for a, _ in ranges]
    counts = [end - start for start, end in ranges]
    offsets = np.cumsum([0, *counts[:-1]]).tolist()
    length = sum(counts) / FPS
    events = [ASS_HEADER]
    if title:
        events.append(label_event("Title", title, 0, min(5, length)))
    last_voice = None
    with (work / "voice.pcm").open("wb") as track:
        track.truncate(round(length * VOICE_RATE) * 2)
        for line in lines:
            if not line["text"].strip():
                continue
            src = folder / line["spoken_file"]
            with wave.open(str(src)) as wav:
                if (wav.getnchannels(), wav.getsampwidth(), wav.getframerate()) == (1, 2, VOICE_RATE):
                    pcm = wav.readframes(wav.getnframes())
                else:
                    pcm = subprocess.check_output([FFMPEG, "-v", "error", "-i", str(src),
                                                   "-ac", "1", "-ar", str(VOICE_RATE), "-f", "s16le", "-"])
            at = edited_time(line["at"], ranges, starts, offsets)
            duration = len(pcm) / (2 * VOICE_RATE)
            end = edited_time(line["at"] + duration, ranges, starts, offsets)
            if abs(end - at - duration) > 1 / FPS:
                raise ValueError(f"Edit would truncate speech in {src.name}")
            track.seek(round(at * VOICE_RATE) * 2)
            track.write(pcm)
            events.append(voice_event(line["text"], at, min(length, at + duration + .15)))
            last_voice = at
    if credit:
        credit_start = last_voice if last_voice is not None else max(0, length - 3)
        events.append(label_event("Credit", credit, credit_start, length))
    (work / "voice.ass").write_text("".join(events), encoding="utf-8-sig")
    return length, starts, offsets


def frame_selector(ranges):
    # Ordered binary search keeps the filter cheap even for a four-hour source.
    if len(ranges) == 1:
        a, b = ranges[0]
        return f"between(n,{a},{b-1})"
    middle = len(ranges) // 2
    return (f"if(lt(n,{ranges[middle][0]}),{frame_selector(ranges[:middle])},"
            f"{frame_selector(ranges[middle:])})")


def render_gameplay(folder, lines, credit, title="", output_name="gameplay.mp4",
                    chapters=(), protected=(), motion_file=None, encoder="libx264", output_dir=None,
                    trim_waits=True):
    folder = Path(folder).resolve()
    source = folder / "game.mkv"
    cap = cv2.VideoCapture(str(source))
    rate = cap.get(cv2.CAP_PROP_FPS)
    cap.release()
    # Matroska duration can include AAC padding beyond the last picture. Count the
    # recorder's H.264 packets without decoding or adding a held frame to the edit.
    count = subprocess.check_output([FFMPEG, "-v", "error", "-nostdin", "-i", str(source),
                                     "-map", "0:v:0", "-c:v", "copy", "-progress", "pipe:1",
                                     "-f", "null", "-"], text=True)
    total = int(re.findall(r"^frame=(\d+)$", count, re.M)[-1])
    if total <= 0 or abs(rate - FPS) > .01:
        raise ValueError("Expected a finalized 24 fps game recording")
    destination = Path(output_dir).resolve() if output_dir else folder
    with tempfile.TemporaryDirectory(prefix="gameplay-", dir=destination) as tmp:
        work = Path(tmp)
        spans = [(0, total)]
        if trim_waits:
            if motion_file is None:
                motion_file = work / "motion.gray"
                sample_motion(source, motion_file)
            samples = np.memmap(motion_file, mode="r", dtype=np.uint8).reshape(-1, HEIGHT, WIDTH)
            if abs(len(samples) / SAMPLE_FPS - total / FPS) > 1:
                raise ValueError("Motion samples and recording lengths differ")
            spans = kept_ranges(lines, samples, total, protected)
            del samples
        length, starts, offsets = write_voice_track(work, folder, lines, spans, credit, title)
        marks = [(round(1000 * edited_time(at, spans, starts, offsets)), name) for at, name in chapters]
        metadata = [";FFMETADATA1"]
        for (a, name), (b, _) in zip(marks, [*marks[1:], (round(length * 1000), "")]):
            if b > a:
                name = re.sub(r"([\\=;#])", r"\\\1", name.replace("\n", " "))
                metadata.extend(("[CHAPTER]", "TIMEBASE=1/1000", f"START={a}", f"END={b}", f"title={name}"))
        (work / "chapters.txt").write_text("\n".join(metadata) + "\n", encoding="utf-8")
        selector = frame_selector(spans)
        video = f"[0:v]fps={FPS},select='{selector}',setpts=N,{gameplay_seams.SCALE}"
        extra_inputs = []
        game = "[game]"
        audio_filter = (f"[0:a]aresample=48000:async=1:first_pts=0,asetnsamples=n={48000//FPS}:p=1,"
                        f"aselect='{selector}',asetpts=N/SR/TB[game];")
        patch, joins = gameplay_seams.patches(source, spans, work)
        if len(spans) > 1:
            gameplay_seams.decode_sound(source, work / "source.pcm")
            gameplay_seams.edit_sound(work / "source.pcm", spans, work / "game.pcm")
            extra_inputs = ["-f", "s16le", "-ar", "48000", "-ac", "2", "-i", "game.pcm"]
            game, audio_filter = "[3:a]", ""
            if patch:
                extra_inputs += ["-i", str(patch)]
                video += "[base];" + gameplay_seams.replace_frames("[base]", "[4:v]", joins)
        filters = (video + ",ass=voice.ass[v];" + audio_filter +
                   game + "[1:a]amix=inputs=2:duration=longest:normalize=0,"
                   "alimiter=limit=0.93:latency=1[a]")
        (work / "timeline.filter").write_text(filters, encoding="ascii")
        codec = (["-c:v", "h264_amf", "-quality", "balanced", "-rc", "cqp", "-qp_i", "19", "-qp_p", "21"]
                 if encoder == "h264_amf" else ["-c:v", "libx264", "-preset", "veryfast", "-crf", "20"])
        print(f"Rendering {length:.1f}s, {len(lines)} decisions, {len(spans)-1} idle cuts", flush=True)
        cmd = [FFMPEG, "-hide_banner", "-loglevel", "error", "-nostdin", "-y",
               "-threads", "8", "-i", str(source),
               "-f", "s16le", "-ar", str(VOICE_RATE), "-ac", "1", "-i", "voice.pcm",
               "-f", "ffmetadata", "-i", "chapters.txt", *extra_inputs, "-/filter_complex", "timeline.filter",
               "-map", "[v]", "-map", "[a]", "-map_chapters", "2", *codec, "-pix_fmt", "yuv420p",
               "-c:a", "aac", "-ar", "48000", "-ac", "2", "-b:a", "192k", "-movflags", "+faststart",
               "-progress", "encode.progress", "output.mp4"]
        subprocess.run(cmd, cwd=work, check=True)
        manifest = {"fps": FPS, "source_frames": total, "kept_ranges": spans,
                    "duration_seconds": length, "chapters_ms": marks}
        output = destination / output_name
        (work / "edit.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        (work / "output.mp4").replace(output)
        (work / "edit.json").replace(output.with_suffix(".json"))
        print(output, flush=True)
