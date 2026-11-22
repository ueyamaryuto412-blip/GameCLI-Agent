"""Record the STS2 window and its audio, then cut a narrated local pilot."""
import argparse
import asyncio
from datetime import datetime
import json
from pathlib import Path
import subprocess
import tempfile
import textwrap
import time
import wave
import winsound

import capturekit as ck
import imageio_ffmpeg
import win32com.client

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
    if isinstance(speaker, Zundamon):
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


def ass_time(seconds):
    cs = round(seconds * 100)
    return f"{cs // 360000}:{cs // 6000 % 60:02}:{cs // 100 % 60:02}.{cs % 100:02}"


def subtitles(path, text, offset, voice_seconds, length, credit):
    header = """[Script Info]
ScriptType: v4.00+
PlayResX: 1280
PlayResY: 720
WrapStyle: 2
ScaledBorderAndShadow: yes
[V4+ Styles]
Format: Name,Fontname,Fontsize,PrimaryColour,SecondaryColour,OutlineColour,BackColour,Bold,Italic,Underline,StrikeOut,ScaleX,ScaleY,Spacing,Angle,BorderStyle,Outline,Shadow,Alignment,MarginL,MarginR,MarginV,Encoding
Style: Voice,Yu Gothic,38,&H00EDFFF3,&H000000FF,&H00233618,&H90000000,-1,0,0,0,100,100,0,0,1,3,1,8,28,28,66,1
Style: Credit,Yu Gothic,23,&H00FFFFFF,&H000000FF,&H00202020,&H80000000,-1,0,0,0,100,100,0,0,1,2,0,3,24,24,20,1
[Events]
Format: Layer,Start,End,Style,Name,MarginL,MarginR,MarginV,Effect,Text
"""
    # Treat generated narration as literal subtitle text, including braces.
    plain = text.replace("{", "｛").replace("}", "｝").replace(chr(92), "＼")
    rows = [line for paragraph in plain.splitlines() for line in textwrap.wrap(paragraph, 28)]
    body = r"\N".join(rows)
    end = ass_time(min(length, offset + voice_seconds + .15))
    events = f"Dialogue: 0,{ass_time(offset)},{end},Voice,,0,0,0,,{body}\n"
    if credit:
        events += f"Dialogue: 1,0:00:00.00,{ass_time(length)},Credit,,0,0,0,,{credit}\n"
    path.write_text(header + events, encoding="utf-8-sig")


def render(folder, lines, duration, speaker, output_name="pilot.mp4"):
    folder = Path(folder).resolve()
    with tempfile.TemporaryDirectory(prefix="mix-", dir=folder) as work:
        mix = Path(work)
        render_parts(folder / "game.mkv", mix, lines, duration, speaker)
        (mix / "pilot.mp4").replace(folder / output_name)
    print(folder / output_name, flush=True)


def render_parts(source, folder, lines, duration, speaker):
    parts = []
    for index, line in enumerate(lines):
        wav = folder / f"voice-{index:03}.wav"
        if "spoken_file" in line and "start" not in line:
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
        credit = speaker.credit if isinstance(speaker, Zundamon) and index == len(lines) - 1 else ""
        subtitles(sub, line["text"], offset, voice_seconds, final_length, credit)
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


