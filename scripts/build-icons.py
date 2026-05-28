"""Generate app icons from a single 1024x1024 master design.

Design: a white rounded-corner page with a folded top-right corner sits on a
brand-purple background. A purple Arabic ت glyph (Adobe Naskh Bold) sits
centered on the page — two dots above a cup form an inadvertent smiley.

Outputs:
  assets/meeting-minutes.png    Linux/AppImage (512x512)
  assets/meeting-minutes.ico    Windows (multi-resolution)
  assets/meeting-minutes.icns   macOS (multi-resolution)

Run from the repo root: python scripts/build-icons.py
"""
from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parent.parent
ASSETS = ROOT / "assets"
# Calibri_Bold draws ت with two clearly separated dots (Adobe Naskh / Almudid
# fuse them into a bowtie shape, which dilutes the smiley-face effect).
FONT_PATH = ASSETS / "typst_fonts" / "Calibri_Bold.ttf"

PURPLE = (132, 70, 157, 255)
PURPLE_DARK = (104, 53, 126, 255)  # for the folded corner backside
PAGE_WHITE = (255, 255, 255, 255)
GLYPH = "ت"

MASTER_SIZE = 1024


def render_master() -> Image.Image:
    """Render the full-fidelity 1024×1024 design."""
    img = Image.new("RGBA", (MASTER_SIZE, MASTER_SIZE), PURPLE)
    draw = ImageDraw.Draw(img)

    # Page rectangle — leave generous padding so the design breathes at
    # taskbar sizes. Rounded corners pick up modern macOS/Windows icon look.
    pad = MASTER_SIZE * 0.16
    page_box = (pad, pad, MASTER_SIZE - pad, MASTER_SIZE - pad)
    radius = int(MASTER_SIZE * 0.07)
    draw.rounded_rectangle(page_box, radius=radius, fill=PAGE_WHITE)

    # Folded top-right corner: a triangle in the darker purple, with a thin
    # diagonal seam so the "fold" reads. Cut into the page's top-right.
    fold_size = MASTER_SIZE * 0.18
    x1, y1, x2, _y2 = page_box
    fold_anchor_x = x2 - fold_size
    fold_anchor_y = y1 + fold_size
    fold_triangle = [
        (x2 - fold_size, y1),         # top edge, where the fold starts
        (x2, y1 + fold_size),         # right edge, where the fold ends
        (x2, y1),                     # the corner being folded back
    ]
    draw.polygon(fold_triangle, fill=PURPLE_DARK)
    # Seam line — a slightly darker stroke along the fold's hypotenuse to
    # give the page some depth.
    draw.line(
        [(x2 - fold_size, y1), (x2, y1 + fold_size)],
        fill=(80, 40, 100, 255),
        width=4,
    )

    # ت glyph — centered on the page, sized to dominate the page area.
    glyph_target_height = (page_box[3] - page_box[1]) * 0.65
    font = _font_at_height(glyph_target_height)
    # Pillow's textbbox gives true ink bounds — use them to center exactly.
    bbox = draw.textbbox((0, 0), GLYPH, font=font, anchor="lt")
    glyph_w = bbox[2] - bbox[0]
    glyph_h = bbox[3] - bbox[1]
    cx = (page_box[0] + page_box[2]) / 2
    cy = (page_box[1] + page_box[3]) / 2
    glyph_x = cx - glyph_w / 2 - bbox[0]
    glyph_y = cy - glyph_h / 2 - bbox[1]
    draw.text((glyph_x, glyph_y), GLYPH, font=font, fill=PURPLE)

    return img


def _font_at_height(target_h: float) -> ImageFont.FreeTypeFont:
    """Binary-search a font size so the glyph's rendered height matches target."""
    if not FONT_PATH.is_file():
        raise SystemExit(
            f"Font missing: {FONT_PATH}\n"
            "Drop AdobeNaskh-Bold.ttf into assets/typst_fonts/ first."
        )
    lo, hi = 1, MASTER_SIZE
    best = lo
    while lo <= hi:
        mid = (lo + hi) // 2
        font = ImageFont.truetype(str(FONT_PATH), mid)
        # textbbox of the glyph gives true ink height
        bbox = font.getbbox(GLYPH)
        h = bbox[3] - bbox[1]
        if h <= target_h:
            best = mid
            lo = mid + 1
        else:
            hi = mid - 1
    return ImageFont.truetype(str(FONT_PATH), best)


def main() -> None:
    master = render_master()

    # Linux/AppImage: a single 512x512 PNG. AppImage spec just wants
    # a square icon at >= 256.
    linux_path = ASSETS / "meeting-minutes.png"
    master.resize((512, 512), Image.LANCZOS).save(linux_path)
    print(f"wrote {linux_path}")

    # Windows .ico: multi-resolution. 256 is the modern max; 16/32/48 cover
    # small system surfaces; 64/128 fill in between.
    ico_path = ASSETS / "meeting-minutes.ico"
    ico_sizes = [(256, 256), (128, 128), (64, 64), (48, 48), (32, 32), (16, 16)]
    master.save(ico_path, format="ICO", sizes=ico_sizes)
    print(f"wrote {ico_path}")

    # macOS .icns: needs 1024 down to 16. Pillow handles the sizing.
    icns_path = ASSETS / "meeting-minutes.icns"
    icns_sizes = [(1024, 1024), (512, 512), (256, 256), (128, 128),
                  (64, 64), (32, 32), (16, 16)]
    master.save(icns_path, format="ICNS", sizes=icns_sizes)
    print(f"wrote {icns_path}")


if __name__ == "__main__":
    main()
