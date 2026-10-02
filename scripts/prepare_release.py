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
import argparse
parser = argparse.ArgumentParser()
parser.add_argument('--platform', choices=['windows', 'macos'], default='windows')
parser.add_argument('--arch', choices=['x64', 'arm64', 'x86_64'])
args = parser.parse_args()
arch = args.arch or ('x64' if args.platform == 'windows' else None)
if (args.platform == 'windows' and arch != 'x64') or (args.platform == 'macos' and arch not in ('arm64', 'x86_64')):
    raise SystemExit('Select a supported platform and architecture')
package = root / 'dist' / f'CourseNestHelper-{__version__}-{args.platform}-{arch}.zip'
if not package.is_file():
    raise SystemExit(f'Missing package: {package}')
meta = {
    'version': __version__,
    'url': f'https://github.com/linwu008/coursenest-releases/releases/download/v{__version__}/{package.name}',
    'sha256': hashlib.sha256(package.read_bytes()).hexdigest(),
}
payload = json.dumps(meta, sort_keys=True, separators=(',', ':')).encode()
meta['signature'] = base64.b64encode(key.sign(payload)).decode()
suffix = '' if args.platform == 'windows' else f'-macos-{arch}'
(root / 'dist' / f'release-{__version__}{suffix}.json').write_text(json.dumps(meta, indent=2), 'utf-8')
print('Release metadata signed; private signing key remains local.')
