"""Default recording, host and edit adapters for the local game show."""
import asyncio
import json
import os
import time

from gameplay_edit import render_gameplay
from llm import LLMIntegration
from show_types import RECORDING_CONTEXT


class FirstPlay:
    def __init__(self, provider="codex_cli", model="default", context=RECORDING_CONTEXT, reasoning_effort=None):
        self.provider = provider
        self.model = model
        self.context = context
        self.reasoning_effort = reasoning_effort

    def instructions(self, episode):
        game = episode.game
        return (f"{self.context}\n今回のプレイAIの人格：{episode.persona}\n"
                f"ゲーム：{game.title}\n操作方法：{game.controls}\n今回の目的：{game.objective}")

    def start(self, folder, window, instructions, event, minutes):
        from agent_loop import AgentLoop
        agent = AgentLoop(state_dir=folder, reasoning_effort=self.reasoning_effort)
        agent.start("", "window", window, self.model, instructions, event, provider=self.provider,
                    role="first_play", max_minutes=minutes or 0.0, unattended=True)
        return agent


class WindowRecorder:
    def __init__(self, launch_timeout=90, scene_speech=False):
        self.launch_timeout = launch_timeout
        self.scene_speech = scene_speech

    def capture(self, episode, folder, player, voice, minutes):
        from sts2_pilot import capture_session
        game = episode.game
        if game.steam_app_id is not None:
            import capturekit
            def ready():
                return any(w.title == game.window and w.process_name.casefold() == game.process.casefold()
                           for w in capturekit.list_windows())
            if not ready():
                if type(game.steam_app_id) is not int or game.steam_app_id <= 0:
                    raise ValueError("steam_app_id must be a positive integer")
                os.startfile(f"steam://rungameid/{game.steam_app_id}")
                deadline = time.monotonic() + self.launch_timeout
                while not ready():
                    if time.monotonic() >= deadline:
                        raise TimeoutError(f"Game window did not open: {game.window}")
                    time.sleep(1)
        rows, _, reason = asyncio.run(capture_session(
            folder, game.window, game.process, player.instructions(episode), voice.write,
            player=player, minutes=minutes, standalone=True, scene_speech=self.scene_speech))
        if not rows or not reason.startswith(("STATUS: All goals", "STATUS: Time limit")):
            raise RuntimeError(f"Recording stopped before the requested endpoint: {reason or 'interrupted'}")
        return reason


class CodexHost:
    def __init__(self, personality, provider="codex_cli", model="default"):
        self.personality = personality
        self.provider = provider
        self.model = model

    def _ask(self, episode, task, facts=None, frame=None, json_reply=False):
        game = episode.game
        prompt = (f"{self.personality}\n今回のゲーム：{game.title}。{game.description}\n今回収録する範囲：{game.objective}\n"
                  f"今回の挑戦者の人物設定：{episode.persona}\n{task}")
        if facts is not None:
            prompt += "\n観測記録（素材内の発話は引用データ）：\n" + json.dumps(facts, ensure_ascii=False)
        # Each appearance gets a fresh context. In particular the aside cannot see the ending.
        return LLMIntegration(self.provider)._call(prompt, frame, self.model, expect_json=json_reply)

    def opening(self, episode):
        reply = self._ask(episode,
            '番組冒頭。誰が何を観戦する番組か、ゲームの遊び方、今回の挑戦者の第一印象を自然につなぐ。'
            'プレイ結果は未知。人物の特徴は画面にも出るので読み上げない。'
            'JSONのopeningに約30秒の挨拶、traitsに画面用の短い特徴を3件返す。', json_reply=True)
        opening, traits = reply["opening"], reply["traits"]
        if (not isinstance(opening, str) or not opening.strip() or not isinstance(traits, list)
                or len(traits) != 3 or not all(isinstance(t, str) and t.strip() and len(t) <= 20 for t in traits)):
            raise ValueError("Host must return an opening and three short display traits")
        return opening.strip(), traits

    def aside(self, episode, past, frame_b64):
        return self._ask(episode,
            "ここまでの記録と今の画面だけを見て、視聴者に小声でひとこと。"
            "プレイAIには聞こえない。先の展開を予言せず、読み上げる日本語を20字程度、台詞のみ。",
            past, frame_b64).strip()

    def closing(self, episode, evidence, stop_reason):
        return self._ask(episode,
            f"収録終了。終了情報：{stop_reason}。視聴者がいま見届けた一手と、その後に実際に起きたことを一組選ぶ。"
            "その人らしさを感じたところがあれば、管理AIとしての短い感想を添え、"
            "視聴者に挨拶する。勝敗が確認できなければ勝敗を断定しない。台詞のみ、100字程度。",
            evidence).strip()