async def record(minutes, observe, speaker, objective=None):
    window = next((w for w in ck.list_windows() if w.title == TITLE
                   and w.process_name.lower() == "slaythespire2.exe"), None)
    if window is None:
        raise RuntimeError(f"Open {TITLE} first.")
    instructions = (ROOT / "Game Instructions.slaythespire2.md").read_text(encoding="utf-8")
    if objective:
        persona, heading, _ = instructions.partition("## 今回の目的")
        instructions = f"{persona}{heading}\n\n{objective}"
    folder = ROOT / "recordings" / datetime.now().strftime("sts2-%Y%m%d-%H%M%S-%f")
    folder.mkdir(parents=True)
    (folder / "spoken").mkdir()
    (folder / "instructions.md").write_text(instructions, encoding="utf-8")
    recorder = ck.Recorder(
        ck.WindowSource(window, include_cursor=True), [ck.ProcessAudioSource.for_window(window)],
        str(folder / "game.mkv"), ck.RecordingConfig(fps=24, encoder=ck.EncoderConfig(
            encoder="libx264", preset="ultrafast", quality=24)))
    lines = []

    async def event(message):
        now = time.monotonic() - started
        if isinstance(message, dict) and message.get("type") == "narration":
            text = message["message"].strip()
            filename = f"spoken/{len(lines):03}.wav"
            path = folder / filename
            write_voice(speaker, text, path)
            line = {"decided_at": now, "at": time.monotonic() - started, "text": text,
                    "spoken_file": filename, "planned_actions": message.get("planned_actions", []),
                    "inputs": []}
            line["last_action"] = line["at"]
            lines.append(line)
            if text:
                await asyncio.to_thread(winsound.PlaySound, str(path),
                                        winsound.SND_FILENAME | winsound.SND_NODEFAULT)
            line["speech_finished_at"] = time.monotonic() - started
        elif isinstance(message, dict) and message.get("type") == "executing" and lines:
            lines[-1]["last_action"] = now
            lines[-1]["inputs"].append({"at": now, "command": message["command"]})
        elif isinstance(message, str) and message.startswith("CHECK: ") and lines:
            lines[-1]["next_observation"] = message.removeprefix("CHECK: ")
        print(message, flush=True)
        if lines:
            (folder / "narration.json").write_text(json.dumps(lines, ensure_ascii=False, indent=2),
                                                    encoding="utf-8")

    recorder.start()
    started = time.monotonic()
    try:
        if observe:
            frame = ScreenCapture().capture_fresh("window", TITLE)
            prompt = ("画面に見える状況と次に確認したいことを、初見の慎重な実況者として"
                      "自然な日本語の一言で伝えてください。自分がまだ実行していない行動を"
                      "過去形で語らないでください。台詞だけを返してください。")
            text = LLMIntegration("codex_cli")._call(prompt, ScreenCapture.pil_to_base64(frame),
                                                     "default", expect_json=False)
            await event({"type": "narration", "message": text})
            await asyncio.sleep(8)
        else:
            agent = AgentLoop()
            agent.start("", "window", TITLE, "default", instructions, event, provider="codex_cli",
                        role="first_play", max_minutes=minutes)
            await agent.task
    finally:
        duration = time.monotonic() - started
        result = recorder.stop()
        if result.error:
            raise RuntimeError(f"Game capture failed: {result.error}")
    render(folder, lines, duration, speaker)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--minutes", type=float, default=6)
    parser.add_argument("--observe", action="store_true", help="Narrate the current scene without input.")
    parser.add_argument("--voice", choices=["zundamon", "windows"], default="zundamon")
    parser.add_argument("--edit", type=Path, metavar="RECORDING", help="Re-edit an existing recording offline.")
    parser.add_argument("--script", type=Path, help="JSON list of timed commentary cues for --edit.")
    parser.add_argument("--objective", help="Episode objective for the live player; keeps the character's preferences.")
    args = parser.parse_args()
    if not 0 < args.minutes < float("inf"):
        parser.error("--minutes must be a positive finite number")
    if bool(args.edit) != bool(args.script) or (args.edit and args.observe):
        parser.error("Use --edit RECORDING with --script FILE, or use --observe for a live preview.")
    if args.objective and (args.edit or args.observe):
        parser.error("--objective applies to a play session.")
    speaker = Zundamon() if args.voice == "zundamon" else japanese_voice()
    if args.edit:
        script = json.loads(args.script.read_text(encoding="utf-8-sig"))
        if not script or not all("start" in cue and "end" in cue and "text" in cue for cue in script):
            parser.error("Each script cue needs start, end and text.")
        render(args.edit, script, script[-1]["end"], speaker, f"{args.script.stem}-{args.voice}.mp4")
    else:
        asyncio.run(record(args.minutes, args.observe, speaker, args.objective))
