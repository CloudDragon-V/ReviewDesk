"""Run from source or from the self-contained Windows distribution."""
import argparse
import json
import os
from pathlib import Path
import sys

if not getattr(sys, "frozen", False):
    project_root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(project_root / "后端功能"))


def main() -> int:
    """Start the desktop UI or run a packaged-runtime smoke test."""
    parser = argparse.ArgumentParser(description="ReviewDesk Python 代码审查")
    parser.add_argument("--smoke-test", metavar="JSON_PATH")
    parser.add_argument("--demo", action="store_true")
    parser.add_argument("--screenshot", metavar="PNG_PATH")
    options = parser.parse_args()
    from PySide6.QtCore import QTimer
    from PySide6.QtGui import QFont, QIcon
    from PySide6.QtWidgets import QApplication
    from reviewdesk_ui.ui import MainWindow
    from reviewdesk import __version__
    app = QApplication(sys.argv[:1])
    app.setApplicationName("ReviewDesk")
    app.setOrganizationName("ReviewDesk")
    app.setApplicationVersion(__version__)
    app.setStyle("Fusion")
    app.setFont(QFont("Microsoft YaHei UI", 10))
    icon = Path(__file__).resolve().parent / "resources" / "reviewdesk.ico"
    if icon.exists():
        app.setWindowIcon(QIcon(str(icon)))
    window = MainWindow()
    window.show()
    if options.demo or options.smoke_test or options.screenshot:
        QTimer.singleShot(120, window.load_demo)
    if options.smoke_test or options.screenshot:
        state = {"attempts": 0, "started": False}

        def check() -> None:
            state["attempts"] += 1
            if window.analysis is not None and not state["started"]:
                state["started"] = True
                window.run_local_review()
            if window.result is not None and getattr(window, "_thread", None) is None and not getattr(window, "_operations", []):
                if options.screenshot:
                    target = Path(options.screenshot)
                    target.parent.mkdir(parents=True, exist_ok=True)
                    window.grab().save(str(target))
                if options.smoke_test:
                    from reviewdesk.storage import app_data_dir
                    target = Path(options.smoke_test)
                    target.parent.mkdir(parents=True, exist_ok=True)
                    window.service.export_report(window.result, target.with_name(target.stem + "-report.md"))
                    window.service.export_report(window.result, target.with_name(target.stem + "-report.json"))
                    history = window.service.store.history()
                    persisted = bool(history) and window.service.store.load_report(history[0]["id"]).file_count > 0
                    target.write_text(json.dumps({"ok": True, "version": __version__,
                        "frozen": bool(getattr(sys, "frozen", False)), "python": sys.version,
                        "files": len(window.analysis.files), "findings": len(window.result.findings),
                        "nodes": len(window.analysis.nodes), "edges": len(window.analysis.edges),
                        "data_dir": str(app_data_dir()), "api_called": False,
                        "report_persisted": persisted, "markdown_exported": True, "json_exported": True}, ensure_ascii=False, indent=2), encoding="utf-8")
                QTimer.singleShot(350, window.close)
                return
            if state["attempts"] > 150:
                if options.smoke_test:
                    Path(options.smoke_test).write_text('{"ok":false,"error":"smoke timeout"}', encoding="utf-8")
                window.close()
                app.exit(2)
                return
            QTimer.singleShot(200, check)
        QTimer.singleShot(300, check)
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
