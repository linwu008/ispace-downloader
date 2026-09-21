"""One translation/logo source; copy only the assets needed by the offline helper."""

from pathlib import Path
import shutil
from PIL import Image

root = Path(__file__).resolve().parent.parent
for name in ("i18n.js", "en.json", "logo.png"):
    shutil.copyfile(root / "cloud" / "public" / name, root / "ispace" / "static" / name)
with Image.open(root / "cloud/public/logo.png") as logo:
    logo.save(
        root / "ispace/static/logo.ico",
        sizes=[(16, 16), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)],
    )
