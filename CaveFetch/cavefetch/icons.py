"""Procedurally drawn icons (no font/emoji dependency, crisp via supersampling)."""
import math

from PIL import Image, ImageDraw, ImageTk

_RES = getattr(Image, "Resampling", Image).LANCZOS


def lightbulb(size, lit, lit_color="#f59e0b", off_color="#9ca3af", base_color="#6b7280"):
    """Lit bulb with rays (light theme) or plain grey bulb (dark theme)."""
    S = 8
    N = size * S
    img = Image.new("RGBA", (N, N), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    cx, cy, r = N / 2, N * 0.40, N * 0.22
    body = lit_color if lit else off_color
    if lit:
        w = max(2, int(N * 0.05))
        for ang in (-90, -135, -45, 180, 0):
            a = math.radians(ang)
            d.line((cx + math.cos(a) * r * 1.35, cy + math.sin(a) * r * 1.35,
                    cx + math.cos(a) * r * 1.75, cy + math.sin(a) * r * 1.75),
                   fill=lit_color, width=w)
    d.polygon([(cx - r * 0.62, cy + r * 0.7), (cx + r * 0.62, cy + r * 0.7),
               (cx + r * 0.42, cy + r * 1.55), (cx - r * 0.42, cy + r * 1.55)], fill=body)
    d.ellipse((cx - r, cy - r, cx + r, cy + r), fill=body)
    d.rectangle((cx - r * 0.5, cy + r * 1.62, cx + r * 0.5, cy + r * 1.85), fill=base_color)
    d.rectangle((cx - r * 0.32, cy + r * 1.93, cx + r * 0.32, cy + r * 2.15), fill=base_color)
    return ImageTk.PhotoImage(img.resize((size, size), _RES))
