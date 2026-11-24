"""Synthesize recorded commentary in order while the player continues."""
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
import time
import wave


class RecordedSpeech:
    def __init__(self, voice, clock):
        self.voice = voice
        self.clock = clock
        self.pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="commentary")
        self.pending = deque()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        try:
            self.flush()
        finally:
            self.pool.shutdown()

    def _synthesize(self, text, path, step, cue_at):
        start = time.monotonic()
        self.voice.write(text, path)
        with wave.open(str(path)) as audio:
            duration = audio.getnframes() / audio.getframerate()
        timing = {"cue_at": cue_at} if cue_at is not None else {}
        self.clock.log("tts_background" if timing else "tts", step=step,
                       seconds=time.monotonic() - start, text=text, file=path.name,
                       audio_seconds=duration, **timing)
        return duration

    def write(self, text, path, *, background):
        if background:
            while self.pending and self.pending[0].done():
                self.pending.popleft().result()
            cue_at = datetime.now().astimezone().isoformat(timespec="milliseconds")
            self.pending.append(self.pool.submit(self._synthesize, text, path, self.clock.step, cue_at))
            return
        self.flush()
        return self._synthesize(text, path, self.clock.step, None)

    def flush(self):
        while self.pending:
            self.pending.popleft().result()
