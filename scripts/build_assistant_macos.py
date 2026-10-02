"""Build on each native Mac architecture. No publishing or signing secrets required."""
import hashlib
import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def run(*args, **kwargs):
    subprocess.run(list(map(str, args)), check=True, cwd=ROOT, **kwargs)


def main():
    if sys.platform != 'darwin' or platform.machine() not in ('arm64', 'x86_64'):
        raise SystemExit('Build on an arm64 or x86_64 Mac with native Python 3.13.')
    from PIL import Image
    from ispace import __version__
    import playwright
    run(sys.executable, ROOT / 'scripts/sync_shared_assets.py')
    (ROOT / 'build').mkdir(exist_ok=True)
    with Image.open(ROOT / 'ispace/static/logo.png') as logo:
        logo.convert('RGBA').resize((1024, 1024)).save(ROOT / 'build/logo.icns')
    run(sys.executable, '-m', 'playwright', 'install', '--no-shell', 'chromium',
        env={**os.environ, 'PLAYWRIGHT_BROWSERS_PATH': '0'})
    run(sys.executable, '-m', 'PyInstaller', '--noconfirm', '--clean', ROOT / 'scripts/macos.spec')
    app = ROOT / 'dist/CourseNestHelper.app'
    browsers = Path(playwright.__file__).parent / 'driver/package/.local-browsers'
    destination = app / 'Contents/Resources/browsers'
    destination.mkdir()
    # Copy only the pinned Chromium revision, preserving its native bundle layout.
    import json
    manifest = json.loads((browsers.parent / 'browsers.json').read_text())
    revision = next(b['revision'] for b in manifest['browsers'] if b['name'] == 'chromium')
    browser = browsers / ('chromium-' + revision)
    if not browser.is_dir():
        raise SystemExit('Pinned Chromium was not installed')
    run('/usr/bin/ditto', browser, destination / browser.name)
    shutil.copyfile(ROOT / 'docs/MACOS-ASSISTANT.md', app / 'Contents/Resources/README.md')
    identity = os.environ.get('MACOS_SIGNING_IDENTITY')
    if identity:
        run(sys.executable, ROOT / 'scripts/sign_macos.py', app, identity)
    else:
        run('/usr/bin/codesign', '--force', '--sign', '-', app)
    run('/usr/bin/codesign', '--verify', '--deep', '--strict', app)
    archive = ROOT / f'dist/CourseNestHelper-{__version__}-macos-{platform.machine()}.zip'
    archive.unlink(missing_ok=True)
    run('/usr/bin/ditto', '-c', '-k', '--sequesterRsrc', '--keepParent', app, archive)
    profile = os.environ.get('MACOS_NOTARY_PROFILE')
    if profile:
        if not identity:
            raise SystemExit('Notarization requires MACOS_SIGNING_IDENTITY')
        run('xcrun', 'notarytool', 'submit', archive, '--keychain-profile', profile, '--wait')
        run('xcrun', 'stapler', 'staple', app)
        run('xcrun', 'stapler', 'validate', app)
        run('/usr/sbin/spctl', '--assess', '--type', 'execute', app)
        archive.unlink()
        run('/usr/bin/ditto', '-c', '-k', '--sequesterRsrc', '--keepParent', app, archive)
    checksum = hashlib.file_digest(archive.open('rb'), 'sha256').hexdigest()
    archive.with_suffix('.zip.sha256').write_text(f'{checksum}  {archive.name}\n')
    print(f'{archive}\nSHA256 {checksum}\n' + ('Notarized' if profile else 'NOT NOTARIZED: testing build'))


if __name__ == '__main__':
    main()
