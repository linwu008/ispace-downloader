import base64
import hashlib
import io
import json
import zipfile
from types import SimpleNamespace
import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
from ispace.course_content import extract, clean_url
from ispace.updater import unpack, verify, signed_bytes
from ispace.local_archive import export, schema
from ispace.state import Store
from test_companion import setup


def test_updated_shortcuts_use_only_newer_valid_installation(tmp_path):
    from ispace.updater import preferred_executable

    current = tmp_path / "versions" / "0.7.1" / "CourseNestHelper.exe"
    current.parent.mkdir(parents=True)
    current.write_bytes(b"fixture")
    pointer = tmp_path / "active-helper.json"
    pointer.write_text(
        json.dumps({"path": str(current), "version": "0.7.1"}), "utf-8-sig"
    )
    assert preferred_executable(tmp_path, "0.7.0") == current
    assert preferred_executable(tmp_path, "0.7.1") is None
    outside = tmp_path / "CourseNestHelper.exe"
    outside.write_bytes(b"fixture")
    pointer.write_text(json.dumps({"path": str(outside), "version": "0.7.2"}))
    assert preferred_executable(tmp_path, "0.7.0") is None
    pointer.write_text('{"path": null, "version":"invalid"}')
    assert preferred_executable(tmp_path, "0.7.0") is None


def test_update_requires_executable_at_expected_location(tmp_path):
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w") as z:
        z.writestr("CourseNestHelper/nested/CourseNestHelper.exe", b"fixture")
    data = stream.getvalue()
    with pytest.raises(ValueError, match="缺少助手"):
        unpack(data, {"sha256": hashlib.sha256(data).hexdigest()}, tmp_path / "target")


def test_semester_archive_survives_catalog_reset_and_folder_migration(setup, tmp_path):
    from ispace import folders
    from ispace.local_archive import publish
    from test_v06 import downloaded

    companion, store, _, root = downloaded(setup)
    config = {"server": "https://nest.test", "device_id": "device"}
    sent = []

    def request(_config, path, body):
        sent.append((path, body))
        return {"archive_id": "archive-" + body.get("term_id", "")}

    companion.request = request
    companion.service.pending_content[1] = [
        {"body": "Due Friday", "source_key": "intro"}
    ]
    publish(companion, config, {"id": "t1", "label": "2026 Fall"})
    assert 1 not in companion.service.pending_content
    first_count = len(sent)
    publish(companion, config, {"id": "t1", "label": "2026 Fall"})
    assert (
        len(sent) == first_count
    )  # Unchanged file catalog does not use another request.
    publish(companion, config, {"id": "t2", "label": "2027 Spring"})
    with store.connect() as db:
        assert (
            db.execute(
                "SELECT COUNT(DISTINCT archive_id) FROM local_archive_files"
            ).fetchone()[0]
            == 2
        )
        # Emulate a school catalog that no longer includes last semester's materials.
        db.execute("DELETE FROM materials")
    target = tmp_path / "relocated"
    target.mkdir()
    assert folders.configure(store, 1, str(target), True)["moved"] == 2
    with store.connect() as db:
        rows = list(db.execute("SELECT path,root FROM local_archive_files"))
    from pathlib import Path

    assert all(Path(r[0]).is_file() and Path(r[1]) == target for r in rows)
    assert not list(root.rglob("*.png"))


def test_content_preserves_deadlines_and_removes_scripts_submissions():
    page = """<h1>Project</h1><main role="main"><div id="intro">Read <b>chapter 1</b><script>bad()</script><img src="a.png"><a href="https://school.test/info?token=secret">Guidance</a></div><div class="activity-dates">Due: 25 September, 11:59 PM</div><div class="submissionstatustable">PRIVATE SUBMISSION</div></main>"""
    rows = extract(page, "https://ispace.test/mod/assign/view.php?id=1&sesskey=secret")
    raw = json.dumps(rows)
    assert "11:59 PM" in raw and "chapter 1" in raw
    assert "bad()" not in raw and "PRIVATE" not in raw and "secret" not in raw
    assert rows[0]["partial"]


