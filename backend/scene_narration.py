"""Schedule recorded remarks inside the observation that prompted them."""
import csv
import json
import re
import wave
from collections import defaultdict
from datetime import datetime

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
