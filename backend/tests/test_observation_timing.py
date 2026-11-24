from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import observation_timing as timing
from llm import FIRST_PLAY_SCHEMA, STEP_SCHEMA


@pytest.mark.parametrize("reply,expected", [
    ({}, 0), ({"observe_after_s": None}, 0), ({"observe_after_s": 0}, 0),
    ({"observe_after_s": 2.5}, 2.5), ({"observe_after_s": 120}, 60),
    ({"observe_after_s": -1}, 0), ({"observe_after_s": True}, 0),
    ({"observe_after_s": "2"}, 0), ({"observe_after_s": float("nan")}, 0),
])
def test_optional_observation_delay(reply, expected):
    assert timing.observation_seconds(reply) == expected


def test_explicit_wait_and_post_input_work_count_towards_delay(monkeypatch):
    monkeypatch.setattr(timing.time, "monotonic", lambda: 10.75)
    assert timing.seconds_remaining(2, 10) == 1.25
    assert timing.seconds_remaining(0.5, 10) == 0


def test_player_schema_accepts_null_without_changing_other_roles():
    assert FIRST_PLAY_SCHEMA["properties"]["observe_after_s"]["type"] == ["number", "null"]
    assert "observe_after_s" in FIRST_PLAY_SCHEMA["required"]
    assert "observe_after_s" not in STEP_SCHEMA["properties"]
