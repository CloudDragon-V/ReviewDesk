"""Loopback-only HTTP surface for the packaged Vue reading workspace."""

from contextlib import asynccontextmanager
import hmac
from pathlib import Path
import secrets
import sys
from typing import Callable
from urllib.parse import urlsplit, quote

from fastapi import FastAPI, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, Response
from pydantic import BaseModel, ConfigDict, Field, StrictBool

from .api import MODELS
from .languages import supported_languages
from .workbench import APP_VERSION, Workbench


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=False)


class SourceInput(Input):
    path: str = Field(min_length=1, max_length=500)
    content: str = Field(max_length=262144)


class ImportInput(Input):
    project_name: str = Field(default="我的 Python 项目", max_length=150)
    language: str = Field(default="python", max_length=40)
    files: list[SourceInput] = Field(min_length=1, max_length=500)


class FolderInput(Input):
    path: str = Field(min_length=1, max_length=2000)
    project_name: str = Field(default="", max_length=150)
    language: str = Field(default="python", max_length=40)


class ManualInput(Input):
    revision_id: str = Field(min_length=1, max_length=100)
    use_ai: StrictBool = False
    model: str = Field(default="deepseek-flash", max_length=60)
    max_cost_cny: str = Field(default="0.20", max_length=30)
    focus: str = Field(default="", max_length=2000)
    budget: int = Field(default=24000, ge=2000, le=64000, strict=True)
    preview_id: str | None = Field(default=None, max_length=100)


class KeyInput(Input):
    key: str = Field(min_length=1, max_length=512)
    remember: StrictBool = True


def frontend_directory() -> Path:
    """Use bundled static resources or the source tree's Vite production build."""
    if getattr(sys, "frozen", False):
        return Path(sys._MEIPASS) / "web"
    return Path(__file__).resolve().parents[2] / "前端功能" / "web" / "dist"


