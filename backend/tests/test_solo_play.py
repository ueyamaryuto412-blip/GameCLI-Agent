from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from solo_play import input_block


@pytest.mark.parametrize("mode,target,command", [
    ("online", "Start", "press enter"),
    ("unknown", "Play", "press enter"),
    (None, "Play", "click 5 8"),
    ("select_solo", "オンライン対戦", "click 5 8"),
    ("local_solo", "Join Lobby", "press enter"),
    ("loading", "Play", "press enter"),
    ("local_solo", "", "click 5 8"),
    ("local_solo", "menu", "run_macro a"),
])
def test_online_or_unverified_input_is_blocked(mode, target, command):
    assert input_block(mode, target, command)


@pytest.mark.parametrize("mode,target,command", [
    ("select_solo", "一人で遊ぶ", "click 5 8"),
    ("local_solo", "CPUの駒", "drag 5 8 50 80"),
    ("loading", "", "wait 0.5"),
])
def test_solo_and_passive_wait_are_allowed(mode, target, command):
    assert input_block(mode, target, command) is None
