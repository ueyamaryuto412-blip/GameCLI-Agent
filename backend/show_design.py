"""Default replaceable 1280×720 studio graphics, with no dependency on a particular game."""
import os
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont


class Observatory:
    def __init__(self, title="AIゲーム観測室", font=None):
        self.title = title
        self.font = str(font or Path(os.environ["WINDIR"]) / "Fonts/YuGothB.ttc")

    def _text(self, draw, xy, text, size, color, max_width):
        while size > 12:
            face = ImageFont.truetype(self.font, size)
            if draw.textlength(text, font=face) <= max_width:
                break
            size -= 1
        draw.text(xy, text, font=ImageFont.truetype(self.font, size), fill=color, anchor="lt")

    def frames(self, scene, episode, traits, credits, count):
        base = Image.new("RGB", (1280, 720), (9, 18, 27))
        draw = ImageDraw.Draw(base)
        pale, mint, dim = "#e5efec", "#76d6ca", "#9cabae"
        draw.line((55, 76, 1225, 76), fill="#32474e")
        draw.line((55, 640, 1225, 640), fill="#32474e")
        self._text(draw, (60, 34), self.title, 22, pale, 620)
        self._text(draw, (68, 172), {"opening": "観測対象", "profile": "今回のプレイAI",
                                     "closing": "管理AIの総評"}[scene], 26, mint, 600)
        if scene == "profile":
            for i, trait in enumerate(traits):
                self._text(draw, (73, 241 + 72*i), "•  " + trait, 38, pale, 640)
        else:
            self._text(draw, (61, 245), episode.game.title, 70, pale, 650)
        self._text(draw, (62, 661), credits if scene == "closing" else "ゲーム実況　／　観戦記録",
                   19, dim, 1160)
        background = np.asarray(base)
        rng = np.random.default_rng(41)
        azimuth = rng.uniform(0, 2*np.pi, 9600)
        height = rng.uniform(-1, 1, len(azimuth))
        latitude = np.arcsin(height)
        radius = np.sqrt(1 - height**2)
        color = np.array([255, 194, 116] if scene == "profile" else [147, 248, 229])
        for i in range(count):
            angle = i / 130
            warp = 1 + .13*np.sin(3*azimuth + 4*latitude + angle) + .05*np.sin(8*latitude - angle)
            x = radius*np.cos(azimuth + angle)*warp
            depth = radius*np.sin(azimuth + angle)*warp
            y = height*warp*.89 + depth*.45
            px = (932 + (x*.97 - y*.25)*175).astype(int)
            py = (342 + (x*.25 + y*.97)*175).astype(int)
            intensity = (.39 + .55*(depth+1)/2) * (.45 + .55*np.cos(9*latitude+3*azimuth)**2)
            buf = background.copy()
            buf[py, px] = np.clip(color * intensity[:, None], 0, 255)
            yield Image.fromarray(buf)
