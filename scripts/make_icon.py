"""Generate the application icon.

Three candidate marks; `CANDIDATE` selects the one that ships. All are flat, high-contrast
shapes because the size that actually matters is 16px in a taskbar, where gradients and
fine detail turn to mud.

  compass  - finding your way to something. Discovery in the general sense.
  spark    - a framed work with a spark: the app surfaces work worth seeing.
  fan      - overlapping creator cards, the front one lit: many artists, one found.
"""

import sys
from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[1]
CANDIDATE = sys.argv[1] if len(sys.argv) > 1 else "spark"
OUT = ROOT / "static" / "app.ico"

BACKDROP = (23, 24, 27)
LIGHT = (240, 241, 245)
ACCENT = (85, 214, 194)
INK = (37, 39, 45)
DIM = (108, 112, 122)
SIZES = [256, 128, 64, 48, 32, 16]
SCALE = 8


def canvas(size):
    box = size * SCALE
    image = Image.new("RGBA", (box, box), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    unit = box / 32
    draw.rounded_rectangle([0, 0, box - 1, box - 1], radius=unit * 7, fill=BACKDROP)
    return image, draw, unit, box


def compass(size):
    image, draw, unit, box = canvas(size)
    draw.ellipse([unit * 5, unit * 5, unit * 27, unit * 27], fill=LIGHT)
    draw.ellipse([unit * 7.5, unit * 7.5, unit * 24.5, unit * 24.5], fill=INK)
    centre = box / 2
    draw.polygon([(centre, unit * 9), (unit * 19.5, centre), (centre, unit * 16)], fill=ACCENT)
    draw.polygon([(centre, unit * 23), (unit * 12.5, centre), (centre, unit * 16)], fill=DIM)
    draw.ellipse([centre - unit * 1.2, centre - unit * 1.2, centre + unit * 1.2, centre + unit * 1.2],
                 fill=LIGHT)
    return image


def star(draw, cx, cy, radius, fill):
    points = []
    for index in range(8):
        reach = radius if index % 2 == 0 else radius * 0.34
        angle = index * 3.14159265 / 4
        points.append((cx + reach * __import__("math").sin(angle),
                       cy - reach * __import__("math").cos(angle)))
    draw.polygon(points, fill=fill)


def spark(size):
    image, draw, unit, box = canvas(size)
    draw.rounded_rectangle([unit * 6, unit * 7, unit * 24, unit * 25], radius=unit * 2, fill=LIGHT)
    draw.rectangle([unit * 8, unit * 9, unit * 22, unit * 23], fill=INK)
    draw.ellipse([unit * 10, unit * 11.5, unit * 13, unit * 14.5], fill=ACCENT)
    draw.polygon([(unit * 8.6, unit * 23), (unit * 14, unit * 15.5), (unit * 19, unit * 23)], fill=ACCENT)
    draw.polygon([(unit * 15, unit * 23), (unit * 18.6, unit * 18.5), (unit * 21.4, unit * 23)], fill=(56, 160, 146))
    star(draw, unit * 24, unit * 8, unit * 6.4, BACKDROP)
    star(draw, unit * 24, unit * 8, unit * 5, ACCENT)
    return image


def fan(size):
    image, draw, unit, box = canvas(size)
    for offset, shade in ((6.5, DIM), (4.0, (150, 154, 164))):
        draw.rounded_rectangle([unit * (5 + offset), unit * 6, unit * (5 + offset + 12), unit * 26],
                               radius=unit * 2, fill=shade)
    draw.rounded_rectangle([unit * 5, unit * 6, unit * 17, unit * 26], radius=unit * 2, fill=LIGHT)
    draw.ellipse([unit * 8, unit * 9.5, unit * 14, unit * 15.5], fill=ACCENT)
    draw.polygon([(unit * 6, unit * 24), (unit * 11, unit * 17), (unit * 16, unit * 24)], fill=INK)
    return image


BUILDERS = {"compass": compass, "spark": spark, "fan": fan}


def render(name, size):
    return BUILDERS[name](size).resize((size, size), Image.LANCZOS)


if CANDIDATE == "preview":
    out = ROOT / "reports" / "discovery-dashboard"
    out.mkdir(parents=True, exist_ok=True)
    for name in BUILDERS:
        strip = Image.new("RGBA", (256 + 128 + 48 + 16 + 60, 256), (10, 10, 12, 255))
        x = 0
        for size in (256, 128, 48, 16):
            strip.paste(render(name, size), (x, (256 - size) // 2))
            x += size + 15
        strip.convert("RGB").save(out / f"icon-{name}.png")
        print(f"preview: {out / f'icon-{name}.png'}")
else:
    frames = [render(CANDIDATE, size) for size in SIZES]
    frames[0].save(OUT, format="ICO", sizes=[(s, s) for s in SIZES])
    print(f"wrote {OUT} ({OUT.stat().st_size:,} bytes, '{CANDIDATE}', sizes {SIZES})")
