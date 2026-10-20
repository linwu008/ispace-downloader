"""Desktop OS integrations. Business services must not depend on a GUI toolkit."""
import logging
import os
import platform
import plistlib
import subprocess
import sys
from pathlib import Path

APP_ID = 'cn.bnbucoursenest.helper'


def is_macos():
    return sys.platform == 'darwin'


def identity():
    machine = platform.machine().lower()
    return {'platform': 'macos' if is_macos() else 'windows' if os.name == 'nt' else sys.platform,
            'arch': {'aarch64': 'arm64', 'amd64': 'x86_64'}.get(machine, machine)}


def default_data_dir():
    if is_macos():
        return Path.home() / 'Library/Application Support/BNBUCourseNest'
    return Path(os.environ.get('LOCALAPPDATA', Path.home())) / 'BNBUCourseNest'


def application_root():
    if is_macos() and getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parents[2]
    return Path(__file__).resolve().parent.parent


def credential_backend():
    if is_macos():
        from keyring.backends.macOS import Keyring
        return Keyring()
    if os.name == 'nt':
        from keyring.backends.Windows import WinVaultKeyring
        return WinVaultKeyring()
    raise RuntimeError('凭据保存仅支持 Windows 和 macOS；测试应注入模拟凭据存储')


def applescript(source, *args, timeout=30):
    return subprocess.run(['/usr/bin/osascript', '-e', source, *map(str, args)],
                          capture_output=True, text=True, timeout=timeout)


def alert(message):
    if is_macos():
        applescript('on run argv\n display alert "BNBU CourseNest" message (item 1 of argv)\nend run', message)
    elif os.name == 'nt':
        import ctypes
        ctypes.windll.user32.MessageBoxW(None, message, 'BNBU CourseNest', 0x40)
    else:
        logging.error('%s', message)


def choose_folder():
    try:
        if is_macos():
            result = applescript('try\n return POSIX path of (choose folder with prompt "CourseNest")\non error number -128\n return ""\nend try', timeout=180)
            value = result.stdout.strip()
        elif os.name == 'nt':
            result = subprocess.run(['powershell.exe', '-NoProfile', '-STA', '-ExecutionPolicy', 'Bypass', '-File',
                                     str(Path(__file__).parent / 'static/choose-folder.ps1')],
                                    capture_output=True, timeout=180, creationflags=subprocess.CREATE_NO_WINDOW)
            value = result.stdout.decode('utf-8-sig').strip()
        else:
            raise ValueError('请选择并输入本地目录的绝对路径')
    except subprocess.TimeoutExpired:
        raise ValueError('文件夹选择已超时，请重试') from None
    if result.returncode:
        raise ValueError('无法打开目录选择器，请直接粘贴文件夹路径')
    return value


def reveal(path):
    if is_macos():
        subprocess.Popen(['/usr/bin/open', '-R', str(path)])
    elif os.name == 'nt':
        subprocess.Popen(['explorer.exe', '/select,', str(path)])
    else:
        raise ValueError('定位文件仅支持 Windows 和 macOS')


def launch_agent_path():
    return Path.home() / 'Library/LaunchAgents' / (APP_ID + '.plist')


def application_bundle():
    exe = Path(sys.executable).resolve()
    if not getattr(sys, 'frozen', False) or exe.parent.name != 'MacOS' or exe.parent.parent.name != 'Contents':
        raise ValueError('登录后启动需要安装后的 CourseNestHelper.app')
    app = exe.parent.parent.parent
    if app.parent not in (Path('/Applications'), Path.home() / 'Applications'):
        raise ValueError('请先将应用移到 Applications 文件夹，再启用登录后启动')
    return app


def autorun_enabled():
    if is_macos():
        try:
            data = plistlib.loads(launch_agent_path().read_bytes())
            return data['ProgramArguments'] == ['/usr/bin/open', '-g', str(application_bundle()), '--args', '--login-start']
        except (OSError, ValueError, KeyError, plistlib.InvalidFileException):
            return False
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r'Software\Microsoft\Windows\CurrentVersion\Run') as key:
            return winreg.QueryValueEx(key, 'BNBUCourseNest')[0] == '"' + sys.executable + '"'
    except OSError:
        return False


def set_autorun(enabled):
    if is_macos():
        path = launch_agent_path()
        if not enabled:
            path.unlink(missing_ok=True)
            return
        # RunAtLoad at next login only; no KeepAlive and no separate sync schedule.
        data = {'Label': APP_ID, 'ProgramArguments': ['/usr/bin/open', '-g', str(application_bundle()), '--args', '--login-start'],
                'RunAtLoad': True, 'LimitLoadToSessionType': 'Aqua'}
        path.parent.mkdir(parents=True, exist_ok=True)
        temp = path.with_suffix('.tmp')
        temp.write_bytes(plistlib.dumps(data))
        temp.chmod(0o600)
        temp.replace(path)
        return
    import winreg
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, r'Software\Microsoft\Windows\CurrentVersion\Run') as key:
        if enabled:
            winreg.SetValueEx(key, 'BNBUCourseNest', 0, winreg.REG_SZ, '"' + sys.executable + '"')
        else:
            winreg.DeleteValue(key, 'BNBUCourseNest')


def notify(tray, message):
    try:
        if is_macos():
            applescript('on run argv\n display notification (item 1 of argv) with title "CourseNest"\nend run', message)
        elif tray.HAS_NOTIFICATION:
            tray.notify(message, 'CourseNest')
    except (OSError, subprocess.SubprocessError):
        logging.exception('Desktop notification unavailable')


def prepare_browser():
    if is_macos() and getattr(sys, 'frozen', False):
        # Browser is copied intact, outside PyInstaller's binary rewriting.
        os.environ['PLAYWRIGHT_BROWSERS_PATH'] = str(Path(sys.executable).resolve().parents[1] / 'Resources/browsers')


def refresh_menu(tray):
    if is_macos():
        from PyObjCTools import AppHelper
        AppHelper.callAfter(tray.update_menu)
    else:
        tray.update_menu()
