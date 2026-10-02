"""Native architecture build; browser bundles are copied intact after freezing."""
from pathlib import Path
from PyInstaller.utils.hooks import collect_all, collect_submodules, collect_data_files, copy_metadata

root = Path(SPECPATH).parent
datas, binaries, hidden = [], [], []
for package in ('playwright', 'keyring', 'pypdf'):
    d, b, h = collect_all(package)
    datas.extend(pair for pair in d if '.local-browsers' not in pair[0])
    binaries.extend(pair for pair in b if '.local-browsers' not in pair[0])
    hidden.extend(h)
datas += collect_data_files('ispace') + collect_data_files('tzdata') + copy_metadata('ispace-downloader')
hidden += collect_submodules('uvicorn') + ['pystray._darwin', 'keyring.backends.macOS', 'PyObjCTools.AppHelper']
a = Analysis([str(root / 'scripts/assistant_launcher.py')], pathex=[str(root)],
             binaries=binaries, datas=datas, hiddenimports=hidden,
             excludes=['keyring.backends.Windows', 'pystray._win32', 'pystray._xorg', 'pystray._gtk', 'pystray._appindicator'])
# Playwright's own hooks also collect browser data; remove those additions too.
a.datas = [entry for entry in a.datas if '.local-browsers' not in entry[0] and '.local-browsers' not in entry[1]]
a.binaries = [entry for entry in a.binaries if '.local-browsers' not in entry[0] and '.local-browsers' not in entry[1]]
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name='CourseNestHelper',
          console=False, target_arch=None, codesign_identity=None)
coll = COLLECT(exe, a.binaries, a.datas, name='CourseNestHelper')
app = BUNDLE(coll, name='CourseNestHelper.app', icon=str(root / 'build/logo.icns'),
             bundle_identifier='cn.bnbucoursenest.helper',
             info_plist={'LSUIElement': True, 'LSMinimumSystemVersion': '14.0',
                         'CFBundleShortVersionString': __import__('ispace').__version__,
                         'CFBundleVersion': __import__('ispace').__version__,
                         'NSHighResolutionCapable': True})
