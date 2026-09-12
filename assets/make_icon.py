"""生成应用图标，并支持输出方案对比图。

用法：
    python assets/make_icon.py                  # 把默认方案写成 icon.ico
    python assets/make_icon.py cat              # 指定方案写成 icon.ico
    python assets/make_icon.py --preview        # 输出各方案对比图，不改 icon.ico
"""

from __future__ import annotations

import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

HERE = Path(__file__).parent
ICO = HERE / "icon.ico"
CANDIDATES = HERE / "icon-candidates"
PREVIEW = HERE / "icon-preview.png"

PURPLE = (168, 85, 247)
PINK = (236, 72, 153)
DEEP = (21, 14, 42)
DEEP_2 = (15, 10, 30)

ICO_SIZES = [(256, 256), (128, 128), (64, 64), (48, 48), (32, 32), (16, 16)]


def gradient(size: int, top, bottom) -> Image.Image:
    image = Image.new("RGB", (size, size))
    draw = ImageDraw.Draw(image)
    for y in range(size):
        ratio = y / max(1, size - 1)
        color = tuple(int(top[i] + (bottom[i] - top[i]) * ratio) for i in range(3))
        draw.line([(0, y), (size, y)], fill=color)
    return image.convert("RGBA")


def rounded_mask(size: int, radius: int, inset: int) -> Image.Image:
    mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(mask).rounded_rectangle(
        [inset, inset, size - 1 - inset, size - 1 - inset], radius=radius, fill=255
    )
    return mask


def star_points(cx: float, cy: float, radius: float, thickness: float) -> list[tuple[float, float]]:
    thin = radius * thickness
    return [
        (cx, cy - radius), (cx + thin, cy - thin), (cx + radius, cy),
        (cx + thin, cy + thin), (cx, cy + radius), (cx - thin, cy + thin),
        (cx - radius, cy), (cx - thin, cy - thin),
    ]


def paste_shape(base: Image.Image, mask: Image.Image, top, bottom, glow: float = 0.55) -> Image.Image:
    """把渐变图形贴到画布上，并带一圈同色辉光。"""
    size = base.size[0]
    shape = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    shape.paste(gradient(size, top, bottom), (0, 0), mask)
    if glow > 0:
        halo = shape.filter(ImageFilter.GaussianBlur(size * 0.055))
        halo.putalpha(halo.getchannel("A").point(lambda v: int(v * glow)))
        base = Image.alpha_composite(base, halo)
    return Image.alpha_composite(base, shape)


def draw_star(size: int, plate: bool) -> Image.Image:
    """霓虹星芒。"""
    base = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    if plate:
        base.paste(
            gradient(size, DEEP_2, DEEP), (0, 0), rounded_mask(size, int(size * 0.26), int(size * 0.03))
        )
    mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(mask).polygon(star_points(size * 0.5, size * 0.5, size * 0.40, 0.30), fill=255)
    small = Image.new("L", (size, size), 0)
    ImageDraw.Draw(small).polygon(star_points(size * 0.79, size * 0.21, size * 0.14, 0.30), fill=255)
    mask = Image.composite(Image.new("L", (size, size), 255), mask, small)
    return paste_shape(base, mask, PURPLE, PINK, 0.6)


def draw_cat(size: int, plate: bool) -> Image.Image:
    """猫耳剪影，二次元指向最直接。"""
    base = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    if plate:
        base.paste(
            gradient(size, DEEP_2, DEEP), (0, 0), rounded_mask(size, int(size * 0.26), int(size * 0.03))
        )
    mask = Image.new("L", (size, size), 0)
    draw = ImageDraw.Draw(mask)
    draw.polygon(
        [(size * 0.18, size * 0.46), (size * 0.24, size * 0.12), (size * 0.48, size * 0.38)], fill=255
    )
    draw.polygon(
        [(size * 0.52, size * 0.38), (size * 0.76, size * 0.12), (size * 0.82, size * 0.46)], fill=255
    )
    draw.ellipse([size * 0.17, size * 0.34, size * 0.83, size * 0.88], fill=255)
    return paste_shape(base, mask, PURPLE, PINK, 0.6)