def test_unknown_attendance_does_not_ingest_class_roster():
    rows = extract(
        '<h1>Attendance</h1><main role="main"><table><tr><td>Other student absent</td></tr></table></main>',
        "https://school.test/mod/attendance/view.php?id=1",
    )
    assert rows[0]["partial"] and "Other student" not in rows[0]["body"]
    assert clean_url("javascript:alert(1)") == ""


def test_upstream_attendance_self_report_and_teacher_announcements():
    report = '<main role="main"><table class="attwidth"><tr><td class="datecol">Monday</td><td class="statuscol">Present</td><td class="pointscol">5</td></tr></table></main>'
    rows = extract(report, "https://school.test/mod/attendance/view.php?id=1")
    assert "Present" in rows[0]["body"] and not rows[0]["partial"]
    assert "5" not in rows[0]["body"]
    assert extract(
        report, "https://school.test/mod/attendance/view.php?id=1&studentid=2"
    )[0]["partial"]
    forum = '<main role="main"><article class="forumpost"><div class="author"><a href="/user/view.php?id=9">Teacher</a></div><div class="posting">Due Friday</div></article></main>'
    assert (
        "Due Friday"
        in extract(
            forum, "https://school.test/mod/forum/discuss.php?d=1", teachers={"9"}
        )[0]["body"]
    )
    assert extract(
        forum, "https://school.test/mod/forum/discuss.php?d=1", teachers={"8"}
    )[0]["partial"]


def test_signed_updates_reject_tampering():
    key = Ed25519PrivateKey.generate()
    public = base64.b64encode(
        key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
    ).decode()
    meta = {
        "version": "0.7.1",
        "url": "https://github.com/linwu008/coursenest-releases/releases/download/v0.7.1/pkg.zip",
        "sha256": "a" * 64,
    }
    meta["signature"] = base64.b64encode(key.sign(signed_bytes(meta))).decode()
    verify(meta, public)
    meta["version"] = "0.7.2"
    with pytest.raises(ValueError):
        verify(meta, public)


@pytest.mark.parametrize(
    "bad",
    [
        "../escape",
        "CourseNestHelper/../../escape",
        "C:/Windows/test",
        "CourseNestHelper/CON.txt",
        "CourseNestHelper/bad.",
    ],
)
def test_update_zip_slip_blocked(tmp_path, bad):
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w") as z:
        z.writestr(bad, b"bad")
    data = stream.getvalue()
    with pytest.raises(ValueError):
        unpack(data, {"sha256": hashlib.sha256(data).hexdigest()}, tmp_path / "target")
    assert not (tmp_path / "escape").exists()


def test_local_export_uses_only_known_files_and_retains_original(tmp_path):
    store = Store(tmp_path / "state")
    schema(store)
    source = tmp_path / "notes.txt"
    source.write_text("lesson")
    sha = hashlib.sha256(source.read_bytes()).hexdigest()
    config = {"server": "https://nest.test", "device_id": "d"}
    with store.connect() as db:
        db.execute(
            "INSERT INTO local_archive_files VALUES (?,?,?,?,?,?,?,?)",
            (
                "https://nest.test:d",
                "a",
                "source",
                str(source),
                str(tmp_path),
                sha,
                "notes.txt",
                "Week 1",
            ),
        )
    companion = SimpleNamespace(
        store=store,
        request=lambda *args: {
            "course_name": "Math",
            "notes": [
                {
                    "title": "<script>",
                    "body": "Due Friday",
                    "url": "",
                    "category": "assignment",
                }
            ],
            "history": [],
        },
    )
    result = export(companion, config, "a")
    assert result["status"] == "success"
    with zipfile.ZipFile(next((store.directory / "exports").glob("*.zip"))) as z:
        assert "&lt;script&gt;" in z.read("课程说明.html").decode()
        assert any(n.endswith("notes.txt") for n in z.namelist())
    assert source.read_text() == "lesson"
    source.unlink()
    assert export(companion, config, "a")["status"] == "partial"


