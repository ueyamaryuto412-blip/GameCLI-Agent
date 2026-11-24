"""Schedule recorded remarks inside the observation that prompted them."""
import csv
import hashlib
import json
import re
import wave
from collections import defaultdict
from datetime import datetime
from tempfile import NamedTemporaryFile
from pathlib import Path

from zundamon import Voicevox


BREATH = 3.0
VOICE_RATE = 24000


def write_edited_voice(work, cues, edited, length):
    last_end = -BREATH
    with (work / "voice.pcm").open("wb") as track, (work / "narration.csv").open(
            "w", newline="", encoding="utf-8-sig") as manifest:
        writer = csv.writer(manifest)
        writer.writerow(["step", "source_start", "source_deadline", "start", "end", "text"])
        track.truncate(round(length * VOICE_RATE) * 2)
        for cue in cues:
            start = edited(cue["at"])
            end = edited(cue["at"] + cue["duration"])
            if (start - last_end < BREATH - .001 or abs(end - start - cue["duration"]) > .001
                    or cue["at"] + cue["duration"] > cue["deadline"] + .001):
                raise ValueError(f'Speech crosses a cut, pause or scene boundary at step {cue["step"]}')
            with wave.open(cue["path"]) as wav:
                if (wav.getnchannels(), wav.getsampwidth(), wav.getframerate()) != (1, 2, VOICE_RATE):
                    raise ValueError(f'Unexpected voice format at step {cue["step"]}')
                if wav.getnframes() != round(cue["duration"] * VOICE_RATE):
                    raise ValueError(f'Voice duration changed at step {cue["step"]}')
                track.seek(round(start * VOICE_RATE) * 2)
                track.write(wav.readframes(wav.getnframes()))
            writer.writerow([cue["step"], round(cue["at"], 3), round(cue["deadline"], 3),
                             round(start, 3), round(end, 3), cue["text"]])
            last_end = end


def sentences(text):
    return [s.strip() for s in re.findall(r"[^。！？!?]+[。！？!?]*", text) if s.strip()]


def render_post_speech(rows, cache, voice, voice_spec, choose_sentences):
    """Select from the recorded words, then fit actual WAVs to their observed scenes."""
    cache.mkdir(exist_ok=True, parents=True)
    choices, windows, candidates = {}, [], []
    for i, row in enumerate(rows):
        pending = row.get("speech_pending", False)
        # Preserve the edit decision for an already narrated archive.
        text = row["original_text"] if pending else row["text"]
        if pending:
            start = row["thinking_at"] + .65
            end = row["decided_at"] - .3
        else:
            start = min(row["at"], row.get("thinking_at", row["at"]) + .65)
            end = max(row["speech_finished_at"], row.get("decided_at", 0) - .3)
        if row.get("inputs"):
            end = min(end, row["inputs"][0]["at"])
        if i+1 < len(rows):
            nxt = rows[i+1]
            end = min(end, nxt.get("thinking_at", nxt["at"]))
        limit = 18 if row.get("greeting") or i == 0 else 8.5
        budget = min(limit, end - start)
        windows.append((start, end, limit))
        if budget <= 0 or not text.strip():
            continue
        parts = sentences(text)
        if pending and len(parts) > 1 and len(text) > budget * 5:
            candidates.append({"id": i, "sentences": parts, "available_seconds": round(budget, 1)})
        else:
            choices[i] = text
    choices.update(choose_sentences(candidates))

    rendered = []
    previous_end = -BREATH
    for i, original in enumerate(rows):
        row = dict(original)
        row.update(text="", spoken_file="", speech_finished_at=row["at"])
        rendered.append(row)
        start, end, limit = windows[i]
        start = max(start, previous_end + BREATH)
        budget = min(limit, end - start)
        text = choices.get(i, "")
        if not text or len(text) > budget * 10:
            continue
        delivery = original.get("delivery") if hasattr(voice, "write_directed") else ""
        identity = json.dumps([voice_spec, text, delivery] if delivery else [voice_spec, text],
                              ensure_ascii=False, sort_keys=True)
        path = cache / (hashlib.sha256(identity.encode()).hexdigest() + ".wav")
        if not path.is_file():
            with NamedTemporaryFile(dir=cache, suffix=".wav", delete=False) as tmp:
                pending_path = Path(tmp.name)
            try:
                if delivery:
                    voice.write_directed(text, pending_path, delivery)
                else:
                    voice.write(text, pending_path)
                with wave.open(str(pending_path)) as wav:
                    if ((wav.getnchannels(), wav.getsampwidth(), wav.getframerate()) != (1, 2, VOICE_RATE)
                            or not wav.getnframes()):
                        raise ValueError("Post-production requires a mono 24 kHz PCM16 WAV.")
                pending_path.replace(path)
            finally:
                pending_path.unlink(missing_ok=True)
        with wave.open(str(path)) as wav:
            duration = wav.getnframes() / wav.getframerate()
        if duration <= budget:
            at = max(start, min(original["at"], end-duration)) if not original.get("speech_pending") else end-duration
            row.update(text=text, at=at, speech_finished_at=at+duration,
                       spoken_file=str(path.resolve()), original_text=original.get("original_text", text))
            previous_end = at+duration
    if any(r.get("original_text") or r["text"] for r in rows) and not any(r["text"] for r in rendered):
        raise ValueError("No selected line fits its scene. Check narration timings and voice pacing before exporting.")
    return rendered


