"""Assemble video and audio on a single clock; publish the MP4 after ffmpeg closes it."""
import math
import subprocess

import cv2
from gameplay_edit import FFMPEG
from gameplay_subtitles import ASS_HEADER, ass_time, ass_literal
import textwrap

FPS = 24


def subtitles(path, cues, style):
    content = [ASS_HEADER]
    for start, end, line in cues:
        wrapped = r"\N".join(textwrap.wrap(ass_literal(line), 29))
        content.append(f"Dialogue: 0,{ass_time(start)},{ass_time(end)},Voice,,0,0,0,,{style}{wrapped}\n")
    path.write_text("".join(content), encoding="utf-8-sig")


class FFmpegShow:
    def __init__(self, profile_seconds=4, encoder="libx264"):
        if not 1 <= profile_seconds <= 20:
            raise ValueError("profile_seconds must be between 1 and 20")
        self.profile_seconds = profile_seconds
        self.encoder = encoder

    def _scene(self, work, name, design, episode, traits, credits, speech):
        seconds = speech.seconds + 1.10 if speech else self.profile_seconds
        count = math.ceil(seconds * FPS)
        audio = (["-i", str(speech.wav)] if speech else
                 ["-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo"])
        filters = []
        if speech:
            cues = [(a+.40, b+.40, t) for a, b, t in speech.cues]
            subtitles(work / f"{name}.ass", cues, r"{\an2\pos(640,610)\fs30}")
            filters = ["-vf", f"ass={name}.ass"]
        command = [FFMPEG, "-v", "error", "-y", "-f", "rawvideo", "-pixel_format", "rgb24",
                   "-video_size", "1280x720", "-framerate", str(FPS), "-i", "-", *audio,
                   "-map", "0:v", "-map", "1:a", *filters, "-t", str(count/FPS),
                   "-af", "adelay=400:all=1,apad", "-ar", "48000", "-ac", "2",
                   "-c:a", "pcm_s16le", "-c:v", "libx264", "-preset", "veryfast",
                   "-pix_fmt", "yuv420p", f"{name}.mkv"]
        with subprocess.Popen(command, cwd=work, stdin=subprocess.PIPE) as proc:
            written = 0
            try:
                for image in design.frames(name, episode, traits, credits, count):
                    if image.mode != "RGB" or image.size != (1280, 720) or written >= count:
                        raise ValueError(f"Invalid design frame in {name}")
                    proc.stdin.write(image.tobytes())
                    written += 1
            finally:
                proc.stdin.close()
            if proc.wait() or written != count:
                raise RuntimeError(f"Incomplete {name} render ({written}/{count} frames)")
        return count/FPS

    def export(self, work, episode, design, traits, body, duration, opening, closing, aside,
               aside_at, credits, target):
        intro = self._scene(work, "opening", design, episode, traits, credits, opening)
        card = self._scene(work, "profile", design, episode, traits, credits, None)
        end = self._scene(work, "closing", design, episode, traits, credits, closing)
        lengths = [intro, card, duration, end]
        sources = ["opening.mkv", "profile.mkv", str(body), "closing.mkv"]
        args = [FFMPEG, "-v", "error", "-y", "-nostdin"]
        for source in sources:
            args += ["-i", source]
        filters = []
        for i, length in enumerate(lengths):
            video = f"[{i}:v]fps=24,scale=1920:1080:force_original_aspect_ratio=decrease:force_divisible_by=2,pad=1920:1080:(ow-iw)/2:(oh-ih)/2,setsar=1,setpts=PTS-STARTPTS"
            if i == 2 and aside is not None:
                cues = [(aside_at+a, aside_at+b, "管理AI　 " + t) for a, b, t in aside.cues]
                subtitles(work / "aside.ass", cues, r"{\an7\pos(34,86)\fs26\c&HEDF5EA&\3c&H322721&\bord4}")
                video += ",ass=aside.ass"
            filters.append(video + f"[v{i}]")
            filters.append(f"[{i}:a]aresample=48000,aformat=channel_layouts=stereo,apad,"
                           f"atrim=end_sample={round(length*48000)},asetpts=PTS-STARTPTS[a{i}]")
        game_audio = "a2"
        chapters = [(0, "管理AIの挨拶"), (intro, "プレイAI紹介"), (intro+card, "ゲーム実況")]
        if aside is not None:
            args += ["-i", str(aside.wav)]
            # Stream silence rather than allocating a multi-hour adelay buffer.
            filters += [f"anullsrc=r=48000:cl=stereo,atrim=end_sample={round(aside_at*48000)}[sil]",
                        "[4:a]aresample=48000,aformat=channel_layouts=stereo[voice]",
                        "[sil][voice]concat=n=2:v=0:a=1[comment]",
                        "[a2][comment]amix=inputs=2:duration=first:normalize=0,alimiter=limit=0.95:latency=1[mix]"]
            game_audio = "mix"
            chapters.append((intro+card+aside_at, "管理AIの観戦席"))
        chapters.append((intro+card+duration, "管理AIの総評"))
        filters.append(f"[v0][a0][v1][a1][v2][{game_audio}][v3][a3]concat=n=4:v=1:a=1[v][a]")
        metadata = [";FFMETADATA1"]
        for (start, name), (stop, _) in zip(chapters, [*chapters[1:], (sum(lengths), "")]):
            metadata.extend(["[CHAPTER]", "TIMEBASE=1/1000", f"START={round(start*1000)}",
                             f"END={round(stop*1000)}", f"title={name}"])
        (work / "chapters.txt").write_text("\n".join(metadata)+"\n", encoding="utf-8")
        codec = (["-preset", "veryfast", "-crf", "20"] if self.encoder == "libx264" else [])
        subprocess.run([*args, "-f", "ffmetadata", "-i", "chapters.txt",
                        "-filter_complex", ";".join(filters), "-map", "[v]", "-map", "[a]",
                        "-map_chapters", str(5 if aside else 4), "-c:v", self.encoder, *codec,
                        "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart",
                        "assembled.mp4"], cwd=work, check=True)
        result = work / "assembled.mp4"
        video = cv2.VideoCapture(str(result))
        count = video.get(cv2.CAP_PROP_FRAME_COUNT)
        video.release()
        if abs(count - round(sum(lengths)*FPS)) > 2:
            raise RuntimeError("Exported frame count differs from the edit timeline")
        result.replace(target)
