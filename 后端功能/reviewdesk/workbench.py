"""Local code-reading workspace: immutable knowledge, jobs and long manuals."""

from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
import hashlib
from pathlib import Path, PurePosixPath
import threading
import time
from typing import Callable
from uuid import uuid4

from .analysis import analyze_sources, scan_project
from .api import ApiError
from .context import redact_secrets
from .demo_project import DEMO_FILES
from .models import SourceFile, Usage
from .service import scrub
from .storage import CredentialStore, app_data_dir
from .workbench_store import WorkbenchStore

APP_VERSION = "0.2.0"


def validated_sources(files: list[dict], language: str = "python") -> list[SourceFile]:
    """Validate only data paths, redact secrets, and enforce the desktop limits."""
    from .languages import get_adapter
    get_adapter(language)
    if not isinstance(files, list) or not 1 <= len(files) <= 500:
        raise ValueError("请提供 1 到 500 个源码文件。")
    result, paths, total = [], set(), 0
    for file in files:
        path, content = file.get("path"), file.get("content")
        if not isinstance(path, str) or not isinstance(content, str):
            raise ValueError("每个文件必须包含文本路径和源码。")
        path = path.replace("\\", "/")
        p = PurePosixPath(path)
        if (not path or len(path) > 500 or p.is_absolute() or ".." in p.parts
                or any(ord(c) < 32 for c in path) or ":" in path or str(p) != path):
            raise ValueError("文件路径必须是项目内的相对路径，不能包含上级目录或控制字符。")
        if language == "python" and p.suffix.lower() != ".py":
            raise ValueError("当前仅支持 Python .py 文件，其他语言适配器尚未启用。")
        if path.casefold() in paths:
            raise ValueError("同一版本中不能有重复文件路径（含大小写冲突）。")
        size = len(content.encode("utf-8"))
        total += size
        if size > 256 * 1024 or total > 8 * 1024 * 1024:
            raise ValueError("单文件限 256 KiB，项目源码合计限 8 MiB，请分模块导入。")
        paths.add(path.casefold())
        content = redact_secrets(content)
        result.append(SourceFile(path, content, hashlib.sha256(content.encode("utf-8")).hexdigest()))
    return result


