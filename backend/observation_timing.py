"""Timing of the next gameplay observation, requested in the player's own decision."""
import time


TIMING_PROMPT = ("observe_after_s は最後の操作が終わってから次の画面を見るまでの秒数。"
                 "演出などで待ちたいときだけ0〜60秒の数値を指定する。すぐ見たいときは null。"
                 "操作がない場合は今回の応答から数える。")


def with_observation_timing(schema):
    return {
        **schema,
        "properties": {**schema["properties"], "observe_after_s": {
            "type": ["number", "null"], "description": TIMING_PROMPT}},
        "required": [*schema["required"], "observe_after_s"],
    }


def observation_seconds(response):
    value = response.get("observe_after_s")
    if type(value) not in (int, float) or not 0 < value < float("inf"):
        return 0
    return min(value, 60)


def seconds_remaining(seconds, since):
    return max(0, seconds - (time.monotonic() - since))
