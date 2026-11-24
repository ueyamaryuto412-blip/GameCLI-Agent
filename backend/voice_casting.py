"""Historical casting for current Gemini voices and archived Supertonic shortlists."""
from urllib.parse import urlsplit


# Approximate pitch percentiles (20/50/80, Hz) measured on one shared Japanese line.
PITCH_REFERENCE = {
    "M1": [123, 152, 181], "M2": [82, 87, 106], "M3": [94, 97, 101],
    "M4": [108, 121, 149], "M5": [80, 94, 104], "F1": [180, 192, 213],
    "F2": [184, 205, 259], "F3": [170, 226, 245], "F4": [188, 196, 226],
}

GEMINI_VOICES = {"Algenib": "少しかすれた響き", "Gacrux": "成熟した響き", "Algieba": "滑らか",
                 "Orus": "芯がある", "Enceladus": "息を含む", "Iapetus": "明瞭",
                 "Sulafat": "温かい", "Achernar": "柔らかい", "Puck": "軽快", "Kore": "きっぱり"}

DELIVERY_PROMPT = ('台詞と一緒に任意の delivery を返せます。今の一言を自分はどう口にするか、'
                   '間、声量、語尾など必要なときだけ短く指定する。自然な地の口調なら空文字。'
                   '演技指示は台詞に混ぜず、人物の声は同じまま。')


def validate_cast(cast):
    if not isinstance(cast, dict) or not all(isinstance(cast.get(k), str) and cast[k].strip()
            for k in ("source_title", "source_url", "evidence", "interpretation")):
        raise ValueError("声の選定には資料名・URL・史料の記述・演技の解釈が必要です。")
    url = urlsplit(cast["source_url"])
    if url.scheme not in ("https", "http") or not url.hostname:
        raise ValueError("声の根拠資料に有効なURLが必要です。")
    choices = cast.get("candidates")
    if not isinstance(choices, list) or not 2 <= len(choices) <= 3:
        raise ValueError("声の候補を推薦順に2〜3件指定してください。")
    seen = set()
    catalog = GEMINI_VOICES if cast.get("engine") == "gemini" else PITCH_REFERENCE
    if cast.get("engine", "supertonic") not in ("supertonic", "gemini"):
        raise ValueError("Unknown casting engine")
    for choice in choices:
        if not isinstance(choice, dict):
            raise ValueError("音声候補の形式が無効です。")
        style = choice.get("style")
        speed = choice.get("speed")
        if (not isinstance(style, str) or style not in catalog or style in seen
                or type(speed) not in (float, int) or not .85 <= speed <= 1.15
                or not isinstance(choice.get("reason"), str) or not choice["reason"].strip()):
            raise ValueError("候補には異なる登録音声、0.85〜1.15の速度、選定理由が必要です。")
        seen.add(style)


def studio_for_pick(studio, selection):
    if not selection or "voice_cast" not in selection:
        return studio
    cast = selection["voice_cast"]
    validate_cast(cast)
    rank = selection.get("voice_pick", cast.get("selected", 1))
    if type(rank) is not int or not 1 <= rank <= len(cast["candidates"]):
        raise ValueError("声の候補番号が範囲外です。")
    best = cast["candidates"][rank-1]
    spec = studio.get("player_voice", {})
    if spec.get("factory") == "gemini_tts:GeminiVoice":
        options = dict(spec.get("options", {}))
        if cast.get("engine") == "gemini" and not options.get("voice", "").startswith(("voice_", "voicekey_")):
            options["voice"] = best["style"]
        directions = (options.get("style"), cast["interpretation"], best.get("base_direction"),
                      f"話す速さは通常の約{best['speed']}倍を目安にする。")
        options["style"] = " ".join(part for part in directions if part)
        return {**studio, "player_voice": {**spec, "options": options}}
    if cast.get("engine") == "gemini":
        return studio
    return {**studio, "player_voice": {"factory": "supertonic_host:PlayerVoice",
                                       "options": {"style": best["style"], "speed": best["speed"]}}}