class Workbench:
    """One bounded worker, durable status, and no automatic external calls."""

    def __init__(self, directory: Path | None = None):
        from .knowledge import KnowledgeStore
        self.directory = Path(directory) if directory else app_data_dir()
        self.directory.mkdir(parents=True, exist_ok=True)
        self.knowledge = KnowledgeStore(self.directory / "knowledge.db")
        self.store = WorkbenchStore(self.directory)
        self.store.recover_jobs()
        self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="reading-workbench")
        self.lock = threading.RLock()
        self.pending = 0
        self.closed = False
        self.session_key = ""
        self.previews: dict[str, dict] = {}

    def key(self) -> str:
        """Read a session or OS credential without exposing it through API."""
        if self.session_key:
            return self.session_key
        try:
            return CredentialStore().get_key()
        except RuntimeError:
            return ""

    def set_key(self, key: str, remember: bool) -> None:
        """Persist only when explicitly requested by the settings form."""
        if not key.strip() or len(key) > 512:
            raise ValueError("密钥不能为空，且不得超过 512 个字符。")
        if remember:
            CredentialStore().set_key(key)
        self.session_key = key.strip()

    def delete_key(self) -> None:
        """Clear this application credential and its memory-only override."""
        CredentialStore().delete_key()
        self.session_key = ""

    def submit(self, kind: str, function: Callable[[str], dict]) -> str:
        """Limit queued tasks to prevent repeated clicks exhausting memory."""
        def execute() -> None:
            try:
                self.store.update_job(identifier, "running", 5, "开始处理")
                result = function(identifier)
                message = ("说明书已保存；自动导出失败，可下载正文或仅重试导出。"
                           if result.get("export_status") == "failed" else "处理完成")
                self.store.update_job(identifier, "succeeded", 100, message, result)
            except (ValueError, RuntimeError, OSError) as exc:
                message = scrub(str(exc))[:1000] or "处理失败，请缩小项目后重试。"
                self.store.update_job(identifier, "failed", 100, "处理失败", error=message)
            except Exception:
                self.store.update_job(identifier, "failed", 100, "处理失败",
                                      error="处理时出现异常；已有版本和说明书保留，请缩小输入后重试。")
            finally:
                with self.lock:
                    self.pending -= 1

        # Queue and close share a lock; a shutdown cannot strand a queued job.
        with self.lock:
            if self.closed:
                raise ValueError("后台正在退出，请重新启动软件。")
            if self.pending >= 4:
                raise ValueError("已有四个任务正在处理或排队，请等待完成。")
            identifier = self.store.create_job(kind)
            self.pending += 1
            try:
                self.executor.submit(execute)
            except RuntimeError:
                self.pending -= 1
                self.store.update_job(identifier, "failed", 100, "任务未提交",
                                      error="后台已关闭；未发送模型请求，请重新打开软件。")
                raise ValueError("后台已关闭，请重新打开软件。") from None
        return identifier

    def import_files(self, name: str, files: list[dict], language: str = "python") -> str:
        """Validate before queueing and preserve a stable sanitized snapshot."""
        sources = validated_sources(files, language)
        return self.submit("import", lambda job: self._index(job, name, sources, language))

    def import_folder(self, name: str, path: str, language: str = "python") -> str:
        """Read only the local folder deliberately chosen by the current user."""
        from .languages import get_adapter
        get_adapter(language)
        if language != "python":
            raise ValueError("目录导入目前仅支持 Python。")
        target = Path(path).expanduser()
        if not target.is_dir():
            raise ValueError("项目目录不存在，请填写本机已有的文件夹路径。")

        def index(job: str) -> dict:
            self.store.update_job(job, "running", 10, "只读扫描 Python 文件")
            analysis = scan_project(target)
            sources = validated_sources([asdict(s) for s in analysis.files], language)
            return self._index(job, name or target.name, sources, language, analysis.limitations)
        return self.submit("import", index)

    def import_demo(self) -> str:
        """Import teaching examples exactly as user source data."""
        return self.import_files("购物订单 · 三模块教学示例",
                                 [{"path": path, "content": content} for path, content in DEMO_FILES.items()])

    def _index(self, job: str, name: str, sources: list[SourceFile], language: str,
               extra_limitations: list[str] | None = None) -> dict:
        from .languages import get_adapter
        self.store.update_job(job, "running", 25, "解析函数、调用与变量变化")
        program = get_adapter(language).analyze(sources)
        analysis = analyze_sources(sources) if language == "python" else None
        program["findings"] = [asdict(item) for item in analysis.findings] if analysis else []
        program.setdefault("limitations", []).extend(extra_limitations or [])
        if analysis:
            program["limitations"].extend(analysis.limitations)
        program["limitations"] = list(dict.fromkeys(program["limitations"]))
        self.store.update_job(job, "running", 75, "保存版本快照与可检索代码单元")
        return self.knowledge.save_revision(scrub(name.strip()) or "未命名 Python 项目", sources, program)

    def get_revision(self, identifier: str) -> dict:
        """Expose one immutable version and the review findings that belong to it."""
        revision = self.knowledge.get_revision(identifier)
        revision["findings"] = revision["program"].get("findings", [])
        return revision

    def _prepared(self, request: dict) -> dict:
        from .manual_ai import prepare_context, preview_request
        revision = self.get_revision(request["revision_id"])
        focus = request.get("focus", "")
        query = focus or "入口 主流程 函数 输入 返回 变量 调用 计算 保存"
        hits = self.knowledge.search(request["revision_id"], query, limit=8, strategy="hybrid")
        return preview_request(prepare_context(revision, hits, focus, request.get("budget", 24000)),
                               request.get("model", "deepseek-flash"), request.get("max_cost_cny", "0.20"))

    @staticmethod
    def _preview_parameters(request: dict) -> tuple:
        return tuple(request.get(k, default) for k, default in (
            ("revision_id", None), ("focus", ""), ("budget", 24000),
            ("model", "deepseek-flash"), ("max_cost_cny", "0.20")))

    def preview_manual(self, request: dict) -> dict:
        """Freeze exact context for a one-use, ten-minute send confirmation."""
        prepared = self._prepared(request)
        with self.lock:
            self.previews = {k: v for k, v in self.previews.items() if time.monotonic()-v["created"] < 600}
            if len(self.previews) >= 32:
                self.previews.pop(next(iter(self.previews)))
            identifier = uuid4().hex
            self.previews[identifier] = {"prepared": prepared, "created": time.monotonic(),
                                         "parameters": self._preview_parameters(request)}
        return {**prepared, "preview_id": identifier}

    def generate_manual(self, request: dict) -> str:
        """Always retain a complete offline manual even when AI enhancement fails."""
        from .manual import build_manual
        self.get_revision(request["revision_id"])
        prepared = None
        if request.get("use_ai"):
            if not self.key():
                raise ValueError("请先在设置中填写 DeepSeek API Key。")
            with self.lock:
                preview = self.previews.get(request.get("preview_id"))
                if (not preview or time.monotonic()-preview["created"] >= 600
                        or preview["parameters"] != self._preview_parameters(request)):
                    raise ValueError("请重新预览并确认本次将发送的源码与费用。")
                prepared = preview["prepared"]
                self.previews.pop(request["preview_id"])

        def generate(job: str) -> dict:
            revision = self.get_revision(request["revision_id"])
            self.store.update_job(job, "running", 20, "撰写模块、函数、调用与变量的完整静态说明")
            markdown = build_manual(revision["program"], revision["findings"], revision["project_name"])
            markdown = scrub(markdown)
            # Validate before any paid call and reserve room for generation metadata.
            if len(markdown) > 1_850_000:
                raise ValueError("该项目的完整说明超过单份文档容量，请分模块导入后生成；尚未发送模型请求。")
            if prepared is not None and len(markdown) > 1_600_000:
                raise ValueError("该项目静态说明已很长，请先使用离线说明，或分模块生成模型讲解；尚未产生本次 API 调用。")
            mode, usage = "local", asdict(Usage(note="离线静态讲解，未调用模型 API。"))
            limitations = list(revision["program"].get("limitations", []))
            if prepared is not None:
                from .manual_ai import expand_manual
                self.store.update_job(job, "running", 55, "DeepSeek 正在逐章讲解；不会自动重试收费请求")
                limitations.extend(prepared["limitations"])
                self.store.record_usage(job, "submitted", asdict(Usage(model=request.get("model", "deepseek-flash"),
                                                                      note="模型请求已提交，结果或用量尚未返回。")))
                try:
                    prose, usage, warnings = expand_manual(prepared, self.key(), request.get("model", "deepseek-flash"))
                    self.store.record_usage(job, "response_received", usage)
                    mode = "ai"
                    title, _, details = markdown.partition("\n")
                    markdown = (title + "\n\n## DeepSeek 辅助讲解\n\n"
                                "建议先读这部分连贯讲解，再查后面的逐项源码索引。以下来自模型；引用编号已经检查，逻辑仍需对照源码。\n\n"
                                + prose + "\n\n---\n\n## 静态结构与逐项源码索引\n" + details)
                    limitations.extend(warnings)
                except ApiError as exc:
                    mode = "partial"
                    usage = asdict(exc.usage or Usage(model=request.get("model", "deepseek-flash"),
                                                      note="模型请求失败，是否产生费用未知，请检查平台账单。"))
                    self.store.record_usage(job, "failed_with_usage" if usage["actual"] else "outcome_unknown", usage)
                    limitations.append("模型辅助讲解未完成：" + str(exc))
                    markdown += "\n\n## 模型讲解状态\n\n" + str(exc) + " 完整静态说明已保留。\n"
            markdown += ("\n\n## 本次生成记录\n\n"
                         f"- 项目版本：`{request['revision_id']}`\n"
                         f"- 生成方式：{'离线静态说明' if mode == 'local' else '静态说明与模型辅助讲解'}\n"
                         f"- API 用量：{'已返回真实 token' if usage['actual'] else usage['note']}\n")
            if usage["actual"]:
                markdown += (f"- 输入 / 输出 token：{usage['prompt_tokens']} / {usage['completion_tokens']}\n"
                             f"- 估算费用：¥{usage['estimated_cost_cny']}，以平台账单为准。\n")
            if limitations:
                markdown += "\n### 阅读时需要保留的边界\n\n" + "\n".join("- " + scrub(t) for t in dict.fromkeys(limitations))
            self.store.update_job(job, "running", 90, "保存说明书；超过 5000 字自动生成 Markdown 文档")
            manual = self.store.save_manual(request["revision_id"], revision["project_name"],
                                            markdown, mode, usage, list(dict.fromkeys(limitations)))
            return {"manual_id": manual["id"], "revision_id": request["revision_id"],
                    "character_count": manual["character_count"], "auto_exported": manual["auto_exported"],
                    "export_status": manual["export_status"], "export_error": manual["export_error"]}
        return self.submit("manual", generate)

    def export_manual(self, identifier: str) -> dict:
        """Serialize manual export retries with project deletion, without API calls."""
        with self.lock:
            return self.store.export_manual(identifier)

    def delete_project(self, project_id: str) -> None:
        """Do not race a running import/manual with deletion."""
        with self.lock:
            if self.pending:
                raise ValueError("请等待当前任务完成，再删除项目。")
            revisions = self.knowledge.list_revisions(project_id)
            self.store.delete_revision_manuals([r.get("id", r.get("revision_id")) for r in revisions])
            self.knowledge.delete_project(project_id)
            self.previews.clear()

    def close(self) -> None:
        """Finish already submitted work before releasing the local worker."""
        with self.lock:
            self.closed = True
        self.executor.shutdown(wait=True)
