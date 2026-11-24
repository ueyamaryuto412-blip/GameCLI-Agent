"""Record the STS2 window and its audio, then cut a narrated local pilot."""
import argparse
import asyncio
from contextlib import nullcontext
from datetime import datetime
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import wave
import winsound

import capturekit as ck
import imageio_ffmpeg
import win32com.client

from gameplay_subtitles import subtitles
from gameplay_edit import render_gameplay
from recording_io import save_json
from show_types import RECORDING_CONTEXT
from agent_loop import AgentLoop
from llm import LLMIntegration
from screen_capture import ScreenCapture
from zundamon import Zundamon

TITLE = "Slay the Spire 2"
ROOT = Path(__file__).resolve().parents[1]
FFMPEG = imageio_ffmpeg.get_ffmpeg_exe()


def japanese_voice():
    speaker = win32com.client.Dispatch("SAPI.SpVoice")
    voices = speaker.GetVoices("Language=411")
    if voices.Count == 0:
        raise RuntimeError("Install a Japanese Windows desktop speech voice.")
    speaker.Voice = voices.Item(0)
    return speaker


def write_voice(speaker, text, path, speed=1.1):
    if not text.strip():
        with wave.open(str(path), "wb") as silent:
            silent.setparams((1, 2, 24000, 0, "NONE", ""))
            silent.writeframes(bytes(480))
        return 0
    if callable(getattr(speaker, "write", None)):
        speaker.write(text, path, speed)
    else:
        write_sapi(speaker, text, path)
    with wave.open(str(path)) as audio:
        return audio.getnframes() / audio.getframerate()


def write_sapi(speaker, text, path):
    stream = win32com.client.Dispatch("SAPI.SpFileStream")
    stream.Open(str(path), 3, False)
    try:
        speaker.AudioOutputStream = stream
        speaker.Speak(text, 16)  # SPF_IS_NOT_XML
    finally:
        stream.Close()


def render(folder, lines, duration, speaker, output_name="pilot.mp4", title=""):
    folder = Path(folder).resolve()
    with tempfile.TemporaryDirectory(prefix="mix-", dir=folder) as work:
        mix = Path(work)
        render_parts(folder / "game.mkv", mix, lines, duration, speaker, title)
        (mix / "pilot.mp4").replace(folder / output_name)
    print(folder / output_name, flush=True)


def render_parts(source, folder, lines, duration, speaker, title=""):
    parts = []
    last_spoken = next((i for i in reversed(range(len(lines))) if lines[i]["text"].strip()), len(lines) - 1)
    for index, line in enumerate(lines):
        wav = folder / f"voice-{index:03}.wav"
        if "spoken_file" in line:
            wav = source.parent / line["spoken_file"]
            with wave.open(str(wav)) as recorded:
                voice_seconds = recorded.getnframes() / recorded.getframerate()
        else:
            voice_seconds = write_voice(speaker, line.get("reading", line["text"]).replace("\n", ""),
                                        wav, line.get("speed", 1.1))
        offset = line.get("offset", 0.3)
        if "start" in line:
            start = line["start"]
            length = line["end"] - start
            if start < 0 or offset < 0 or length < offset + voice_seconds:
                raise ValueError(f"Cue {index + 1} needs {offset + voice_seconds:.2f}s in {length:.2f}s")
        else:
            start = max(0, line["at"] - 0.4)
            offset = line["at"] - start
            boundary = lines[index + 1]["at"] - 0.4 if index + 1 < len(lines) else duration
            wanted = max(voice_seconds + 1.0, line["last_action"] - start + 2.0)
            length = min(min(duration, boundary) - start, wanted)
        if length <= 0:
            continue
        # Hold the last frame when speech is longer than the recorded action.
        part = f"cut-{index:03}.mp4"
        final_length = max(length, offset + voice_seconds + .2)
        sub = folder / f"sub-{index:03}.ass"
        credit = getattr(speaker, "credit", "") if index >= last_spoken else ""
        opening_title = (title or line.get("title", "")) if not parts else ""
        subtitles(sub, line["text"], offset, voice_seconds, final_length, credit, opening_title)
        filters = ("[0:v]scale=1280:720:force_original_aspect_ratio=decrease,"
                   "pad=1280:720:(ow-iw)/2:(oh-ih)/2,setsar=1,fps=24,"
                   f"tpad=stop_mode=clone:stop_duration={final_length:.3f},ass={sub.name}[v];"
                   "[0:a]volume=1.0,apad[game];"
                   f"[1:a]adelay={round(offset * 1000)}:all=1,apad[voice];"
                   "[game][voice]amix=inputs=2:normalize=0,alimiter=limit=0.95[a]")
        subprocess.run([FFMPEG, "-loglevel", "error", "-y", "-ss", str(start),
                        "-t", str(length), "-i", str(source), "-i", str(wav),
                        "-filter_complex", filters, "-map", "[v]", "-map", "[a]",
                        "-t", str(final_length), "-c:v", "libx264",
                        "-preset", "veryfast", "-pix_fmt", "yuv420p", "-c:a", "aac",
                        "-ar", "48000", "-ac", "2", part], check=True, cwd=folder)
        parts.append(f"file '{part}'")
    if not parts:
        raise RuntimeError(f"No narrated moments captured. Raw recording: {folder}")
    (folder / "cuts.txt").write_text("\n".join(parts), encoding="ascii")
    subprocess.run([FFMPEG, "-loglevel", "error", "-y", "-f", "concat", "-i", "cuts.txt",
                    "-c:v", "copy", "-af", "loudnorm=I=-16:TP=-1.5:LRA=9", "-ar", "48000",
                    "-c:a", "aac", "-b:a", "160k", "-movflags", "+faststart", "pilot.mp4"],
                   cwd=folder, check=True)


