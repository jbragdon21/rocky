"""
Generates the Maple brand logo PNG used in the daily Maple activity digest email
(Icon/maple_logo.png): a forest-green rounded tile with a white trunk and an
autumn maple-leaf canopy, matching Maple's brand sheet.

Run once to (re)produce the asset:  python make_maple_logo.py
Pillow is the only dependency.
"""

import math
import random
from pathlib import Path

from PIL import Image, ImageDraw

# --- Brand palette -----------------------------------------------------------
GREEN = (35, 77, 46)            # forest-green tile  (#234D2E)
WHITE = (255, 255, 255)
AUTUMN = [                      # canopy leaf colors (reds -> oranges -> gold)
    (176, 48, 24),
    (200, 64, 28),
    (222, 92, 30),
    (236, 124, 36),
    (242, 158, 52),
]

# Stylized 5-lobe maple leaf, normalized to roughly [-1, 1], pointing up
# (PIL image coords: +y is down, so the tip is at negative y).
_LEAF = [
    (0.00, -1.00),
    (0.20, -0.52),
    (0.58, -0.60),
    (0.40, -0.26),
    (0.96, -0.14),
    (0.52, 0.06),
    (0.74, 0.50),
    (0.28, 0.30),
    (0.30, 0.82),
    (0.10, 0.56),
    (0.10, 1.00),
    (-0.10, 1.00),
    (-0.10, 0.56),
    (-0.30, 0.82),
    (-0.28, 0.30),
    (-0.74, 0.50),
    (-0.52, 0.06),
    (-0.96, -0.14),
    (-0.40, -0.26),
    (-0.58, -0.60),
    (-0.20, -0.52),
]


def _leaf_points(cx, cy, r, angle_deg):
    a = math.radians(angle_deg)
    ca, sa = math.cos(a), math.sin(a)
    pts = []
    for x, y in _LEAF:
        xr = x * ca - y * sa
        yr = x * sa + y * ca
        pts.append((cx + xr * r, cy + yr * r))
    return pts


def main():
    random.seed(7)               # deterministic output
    S = 2048                     # work at 4x, downsample for smooth edges
    img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)

    # Rounded green tile.
    m = 96
    d.rounded_rectangle([m, m, S - m, S - m], radius=210, fill=GREEN)

    cx = S // 2
    base_y = 1640

    # Trunk + branches, white, radiating from the base.
    branch_origin = (cx, 1180)
    d.line([(cx, base_y), branch_origin], fill=WHITE, width=130, joint="curve")
    branch_tips = [
        (560, 820), (770, 690), (cx, 600), (1278, 690), (1488, 820),
    ]
    for tip in branch_tips:
        d.line([branch_origin, tip], fill=WHITE, width=72, joint="curve")
    # Soften the branch joint.
    d.ellipse([cx - 70, 1110, cx + 70, 1250], fill=WHITE)

    # Canopy: maple leaves clustered over the branch tips, plus fillers.
    anchors = [
        (560, 820, 320), (770, 690, 300), (cx, 600, 340),
        (1278, 690, 300), (1488, 820, 320),
        (680, 560, 300), (1368, 560, 300), (cx, 470, 300),
        (cx, 760, 300), (900, 640, 280), (1148, 640, 280),
    ]
    for i, (ax, ay, r) in enumerate(anchors):
        color = AUTUMN[i % len(AUTUMN)]
        angle = random.uniform(-25, 25)
        d.polygon(_leaf_points(ax, ay, r, angle), fill=color)

    out = Path(__file__).parent / "Icon" / "maple_logo.png"
    out.parent.mkdir(parents=True, exist_ok=True)
    img.resize((512, 512), Image.LANCZOS).save(out)
    print(f"Wrote {out}")


if __name__ == "__main__":
    main()
