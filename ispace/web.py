from __future__ import annotations

import os
import secrets
import subprocess
from pathlib import Path
from urllib.parse import urlsplit

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.exceptions import RequestValidationError
from fastapi.staticfiles import StaticFiles
from filelock import Timeout
from pydantic import BaseModel, Field

from . import __version__, catalog, organize
from .scheduler import configure, next_run
from .service import Service, BusyError
from .state import Store, data_dir


class LoginBody(BaseModel):
    username: str = Field(min_length=1, max_length=200)
    password: str = Field(min_length=1, max_length=1000, repr=False)


class BindingBody(BaseModel):
    folder: str = Field(min_length=1, max_length=2000)
    enabled: bool = True


class ScheduleBody(BaseModel):
    enabled: bool
    time: str = Field(pattern=r"^(?:[01]\d|2[0-3]):[0-5]\d$")



class GroupBody(BaseModel):
    mode: str = Field(pattern="^(auto|manual)$")
    folder: str | None = Field(default=None, max_length=1000)


class PlacementBody(BaseModel):
    group_id: str


class PreviewBody(BaseModel):
    course_id: int | None = None
    group_id: str | None = None


class ExecuteBody(BaseModel):
    preview_id: str
    selected: list[str] = Field(min_length=1, max_length=1000)


