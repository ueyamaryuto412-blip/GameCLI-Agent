"""Shared subtitles for live footage and scripted edits."""
import textwrap


def ass_time(seconds):
    cs = round(seconds * 100)
    return f"{cs // 360000}:{cs // 6000 % 60:02}:{cs // 100 % 60:02}.{cs % 100:02}"


def ass_literal(text):
    return text.replace("{", "｛").replace("}", "｝").replace(chr(92), "＼")


ASS_HEADER = """[Script Info]
ScriptType: v4.00+
PlayResX: 1280
PlayResY: 720
WrapStyle: 2
ScaledBorderAndShadow: yes
[V4+ Styles]
Format: Name,Fontname,Fontsize,PrimaryColour,SecondaryColour,OutlineColour,BackColour,Bold,Italic,Underline,StrikeOut,ScaleX,ScaleY,Spacing,Angle,BorderStyle,Outline,Shadow,Alignment,MarginL,MarginR,MarginV,Encoding
Style: Voice,Yu Gothic,38,&H00EDFFF3,&H000000FF,&H00233618,&H90000000,-1,0,0,0,100,100,0,0,1,3,1,8,28,28,66,1
Style: Credit,Yu Gothic,23,&H00FFFFFF,&H000000FF,&H00202020,&H80000000,-1,0,0,0,100,100,0,0,1,2,0,3,24,24,20,1
Style: Title,Yu Gothic,29,&H00FAFFF4,&H000000FF,&H552A2814,&H552A2814,-1,0,0,0,100,100,0,0,3,9,0,1,38,38,36,1
[Events]
Format: Layer,Start,End,Style,Name,MarginL,MarginR,MarginV,Effect,Text
"""


def voice_event(text, start, end):
    # Treat generated narration as literal subtitle text, including braces.
    plain = ass_literal(text)
    rows = [line for paragraph in plain.splitlines() for line in textwrap.wrap(paragraph, 28)]
    body = r"\N".join(rows)
    return f"Dialogue: 0,{ass_time(start)},{ass_time(end)},Voice,,0,0,0,,{body}\n"


def label_event(style, text, start, end):
    return f"Dialogue: 1,{ass_time(start)},{ass_time(end)},{style},,0,0,0,,{ass_literal(text)}\n"


def subtitles(path, text, offset, voice_seconds, length, credit, title=""):
    events = voice_event(text, offset, min(length, offset + voice_seconds + .15))
    if title:
        events += label_event("Title", title, 0, min(5, length))
    if credit:
        events += label_event("Credit", credit, 0, length)
    path.write_text(ASS_HEADER + events, encoding="utf-8-sig")
