"""Gemini 3.8 single-speaker WAV synthesis for post-production."""
import base64
import io
import os
from pathlib import Path
import wave

from dotenv import load_dotenv
import requests


class GeminiVoice:
    def __init__(self, model="gemini-3.8-flash-tts", voice="Algenib",
                 style="自然な日本語。ゲーム画面を見ながら、小さな声で考えがこぼれるように話す。"):
        load_dotenv(Path(__file__).resolve().parents[1] / ".env")
        self.model = model
        self.voice = voice
        self.style = style
        self.credit = f"Gemini TTS · {voice}"
        self.project = os.environ.get("GEMINI_TTS_VERTEX_PROJECT")
        if self.project:
            import google.auth
            from google.auth.transport.requests import AuthorizedSession
            credentials, _ = google.auth.default(scopes=["https://www.googleapis.com/auth/cloud-platform"])
            self.session = AuthorizedSession(credentials)
        else:
            self.session = requests.Session()
            key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
            if not key:
                raise ValueError("Gemini TTS: set GEMINI_API_KEY or GEMINI_TTS_VERTEX_PROJECT on the editing PC.")
            self.session.headers.update({"x-goog-api-key": key})

    def write(self, text, path):
        self.write_directed(text, path, "")

    def write_directed(self, text, path, delivery):
        style = self.style
        if delivery:
            style += f"\n同じ人物・声色のまま、この一言の口調：{delivery}"
        if self.project:
            url = (f"https://aiplatform.googleapis.com/v1/projects/{self.project}/locations/global/"
                   f"publishers/google/models/{self.model}:generateContent")
            body = {"contents": [{"role": "user", "parts": [
                {"text": text, "speechMetadata": {"style": style}}]}],
                "generationConfig": {"responseModalities": ["AUDIO"],
                                     "speechConfig": {"voiceConfig": {"voice": self.voice}}}}
        else:
            url = "https://generativelanguage.googleapis.com/v1beta/interactions"
            body = {"model": self.model, "input": [{"type": "user_input", "content": [
                {"type": "text", "text": text, "annotations": [
                    {"type": "speech_metadata", "style": style}]}]}],
                "response_format": {"type": "audio"},
                "generation_config": {"speech_config": [{"voice": self.voice}]}}
        response = self.session.post(url, json=body, timeout=(10, 120), allow_redirects=False)
        response.raise_for_status()
        data = response.json()
        if self.project:
            parts = [p["inlineData"] for c in data.get("candidates", []) for p in c["content"]["parts"]
                     if "inlineData" in p]
        else:
            parts = [p for s in data.get("steps", []) if s["type"] == "model_output"
                     for p in s["content"] if p["type"] == "audio"]
        if not parts:
            raise RuntimeError("Gemini returned no audio for the selected line.")
        audio = base64.b64decode(parts[-1]["data"], validate=True)
        with wave.open(io.BytesIO(audio)) as wav:
            frames = wav.getnframes()
            if (wav.getnchannels(), wav.getsampwidth(), wav.getframerate()) != (1, 2, 24000) or (
                    not frames or len(wav.readframes(frames)) != frames * 2):
                raise ValueError("Gemini returned an invalid 24 kHz mono PCM16 WAV.")
        Path(path).write_bytes(audio)


if __name__ == "__main__":
    import argparse
    import json
    from time import perf_counter
    parser = argparse.ArgumentParser(description="Audition one line with the studio's Gemini voice.")
    parser.add_argument("--text", default="おや、ビッグバンから剣が出た。")
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--studio", type=Path, default=Path(__file__).resolve().parents[1] / "studio/default.json")
    args = parser.parse_args()
    spec = json.loads(args.studio.read_text(encoding="utf-8-sig"))["player_voice"]
    if spec["factory"] != "gemini_tts:GeminiVoice":
        parser.error("Select a studio configured for Gemini TTS.")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    speaker = GeminiVoice(**spec["options"])
    started = perf_counter()
    speaker.write(args.text, args.output)
    with wave.open(str(args.output)) as wav:
        print(f"{args.output}: {wav.getnframes()/wav.getframerate():.2f}s audio "
              f"in {perf_counter()-started:.2f}s ({speaker.credit})")
