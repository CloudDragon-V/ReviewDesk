"""Start a single local Vue workspace with bundled Python and no Node runtime."""

import argparse
import json
import logging
import os
from pathlib import Path
import socket
import sys
import threading
import time
import urllib.request
import webbrowser

if not getattr(sys, "frozen", False):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "后端功能"))


def _json_request(url: str, token: str = "", payload: dict | None = None) -> dict:
    body = json.dumps(payload).encode("utf-8") if payload is not None else None
    req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json", "X-ReviewDesk-Token": token})
    with urllib.request.urlopen(req, timeout=10) as response:
        return json.load(response)


def main() -> int:
    """Start, reuse the existing instance, or exercise the complete offline API."""
    parser = argparse.ArgumentParser(description="ReviewDesk 中文代码阅读工作台")
    parser.add_argument("--port", type=int, default=0)
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument("--data-dir", type=Path)
    parser.add_argument("--smoke-test", type=Path)
    options = parser.parse_args()
    if not 0 <= options.port <= 65535:
        parser.error("端口必须在 0 到 65535 之间")
    from reviewdesk.storage import app_data_dir
    directory = options.data_dir or app_data_dir()
    directory.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(filename=str(directory / "workbench.log"), encoding="utf-8", level=logging.WARNING,
                        format="%(asctime)s %(levelname)s %(message)s", force=True)
    instance_path = directory / "workbench-instance.json"
    lock = (directory / "workbench.lock").open("a+b")
    try:
        if lock.tell() == 0:
            lock.write(b"0")
            lock.flush()
        lock.seek(0)
        if os.name == "nt":
            import msvcrt
            msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        lock.close()
        for _ in range(15):
            try:
                info = json.loads(instance_path.read_text(encoding="utf-8"))
                port = int(info["port"])
                if not 1 <= port <= 65535:
                    break
                url = f"http://127.0.0.1:{port}"
                if _json_request(url + "/api/bootstrap").get("application") == "ReviewDesk":
                    if not options.no_browser:
                        webbrowser.open(url)
                    return 0
            except (OSError, ValueError, KeyError):
                time.sleep(.2)
        raise RuntimeError("工作台已启动但尚未就绪，请稍后再次打开。")

    import uvicorn
    from reviewdesk.web_api import create_app
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", options.port))
    sock.listen(128)
    port = sock.getsockname()[1]
    url = f"http://127.0.0.1:{port}"
    instance_path.write_text(json.dumps({"pid": os.getpid(), "port": port}), encoding="utf-8")
    server = None
    app = create_app(directory, shutdown=lambda: setattr(server, "should_exit", True))
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_config=None,
                                         access_log=False, loop="asyncio", http="h11", ws="none"))
    outcome = {"ok": False}

    def after_start() -> None:
        try:
            deadline = time.monotonic() + 30
            while not server.started:
                if time.monotonic() > deadline or server.should_exit:
                    raise RuntimeError("本机服务启动超时。")
                time.sleep(.1)
            if options.smoke_test:
                token = _json_request(url + "/api/bootstrap")["token"]

                def wait_job(job: str) -> dict:
                    until = time.monotonic() + 60
                    while time.monotonic() < until:
                        state = _json_request(url + "/api/jobs/" + job, token)
                        if state["status"] == "succeeded":
                            return state["result"]
                        if state["status"] not in {"queued", "running"}:
                            raise RuntimeError(state.get("error") or state["status"])
                        time.sleep(.1)
                    raise RuntimeError("测试任务超时。")

                revision = wait_job(_json_request(url + "/api/demo", token, {})["job_id"])["revision_id"]
                data = _json_request(url + "/api/revisions/" + revision, token)
                generated = wait_job(_json_request(url + "/api/manual", token,
                                                   {"revision_id": revision, "use_ai": False})["job_id"])
                manual = _json_request(url + "/api/manuals/" + generated["manual_id"], token)
                assert manual["character_count"] > 5000 and manual["auto_exported"]
                assert Path(manual["document_path"]).is_file()
                with urllib.request.urlopen(url + manual["download_url"] + "?token=" + token) as response:
                    downloaded = response.read().decode("utf-8")
                assert downloaded == manual["markdown"]
                with urllib.request.urlopen(url + "/") as response:
                    assert b"<html" in response.read().lower()
                outcome.update(ok=True, frozen=bool(getattr(sys, "frozen", False)), port=port,
                               files=len(data["sources"]), symbols=len(data["program"]["symbols"]),
                               calls=len(data["program"]["calls"]), character_count=manual["character_count"],
                               auto_exported=True, download_verified=True, api_called=False,
                               frontend_served=True, data_dir=str(directory))
            elif not options.no_browser:
                webbrowser.open(url)
        except Exception as exc:
            outcome["error"] = type(exc).__name__ + ": " + str(exc)[:500]
            logging.error("Startup check failed: %s", type(exc).__name__)
        finally:
            if options.smoke_test:
                options.smoke_test.parent.mkdir(parents=True, exist_ok=True)
                options.smoke_test.write_text(json.dumps(outcome, ensure_ascii=False, indent=2), encoding="utf-8")
                server.should_exit = True

    starter = threading.Thread(target=after_start, daemon=True)
    starter.start()
    try:
        server.run(sockets=[sock])
    finally:
        instance_path.unlink(missing_ok=True)
        sock.close()
        lock.close()
    return 0 if not options.smoke_test or outcome["ok"] else 1


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        logging.error("Workspace startup failed: %s", type(exc).__name__)
        if os.name == "nt" and getattr(sys, "frozen", False):
            import ctypes
            ctypes.windll.user32.MessageBoxW(None, "工作台启动失败，请查看用户数据目录中的 workbench.log。\n" + str(exc)[:300],
                                            "ReviewDesk", 0x10)
        else:
            raise
