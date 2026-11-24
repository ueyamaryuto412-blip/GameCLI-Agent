"""The channel's gameplay-mode rule, shared by casting, preparation and the default player."""
import re


SOLO_RULE = ("実況はローカルのソロプレイのみ。CPU戦は可。オンライン対戦・協力、ロビーへの参加、"
             "マッチング、共有ランキングへのスコア送信は行わない。")
MODES = ("local_solo", "select_solo", "loading", "online", "unknown")
MODE_PROMPT = (SOLO_RULE + "\n各応答のJSONに input_mode と input_target を含める。"
               "画面と今回実際に入ったモードから、いまの操作がローカル一人用ならlocal_solo、"
               "メニューで一人用の入口を明確に選べるならselect_solo。オンラインの項目が隣に見えるだけなら選択可。"
               "読み込み待ちはloading、オンラインのセッション中またはそこに入る操作はonline、"
               "区別がつかない場合はunknown。ソロ対応という作品情報だけで現在のモードを推測しない。"
               "input_targetには次の入力対象の表示名または盤面の対象を短く書く。入力は画面ごとに一手。")
ONLINE_TARGET = re.compile(
    r"オンライン(?:対戦|協力|プレイ|マルチ)|マルチプレイ|マッチ(?:メイク|ング)|ロビー(?:作成|参加)|"
    r"^(?:オンライン|通信対戦|通信協力|ロビー|協力プレイ)$|"
    r"\b(?:multiplayer|matchmaking|play online|online play|co-?op|(?:join|create|host) (?:a )?(?:lobby|server))\b|^online$",
    re.I)


def input_block(mode, target, command):
    """Return the reason to stop, or None for an eligible single decision."""
    if mode == "loading" and (not command or re.fullmatch(r"wait (?:\d+(?:\.\d+)?|\.\d+)", command)):
        return None
    if mode not in ("local_solo", "select_solo"):
        return "ローカルのソロプレイを確認できません。"
    if command:
        if not isinstance(target, str) or not target.strip():
            return "次の入力対象が特定されていません。"
        if ONLINE_TARGET.search(target.strip()):
            return f"オンライン項目が入力対象です（{target}）。"
        if command.partition(" ")[0].lower() == "run_macro":
            return "一手ずつ確認できないマクロ操作です。"
    return None