def fit_scene_line(text, observed, decided, previous_end, speak, path, greeting=False):
    """Place an original line over its decision window, with a real pause after the last voice."""
    earliest = max(observed + .65, previous_end + BREATH)
    budget = 18 if greeting else min(8.5, decided - earliest - .3)
    for candidate in dict.fromkeys([text, *sentences(text)]):
        if not candidate or len(candidate) > budget * 10:
            continue
        speak(candidate, path)
        with wave.open(str(path)) as wav:
            duration = wav.getnframes() / wav.getframerate()
        if duration <= budget:
            start = max(earliest, decided - duration - .3)
            return candidate, start, start + duration
    return "", decided, decided


def excerpts(text):
    parts = sentences(text)
    if not parts:
        return []
    # Keep an observation and its explanation together when the setup is short.
    pair = "".join(parts[:2])
    choices = [pair, parts[0]] if len(parts[0]) <= 16 and len(pair) <= 49 else [parts[0]]
    if len(parts[0]) <= 12 and len(parts) > 1:
        choices.append(parts[1])
    return list(dict.fromkeys(s for s in choices if len(s) > 4))


def schedule(source, player, work, selections):
    events = [json.loads(l) for l in (source / player / "trace.jsonl").read_text("utf-8").splitlines()]
    epoch = float((source / "recording-start.txt").read_text("ascii"))
    offset = datetime.fromisoformat(events[0]["at"]).timestamp() - epoch - events[0]["t"]
    steps = defaultdict(list)
    for e in events:
        steps[e["step"]].append(e)
    captures = [e for e in events if e["kind"] == "capture"]
    next_capture = {a["step"]: b["t"] for a, b in zip(captures, captures[1:])}
    voice = Voicevox()
    cues = []
    previous_end = -BREATH
    for n, group in sorted(steps.items()):
        capture = next((e for e in group if e["kind"] == "capture"), None)
        speech = [e for e in group if e["kind"] in ("tts", "tts_background")]
        if not capture or not speech:
            continue
        phase = capture["phase"]
        at = capture["t"] + offset + .65
        next_frame = next_capture.get(n)
        limits = [e["t"] - e["seconds"] for e in group if e["kind"] == "input"]
        limits += [e["t"] for e in group if e["kind"] in ("fight_start", "fight_end", "intro_end", "complete")]
        if next_frame is not None:
            limits.append(next_frame)
        if not limits:
            continue
        deadline = min(limits) + offset - .30
        if phase == "play":
            # A queued move can change the board even before the voice starts.
            moves = [e["t"] + offset for e in group if e["kind"] == "game" and
                     e["t"] >= capture["t"]]
            if moves:
                deadline = min(deadline, min(moves) - .30)
        for event in speech:
            decision = next((e for e in group if e["kind"] == "cli"), {})
            index = int(event["file"].split("-")[-1].split(".")[0])
            if any(v["kind"] == "input" and not v["value"].startswith("wait ")
                   for v in decision.get("events", [])[:index]):
                # This observation no longer proves what the player saw after their input.
                continue
            takes = selections.get(event["file"])
            # Each explicit take is an original sentence or adjacent original sentences.
            if takes is None:
                groups = [excerpts(event["text"])]
            else:
                parts = sentences(event["text"])
                groups = [["".join(parts[i] for i in indices)] for indices in takes]
            for j, options in enumerate(groups):
                at = max(at, previous_end + BREATH)
                budget = min(10.0 if phase == "intro" else 8.5, deadline - at)
                if budget < 1.1:
                    continue
                for text in options:
                    if not text or len(text) > budget * 10:
                        continue
                    if text == event["text"]:
                        path = source / player / event["file"]
                    else:
                        path = work / (f"take-{event['file'][:-4]}-{j}.wav")
                        voice.write(text, path)
                    with wave.open(str(path)) as audio:
                        duration = audio.getnframes() / audio.getframerate()
                    if duration > budget:
                        continue
                    cues.append(dict(step=n, at=at, duration=duration, deadline=deadline,
                                     text=text, path=str(path.resolve()), phase=phase))
                    previous_end = at + duration
                    break
    if not cues or cues[0]["phase"] != "intro" or cues[-1]["phase"] != "outro":
        raise ValueError("Expected both an opening and a closing greeting")
    return cues