def create_app(store=None, service=None):
    store = store or Store(data_dir())
    service = service or Service(store)
    app = FastAPI(title="iSpace 学习资料", version=__version__, docs_url=None, redoc_url=None, openapi_url=None)
    app.state.service = service
    csrf = secrets.token_urlsafe(32)
    static = Path(__file__).parent / "static"

    @app.middleware("http")
    async def local_only(request: Request, call_next):
        host = urlsplit("http://" + request.headers.get("host", "")).hostname
        if host not in {"127.0.0.1", "localhost", "::1"}:
            return JSONResponse({"detail": "仅允许本机访问"}, status_code=403)
        origin = request.headers.get("origin")
        if origin and origin != str(request.base_url).rstrip("/"):
            return JSONResponse({"detail": "拒绝跨站请求"}, status_code=403)
        if request.headers.get("sec-fetch-site") == "cross-site":
            return JSONResponse({"detail": "拒绝跨站请求"}, status_code=403)
        if request.method not in {"GET", "HEAD"} and not secrets.compare_digest(request.headers.get("x-ispace-token", ""), csrf):
            return JSONResponse({"detail": "页面已过期，请刷新后再试"}, status_code=403)
        if int(request.headers.get("content-length", "0")) > 8192:
            return JSONResponse({"detail": "请求过大"}, status_code=413)
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Content-Security-Policy"] = "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
        return response

    @app.exception_handler(RequestValidationError)
    async def validation_handler(request, exc):
        return JSONResponse({"detail": "输入无效，请检查必填内容和格式"}, status_code=422)

    @app.exception_handler(BusyError)
    async def busy_handler(request, exc):
        return JSONResponse({"detail": str(exc)}, status_code=409)

    @app.exception_handler(Timeout)
    async def lock_handler(request, exc):
        return JSONResponse({"detail": "任务执行中，请稍后修改设置"}, status_code=409)

    @app.exception_handler(ValueError)
    async def value_handler(request, exc):
        return JSONResponse({"detail": str(exc)}, status_code=400)

    @app.get("/")
    def index():
        return FileResponse(static / "index.html")

    @app.get("/api/state")
    def state():
        schedule_time = store.setting("schedule_time", "20:00")
        enabled = store.setting("schedule_enabled", False)
        busy = service.busy()
        operation = store.setting("operation", {})
        if not busy and operation.get("status") == "running":
            operation = {**operation, "status": "interrupted", "message": "上次操作已中断，请重试"}
        return {
            "version": __version__, "csrf": csrf, "busy": busy,
            "auth": store.setting("auth_state", "not_logged_in"),
            "auth_checked_at": store.setting("auth_checked_at"),
            "courses": store.courses(), "operation": operation,
            "schedule": {"enabled": enabled, "time": schedule_time, "next": next_run(schedule_time) if enabled else None},
            "groups": catalog.groups(store),
            "organization_history": organize.history(store),
            **store.history(),
        }

    @app.post("/api/login", status_code=202)
    def login(body: LoginBody):
        service.start("login", username=body.username, password=body.password)
        return {"accepted": True}

    @app.post("/api/login/manual", status_code=202)
    def manual_login():
        service.start("manual_login")
        return {"accepted": True}

    @app.post("/api/logout")
    def logout():
        with service.lock():
            service.vault.clear()
            store.set("auth_state", "not_logged_in")
            store.set("auth_blocked", False)
        return {"ok": True}

    @app.post("/api/courses/refresh", status_code=202)
    def courses():
        service.start("courses")
        return {"accepted": True}

    @app.put("/api/courses/{course_id}")
    def bind(course_id: int, body: BindingBody):
        with service.lock():
            store.bind(course_id, body.folder, body.enabled)
        return {"ok": True}

    @app.post("/api/sync", status_code=202)
    def sync():
        service.start("sync")
        return {"accepted": True}

    @app.put("/api/schedule")
    def schedule(body: ScheduleBody):
        with service.lock():
            configure(store, body.enabled, body.time)
        return {"ok": True}

    @app.post("/api/folder")
    def choose_folder():
        if os.name != "nt":
            raise ValueError("请选择并输入本地目录的绝对路径")
        script = Path(__file__).resolve().parent / "static" / "choose-folder.ps1"
        try:
            result = subprocess.run(["powershell.exe", "-NoProfile", "-STA", "-ExecutionPolicy", "Bypass", "-File", str(script)], capture_output=True, timeout=180, creationflags=subprocess.CREATE_NO_WINDOW)
        except subprocess.TimeoutExpired:
            raise ValueError("文件夹选择已超时，请重试") from None
        if result.returncode:
            raise ValueError("无法打开目录选择器，请直接粘贴文件夹路径")
        return {"folder": result.stdout.decode("utf-8-sig").strip()}

    @app.get("/api/groups")
    def group_list(course_id: int | None = None):
        return {"groups": catalog.groups(store, course_id)}

    @app.put("/api/groups/{group_id}")
    def group_binding(group_id: str, body: GroupBody):
        with service.lock():
            return organize.preview(store, group_id=group_id, folder=body.folder, mode=body.mode)

    @app.get("/api/materials")
    def materials(q: str = "", course_id: int | None = None, group_id: str | None = None, status: str | None = None, page: int = 1, page_size: int = 30):
        return catalog.material_page(store, q, course_id, group_id, status, page, page_size)

    @app.put("/api/materials/{item_id}/group")
    def placement(item_id: int, body: PlacementBody):
        with service.lock():
            return organize.preview(store, item_id=item_id, target_group_id=body.group_id)

    @app.post("/api/materials/{item_id}/locate")
    def locate(item_id: int):
        item = catalog.material(store, item_id)
        root = Path(catalog.course_for(store, item["course_id"])["folder"]).resolve()
        path = Path(item["path"] or "")
        if not item["path"] or not path.is_file() or path.is_symlink() or not path.resolve().is_relative_to(root):
            raise ValueError("本地文件不存在或不在课程目录内，请重新检查")
        if os.name != "nt":
            raise ValueError("定位文件仅支持 Windows")
        subprocess.Popen(["explorer.exe", "/select,", str(path.resolve())])
        return {"ok": True}

    @app.post("/api/organization/preview")
    def preview(body: PreviewBody):
        with service.lock():
            return organize.preview(store, course_id=body.course_id, group_id=body.group_id)

    @app.get("/api/organization/{preview_id}")
    def organization_plan(preview_id: str):
        return organize.load(store, preview_id)

    @app.post("/api/organization/execute", status_code=202)
    def execute_plan(body: ExecuteBody):
        service.start("organize", preview_id=body.preview_id, selected=body.selected)
        return {"accepted": True}

    app.mount("/static", StaticFiles(directory=static), name="static")
    return app
