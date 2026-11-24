"""Bind a researched game to its installed window, prove an input, and verify its ending."""
from dataclasses import replace
import os
from pathlib import Path
import re
import subprocess
import time

import cv2
from PIL import Image, ImageChops

from llm import LLMIntegration
from solo_play import MODE_PROMPT, input_block


class NotPlayable(RuntimeError):
    """This candidate cannot be prepared on the current machine."""


def steam_root():
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam") as key:
            return Path(winreg.QueryValueEx(key, "SteamPath")[0])
    except FileNotFoundError as exc:
        raise NotPlayable("このアカウントのSteamライブラリがありません。") from exc


def steam_install(app_id):
    steam = steam_root()
    vdf = steam / "steamapps/libraryfolders.vdf"
    libraries = [steam]
    if vdf.is_file():
        libraries.extend(Path(s.replace("\\\\", "\\")) for s in
                         re.findall(r'"path"\s*"([^"]+)"', vdf.read_text(encoding="utf-8-sig")))
    for library in dict.fromkeys(libraries):
        apps = library / "steamapps"
        manifest = apps / f"appmanifest_{app_id}.acf"
        if manifest.is_file():
            match = re.search(r'"installdir"\s*"([^"]+)"', manifest.read_text(encoding="utf-8-sig"))
            if match:
                common = (apps / "common").resolve()
                folder = (common / match[1]).resolve()
                if folder.is_relative_to(common) and folder.is_dir():
                    return folder
    raise NotPlayable(f"Steam {app_id} はこのPCに未インストール。")


def process_path(pid):
    import ctypes
    from ctypes import wintypes
    import win32api
    try:
        handle = win32api.OpenProcess(0x1000, False, pid)
    except OSError:
        return None
    try:
        buf = ctypes.create_unicode_buffer(32768)
        size = wintypes.DWORD(len(buf))
        if ctypes.windll.kernel32.QueryFullProcessImageNameW(wintypes.HANDLE(int(handle)), 0, buf, ctypes.byref(size)):
            return Path(buf.value).resolve()
    finally:
        handle.Close()
    return None


def bind_window(game, app_id, timeout):
    import capturekit
    if app_id is not None and (type(app_id) is not int or app_id <= 0):
        raise NotPlayable("Steam App ID が無効。")
    install = steam_install(app_id) if app_id else None

    def find():
        windows = capturekit.list_windows()
        candidates = []
        for w in windows:
            if game.process and w.process_name.casefold() != game.process.casefold():
                continue
            if install:
                executable = process_path(w.pid)
                if not executable or not executable.is_relative_to(install):
                    continue
            elif w.title != game.window:
                continue
            candidates.append(w)
        exact = [w for w in candidates if w.title == game.window]
        valid = exact or candidates
        if len(valid) == 1 and sum(w.title == valid[0].title for w in windows) == 1:
            return valid[0]
        return None

    window = find()
    if window is None and install:
        log = steam_root() / "logs/console_log.txt"
        offset = log.stat().st_size if log.exists() else 0
        try:
            os.startfile(f"steam://rungameid/{app_id}")
        except OSError as exc:
            raise NotPlayable(f"Steamに起動要求を渡せませんでした（{exc}）。") from exc
        deadline = time.monotonic() + timeout
        while window is None and time.monotonic() < deadline:
            time.sleep(1)
            window = find()
            if window is None and log.is_file():
                with log.open("rb") as stream:
                    stream.seek(offset)
                    rows = stream.read().decode("utf-8", errors="replace").splitlines()
                for line in reversed(rows):
                    if f"AppID {app_id}," in line and "LaunchApp" in line:
                        if "waiting for user response to KickingOtherSession" in line:
                            raise NotPlayable("Steamが別端末のプレイ中断の確認を待っています。")
                        break
    if window is None:
        raise NotPlayable("対象プロセスのゲームウィンドウを一意に特定できません。")
    return replace(game, window=window.title, process=window.process_name), window