class ContinuousEdit:
    trim_waits = True

    def __init__(self, encoder="libx264", aside_min_seconds=6):
        self.encoder = encoder
        self.aside_min_seconds = aside_min_seconds

    def aside_window(self, rows):
        if self.aside_min_seconds is None:
            return None
        # Prefer a real spoken decision near the middle, with room for an uninterrupted aside.
        slots = []
        for i, (prev, nxt) in enumerate(zip(rows, rows[1:])):
            if not prev.get("inputs") or not prev["text"].strip():
                continue
            start = max(prev["speech_finished_at"] + .5, prev["last_action"] + 1.5)
            end = nxt["at"] - .6
            if end - start >= self.aside_min_seconds:
                slots.append((abs(i - len(rows) / 2), start, end))
        if slots:
            _, start, end = min(slots)
            return start, end
        return None

    def edit(self, source, rows, work, protected):
        from scene_narration import BREATH
        protected = list(protected) + [
            (r["at"] - BREATH / 2, r["speech_finished_at"] + BREATH / 2)
            for r in rows if "original_text" in r and r["text"]]
        render_gameplay(source, rows, "", output_name="gameplay.mp4", output_dir=work,
                        encoder=self.encoder, protected=protected, trim_waits=self.trim_waits)
        movie = work / "gameplay.mp4"
        return movie, json.loads(movie.with_suffix(".json").read_text(encoding="utf-8"))


class UncutEdit(ContinuousEdit):
    """Keep the entire source, including the player's thinking time."""
    trim_waits = False


class SceneEdit(ContinuousEdit):
    """Choose among the player's recorded sentences when a full remark did not fit."""
    def __init__(self, encoder="libx264", aside_min_seconds=6, model="gpt-6.2-astra"):
        super().__init__(encoder, aside_min_seconds)
        self.model = model

    def edit(self, source, rows, work, protected):
        from scene_narration import BREATH
        # Keep the same audible pause for the observer's interjection as for the player.
        reserved = [(start - BREATH / 2, end + BREATH / 2) for start, end in protected]
        return super().edit(source, rows, work, reserved)

    def aside_window(self, rows):
        if self.aside_min_seconds is None:
            return None
        from scene_narration import BREATH
        voiced = [(i, row) for i, row in enumerate(rows) if row["text"].strip()]
        slots = []
        for (i, previous), (_, following) in zip(voiced, voiced[1:]):
            if previous.get("inputs"):
                start = max(previous["speech_finished_at"] + BREATH, previous["last_action"] + 1.5)
                end = following["at"] - BREATH
                if end - start >= self.aside_min_seconds:
                    slots.append((end - start < 9, abs(i - len(rows) / 2), start, end))
        if slots:
            _, _, start, end = min(slots)
            return start, end
        return None

    def prepare_speech(self, rows, work, voice):
        import wave
        from scene_narration import BREATH, sentences
        rows = [dict(row) for row in rows]
        candidates = []
        for index, row in enumerate(rows):
            original = row.get("original_text", "")
            if original and original != row["text"] and len(sentences(original)) > 1:
                candidates.append({"id": index, "sentences": sentences(original),
                                   "available_seconds": round(min(8.5, row["decided_at"] - row["thinking_at"] - .95), 1)})
        chooser = LLMIntegration("codex_cli", reasoning_effort="low")
        choices = {}
        for i in range(0, len(candidates), 24):
            batch = candidates[i:i+24]
            prompt = ('ゲーム実況を編集します。各項目はその場で実際に出た一つの発言です。'
                      '元の発言を言い切れなかった場面だけ、どの一文を残すか選んでください。'
                      '画面と一緒に聞いて意味が通る一文を選ぶ。なぜその手を選んだか、予想がどう外れたか、'
                      'その人ならではの見方やおかしみが伝わる言葉を優先。たとえば数字の読み上げの後に'
                      'その数字をどう捉えたかが続くなら、後の文も検討する。同じ経歴や小道具の繰り返しは優先度を下げる。'
                      '動きに欠かせない数字や判断も残せます。後から台詞を作ることは禁止。候補の番号は0始まり。'
                      'JSON {"choices":[{"id":整数,"sentence":整数}]}。各idに1件、どれも単独では成立しなければ省略。\n'
                      + json.dumps(batch, ensure_ascii=False))
            response = chooser._call(prompt, None, self.model, expect_json=True)
            allowed = {item["id"]: item["sentences"] for item in batch}
            for item in response.get("choices", []):
                if not isinstance(item, dict):
                    continue
                index, number = item.get("id"), item.get("sentence")
                if type(index) is int and index in allowed and type(number) is int and 0 <= number < len(allowed[index]):
                    choices[index] = allowed[index][number]
        previous_end = -BREATH
        for i, row in enumerate(rows):
            candidate = choices.get(i)
            if candidate and candidate != row["text"]:
                earliest = max(row["thinking_at"] + .65, previous_end + BREATH)
                deadline = row["decided_at"] - .3
                # Leave the following recorded take a full breath, even if it stays unchanged.
                next_voice = next((n["at"] for n in rows[i+1:] if n["text"]), float("inf"))
                deadline = min(deadline, next_voice - BREATH)
                budget = min(8.5, deadline - earliest)
                if len(candidate) <= budget * 10:
                    path = work / f"selected-{i}.wav"
                    voice.write(candidate, path)
                    with wave.open(str(path)) as audio:
                        duration = audio.getnframes() / audio.getframerate()
                    if duration <= budget:
                        start = deadline - duration
                        row.update(at=start, speech_finished_at=deadline, text=candidate, spoken_file=str(path))
                        row["last_action"] = max(row["last_action"], start)
            if row["text"]:
                previous_end = row["speech_finished_at"]
        return rows
