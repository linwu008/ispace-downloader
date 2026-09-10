from pathlib import Path

import pytest

from ispace.moodle import Discovery, Resource, ResourceError
from ispace.security import LoginRequired
from ispace.state import Store
from ispace.sync import SyncEngine, safe_name


class Platform:
    def __init__(self, content=b"lesson-one", etag='"v1"'):
        self.content, self.etag = content, etag
        self.downloads = 0
        self.errors = []
        self.failure = None

    def discover(self, course_id):
        return Discovery([Resource(f"https://school.test/pluginfile.php/{course_id}/mod_resource/content/1/讲义.pdf", "讲义.pdf")], self.errors)

    def metadata(self, resource):
        return {"etag": self.etag} if self.etag else {}

    def download(self, resource, target):
        self.downloads += 1
        target.write_bytes(self.content)
        if self.failure:
            raise self.failure
        return self.metadata(resource)


@pytest.fixture
def setup(tmp_path):
    store = Store(tmp_path / "state")
    store.refresh_courses([{"id": 1, "name": "数学"}, {"id": 2, "name": "物理"}])
    folders = [tmp_path / "数学", tmp_path / "物理"]
    for i, folder in enumerate(folders, 1):
        folder.mkdir()
        store.bind(i, str(folder), True)
    return store, folders


def run(store, platform):
    return SyncEngine(store, platform, sleep=lambda _: None).run(store.start_run())


def files(folder):
    return [p for p in folder.rglob("*") if p.is_file()]


def test_course_isolation_and_repeated_sync(setup):
    store, folders = setup
    platform = Platform()
    result = run(store, platform)
    assert result["downloaded"] == 2
    assert all(len(files(folder)) == 1 for folder in folders)
    result = run(store, platform)
    assert result["skipped"] == 2
    assert result["downloaded"] == 0
    assert platform.downloads == 2


def test_existing_nested_renamed_file_is_reused(setup):
    store, folders = setup
    existing = folders[0] / "第一周" / "我改过名字.pdf"
    existing.parent.mkdir()
    existing.write_bytes(b"lesson-one")
    platform = Platform()
    result = run(store, platform)
    assert result["skipped"] == 1
    assert files(folders[0]) == [existing]
    assert existing.read_bytes() == b"lesson-one"


def test_changed_same_name_retains_both_versions(setup):
    store, folders = setup
    platform = Platform()
    run(store, platform)
    platform.content, platform.etag = b"lesson-two", '"v2"'
    run(store, platform)
    assert {p.read_bytes() for p in files(folders[0])} == {b"lesson-one", b"lesson-two"}


def test_deleted_file_is_repaired_and_renamed_file_is_reused(setup):
    store, folders = setup
    platform = Platform()
    run(store, platform)
    files(folders[0])[0].unlink()
    files(folders[1])[0].rename(folders[1] / "renamed.pdf")
    result = run(store, platform)
    assert result["downloaded"] == 1 and result["skipped"] == 1
    assert platform.downloads == 3


def test_weak_or_missing_etag_requires_content_verification(setup):
    store, folders = setup
    platform = Platform(etag='W/"v1"')
    run(store, platform)
    run(store, platform)
    assert platform.downloads == 4
    assert len(files(folders[0])) == 1


def test_local_modified_file_not_mistaken_for_original(setup):
    store, folders = setup
    platform = Platform()
    run(store, platform)
    files(folders[0])[0].write_bytes(b"my annotations")
    result = run(store, platform)
    assert result["downloaded"] == 1
    assert {p.read_bytes() for p in files(folders[0])} == {b"lesson-one", b"my annotations"}


def test_failure_retries_three_times_and_leaves_no_partial(setup):
    store, folders = setup
    platform = Platform()
    platform.failure = OSError("disk full")
    result = run(store, platform)
    assert platform.downloads == 8
    assert result["status"] == "partial"
    assert result["failed"] == 2
    assert not any(files(folder) for folder in folders)


def test_discovery_error_is_never_success(setup):
    store, folders = setup
    platform = Platform()
    platform.errors = ["第二页读取失败"]
    assert run(store, platform)["status"] == "partial"


def test_expired_login_stops_without_retry(setup):
    store, folders = setup
    platform = Platform()
    platform.failure = LoginRequired("expired")
    with pytest.raises(LoginRequired):
        run(store, platform)
    assert platform.downloads == 1
    assert store.history()["runs"][0]["status"] == "auth_required"
    assert not files(folders[0])


@pytest.mark.parametrize("name", ["../../bad.pdf", "CON.txt", "NUL", "..", "a:b?.pdf", "讲义.pdf"])
def test_safe_windows_filename(name):
    clean = safe_name(name)
    assert "/" not in clean and "\\" not in clean and ":" not in clean
    assert not clean.endswith((".", " "))
    assert clean not in {"NUL", "CON.txt"}


def test_overlapping_course_folders_rejected(setup):
    store, folders = setup
    with pytest.raises(ValueError):
        store.bind(2, str(folders[0]), True)
    child = folders[0] / "nested"
    child.mkdir()
    with pytest.raises(ValueError):
        store.bind(2, str(child), True)


def test_rebinding_and_course_rename_preserve_settings(setup):
    store, folders = setup
    store.refresh_courses([{"id": 1, "name": "高等数学"}])
    course = next(c for c in store.courses() if c["id"] == 1)
    assert course["folder"] == str(folders[0].resolve())
    assert course["enabled"] == 1
