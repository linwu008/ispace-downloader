from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

from filelock import FileLock, Timeout

from .moodle import Moodle, browser_login, safe_error
from .security import LoginRequired, Vault
from .state import now
from .sync import SyncEngine


class BusyError(Exception):
    pass


class Service:
    def __init__(self, store, vault=None, platform_factory=Moodle, login=browser_login):
        self.store = store
        self.vault = vault or Vault(store.directory)
        self.platform_factory = platform_factory
        self.login = login
        try:
            with self.lock():
                from .previews import cleanup
                cleanup(store)
        except Timeout:
            pass
        self.pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="ispace")

    def lock(self):
        return FileLock(self.store.directory / "operation.lock", timeout=0, thread_local=False)

    def busy(self):
        try:
            with self.lock():
                return False
        except Timeout:
            return True

    def authenticated(self):
        if self.store.setting("auth_blocked", False):
            raise LoginRequired("自动登录已暂停，请手动重新登录或更新密码")
        platform = self.platform_factory(self.vault.load_session())
        try:
            platform.check_login()
            self.store.set("auth_state", "logged_in")
            return platform
        except LoginRequired:
            platform.close()
        except Exception:
            platform.close()
            raise
        credentials = self.vault.credentials()
        if not credentials:
            raise LoginRequired("请先登录并保存账号，或打开手动登录窗口")
        try:
            self.login(self.vault, **credentials)
        except LoginRequired:
            self.store.set("auth_blocked", True)
            raise
        platform = self.platform_factory(self.vault.load_session())
        try:
            platform.check_login()
        except Exception:
            platform.close()
            raise
        self.store.set("auth_state", "logged_in")
        return platform

    def start(self, operation, **kwargs):
        lock = self.lock()
        try:
            lock.acquire()
        except Timeout:
            raise BusyError("已有任务正在进行，请等待完成") from None
        self.store.set("operation", {"name": operation, "status": "running", "started": now()})
        try:
            self.pool.submit(self._reserved, lock, operation, kwargs)
        except Exception:
            lock.release()
            raise

    def execute(self, operation, **kwargs):
        lock = self.lock()
        try:
            lock.acquire()
        except Timeout:
            raise BusyError("已有任务正在进行") from None
        return self._reserved(lock, operation, kwargs)

    def _reserved(self, lock, operation, kwargs):
        run_id, platform = None, None
        self.store.set("operation", {"name": operation, "status": "running", "started": now()})
        try:
            if operation == "preview":
                from .previews import prepare
                result = prepare(self.store, kwargs["item_id"])
                if result is None:
                    platform = self.authenticated()
                    result = prepare(self.store, kwargs["item_id"], platform)
            elif operation == "catalog":
                from .courses import discover
                platform = self.authenticated()
                result = discover(self.store, platform, kwargs["course_id"])
            elif operation == "organize":
                from .organize import execute
                result = execute(self.store, kwargs["preview_id"], kwargs["selected"])
            elif operation in {"login", "manual_login"}:
                if operation == "login":
                    # A new explicit login must validate the submitted account, not reuse another session.
                    self.vault.path.unlink(missing_ok=True)
                self.login(self.vault, username=kwargs.get("username"), password=kwargs.get("password"), manual=operation == "manual_login")
                if operation == "login":
                    self.vault.save_credentials(kwargs["username"], kwargs["password"])
                self.store.set("auth_blocked", False)
                self.store.set("auth_state", "logged_in")
                self.store.set("auth_checked_at", now())
                result = {"status": "success", "message": "登录成功，请刷新课程并绑定目录"}
            else:
                if operation in {"sync", "download"}:
                    run_id = self.store.start_run()
                platform = self.authenticated()
                self.store.set("auth_checked_at", now())
                if operation == "courses":
                    courses = platform.courses()
                    self.store.refresh_courses(courses)
                    self.store.set("courses_checked_at", now())
                    result = {"status": "success", "message": f"已读取 {len(courses)} 门课程，请为需要同步的课程绑定目录"}
                elif operation in {"sync", "download"}:
                    self.store.refresh_courses(platform.courses())
                    self.store.set("courses_checked_at", now())
                    result = SyncEngine(self.store, platform).run(run_id, course_ids=kwargs.get("course_ids"), material_ids=kwargs.get("material_ids"))
                else:
                    raise ValueError("未知操作")
            self.store.set("operation", {"name": operation, "finished": now(), **result})
            return result
        except Exception as exc:
            message = safe_error(exc)
            status = "auth_required" if isinstance(exc, LoginRequired) else "failed"
            if isinstance(exc, LoginRequired):
                self.store.set("auth_state", "auth_required")
                if operation in {"login", "manual_login"}:
                    self.store.set("auth_blocked", True)
            if run_id:
                with self.store.connect() as db:
                    row = db.execute("SELECT status FROM runs WHERE id=?", (run_id,)).fetchone()
                if row[0] == "running":
                    self.store.finish(run_id, status, 0, 0, 1, message)
            result = {"name": operation, "status": status, "message": message, "finished": now()}
            self.store.set("operation", result)
            return result
        finally:
            if platform:
                platform.close()
            lock.release()
