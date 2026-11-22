"""Record the STS2 window and its audio, then cut a narrated local pilot."""
import argparse
import asyncio
from datetime import datetime
import json
from pathlib import Path
import subprocess
import time
import wave

import capturekit as ck
import imageio_ffmpeg
import win32com.client

from agent_loop import AgentLoop
from llm import LLMIntegration
from screen_capture import ScreenCapture

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


def write_voice(speaker, text, path):
    stream = win32com.client.Dispatch("SAPI.SpFileStream")
    stream.Open(str(path), 3, False)
    try:
        speaker.AudioOutputStream = stream
        speaker.Speak(text, 16)  # SPF_IS_NOT_XML
    finally:
        stream.Close()
    with wave.open(str(path)) as audio:
        return audio.getnframes() / audio.getframerate()


def render(folder, lines, duration, speaker):
    parts = []
    for index, line in enumerate(lines):
        wav = folder / f"voice-{index:03}.wav"
        voice_seconds = write_voice(speaker, line["text"], wav)
        start = max(0, line["at"] - 0.4)
        boundary = lines[index + 1]["at"] - 0.4 if index + 1 < len(lines) else duration
        available = min(duration, boundary) - start
        wanted = max(voice_seconds + 1.0, line["last_action"] - start + 2.0)
        length = min(available, wanted)
        if length <= 0:
            continue
        # Hold the last frame when speech is longer than the recorded action.
        part = f"cut-{index:03}.mp4"
        filters = ("[0:v]scale=1280:720:force_original_aspect_ratio=decrease,"
                   "pad=1280:720:(ow-iw)/2:(oh-ih)/2,setsar=1,fps=24,"
                   f"tpad=stop_mode=clone:stop_duration={voice_seconds:.3f}[v];"
                   "[0:a]volume=0.40,apad[game];"
                   "[1:a]adelay=300:all=1,apad[voice];"
                   "[game][voice]amix=inputs=2:normalize=0,alimiter=limit=0.95[a]")
        subprocess.run([FFMPEG, "-loglevel", "error", "-y", "-ss", str(start),
                        "-t", str(length), "-i", "game.mkv", "-i", wav.name,
                        "-filter_complex", filters, "-map", "[v]", "-map", "[a]",
                        "-t", str(max(length, voice_seconds + 0.6)), "-c:v", "libx264",
                        "-preset", "veryfast", "-pix_fmt", "yuv420p", "-c:a", "aac",
                        "-ar", "48000", "-ac", "2", part], check=True, cwd=folder)
        parts.append(f"file '{part}'")
    if not parts:
        raise RuntimeError(f"No narrated moments captured. Raw recording: {folder}")
    (folder / "cuts.txt").write_text("\n".join(parts), encoding="ascii")
    subprocess.run([FFMPEG, "-loglevel", "error", "-y", "-f", "concat", "-i", "cuts.txt",
                    "-c", "copy", "-movflags", "+faststart", "pilot.mp4"], cwd=folder, check=True)
    print(folder / "pilot.mp4", flush=True)


async def record(minutes, observe):
    window = next((w for w in ck.list_windows() if w.title == TITLE
                   and w.process_name.lower() == "slaythespire2.exe"), None)
    if window is None:
        raise RuntimeError(f"Open {TITLE} first.")
    speaker = japanese_voice()
    instructions = (ROOT / "Game Instructions.slaythespire2.md").read_text(encoding="utf-8")
    folder = ROOT / "recordings" / datetime.now().strftime("sts2-%Y%m%d-%H%M%S-%f")
    folder.mkdir(parents=True)
    recorder = ck.Recorder(
        ck.WindowSource(window, include_cursor=True), [ck.ProcessAudioSource.for_window(window)],
        str(folder / "game.mkv"), ck.RecordingConfig(fps=24, encoder=ck.EncoderConfig(
            encoder="libx264", preset="ultrafast", quality=24)))
    lines = []

    async def event(message):
        now = time.monotonic() - started
        if isinstance(message, str) and message.startswith("NARRATION: "):
            lines.append({"at": now, "last_action": now, "text": message.removeprefix("NARRATION: ")})
        elif isinstance(message, dict) and message.get("type") == "executing" and lines:
            lines[-1]["last_action"] = now
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
            await event("NARRATION: " + text)
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
    args = parser.parse_args()
    if not 0 < args.minutes < float("inf"):
        parser.error("--minutes must be a positive finite number")
    asyncio.run(record(args.minutes, args.observe))
