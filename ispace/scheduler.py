from __future__ import annotations

import os
import re
import subprocess
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path
from xml.sax.saxutils import escape
from zoneinfo import ZoneInfo

TASK_NAME = "iSpaceDownloader-v0.1"
ZONE = ZoneInfo("Asia/Shanghai")


def next_run(clock="20:00", current=None):
    if not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", clock):
        raise ValueError("时间格式必须为 HH:MM")
    current = current or datetime.now(ZONE)
    hour, minute = map(int, clock.split(":"))
    value = current.astimezone(ZONE).replace(hour=hour, minute=minute, second=0, microsecond=0)
    if value <= current:
        value += timedelta(days=1)
    return value.isoformat()


def task_xml(clock, directory, identity, current=None):
    boundary = next_run(clock, current)
    command = escape(sys.executable)
    arguments = escape(subprocess.list2cmdline(["-m", "ispace", "--data-dir", str(directory), "sync"]))
    return f'''<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.4" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo><Description>每天检查并下载已绑定的 iSpace 课程资料</Description></RegistrationInfo>
  <Triggers><CalendarTrigger><StartBoundary>{boundary}</StartBoundary><Enabled>true</Enabled><ScheduleByDay><DaysInterval>1</DaysInterval></ScheduleByDay></CalendarTrigger></Triggers>
  <Principals><Principal id="Author"><UserId>{escape(identity)}</UserId><LogonType>InteractiveToken</LogonType><RunLevel>LeastPrivilege</RunLevel></Principal></Principals>
  <Settings><MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy><DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries><StopIfGoingOnBatteries>false</StopIfGoingOnBatteries><StartWhenAvailable>true</StartWhenAvailable><RunOnlyIfNetworkAvailable>true</RunOnlyIfNetworkAvailable><ExecutionTimeLimit>PT0S</ExecutionTimeLimit><Enabled>true</Enabled></Settings>
  <Actions Context="Author"><Exec><Command>{command}</Command><Arguments>{arguments}</Arguments><WorkingDirectory>{escape(str(Path(__file__).resolve().parent.parent))}</WorkingDirectory></Exec></Actions>
</Task>'''


def configure(store, enabled, clock):
    next_run(clock)
    if os.name != "nt":
        raise ValueError("每日自动检查使用 Windows 任务计划程序")
    flags = subprocess.CREATE_NO_WINDOW
    if enabled:
        identity = subprocess.run(["whoami"], capture_output=True, text=True, check=True, creationflags=flags).stdout.strip()
        with tempfile.NamedTemporaryFile(suffix=".xml", delete=False, dir=store.directory) as file:
            path = Path(file.name)
        try:
            path.write_text(task_xml(clock, store.directory, identity), encoding="utf-16")
            result = subprocess.run(["schtasks", "/Create", "/TN", TASK_NAME, "/XML", str(path), "/F"], capture_output=True, creationflags=flags)
        finally:
            path.unlink(missing_ok=True)
    else:
        query = subprocess.run(["schtasks", "/Query", "/TN", TASK_NAME], capture_output=True, creationflags=flags)
        result = subprocess.run(["schtasks", "/Delete", "/TN", TASK_NAME, "/F"], capture_output=True, creationflags=flags) if query.returncode == 0 else query
        if query.returncode != 0 and not store.setting("schedule_enabled", False):
            store.set("schedule_time", clock)
            return
    if result.returncode:
        raise ValueError("更新任务计划失败，请确认 Windows 任务计划服务可用及当前用户具有权限")
    store.set("schedule_enabled", enabled)
    store.set("schedule_time", clock)
