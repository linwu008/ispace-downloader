"""Optional Developer ID signing, inside out, including the bundled browser."""
import subprocess
import sys
from pathlib import Path

app = Path(sys.argv[1]).resolve()
identity = sys.argv[2]
entitlements = Path(__file__).with_name('macos-entitlements.plist')
magic = {b'\xcf\xfa\xed\xfe', b'\xce\xfa\xed\xfe', b'\xca\xfe\xba\xbe', b'\xbe\xba\xfe\xca'}
for path in sorted(app.rglob('*'), key=lambda p: len(p.parts), reverse=True):
    if path.is_symlink():
        continue
    binary = False
    if path.is_file():
        with path.open('rb') as file:
            binary = file.read(4) in magic
    if binary or path.suffix in ('.app', '.framework', '.xpc'):
        subprocess.run(['codesign', '--force', '--options', 'runtime', '--timestamp', '--sign', identity,
                        '--entitlements', str(entitlements), str(path)], check=True)
subprocess.run(['codesign', '--force', '--options', 'runtime', '--timestamp', '--sign', identity,
                '--entitlements', str(entitlements), str(app)], check=True)