class VisualPreparation:
    def __init__(self, provider="codex_cli", model="default", launch_timeout=90, attempts=3,
                 local_launchers=None):
        self.vision = LLMIntegration(provider)
        self.model = model
        self.launch_timeout = launch_timeout
        self.attempts = attempts
        self.local_launchers = local_launchers or {}

    def ask(self, text, image):
        from screen_capture import ScreenCapture
        return self.vision._call(text, ScreenCapture.pil_to_base64(image), self.model, expect_json=True)

    def prepare(self, episode, candidate, folder):
        from input_controller import InputController, set_target_window
        from screen_capture import ScreenCapture, client_rect, find_window
        app_id = episode.game.steam_app_id or candidate.get("steam_app_id")
        if app_id is None and candidate["id"].isdecimal():
            app_id = int(candidate["id"])
        game = episode.game
        launcher = self.local_launchers.get(candidate["id"])
        if not app_id and launcher:
            import capturekit
            game = replace(game, window=launcher["window"], process=launcher["process"])
            def is_open():
                return any(w.title == game.window and w.process_name.casefold() == game.process.casefold()
                           for w in capturekit.list_windows())
            if not is_open():
                # Launch commands come exclusively from local adapter configuration, never the shortlist.
                argv = list(launcher["argv"])
                program = Path(argv[0])
                if not program.is_absolute() and program.parent != Path("."):
                    argv[0] = str(Path(__file__).resolve().parents[1] / program)
                try:
                    subprocess.Popen(argv, shell=False)
                except OSError as exc:
                    raise NotPlayable(f"登録された起動コマンドを実行できませんでした（{exc}）。") from exc
                deadline = time.monotonic() + self.launch_timeout
                while not is_open() and time.monotonic() < deadline:
                    time.sleep(.5)
        game, window = bind_window(game, app_id, self.launch_timeout)
        screen, controller = ScreenCapture(), InputController()
        feedback = ""
        for i in range(self.attempts):
            frame = screen.capture("window", game.window)
            hwnd = frame["hwnd"]
            if hwnd != window.handle:
                raise NotPlayable("試験中に対象ウィンドウが変わりました。")
            before = frame["pil_image"]
            plan = self.ask(f"対象は{game.title}。終了予定は{game.objective}。画像は{before.width}×{before.height}。"
                "いまゲームが入力可能なら、収録前の可逆的な操作を一手だけ選ぶ。"
                "メニューやカードをhover X Yして強調・説明を表示するか、press down/upでメニューの選択を動かす。"
                "タイトルまたは前局の終了画面でスペースで開始・再戦と明記され、セーブ選択も戦略上の選択も伴わない場合だけpress spaceも可。"
                "ロード中や他アプリが見える場合はwait。command、変化するはずの箇所expected、"
                "画面や既知の操作情報からのcontrols、本編の区切りが分かる具体的な画面end_screenをJSONで。"
                f"既知の操作：{game.controls}。前の試行：{feedback}\n{MODE_PROMPT}", before)
            command = str(plan.get("command", "")).strip().lower()
            if plan.get("input_mode") == "loading":
                feedback = "画面を再確認。"
                time.sleep(2)
                continue
            blocked = input_block(plan.get("input_mode"), plan.get("input_target"), command)
            if blocked:
                raise NotPlayable(blocked)
            hover = re.fullmatch(r"hover (\d+) (\d+)", command)
            if not ((hover and int(hover[1]) < before.width and int(hover[2]) < before.height)
                    or command in ("press up", "press down", "press space")):
                feedback = "入力可能な対象を再確認。"
                time.sleep(2)
                continue
            if not all(isinstance(plan.get(k), str) and plan[k].strip()
                       for k in ("expected", "controls", "end_screen")):
                feedback = "確認に必要な情報が不足。"
                continue
            if find_window(game.window)._hWnd != hwnd or client_rect(hwnd) != frame["region"]:
                raise NotPlayable("入力前に対象の位置またはウィンドウが変わりました。")
            set_target_window(hwnd)
            try:
                status = controller.execute_action(command, frame["offset_x"], frame["offset_y"],
                                                   frame["scale_x"], frame["scale_y"])
            finally:
                set_target_window(None)
            if status != "ok":
                feedback = status
                continue
            time.sleep(.65)
            latest = screen.capture("window", game.window)
            after = latest["pil_image"]
            if latest["hwnd"] != hwnd or after.size != before.size:
                raise NotPlayable("試験中に対象画面または解像度が変わりました。")
            before.save(folder / f"before-{i+1}.jpg")
            after.save(folder / f"after-{i+1}.jpg")
            if not ImageChops.difference(before, after).getbbox():
                feedback = f"{command} を送っても画面は変化しませんでした。別の操作を試してください。"
                continue
            pair = Image.new("RGB", (before.width, before.height*2))
            pair.paste(before)
            pair.paste(after, (0, before.height))
            verdict = self.ask(f"上段は操作前、下段は操作後。{command} の期待結果は {plan['expected']}。"
                "対象の選択移動・ハイライト・ツールチップ等が上下で実際に変わったか。"
                "背景アニメ・時刻・マウス位置だけは不可。confirmed(boolean), evidence(具体的な差)をJSONで。", pair)
            feedback = str(verdict.get("evidence", ""))
            if verdict.get("confirmed") is True and feedback.strip():
                game = replace(game, controls=game.controls + "\n" + plan["controls"], end_screen=plan["end_screen"],
                               objective=game.objective + "\n画面で確認する終了条件：" + plan["end_screen"])
                return game, {"command": command, "evidence": feedback, "end_screen": plan["end_screen"],
                              "before": f"before-{i+1}.jpg", "after": f"after-{i+1}.jpg"}
        raise NotPlayable("入力で予期した画面の変化を確認できませんでした。" + feedback)

    def ending(self, episode, source, stop, output):
        if "Time limit" in stop:
            return {"confirmed": False, "evidence": "収録は時間上限で停止。"}
        cap = cv2.VideoCapture(str(source / "game.mkv"))
        frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        fps = cap.get(cv2.CAP_PROP_FPS)
        images = []
        try:
            if frames <= 0 or fps <= 0:
                raise ValueError("録画の長さを取得できず、終了位置を特定できません。")
            for offset in (8, 2, .5):
                cap.set(cv2.CAP_PROP_POS_FRAMES, max(0, frames-1-round(offset*fps)))
                ok, img = cap.read()
                if ok:
                    im = Image.fromarray(cv2.cvtColor(img, cv2.COLOR_BGR2RGB))
                    im.thumbnail((1000, 600))
                    images.append(im)
        finally:
            cap.release()
        if not images:
            raise RuntimeError("録画の終了画面を読み込めません。")
        sheet = Image.new("RGB", (max(i.width for i in images), sum(i.height for i in images)))
        y = 0
        for im in images:
            sheet.paste(im, (0, y))
            y += im.height
        sheet.save(output / "ending.jpg")
        criterion = episode.game.end_screen or episode.game.objective
        result = self.ask(f"{episode.game.title}。収録末尾の場面を上から時系列で確認。終了判定：{criterion}。"
            "判定対象は終端の画面状態。開始時の操作や途中の手順が末尾に写る必要はない。"
            "例えば勝敗を問わない一局なら勝利、敗北のどちらも終了。途中画面・目的と違う結果・不明はfalse。"
            "終了を特定できる画面が見えた場合だけconfirmed=true。confirmed(boolean),"
            " evidence(画面上の具体的な表示), resume_from(次回開始時に確認する場所・状態)をJSONで。", sheet)
        if (type(result.get("confirmed")) is not bool or
                any(not isinstance(result.get(k), str) or not result[k].strip() for k in ("evidence", "resume_from"))):
            raise ValueError("終了判定の応答が不完全です。")
        return result
