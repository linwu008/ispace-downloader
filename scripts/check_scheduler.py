"""Register and run a temporary harmless task; never change the user's daily task."""
import os
import subprocess
import sys
import time
import uuid
from pathlib import Path
from xml.etree import ElementTree as ET

from ispace.scheduler import task_xml

if os.name != "nt":
    raise SystemExit("Windows only")
root = Path(__file__).resolve().parent.parent / ".runtime"
root.mkdir(exist_ok=True)
suffix = uuid.uuid4().hex
task = "iSpaceDownloader-Validation-" + suffix
xml_path, marker = root / (suffix + ".xml"), root / (suffix + ".ok")
flags = subprocess.CREATE_NO_WINDOW
identity = subprocess.run(["whoami"],check=True,capture_output=True,text=True,creationflags=flags).stdout.strip()
ns = {"t":"http://schemas.microsoft.com/windows/2004/02/mit/task"}
ET.register_namespace("",ns["t"])
tree = ET.fromstring(task_xml("20:00",root,identity))
code = "from pathlib import Path; Path(" + repr(str(marker)) + ").write_text('ok')"
tree.find("t:Actions/t:Exec/t:Arguments",ns).text = subprocess.list2cmdline(["-c",code])
xml_path.write_bytes(ET.tostring(tree,encoding="utf-16",xml_declaration=True))
registered = False
try:
    result = subprocess.run(["schtasks","/Create","/TN",task,"/XML",str(xml_path)],capture_output=True,creationflags=flags)
    if result.returncode:
        raise RuntimeError("Temporary task registration failed: " + result.stderr.decode(errors="replace"))
    registered = True
    subprocess.run(["schtasks","/Run","/TN",task],check=True,capture_output=True,creationflags=flags)
    for _ in range(30):
        if marker.exists():
            assert marker.read_text() == "ok"
            print("Windows Task Scheduler accepted the generated XML and executed the Python action.")
            break
        time.sleep(1)
    else:
        raise RuntimeError("Task registered but no result arrived within 30 seconds")
finally:
    if registered:
        subprocess.run(["schtasks","/Delete","/TN",task,"/F"],check=True,capture_output=True,creationflags=flags)
    for path in (xml_path,marker):
        if path.resolve().parent != root.resolve():
            raise RuntimeError("Unexpected cleanup path")
        path.unlink(missing_ok=True)