def draw_bubble(size: int, plate: bool) -> Image.Image:
    """对话气泡，最直白地说明这是聊天助手。"""
    base = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    if plate:
        base.paste(
            gradient(size, PURPLE, PINK), (0, 0), rounded_mask(size, int(size * 0.26), int(size * 0.03))
        )
    bubble = Image.new("L", (size, size), 0)
    draw = ImageDraw.Draw(bubble)
    draw.rounded_rectangle(
        [size * 0.15, size * 0.18, size * 0.85, size * 0.67], radius=int(size * 0.17), fill=255
    )
    draw.polygon(
        [(size * 0.33, size * 0.62), (size * 0.29, size * 0.86), (size * 0.50, size * 0.65)], fill=255
    )
    white = Image.new("RGBA", (size, size), (255, 255, 255, 255))
    if not plate:
        white.putalpha(bubble)
        halo = white.filter(ImageFilter.GaussianBlur(size * 0.05))
        base = Image.alpha_composite(base, halo)
        return Image.alpha_composite(base, white)
    out = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    out.paste(white, (0, 0), bubble)
    spark = Image.new("L", (size, size), 0)
    ImageDraw.Draw(spark).polygon(star_points(size * 0.5, size * 0.43, size * 0.19, 0.28), fill=255)
    accent = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    accent.paste(gradient(size, PURPLE, PINK), (0, 0), spark)
    out = Image.alpha_composite(out, accent)
    return Image.alpha_composite(base, out)


DESIGNS = {
    "star": ("霓虹星芒（透明底）", lambda s: draw_star(s, False)),
    "star-plate": ("霓虹星芒（深底方块）", lambda s: draw_star(s, True)),
    "cat": ("猫耳剪影（深底方块）", lambda s: draw_cat(s, True)),
    "bubble": ("对话气泡（渐变方块）", lambda s: draw_bubble(s, True)),
}


def build_preview() -> None:
    """按真实尺寸在深浅两种背景上排一遍，方便挑。"""
    CANDIDATES.mkdir(parents=True, exist_ok=True)
    try:
        font = ImageFont.truetype("C:/Windows/Fonts/msyh.ttc", 20)
    except OSError:
        font = ImageFont.load_default()
    row_h = 280
    canvas = Image.new("RGB", (1180, row_h * len(DESIGNS) + 20), (24, 16, 46))
    draw = ImageDraw.Draw(canvas)
    for index, (key, (label, render)) in enumerate(DESIGNS.items()):
        top = index * row_h + 10
        big = render(256)
        canvas.paste(big, (16, top + 12), big)
        draw.text((296, top + 16), f"{label}   [{key}]", fill=(232, 222, 255), font=font)
        dark_strip = Image.new("RGB", (860, 96), (20, 13, 40))
        light_strip = Image.new("RGB", (860, 96), (246, 246, 250))
        x = 10
        for size in (64, 48, 40, 32, 24, 16):
            icon = render(size)
            dark_strip.paste(icon, (x, (96 - size) // 2), icon)
            light_strip.paste(icon, (x, (96 - size) // 2), icon)
            x += size + 20
        draw.text((298, top + 40), "深色背景（标题栏 / 深色任务栏）", fill=(146, 136, 186), font=font)
        canvas.paste(dark_strip, (296, top + 58))
        draw.text((298, top + 158), "浅色背景（部分系统区域仍是白底）", fill=(146, 136, 186), font=font)
        canvas.paste(light_strip, (296, top + 176))
        big.save(CANDIDATES / f"{key}.png")
    canvas.save(PREVIEW)
    print(f"对比图已生成：{PREVIEW}")


def write_ico(name: str) -> None:
    if name not in DESIGNS:
        raise SystemExit(f"没有这个方案：{name}（可选 {', '.join(DESIGNS)}）")
    render = DESIGNS[name][1]
    render(256).save(ICO, sizes=ICO_SIZES)
    print(f"图标已生成：{ICO}（方案 {name}：{DESIGNS[name][0]}）")


if __name__ == "__main__":
    argv = sys.argv[1:]
    if "--preview" in argv:
        build_preview()
    else:
        write_ico(argv[0] if argv else "star")
