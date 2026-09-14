"""Windows tray launcher; also accepts the existing scheduled-task CLI arguments."""
import multiprocessing
import os
import sys
import threading
import time
import webbrowser
from pathlib import Path


def alert(message):
    import ctypes
    ctypes.windll.user32.MessageBoxW(None, message, 'BNBU CourseNest', 0x40)


def main():
    multiprocessing.freeze_support()
    os.environ.setdefault('ISPACE_DATA_DIR', str(Path(os.environ['LOCALAPPDATA']) / 'BNBUCourseNest'))
    if len(sys.argv) > 1:
        # Windows Task Scheduler uses the same -m ispace arguments as Python.
        if sys.argv[1:3] == ['-m', 'ispace']:
            sys.argv = [sys.argv[0], *sys.argv[3:]]
        from ispace.__main__ import main as cli
        cli()
        return
    os.environ['ISPACE_COMPANION_AUTOSTART'] = '1'
    import httpx
    import uvicorn
    import pystray
    from PIL import Image, ImageDraw
    from filelock import Timeout
    from ispace.web import create_app
    from ispace import __version__
    url = 'http://127.0.0.1:8765'
    try:
        response = httpx.get(url + '/api/state', timeout=2, trust_env=False)
        if response.status_code == 200 and response.json().get('version') == __version__:
            webbrowser.open(url + '/#/settings')
            return
        alert('本机 8765 端口已有其他版本运行，请先关闭旧版助手，再启动 CourseNest。')
        return
    except httpx.HTTPError:
        pass
    app = create_app()
    server = uvicorn.Server(uvicorn.Config(app, host='127.0.0.1', port=8765, access_log=False, log_config=None))
    serving = threading.Thread(target=server.run, name='local-web', daemon=True)
    serving.start()
    for _ in range(60):
        if server.started:
            break
        if not serving.is_alive():
            alert('同步助手启动失败，请检查 8765 端口是否被占用。')
            return
        time.sleep(.2)
    else:
        alert('同步助手启动超时。')
        server.should_exit = True
        return
    picture = Image.new('RGB', (64,64), '#205b4c')
    draw = ImageDraw.Draw(picture)
    draw.rounded_rectangle((12,18,52,49), radius=5, outline='#e5ecd8', width=4)
    draw.line((20,29,44,29), fill='#e5ecd8', width=3)
    draw.line((20,38,38,38), fill='#e5ecd8', width=3)
    def open_ui(icon=None, item=None):
        webbrowser.open(url + '/#/settings')
    def autorun_enabled(item=None):
        import winreg
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r'Software\Microsoft\Windows\CurrentVersion\Run') as key:
                return winreg.QueryValueEx(key, 'BNBUCourseNest')[0] == '"' + sys.executable + '"'
        except OSError:
            return False
    def toggle_autorun(icon, item):
        import winreg
        enabled = autorun_enabled()
        try:
            with winreg.CreateKey(winreg.HKEY_CURRENT_USER, r'Software\Microsoft\Windows\CurrentVersion\Run') as key:
                if enabled:
                    winreg.DeleteValue(key, 'BNBUCourseNest')
                else:
                    winreg.SetValueEx(key, 'BNBUCourseNest', 0, winreg.REG_SZ, '"' + sys.executable + '"')
            icon.update_menu()
        except OSError:
            alert('无法修改登录后启动设置，请稍后重试。')
    def quit_app(icon, item):
        try:
            with app.state.companion.lock(), app.state.service.lock():
                app.state.companion.stop()
                server.should_exit = True
                icon.stop()
        except Timeout:
            alert('仍有任务正在执行，请等待完成后退出，以保证文件完整。')
    tray = pystray.Icon('BNBUCourseNest', picture, 'BNBU CourseNest · v0.4 正式版', pystray.Menu(
        pystray.MenuItem('打开同步助手', open_ui, default=True),
        pystray.MenuItem('Windows 登录后启动', toggle_autorun, checked=autorun_enabled),
        pystray.MenuItem('退出同步助手', quit_app)))
    if os.environ.get('COURSENEST_NO_BROWSER') != '1':
        open_ui()
    try:
        tray.run()
    finally:
        app.state.companion.stop()
        server.should_exit = True
        serving.join(timeout=10)
        app.state.service.pool.shutdown(wait=True)


if __name__ == '__main__':
    main()
