"""Local VOICEVOX synthesis for the channel's Japanese narrator."""
import os
from pathlib import Path


class Zundamon:
    credit = "VOICEVOX:ずんだもん"

    def __init__(self):
        try:
            from voicevox_core.blocking import Onnxruntime, OpenJtalk, Synthesizer, VoiceModelFile
        except ImportError as exc:
            raise RuntimeError("Run backend/setup_zundamon.ps1 to install VOICEVOX Core.") from exc

        root = Path(os.environ["LOCALAPPDATA"]) / "GameCLI-Agent" / "voicevox-0.17.1"
        dll = root / "voicevox_onnxruntime-win-x64-1.23.2/lib/voicevox_onnxruntime.dll"
        if not dll.is_file() or not (root / "0.vvm").is_file():
            raise RuntimeError("Run backend/setup_zundamon.ps1 before rendering.")
        self.synth = Synthesizer(Onnxruntime.load_once(filename=str(dll)),
                                 OpenJtalk(root / "open_jtalk_dic_utf_8-1.11"),
                                 acceleration_mode="CPU", cpu_num_threads=4)
        with VoiceModelFile.open(root / "0.vvm") as model:
            self.style = next(s.id for c in model.metas if c.name == "ずんだもん"
                              for s in c.styles if s.name == "ノーマル")
            self.synth.load_voice_model(model)

    def write(self, text, path, speed):
        query = self.synth.create_audio_query(text, self.style)
        query.speed_scale = speed
        query.intonation_scale = 1.12
        query.pre_phoneme_length = 0.05
        query.post_phoneme_length = 0.12
        path.write_bytes(self.synth.synthesis(query, self.style))
