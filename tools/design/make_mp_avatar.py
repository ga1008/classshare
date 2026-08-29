# -*- coding: utf-8 -*-
"""LanShare蓝享 小程序头像生成器（1024x1024 PNG）。

设计语义（一形三义）：
- 展开的书  = 课堂 / 学习
- 三道波纹  = 「享」——知识像信号一样分享出去
- 整体轮廓  = 展开的翅膀，呼应域名 guardianangel（守护天使）
配色 = 小程序磨砂玻璃 UI 的品牌渐变 #5b8cff → #7c4fd0（「蓝」享）。

4x 超采样绘制后缩回 1024，保证圆角与弧线平滑。
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter

SS = 4  # supersample
SIZE = 1024
W = SIZE * SS

C_TL = (91, 140, 255)   # #5b8cff
C_BR = (124, 79, 208)   # #7c4fd0
WHITE = (255, 255, 255)

OUT = Path(__file__).resolve().parents[2] / "miniapp" / "design" / "avatar-1024.png"


def lerp(a: float, b: float, t: float) -> float:
    return a + (b - a) * t


def build_gradient() -> Image.Image:
    """对角线性渐变 + 左上柔和高光 + 底部轻压暗。"""
    img = Image.new("RGB", (W, W))
    px = img.load()
    for y in range(W):
        for x in range(0, W, 4):  # 每 4 像素取样一次再横向填充，提速
            t = (x + y) / (2 * W)
            r = int(lerp(C_TL[0], C_BR[0], t))
            g = int(lerp(C_TL[1], C_BR[1], t))
            b = int(lerp(C_TL[2], C_BR[2], t))
            for dx in range(4):
                if x + dx < W:
                    px[x + dx, y] = (r, g, b)

    # 左上柔光（磨砂玻璃的"受光面"）
    glow = Image.new("L", (W, W), 0)
    gd = ImageDraw.Draw(glow)
    gd.ellipse((-W * 0.35, -W * 0.45, W * 0.75, W * 0.55), fill=70)
    glow = glow.filter(ImageFilter.GaussianBlur(W * 0.12))
    img = Image.composite(Image.new("RGB", (W, W), WHITE), img, glow)

    # 底部轻压暗，增加体积感
    shade = Image.new("L", (W, W), 0)
    sd = ImageDraw.Draw(shade)
    sd.ellipse((-W * 0.2, W * 0.55, W * 1.2, W * 1.5), fill=48)
    shade = shade.filter(ImageFilter.GaussianBlur(W * 0.12))
    img = Image.composite(Image.new("RGB", (W, W), (36, 30, 90)), img, shade)
    return img


def rounded_mask(radius_ratio: float = 0.225) -> Image.Image:
    mask = Image.new("L", (W, W), 0)
    d = ImageDraw.Draw(mask)
    d.rounded_rectangle((0, 0, W - 1, W - 1), radius=int(W * radius_ratio), fill=255)
    return mask


def draw_glyph(img: Image.Image) -> None:
    d = ImageDraw.Draw(img, "RGBA")
    cx = W / 2
    base_y = W * 0.665          # 书本水平中线
    stroke = int(W * 0.030)     # 弧线笔宽

    # ---- 展开的书（两片对称页面，微微上扬似翅膀）----
    page_w = W * 0.235
    page_h = W * 0.128
    lift = W * 0.052            # 外缘上扬量（翅膀感）
    gap = W * 0.012             # 书脊留缝

    def page(sign: int) -> list[tuple[float, float]]:
        # sign=-1 左页, +1 右页；从书脊向外：内下 → 外下 → 外上(上扬) → 内上
        x0 = cx + sign * gap
        x1 = cx + sign * (gap + page_w)
        return [
            (x0, base_y + page_h * 0.5),
            (x1, base_y + page_h * 0.5 - lift),
            (x1, base_y - page_h * 0.5 - lift),
            (x0, base_y - page_h * 0.5),
        ]

    for sign in (-1, 1):
        d.polygon(page(sign), fill=WHITE + (255,))

    # 页面底部投影层，增加层次
    d.polygon(
        [
            (cx - gap - page_w, base_y + page_h * 0.5 - lift),
            (cx + gap + page_w, base_y + page_h * 0.5 - lift),
            (cx + gap + page_w * 0.92, base_y + page_h * 0.5 - lift + W * 0.018),
            (cx - gap - page_w * 0.92, base_y + page_h * 0.5 - lift + W * 0.018),
        ],
        fill=(255, 255, 255, 90),
    )

    # ---- 三道分享波纹（自书脊上方展开，渐远渐淡）----
    arc_cy = base_y - page_h * 0.62
    radii = (W * 0.088, W * 0.152, W * 0.216)
    alphas = (255, 205, 150)
    for radius, alpha in zip(radii, alphas):
        bbox = (cx - radius, arc_cy - radius, cx + radius, arc_cy + radius)
        d.arc(bbox, start=214, end=326, fill=WHITE + (alpha,), width=stroke)

    # 波纹圆心一点（信号源 = 书）
    dot_r = W * 0.014
    d.ellipse(
        (cx - dot_r, arc_cy - dot_r - W * 0.012, cx + dot_r, arc_cy + dot_r - W * 0.012),
        fill=WHITE + (255,),
    )


def main() -> None:
    img = build_gradient()
    draw_glyph(img)

    out = Image.new("RGBA", (W, W), (0, 0, 0, 0))
    out.paste(img, (0, 0), rounded_mask())
    out = out.resize((SIZE, SIZE), Image.LANCZOS)

    OUT.parent.mkdir(parents=True, exist_ok=True)
    out.save(OUT, "PNG")
    print(f"saved: {OUT} ({OUT.stat().st_size / 1024:.0f} KB)")


if __name__ == "__main__":
    main()
