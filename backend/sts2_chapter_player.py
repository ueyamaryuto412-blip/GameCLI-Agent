"""STS2 player adapter that stops on the first chapter boss result or a run loss."""
import asyncio
import json
import os
import re
import subprocess
import time
from pathlib import Path

from show_adapters import FirstPlay
from llm import LLMIntegration
from voice_casting import DELIVERY_PROMPT


def chapter_result(game):
    for line in game.splitlines():
        if "ENCOUNTER." in line and "_BOSS" in line and ("has won against" in line or " and WON" in line):
            return "第1章のボス戦に勝利。 " + line.strip()
        if "has died to" in line or ("ENCOUNTER." in line and (" and LOST" in line or "has lost" in line)):
            return "第1章で敗北。 " + line.strip()
    return ""


class ChapterPlayer(FirstPlay):
    def start(self, folder, window, instructions, event, minutes):
        self.task = asyncio.create_task(self.play(folder, window, instructions, event, minutes))
        return self

    def stop(self):
        self.task.cancel()

    async def play(self, folder, title, instructions, event, minutes):
        from agent_loop import AgentLoop
        from input_controller import InputController
        from screen_capture import ScreenCapture
        screen, controller = ScreenCapture(), InputController()
        model = LLMIntegration(self.provider, reasoning_effort=self.reasoning_effort)
        log = Path(os.environ["APPDATA"]) / "SlayTheSpire2/logs/godot.log"
        offset = log.stat().st_size
        history = []
        protocol = ('返答はJSON。speechはその場での実際の発言（無言なら空文字）、actionsはこの画像の座標で'
                    'click x y、hover x y、drag x1 y1 x2 y2、scroll x y 量 の文字列配列。'
                    '画面が切り替わるときは結果を観察してから次を選ぶ。waitは操作後に待つ秒数。'
                    'modeはローカルの一人用またはそれを選択できる画面ならsolo、他はunknownまたはonline。')
        started = time.monotonic()
        step = 0
        while not minutes or time.monotonic() - started < minutes * 60:
            step += 1
            AgentLoop._focus_window("window", title)
            with log.open("rb") as file:
                file.seek(offset)
                changes = file.read().decode("utf-8", errors="replace")
                offset = file.tell()
            result = chapter_result(changes)
            if result:
                await asyncio.sleep(2)
            frame = screen.capture_fresh("window", title)
            image = folder / f"step-{step:04d}.jpg"
            frame.save(image, quality=90)
            await event("THINKING: " + str(step))
            prompt = instructions + "\n" + protocol + "\n" + DELIVERY_PROMPT + "\n今回の自分の発言と操作：" + json.dumps(history[-18:], ensure_ascii=False)
            if step == 1:
                prompt += "\nいまタイトル画面でプレイAIの出番が始まりました。視聴者に挨拶してから始めてください。"
            if result:
                prompt += "\n今回の決着：" + result + "。今の画面で自分の実況を締めて、操作は終了。"
            for retry in range(3):
                try:
                    reply = await asyncio.to_thread(model._call, prompt, screen.pil_to_base64(frame), self.model, expect_json=True)
                    if not isinstance(reply, dict) or not isinstance(reply.get("speech"), str) or not isinstance(reply.get("actions"), list):
                        raise ValueError("Missing speech or actions")
                    break
                except (RuntimeError, ValueError, subprocess.TimeoutExpired):
                    if retry == 2:
                        raise
                    await asyncio.sleep(1)
            actions = reply["actions"] if not result and reply.get("mode") == "solo" else []
            with (folder / "decisions.jsonl").open("a", encoding="utf8") as trace:
                trace.write(json.dumps(dict(step=step, reply=reply, game=changes, result=result), ensure_ascii=False) + "\n")
            await event(dict(type="narration", message=reply["speech"], delivery=reply.get("delivery", ""), planned_actions=[dict(command=c, reason="") for c in actions],
                             greeting=step == 1 or bool(result)))
            if result:
                await event("STATUS: All goals done — " + result)
                return
            if reply.get("mode") == "online":
                raise RuntimeError("Player reported an online game screen")
            results = []
            for cmd in actions:
                if not isinstance(cmd, str) or not re.fullmatch(r"(?:click|hover) \d+ \d+|drag \d+ \d+ \d+ \d+|scroll \d+ \d+ -?\d+", cmd):
                    continue
                vals = list(map(int, cmd.split()[1:]))
                points = vals[:4] if cmd.startswith("drag ") else vals[:2]
                if any(v >= (frame.width, frame.height)[i % 2] for i, v in enumerate(points)):
                    continue
                AgentLoop._focus_window("window", title)
                x, y, width, height = screen.current_region("window", title)
                await event(dict(type="executing", command=cmd))
                status = controller.execute_action(cmd, x, y, width / frame.width, height / frame.height)
                results.append([cmd, status])
                if status != "ok":
                    break
                await asyncio.sleep(.3)
            history.append(dict(speech=reply["speech"], actions=results))
            await asyncio.sleep(min(12, max(0, float(reply.get("wait") or 0))))
        raise RuntimeError("Recording limit reached before the chapter result")
