import io
import os
import sys

from PIL import Image, ImageDraw

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

SAGE, BEIGE, BROWN = (160, 175, 140), (222, 205, 175), (60, 40, 30)
RED, BLUE = (200, 30, 40), (40, 90, 200)


def stripes(bands, size=200, fmt="PNG"):
    """Image made of horizontal bands: [((r, g, b), height_px), ...] summing to `size`."""
    im = Image.new("RGB", (size, size))
    d = ImageDraw.Draw(im)
    y = 0
    for color, h in bands:
        d.rectangle([0, y, size, y + h], fill=color)
        y += h
    buf = io.BytesIO()
    im.save(buf, fmt)
    return buf.getvalue()