async def record(minutes, observe, speaker, objective=None, title=""):
    instructions = (ROOT / "Game Instructions.slaythespire2.md").read_text(encoding="utf-8")
    if objective:
        persona, heading, _ = instructions.partition("## 今回の目的")
        instructions = f"{persona}{heading}\n\n{objective}"
    instructions = f"{RECORDING_CONTEXT}\n\n{instructions}"
    folder = ROOT / "recordings" / datetime.now().strftime("sts2-%Y%m%d-%H%M%S-%f")
    folder.mkdir(parents=True)
    lines, duration, _ = await capture_session(folder, TITLE, "slaythespire2.exe", instructions,
                                               lambda text, path: write_voice(speaker, text, path),
                                               minutes=minutes, observe=observe)
    if observe:
        render(folder, lines, duration, speaker, title=title)
    else:
        render_gameplay(folder, lines, getattr(speaker, "credit", ""),
                        title=title, output_name="pilot.mp4")


async def capture_session(folder, window_title, process, instructions, speak, *,
                          minutes=None, observe=False, provider="codex_cli", model="default",
                          standalone=False, player=None, scene_speech=False):
    window = next((w for w in ck.list_windows() if w.title == window_title
                   and w.process_name.casefold() == process.casefold()), None)
    if window is None:
        raise RuntimeError(f"Open the game window: {window_title} ({process})")
    (folder / "spoken").mkdir()
    (folder / "instructions.md").write_text(instructions, encoding="utf-8")
    recorder = ck.Recorder(
        ck.WindowSource(window, include_cursor=True), [ck.ProcessAudioSource.for_window(window)],
        str(folder / "game.mkv"), ck.RecordingConfig(fps=24, encoder=ck.EncoderConfig(
            encoder="libx264", preset="ultrafast", quality=24)))
    lines = []
    thinking_at = None
    stop_reason = ""
    last_voice_end = -3.0

    def save_log():
        save_json(folder / "narration.json", lines)

    async def event(message):
        nonlocal thinking_at, stop_reason, last_voice_end
        now = time.monotonic() - started
        if isinstance(message, str):
            if message.startswith(("STATUS: All goals", "STATUS: Time limit", "ERROR:")):
                stop_reason = message
            if standalone and message.startswith("PAUSED:"):
                stop_reason = message
                asyncio.get_running_loop().call_soon(agent.stop)
        if isinstance(message, str) and message.startswith("THINKING:"):
            thinking_at = now
        elif isinstance(message, dict) and message.get("type") == "narration":
            text = message["message"].strip()
            delivery = message.get("delivery")
            delivery = delivery.strip() if isinstance(delivery, str) and text else ""
            filename = f"spoken/{len(lines):03}.wav"
            path = folder / filename
            if speak is None:
                observed = thinking_at if thinking_at is not None else now
                lines.append(dict(decided_at=now, thinking_at=observed, at=observed, text="",
                                  original_text=text, delivery=delivery, speech_pending=True, spoken_file="",
                                  last_action=now, inputs=[], planned_actions=message.get("planned_actions", []),
                                  greeting=bool(message.get("greeting")), speech_finished_at=observed))
                thinking_at = None
                save_log()
                return
            if scene_speech and thinking_at is not None:
                from scene_narration import fit_scene_line
                greeting = message.get("greeting", False)
                candidate, at, end = fit_scene_line(text, thinking_at, now, last_voice_end,
                                                    speak, path, greeting)
                if candidate:
                    last_voice_end = end
                lines.append(dict(decided_at=now, thinking_at=thinking_at, at=at, text=candidate,
                                  original_text=text, delivery=delivery, spoken_file=filename if candidate else "",
                                  last_action=at, inputs=[], planned_actions=message.get("planned_actions", []),
                                  speech_finished_at=end))
                thinking_at = None
                save_log()
                if candidate and greeting:
                    await asyncio.sleep(max(0, end + .8 - (time.monotonic() - started)))
                print(f"[scene] {len(lines)} {candidate}", flush=True)
                return
            if text:
                try:
                    speak(text, path)
                except Exception:
                    if standalone:
                        asyncio.get_running_loop().call_soon(agent.stop)
                    raise
            else:
                with wave.open(str(path), "wb") as silent:
                    silent.setparams((1, 2, 24000, 0, "NONE", ""))
                    silent.writeframes(bytes(480))
            line = {"decided_at": now, "at": time.monotonic() - started, "text": text,
                    "spoken_file": filename, "delivery": delivery, "planned_actions": message.get("planned_actions", []),
                    "observe_after_s": message.get("observe_after_s", 0),
                    "inputs": []}
            line["last_action"] = line["at"]
            if thinking_at is not None:
                line["thinking_at"] = thinking_at
                thinking_at = None
            lines.append(line)
            if text:
                playback = asyncio.create_task(asyncio.to_thread(
                    winsound.PlaySound, str(path), winsound.SND_FILENAME | winsound.SND_NODEFAULT))
                try:
                    await asyncio.shield(playback)
                except asyncio.CancelledError:
                    # A timed stop must leave the recording and spoken WAV on the same clock.
                    await playback
                    line["speech_finished_at"] = time.monotonic() - started
                    save_log()
                    raise
            line["speech_finished_at"] = time.monotonic() - started
            save_log()
        elif isinstance(message, dict) and message.get("type") == "executing" and lines:
            lines[-1]["last_action"] = now
            lines[-1]["inputs"].append({"at": now, "command": message["command"]})
            save_log()
        elif isinstance(message, str) and message.startswith("CHECK: ") and lines:
            lines[-1]["next_observation"] = message.removeprefix("CHECK: ")
            save_log()
        print(message, flush=True)

    recorder.start()
    started = time.monotonic()
    try:
        if observe:
            frame = ScreenCapture().capture_fresh("window", window_title)
            prompt = (f"{RECORDING_CONTEXT}\n\n今の場面で思わず口に出る一言を、"
                      "自然な日本語で話してください。自分がまだ実行していない行動を"
                      "過去形で語らないでください。台詞だけを返してください。")
            text = LLMIntegration("codex_cli")._call(prompt, ScreenCapture.pil_to_base64(frame),
                                                     "default", expect_json=False)
            await event({"type": "narration", "message": text})
            await asyncio.sleep(8)
        else:
            if player is None:
                agent = AgentLoop(state_dir=folder) if standalone else AgentLoop()
                agent.start("", "window", window_title, model, instructions, event, provider=provider,
                            role="first_play", max_minutes=minutes or 0.0, unattended=True)
            else:
                agent = player.start(folder, window_title, instructions, event, minutes)
            try:
                await agent.task
            except asyncio.CancelledError:
                if not stop_reason.startswith("STATUS: Time limit"):
                    raise
    finally:
        duration = time.monotonic() - started
        result = recorder.stop()
        if result.error:
            raise RuntimeError(f"Game capture failed: {result.error}")
    return lines, duration, stop_reason


