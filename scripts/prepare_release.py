"""Local release signature; key stays in ignored .runtime and is never distributed."""

import base64
import json
import hashlib
from pathlib import Path
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import (
    Encoding,
    PublicFormat,
)

root = Path(__file__).resolve().parent.parent
key_path = root / ".runtime" / "release-signing.key"
if not key_path.exists():
    raise SystemExit(
        "Release signing key is missing. Restore it from your private backup; never silently rotate the pinned key."
    )
key = Ed25519PrivateKey.from_private_bytes(key_path.read_bytes())
public = base64.b64encode(
    key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
).decode()
source = root / "ispace" / "updater.py"
value = source.read_text("utf-8")
import re

match = re.search(r"^PUBLIC_KEY = [\"']([^\"']+)[\"']", value, flags=re.M)
if not match or match[1] != public:
    raise SystemExit(
        "Signing key does not match the client verification key. Release stopped."
    )
from ispace import __version__
package = root / "dist" / f"CourseNestHelper-{__version__}-windows-x64.zip"
if package.exists():
    meta = {
        "version": __version__,
        "url": f"https://github.com/linwu008/coursenest-releases/releases/download/v{__version__}/{package.name}",
        "sha256": hashlib.sha256(package.read_bytes()).hexdigest(),
    }
    payload = json.dumps(meta, sort_keys=True, separators=(",", ":")).encode()
    meta["signature"] = base64.b64encode(key.sign(payload)).decode()
    (root / "dist" / f"release-{__version__}.json").write_text(
        json.dumps(meta, indent=2), "utf-8"
    )
print("Public verification key prepared; private signing key remains local.")
