"""Regenerate the synthetic photos in tests/fixtures/hardware/.

The images are drawn here from simple shapes, so they carry no third-party
rights (dedicated to the public domain, CC0). They are deliberately simple:
the hardware smoke test checks that the real model answers in the contract's
shape and how fast, not how good the suggestions are.

Needs Pillow (not a service dependency):
    pip install pillow && python scripts/make_hardware_fixtures.py
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter

OUT = Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "hardware"
SIZE = (1024, 768)  # Apotheke resizes uploads to ~1024 px on the long edge


def _table(color: tuple[int, int, int]) -> tuple[Image.Image, ImageDraw.ImageDraw]:
    img = Image.new("RGB", SIZE, (236, 232, 224))
    d = ImageDraw.Draw(img)
    d.rectangle([0, 520, SIZE[0], SIZE[1]], fill=color)  # tabletop
    return img, d


def mug() -> Image.Image:
    img, d = _table((150, 110, 70))
    d.ellipse([330, 600, 690, 660], fill=(110, 80, 50))  # shadow
    d.rectangle([360, 300, 660, 630], fill=(200, 30, 35))
    d.ellipse([360, 270, 660, 330], fill=(170, 20, 25))
    d.ellipse([380, 280, 640, 320], fill=(60, 35, 20))  # coffee
    d.ellipse([360, 600, 660, 660], fill=(200, 30, 35))
    d.ellipse([620, 360, 780, 540], outline=(200, 30, 35), width=32)  # handle
    return img


def screwdriver() -> Image.Image:
    img, d = _table((120, 120, 125))
    d.rounded_rectangle([150, 360, 470, 450], radius=40, fill=(250, 200, 0))
    for x in range(180, 460, 40):
        d.line([x, 365, x, 445], fill=(40, 40, 40), width=6)
    d.rectangle([470, 390, 860, 420], fill=(190, 190, 200))  # shaft
    d.polygon([(860, 390), (900, 400), (900, 410), (860, 420)], fill=(170, 170, 180))
    return img


def bottle() -> Image.Image:
    img, d = _table((90, 130, 90))
    d.rectangle([450, 120, 570, 180], fill=(30, 30, 160))  # cap
    d.polygon([(460, 180), (560, 180), (620, 300), (400, 300)], fill=(120, 190, 230))
    d.rectangle([400, 300, 620, 640], fill=(120, 190, 230))
    d.rectangle([400, 380, 620, 520], fill=(250, 250, 250))  # label
    d.text((440, 430), "WATER 1L", fill=(30, 30, 160))
    return img


def books() -> Image.Image:
    img, d = _table((160, 120, 80))
    colors = [(30, 70, 140), (150, 30, 40), (40, 110, 60), (200, 160, 40)]
    x = 260
    for i, c in enumerate(colors):
        w = 90 + 15 * (i % 2)
        d.rectangle([x, 220 + 20 * i, x + w, 530], fill=c)
        d.rectangle([x + 10, 260 + 20 * i, x + w - 10, 280 + 20 * i], fill=(240, 230, 200))
        x += w + 6
    return img


FIXTURES = {"mug": mug, "screwdriver": screwdriver, "bottle": bottle, "books": books}


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    for name, draw in FIXTURES.items():
        img = draw().filter(ImageFilter.GaussianBlur(1.2))  # soften hard vector edges
        path = OUT / f"{name}.jpg"
        img.save(path, "JPEG", quality=85, optimize=True)
        print(f"{path.relative_to(OUT.parent.parent.parent)}  {path.stat().st_size} bytes")


if __name__ == "__main__":
    main()