def create_app(directory: Path | None = None, frontend: Path | None = None,
               shutdown: Callable[[], None] | None = None) -> FastAPI:
    """Create an isolated app; secrets remain in backend memory/OS credentials."""
    workspace = Workbench(directory)
    static = (frontend or frontend_directory()).resolve()
    token = secrets.token_urlsafe(32)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        yield
        workspace.close()

    app = FastAPI(title="ReviewDesk 阅读工作台", version=APP_VERSION,
                  docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)
    app.state.workspace = workspace
    app.state.token = token

    @app.middleware("http")
    async def local_boundary(request: Request, call_next):
        try:
            hostname = urlsplit("//" + request.headers.get("host", "")).hostname
        except ValueError:
            return JSONResponse({"detail": "无效的本机地址。"}, status_code=403)
        if hostname not in {"127.0.0.1", "localhost", "::1"}:
            return JSONResponse({"detail": "本地工作台仅接受本机访问。"}, status_code=403)
        origin = request.headers.get("origin")
        if origin and origin != f"{request.url.scheme}://{request.url.netloc}":
            return JSONResponse({"detail": "已拒绝其他网页发起的跨来源请求。"}, status_code=403)
        if request.headers.get("sec-fetch-site") == "cross-site":
            return JSONResponse({"detail": "已拒绝跨站请求。"}, status_code=403)
        if request.url.path.startswith("/api/") and request.url.path != "/api/bootstrap":
            supplied = request.headers.get("x-reviewdesk-token", "")
            if request.url.path.endswith("/download") and request.method == "GET":
                supplied = supplied or request.query_params.get("token", "")
            if not hmac.compare_digest(supplied.encode("utf-8"), token.encode("ascii")):
                return JSONResponse({"detail": "会话已过期，请刷新工作台。"}, status_code=403)
        if request.method in {"POST", "PUT", "PATCH"}:
            if request.headers.get("content-type", "").split(";")[0].strip() != "application/json":
                return JSONResponse({"detail": "请求必须使用 JSON 格式。"}, status_code=415)
            maximum = 12 * 1024 * 1024
            try:
                if int(request.headers.get("content-length", "0")) > maximum:
                    return JSONResponse({"detail": "上传数据超过 12 MiB 限制。"}, status_code=413)
            except ValueError:
                return JSONResponse({"detail": "无效的请求长度。"}, status_code=400)
            pieces, size = [], 0
            async for part in request.stream():
                size += len(part)
                if size > maximum:
                    return JSONResponse({"detail": "上传数据超过 12 MiB 限制。"}, status_code=413)
                pieces.append(part)
            request._body = b"".join(pieces)
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
            "img-src 'self' data:; connect-src 'self'; object-src 'none'; frame-ancestors 'none'; base-uri 'none'")
        if request.url.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
        return response

    @app.exception_handler(ValueError)
    async def value_error(request: Request, exc: ValueError):
        return JSONResponse({"detail": str(exc)}, status_code=400)

    @app.exception_handler(RuntimeError)
    async def runtime_error(request: Request, exc: RuntimeError):
        return JSONResponse({"detail": str(exc)}, status_code=400)

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, exc: RequestValidationError):
        # Pydantic's default payload can echo a password or an entire source file.
        fields = [".".join(str(part) for part in error["loc"][1:]) for error in exc.errors()]
        return JSONResponse({"detail": "输入格式不正确，请检查：" + "、".join(fields[:6])}, status_code=422)

    @app.get("/api/bootstrap")
    def bootstrap():
        return {"application": "ReviewDesk", "token": token, "version": APP_VERSION, "languages": supported_languages(),
                "models": list(MODELS), "has_key": bool(workspace.key()),
                "retrieval": "符号与关键词检索 + 调用关系扩展", "character_threshold": 5000}

    @app.get("/api/projects")
    def projects():
        return {"projects": workspace.knowledge.list_projects()}

    @app.post("/api/import")
    def import_files(payload: ImportInput):
        return {"job_id": workspace.import_files(payload.project_name,
                                                 [f.model_dump() for f in payload.files], payload.language)}

    @app.post("/api/import-folder")
    def import_folder(payload: FolderInput):
        return {"job_id": workspace.import_folder(payload.project_name, payload.path, payload.language)}

    @app.post("/api/demo")
    def demo():
        return {"job_id": workspace.import_demo()}

    @app.get("/api/jobs/{identifier}")
    def job(identifier: str):
        return workspace.store.get_job(identifier)

    @app.get("/api/jobs")
    def jobs(limit: int = Query(default=50, ge=1, le=100)):
        return {"jobs": workspace.store.list_jobs(limit)}

    @app.get("/api/projects/{identifier}/revisions")
    def revisions(identifier: str):
        return {"revisions": workspace.knowledge.list_revisions(identifier)}

    @app.delete("/api/projects/{identifier}")
    def delete_project(identifier: str):
        workspace.delete_project(identifier)
        return {"ok": True}

    @app.get("/api/revisions/{identifier}")
    def revision(identifier: str):
        return workspace.get_revision(identifier)

    @app.get("/api/search")
    def search(revision_id: str, q: str, strategy: str = "hybrid"):
        return {"results": workspace.knowledge.search(revision_id, q, strategy=strategy)}

    @app.post("/api/manual/preview")
    def preview(payload: ManualInput):
        return workspace.preview_manual(payload.model_dump())

    @app.post("/api/manual")
    def generate_manual(payload: ManualInput):
        return {"job_id": workspace.generate_manual(payload.model_dump())}

    @app.get("/api/manuals")
    def manuals(revision_id: str):
        workspace.get_revision(revision_id)
        return {"manuals": workspace.store.list_manuals(revision_id)}

    @app.get("/api/manuals/{identifier}")
    def manual(identifier: str):
        return workspace.store.get_manual(identifier)

    @app.post("/api/manuals/{identifier}/export")
    def export_manual(identifier: str, payload: Input):
        return workspace.export_manual(identifier)

    @app.get("/api/manuals/{identifier}/download")
    def download(identifier: str):
        result = workspace.store.get_manual(identifier)
        filename = quote(f"代码说明书_{identifier[:8]}.md")
        return Response(result["markdown"].encode("utf-8"), media_type="text/markdown; charset=utf-8",
                        headers={"Content-Disposition": "attachment; filename*=UTF-8''" + filename})

    @app.get("/api/compare")
    def compare(base_id: str, target_id: str):
        return workspace.knowledge.compare_revisions(base_id, target_id)

    @app.post("/api/settings/key")
    def save_key(payload: KeyInput):
        workspace.set_key(payload.key, payload.remember)
        return {"ok": True, "has_key": True}

    @app.delete("/api/settings/key")
    def delete_key():
        workspace.delete_key()
        return {"ok": True, "has_key": False}

    @app.post("/api/shutdown")
    def stop():
        if workspace.pending:
            raise ValueError("任务尚未结束，请完成后再退出后台。")
        if shutdown:
            shutdown()
        return {"ok": True}

    @app.get("/{resource:path}")
    def frontend_file(resource: str):
        if resource.startswith("api/"):
            return JSONResponse({"detail": "接口不存在。"}, status_code=404)
        target = (static / (resource or "index.html")).resolve()
        if not target.is_relative_to(static):
            return JSONResponse({"detail": "文件不存在。"}, status_code=404)
        if not target.is_file():
            # Vue history routes get the shell; unknown assets do not.
            if "." in resource or resource.startswith("assets/"):
                return JSONResponse({"detail": "文件不存在。"}, status_code=404)
            target = static / "index.html"
        if not target.is_file():
            return JSONResponse({"detail": "前端尚未构建，请执行新版构建脚本。"}, status_code=503)
        return FileResponse(target)

    return app
