"""Supertonic host voice and the stereo room response selected in the audition."""
import re
import subprocess
import tempfile
from pathlib import Path

import imageio_ffmpeg
import numpy as np
from supertonic import TTS


FFMPEG = imageio_ffmpeg.get_ffmpeg_exe()
RATE = 44100


class PlayerVoice:
    def __init__(self, style, speed):
        self.tts = TTS(model="supertonic-3", intra_op_num_threads=4, inter_op_num_threads=1)
        self.style = self.tts.get_voice_style(style)
        self.speed = speed
        self.credit = f"Supertonic 3 · {style}"

    def write(self, text, path):
        reading = re.sub(r"(?<![A-Za-z])AI(?![A-Za-z])", "エーアイ", text)
        speech, _ = self.tts.synthesize(reading, self.style, lang="ja", total_steps=12, speed=self.speed)
        subprocess.run([FFMPEG, "-v", "error", "-y", "-f", "f32le", "-ar", str(self.tts.sample_rate),
                        "-ac", "1", "-i", "pipe:0", "-ar", "24000", "-c:a", "pcm_s16le", str(path)],
                       input=speech.astype("<f4").tobytes(), check=True)


class RoomVoice(PlayerVoice):
    def __init__(self, style="F5", speed=.97):
        super().__init__(style, speed)

    def finish_speech(self, path):
        pcm = subprocess.check_output([FFMPEG, "-v", "error", "-i", str(path), "-af", "highpass=f=70",
                                       "-f", "f32le", "-ar", str(RATE), "-ac", "1", "-"])
        dry = np.frombuffer(pcm, dtype="<f4")
        tail = round(RATE * .75)
        size = 1 << (len(dry) + round(RATE * (.034 + .51 * 1.7)) - 2).bit_length()
        spectrum = np.fft.rfft(dry, size)
        freq = np.fft.rfftfreq(size, 1 / RATE)
        band = (1 - 1 / (1 + (freq / 290)**4)) / (1 + (freq / 4900)**4)
        channels = []
        for side in range(2):
            impulse = np.zeros(round(RATE * (.034 + .51 * 1.7)))
            for delay, gain in [(0, 1), (.009, .68), (.024, .39), (.043, .23)]:
                impulse[round(RATE * (.034 + delay + .003 * side))] = gain
            start = round(RATE * (.034 + .041))
            times = np.arange(len(impulse) - start) / RATE
            noise = np.random.default_rng(117 + side).normal(size=len(times))
            impulse[start:] += noise * np.exp(-6.908 * times / .51) * .030
            wet = np.fft.irfft(spectrum * np.fft.rfft(impulse, size) * band, size)
            channel = .245 * wet[:len(dry) + tail]
            channel[:len(dry)] += dry
            channels.append(channel)
        with tempfile.TemporaryDirectory(dir=path.parent) as tmp:
            result = Path(tmp) / "stereo.wav"
            subprocess.run([FFMPEG, "-v", "error", "-f", "f32le", "-ar", str(RATE), "-ac", "2",
                            "-i", "pipe:0", "-af", "loudnorm=I=-18:TP=-2:LRA=8", "-ar", str(RATE),
                            "-c:a", "pcm_s16le", str(result)],
                           input=np.column_stack(channels).astype("<f4").tobytes(), check=True)
            result.replace(path)
