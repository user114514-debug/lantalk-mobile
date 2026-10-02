# -*- coding: utf-8 -*-
"""
生成国旗 emoji 的 PNG 图片，供消息气泡内图文混排使用。

背景：Flutter/Skia 对 regional-indicator 国旗序列的字体回退存在缺陷
（普通字体里的字母字形会"抢先认领"国旗字符，导致显示成 CN/US 字母，
不应用 Twemoji 的国旗连字）。因此把每面国旗离线渲染成 PNG 打包。

用法：python generate_flag_png.py
输出：assets/flags/<iso 国家码小写>.png，例如 cn.png / us.png
"""
import os
from fontTools.ttLib import TTFont
from fontTools.pens.basePen import BasePen
from PIL import Image, ImageDraw
import uharfbuzz as hb

ROOT = os.path.dirname(os.path.abspath(__file__))
FONT_PATH = os.path.join(ROOT, "assets", "fonts", "Twemoji.ttf")
OUT_DIR = os.path.join(ROOT, "assets", "flags")
TARGET_H = 80          # 输出图片高度（像素），宽度按国旗比例
EM_PX = 160            # 渲染用 em 像素（高清，再缩放）


class PILPen(BasePen):
    def __init__(self, glyph_set, draw, scale, ox, oy, color):
        super().__init__(glyph_set)
        self.draw, self.scale, self.ox, self.oy = draw, scale, ox, oy
        self._color = color
        self.sub = []

    def _xy(self, p):
        return (self.ox + p[0] * self.scale, self.oy - p[1] * self.scale)

    def _moveTo(self, p):
        if self.sub:
            self._flush()
        self.sub = [self._xy(p)]

    def _lineTo(self, p):
        self.sub.append(self._xy(p))

    @staticmethod
    def _q(a, b, c, t):
        m = 1 - t
        return (m*m*a[0] + 2*m*t*b[0] + t*t*c[0],
                m*m*a[1] + 2*m*t*b[1] + t*t*c[1])

    @staticmethod
    def _c(a, b, c, d, t):
        m = 1 - t
        return (m**3*a[0] + 3*m*m*t*b[0] + 3*m*t*t*c[0] + t**3*d[0],
                m**3*a[1] + 3*m*m*t*b[1] + 3*m*t*t*c[1] + t**3*d[1])

    def _inv(self, px):
        return ((px[0] - self.ox) / self.scale, (self.oy - px[1]) / self.scale)

    def _qCurveToOne(self, p1, p2):
        a = self._inv(self.sub[-1])
        b, c = self._inv(self._xy(p1)), self._inv(self._xy(p2))
        for i in range(1, 25):
            self.sub.append(self._xy(self._q(a, b, c, i / 24)))

    def _curveToOne(self, p1, p2, p3):
        a = self._inv(self.sub[-1])
        b, c, d = self._inv(self._xy(p1)), self._inv(self._xy(p2)), self._inv(self._xy(p3))
        for i in range(1, 28):
            self.sub.append(self._xy(self._c(a, b, c, d, i / 28)))

    def _closePath(self):
        if len(self.sub) >= 3:
            self._flush()
        self.sub = []

    def _endPath(self):
        self._flush()
        self.sub = []

    def _flush(self):
        if len(self.sub) >= 2:
            self.draw.polygon(self.sub, fill=self._color)


def main():
    os.makedirs(OUT_DIR, exist_ok=True)
    f = TTFont(FONT_PATH)
    upem = f["head"].unitsPerEm
    colr = f["COLR"]
    pal = f["CPAL"].palettes[0]
    glyph_set = f.getGlyphSet()

    hbface = hb.Face(hb.Blob(open(FONT_PATH, "rb").read()))
    hbfont = hb.Font(hbface)
    hbfont.scale = (upem, upem)

    scale = EM_PX / upem
    count = 0
    for a in range(26):
        for b in range(26):
            code = chr(65 + a) + chr(65 + b)
            text = chr(0x1F1E6 + a) + chr(0x1F1E6 + b)
            buf = hb.Buffer()
            buf.add_str(text)
            buf.guess_segment_properties()
            hb.shape(hbfont, buf)
            gids = [g.codepoint for g in buf.glyph_infos]
            if len(gids) != 1 or gids[0] == 0:
                continue
            gid = gids[0]
            name = f.getGlyphName(gid)
            layers = colr.ColorLayers.get(name)
            if not layers:
                continue

            W, H = int(EM_PX * 1.4), EM_PX
            img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
            draw = ImageDraw.Draw(img)
            ox, oy = EM_PX * 0.18, EM_PX
            for rec in layers:
                ci = rec.colorID
                if ci == 0xFFFF:
                    color = (0, 0, 0, 255)
                else:
                    # fontTools CPAL Color 字段顺序为 (blue, green, red, alpha)
                    cc = pal[ci]
                    color = (cc.red, cc.green, cc.blue, cc.alpha)
                pen = PILPen(glyph_set, draw, scale, ox, oy, color)
                glyph_set[rec.name].draw(pen)

            box = img.getbbox()
            if not box:
                continue
            pad = 3
            l, t, rr, bb2 = box
            l = max(0, l - pad); t = max(0, t - pad)
            rr = min(W, rr + pad); bb2 = min(H, bb2 + pad)
            img = img.crop((l, t, rr, bb2))
            w2 = max(1, round(img.width * TARGET_H / img.height))
            img = img.resize((w2, TARGET_H), Image.LANCZOS)
            img.save(os.path.join(OUT_DIR, code.lower() + ".png"))
            count += 1
    print(f"generated {count} flag PNGs -> {OUT_DIR}")


if __name__ == "__main__":
    main()