if __name__ == "__main__":
    # The Windows redirected-console default (CP932) cannot print all game logs.
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--minutes", type=float, help="Optional time limit in minutes. Defaults to playing to the session objective.")
    parser.add_argument("--observe", action="store_true", help="Narrate the current scene without input.")
    parser.add_argument("--voice", choices=["zundamon", "windows"], default="zundamon")
    parser.add_argument("--edit", type=Path, metavar="RECORDING", help="Re-edit an existing recording offline.")
    parser.add_argument("--script", type=Path, help="JSON list of timed commentary cues for --edit.")
    story = parser.add_mutually_exclusive_group()
    story.add_argument("--objective", help="Episode objective for the live player; keeps the character's preferences.")
    story.add_argument("--episode", type=Path, help="Live episode outline in Markdown with a title on the first line.")
    args = parser.parse_args()
    if args.minutes is not None and not 0 < args.minutes < float("inf"):
        parser.error("--minutes must be a positive finite number")
    if (args.script and not args.edit) or (args.edit and args.observe):
        parser.error("Use --edit RECORDING for an offline edit, or --observe for a live preview.")
    if (args.objective or args.episode) and (args.edit or args.observe):
        parser.error("--objective and --episode apply to play sessions.")
    title = ""
    if args.episode:
        args.objective = args.episode.read_text(encoding="utf-8-sig").strip()
        if not args.objective.startswith("# "):
            parser.error("The episode outline must begin with a Markdown title (# Title).")
        title = args.objective.splitlines()[0][2:].strip()
    if args.edit and not args.script:
        lines = json.loads((args.edit / "narration.json").read_text(encoding="utf-8"))
        if any("spoken_file" not in line for line in lines):
            parser.error("This recording predates live narration. Use --script with the original cue timings.")
        render_gameplay(args.edit, lines, Zundamon.credit if args.voice == "zundamon" else "")
    elif args.edit:
        speaker = Zundamon() if args.voice == "zundamon" else japanese_voice()
        script = json.loads(args.script.read_text(encoding="utf-8-sig"))
        if not script or not all("start" in cue and "end" in cue and "text" in cue for cue in script):
            parser.error("Each script cue needs start, end and text.")
        render(args.edit, script, script[-1]["end"], speaker, f"{args.script.stem}-{args.voice}.mp4")
    else:
        with (Zundamon.for_recording() if args.voice == "zundamon"
              else nullcontext(japanese_voice())) as speaker:
            asyncio.run(record(args.minutes, args.observe, speaker, args.objective, title))
