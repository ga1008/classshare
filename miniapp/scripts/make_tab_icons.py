# -*- coding: utf-8 -*-
"""生成 tabBar 线性图标（81x81 PNG，普通/选中两态）。

用法：python miniapp/scripts/make_tab_icons.py
输出：miniapp/src/static/tab/{today,tasks,classroom,me,work}[-active].png

设计约定：4 倍尺寸（324px）绘制后缩到 81px 抗锯齿；线宽统一；
普通态灰 #9AA6BF，选中态品牌蓝紫 #5B6EE0（与磨砂玻璃令牌同族）。
"""
from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw

SIZE = 324
OUT_SIZE = 81
LINE = 22
NORMAL = "#9AA6BF"
ACTIVE = "#5B6EE0"
OUT_DIR = Path(__file__).resolve().parent.parent / "src" / "static" / "tab"


def _canvas() -> tuple[Image.Image, ImageDraw.ImageDraw]:
    img = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    return img, ImageDraw.Draw(img)


def draw_today(color: str) -> Image.Image:
    """日历：圆角框 + 顶部双耳 + 中央圆点。"""
    img, d = _canvas()
    d.rounded_rectangle((46, 66, 278, 282), radius=44, outline=color, width=LINE)
    for x in (110, 214):
        d.line((x, 34, x, 96), fill=color, width=LINE)
    d.line((46, 138, 278, 138), fill=color, width=LINE)
    d.ellipse((138, 184, 186, 232), fill=color)
    return img


def draw_tasks(color: str) -> Image.Image:
    """清单：圆角框 + 两行条目 + 对勾。"""
    img, d = _canvas()
    d.rounded_rectangle((54, 40, 270, 284), radius=44, outline=color, width=LINE)
    d.line((104, 118, 224, 118), fill=color, width=LINE)
    d.line((104, 178, 224, 178), fill=color, width=LINE)
    d.line((104, 232, 140, 262), fill=color, width=LINE)
    d.line((140, 262, 214, 214), fill=color, width=LINE)
    return img


def draw_classroom(color: str) -> Image.Image:
    """讲台板：横向白板 + 支架 + 板上两横。"""
    img, d = _canvas()
    d.rounded_rectangle((38, 60, 286, 216), radius=36, outline=color, width=LINE)
    d.line((96, 122, 228, 122), fill=color, width=LINE)
    d.line((96, 168, 180, 168), fill=color, width=LINE)
    d.line((118, 216, 84, 288), fill=color, width=LINE)
    d.line((206, 216, 240, 288), fill=color, width=LINE)
    return img


def draw_me(color: str) -> Image.Image:
    """人像：头 + 肩弧。"""
    img, d = _canvas()
    d.ellipse((112, 40, 212, 140), outline=color, width=LINE)
    d.arc((56, 158, 268, 372), start=180, end=360, fill=color, width=LINE)
    return img


def draw_work(color: str) -> Image.Image:
    """工作台（教师第 4 tab）：公文包。"""
    img, d = _canvas()
    d.rounded_rectangle((42, 108, 282, 278), radius=40, outline=color, width=LINE)
    d.rounded_rectangle((118, 48, 206, 108), radius=22, outline=color, width=LINE)
    d.line((42, 186, 282, 186), fill=color, width=LINE)
    return img


GLYPHS = {
    "today": draw_today,
    "tasks": draw_tasks,
    "classroom": draw_classroom,
    "me": draw_me,
    "work": draw_work,
}


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    for name, fn in GLYPHS.items():
        for suffix, color in (("", NORMAL), ("-active", ACTIVE)):
            img = fn(color).resize((OUT_SIZE, OUT_SIZE), Image.LANCZOS)
            path = OUT_DIR / f"{name}{suffix}.png"
            img.save(path)
            print("wrote", path)


if __name__ == "__main__":
    main()
