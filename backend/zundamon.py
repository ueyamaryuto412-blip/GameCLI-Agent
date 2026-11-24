"""Local VOICEVOX synthesis for the channel's Japanese narrator."""
import os
from pathlib import Path
from contextlib import contextmanager
import json
import subprocess
import sys


class Voicevox:
    @classmethod
    def for_recording(cls, **options):
        # The blocking native synthesizer can starve capturekit's Python callbacks.
        return recording_voice(parallel=True, **options)

    def __init__(self, character="ずんだもん", style="ノーマル", speed=1.1,
                 pitch=0.0, intonation=1.12, model_file="0.vvm"):
        try:
            from voicevox_core.blocking import Onnxruntime, OpenJtalk, Synthesizer, VoiceModelFile
        except ImportError as exc:
            raise RuntimeError("Run backend/setup_zundamon.ps1 to install VOICEVOX Core.") from exc

        root = Path(os.environ["LOCALAPPDATA"]) / "GameCLI-Agent" / "voicevox-0.17.1"
        dll = root / "voicevox_onnxruntime-win-x64-1.23.2/lib/voicevox_onnxruntime.dll"
        if not dll.is_file() or not (root / model_file).is_file():
            raise RuntimeError("Run backend/setup_zundamon.ps1 before rendering.")
        self.synth = Synthesizer(Onnxruntime.load_once(filename=str(dll)),
                                 OpenJtalk(root / "open_jtalk_dic_utf_8-1.11"),
                                 acceleration_mode="CPU", cpu_num_threads=4)
        self.credit = f"VOICEVOX:{character}"
        self.speed, self.pitch, self.intonation = speed, pitch, intonation
        with VoiceModelFile.open(root / model_file) as model:
            self.style = next((s.id for c in model.metas if c.name == character
                               for s in c.styles if s.name == style), None)
            if self.style is None:
                raise ValueError(f"{character}/{style} is not in {model_file}")
            self.synth.load_voice_model(model)

    def write(self, text, path, speed=None):
        query = self.synth.create_audio_query(text, self.style)
        query.speed_scale = self.speed if speed is None else speed
        query.pitch_scale = self.pitch
        query.intonation_scale = self.intonation
        query.pre_phoneme_length = 0.05
        query.post_phoneme_length = 0.12
        path.write_bytes(self.synth.synthesis(query, self.style))


class Zundamon(Voicevox):
    credit = "VOICEVOX:ずんだもん"


class _WorkerVoice:
    def __init__(self, process, credit):
        self.process = process
        self.credit = credit

    def write(self, text, path, speed=None):
        request = json.dumps([text, str(path.resolve()), speed], ensure_ascii=True)
        print(request, file=self.process.stdin, flush=True)
        if self.process.stdout.readline().strip() != "ok":
            raise RuntimeError("VOICEVOX worker failed to render the requested speech")


@contextmanager
def recording_voice(parallel=False, **options):
    if not parallel:
        yield Voicevox(**options)
        return
    with subprocess.Popen([sys.executable, str(Path(__file__).resolve()), "--worker"],
                          stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                          text=True, encoding="utf-8",
                          creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)) as process:
        try:
            print(json.dumps(options, ensure_ascii=True), file=process.stdin, flush=True)
            response = process.stdout.readline()
            if not response:
                raise RuntimeError("VOICEVOX worker failed to initialize")
            yield _WorkerVoice(process, json.loads(response)["credit"])
        finally:
            process.stdin.close()


if __name__ == "__main__" and sys.argv[1:] == ["--worker"]:
    voice = Voicevox(**json.loads(sys.stdin.readline()))
    print(json.dumps({"credit": voice.credit}, ensure_ascii=True), flush=True)
    for line in sys.stdin:
        text, filename, speed = json.loads(line)
        voice.write(text, Path(filename), speed)
        print("ok", flush=True)
