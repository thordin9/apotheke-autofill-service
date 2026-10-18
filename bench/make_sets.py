"""Make the client-side size sets for sweep.py: sets/<px>/ with every bench photo shrunk to
<px> on the long side (JPEG quality 85, Pillow, like the bench photos). 1024 is a copy."""

import glob
import os
import shutil
import sys

from PIL import Image

src, dst = sys.argv[1], sys.argv[2]
for px in (448, 560, 672, 1024):
    os.makedirs(f"{dst}/{px}", exist_ok=True)
    for f in sorted(glob.glob(f"{src}/*.jpg")):
        o = f"{dst}/{px}/{os.path.basename(f)}"
        if px == 1024:
            shutil.copy(f, o)
            continue
        im = Image.open(f).convert("RGB")
        im.thumbnail((px, px), Image.LANCZOS)
        im.save(o, quality=85)
