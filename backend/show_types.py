"""Contracts used at the production stage boundaries. Times are seconds in the raw video."""
from dataclasses import dataclass
from pathlib import Path
from collections.abc import Awaitable, Callable, Iterator
from typing import Protocol

from PIL import Image
from solo_play import SOLO_RULE

RECORDING_CONTEXT = (
    "いま、YouTube向けのゲーム実況動画を収録しています。"
    "視聴者は、AIのあなたがどう遊ぶのかを見に来ます。"
    "ゲーム画面と一緒に、あなたが実況として書いた言葉がそのまま声になって動画に入ります。"
    + SOLO_RULE
)


@dataclass(frozen=True)
class Game:
    title: str
    description: str
    window: str
    process: str
    controls: str
    objective: str
    steam_app_id: int | None = None
    end_screen: str = ""


@dataclass(frozen=True)
class Episode:
    game: Game
    persona: str


class Voice(Protocol):
    credit: str

    def write(self, text: str, path: Path) -> None:
        """Write a mono signed 16-bit PCM WAV at 24 kHz."""


class PlaySession(Protocol):
    task: Awaitable
    def stop(self): ...


class Player(Protocol):
    def instructions(self, episode: Episode) -> str: ...
    def start(self, folder: Path, window: str, instructions: str,
              event: Callable[[str | dict], Awaitable[None]], minutes: float) -> PlaySession:
        """Start a session. Await event({"type":"narration","message":...,"planned_actions":[...]})
        before acting and event({"type":"executing","command":...}) for each dispatched input.
        Send "THINKING: ..." and "CHECK: ..." for decision timing and observations.
        End with "STATUS: All goals ..." or "STATUS: Time limit ..."; report failures as "ERROR: ...".
        """


class Recorder(Protocol):
    def capture(self, episode: Episode, folder: Path, player: Player, voice: Voice, minutes: float) -> str:
        """Write game.mkv (24 fps + audio), narration.json and spoken WAVs; return stop reason."""


class Host(Protocol):
    def opening(self, episode: Episode) -> tuple[str, list[str]]: ...
    def aside(self, episode: Episode, past: list[dict], frame_b64: str) -> str: ...
    def closing(self, episode: Episode, evidence: list[dict], stop_reason: str) -> str: ...


class Editor(Protocol):
    def aside_window(self, rows: list[dict]) -> tuple[float, float] | None:
        """Choose a speech-free source interval or omit the aside."""
    def edit(self, source: Path, rows: list[dict], work: Path, protected: list) -> tuple[Path, dict]:
        """Return subtitled game MP4 and frame map (fps, source_frames, kept_ranges, duration_seconds)."""


class Design(Protocol):
    def frames(self, scene: str, episode: Episode, traits: list[str], credits: str, count: int) -> Iterator[Image.Image]:
        """Yield count RGB PIL images, 1280x720 at 24 fps; scene is opening, profile or closing."""


@dataclass
class Speech:
    wav: Path
    seconds: float
    cues: list[tuple[float, float, str]]


class Exporter(Protocol):
    def export(self, work: Path, episode: Episode, design: Design, traits: list[str],
               body: Path, duration: float, opening: Speech, closing: Speech,
               aside: Speech | None, aside_at: float | None, credits: str, target: Path) -> None: ...
