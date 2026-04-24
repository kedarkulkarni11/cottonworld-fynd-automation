"""
Generate logo for the Cottonworld → Fynd extension.

Produces:
- logo_220.png  (exact Fynd partner panel size)
- logo_512.png  (for higher-density / future use)

Design: rounded-square navy background, cream "cw" monogram, warm arrow
beneath signifying Logic → Fynd transformation.
"""
from PIL import Image, ImageDraw, ImageFont
from pathlib import Path

ASSETS = Path(__file__).parent

# Brand palette
BG_TOP = (45, 83, 115)       # #2D5373 — indigo, lighter
BG_BOT = (23, 57, 90)        # #17395A — indigo, deeper
CREAM  = (245, 235, 221)     # #F5EBDD — cotton/off-white
ACCENT = (232, 168, 124)     # #E8A87C — warm terracotta arrow


def vertical_gradient(w: int, h: int, top: tuple, bot: tuple) -> Image.Image:
    base = Image.new("RGB", (w, h), top)
    dr = ImageDraw.Draw(base)
    for y in range(h):
        t = y / max(h - 1, 1)
        r = int(top[0] + (bot[0] - top[0]) * t)
        g = int(top[1] + (bot[1] - top[1]) * t)
        b = int(top[2] + (bot[2] - top[2]) * t)
        dr.line([(0, y), (w, y)], fill=(r, g, b))
    return base


def pick_font(size: int) -> ImageFont.FreeTypeFont:
    # Try a few common macOS/Linux sans-serif fonts in descending preference
    candidates = [
        "/System/Library/Fonts/Helvetica.ttc",
        "/System/Library/Fonts/HelveticaNeue.ttc",
        "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
        "/System/Library/Fonts/SFNSRounded.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    ]
    for p in candidates:
        if Path(p).exists():
            try:
                return ImageFont.truetype(p, size)
            except Exception:
                pass
    return ImageFont.load_default()


def render_logo(size: int) -> Image.Image:
    """Render logo at given square size."""
    # We render at 4x then downsample for crisp edges (anti-aliasing)
    scale = 4
    s = size * scale

    # Transparent base
    img = Image.new("RGBA", (s, s), (0, 0, 0, 0))

    # Rounded-square mask (corner radius 18% of size)
    radius = int(s * 0.18)
    mask = Image.new("L", (s, s), 0)
    mdraw = ImageDraw.Draw(mask)
    mdraw.rounded_rectangle((0, 0, s - 1, s - 1), radius=radius, fill=255)

    # Gradient background clipped by mask
    grad = vertical_gradient(s, s, BG_TOP, BG_BOT).convert("RGBA")
    img.paste(grad, (0, 0), mask)

    draw = ImageDraw.Draw(img)

    # Monogram "cw" — centered, bold
    font_size = int(s * 0.42)
    font = pick_font(font_size)
    text = "cw"
    bbox = draw.textbbox((0, 0), text, font=font)
    tw = bbox[2] - bbox[0]
    th = bbox[3] - bbox[1]
    # Offset for font's internal baseline padding (bbox starts at tracking origin)
    tx = (s - tw) // 2 - bbox[0]
    ty = int(s * 0.28) - bbox[1]
    draw.text((tx, ty), text, font=font, fill=CREAM)

    # Arrow beneath — indicates transformation (Logic → Fynd)
    arrow_y = int(s * 0.72)
    ax1 = int(s * 0.30)
    ax2 = int(s * 0.70)
    stroke = max(int(s * 0.018), 2)
    draw.line([(ax1, arrow_y), (ax2, arrow_y)], fill=ACCENT, width=stroke)
    # Arrowhead chevron
    head = int(s * 0.04)
    draw.line(
        [(ax2 - head, arrow_y - head), (ax2, arrow_y), (ax2 - head, arrow_y + head)],
        fill=ACCENT,
        width=stroke,
        joint="curve",
    )

    # Downsample with high-quality filter
    return img.resize((size, size), Image.Resampling.LANCZOS)


def main() -> None:
    for px in (220, 512):
        logo = render_logo(px)
        out = ASSETS / f"logo_{px}.png"
        logo.save(out, format="PNG", optimize=True)
        print(f"✓ wrote {out} ({out.stat().st_size / 1024:.1f} KB)")


if __name__ == "__main__":
    main()