def test_export_reports_cloud_index_without_local_reference(tmp_path):
    store = Store(tmp_path / "state")
    data = {
        "course_name": "Math",
        "notes": [],
        "history": [],
        "files": [{"source_key": "unknown", "name": "missing.pdf"}],
    }
    helper = SimpleNamespace(store=store, request=lambda *args: data)
    result = export(helper, {"server": "https://nest.test", "device_id": "d"}, "a")
    assert result["failed"] == 1 and result["status"] == "partial"


def test_export_cancel_keeps_files_and_removes_incomplete_zip(tmp_path):
    import threading
    from ispace.cancellation import scope, Cancelled

    store = Store(tmp_path / "state")
    schema(store)
    event = threading.Event()
    source = tmp_path / "file.txt"
    source.write_text("lesson")
    sha = hashlib.sha256(source.read_bytes()).hexdigest()
    with store.connect() as db:
        db.execute(
            "INSERT INTO local_archive_files VALUES (?,?,?,?,?,?,?,?)",
            (
                "https://nest.test:d",
                "a",
                "f",
                str(source),
                str(tmp_path),
                sha,
                "file.txt",
                "Week 1",
            ),
        )

    def request(*args):
        event.set()
        return {"course_name": "Math", "notes": [], "history": []}

    with pytest.raises(Cancelled), scope(event):
        export(
            SimpleNamespace(store=store, request=request),
            {"server": "https://nest.test", "device_id": "d"},
            "a",
        )
    assert source.read_text() == "lesson"
    assert not list((store.directory / "exports").glob("*"))


@pytest.mark.skipif(__import__("os").name != "nt", reason="Windows update supervisor")
def test_windows_update_start_failure_rolls_back_without_touching_data(tmp_path):
    import subprocess
    import time
    from ispace.updater import switch_script

    # Compile a harmless sentinel executable, not a copy of the production client.
    exe = tmp_path / "old.exe"
    data = tmp_path / "profile"
    data.mkdir()
    (data / "pairing.dat").write_bytes(b"unchanged-pairing-fixture")
    source = tmp_path / "sentinel.cs"
    source.write_text(
        'using System; using System.IO; public class Program { public static void Main() { File.WriteAllText(Path.Combine(Environment.GetEnvironmentVariable("ISPACE_DATA_DIR"), "restored.txt"), "ok"); }}'
    )
    compile = tmp_path / "compile.ps1"
    quote = lambda p: "'" + str(p).replace("'", "''") + "'"
    compile.write_text(
        f"Add-Type -Path {quote(source)} -OutputAssembly {quote(exe)} -OutputType ConsoleApplication",
        encoding="utf-8-sig",
    )
    subprocess.run(
        [
            "powershell.exe",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(compile),
        ],
        check=True,
        capture_output=True,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    script = tmp_path / "apply.ps1"
    script.write_text(
        switch_script(
            tmp_path / "missing.exe",
            exe,
            data,
            "0.7.1",
            2147483647,
            port=18779,
            attempts=1,
        ),
        encoding="utf-8-sig",
    )
    result = subprocess.run(
        [
            "powershell.exe",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(script),
        ],
        timeout=20,
        capture_output=True,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    assert result.returncode == 0, result.stderr.decode(errors="replace")
    for _ in range(50):
        if (data / "restored.txt").exists():
            break
        time.sleep(0.05)
    assert (data / "restored.txt").read_text() == "ok"
    assert (data / "pairing.dat").read_bytes() == b"unchanged-pairing-fixture"
    assert (data / "update-rollback.txt").exists()
