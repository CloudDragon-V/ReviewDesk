"""ReviewDesk desktop workspace. Workers keep filesystem and API calls off Qt's UI thread."""

from __future__ import annotations

from collections import deque
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Callable

from PySide6.QtCore import QObject, QSize, Qt, QThread, QTimer, Signal, Slot
from PySide6.QtGui import QColor, QFont, QIcon, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QDialog, QDialogButtonBox, QDoubleSpinBox,
    QFileDialog, QFormLayout, QFrame, QHBoxLayout, QHeaderView, QLabel, QLineEdit,
    QMainWindow, QMessageBox, QPlainTextEdit, QProgressBar, QPushButton, QScrollArea,
    QSizePolicy, QSpinBox, QSplitter, QStackedWidget, QTableWidget, QTableWidgetItem,
    QTextEdit, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget,
)

from reviewdesk.models import Analysis, Context, Finding, ReviewResult
from .widgets import CodeEditor, GraphView


SEVERITY = {"critical": "严重", "high": "高", "medium": "中", "low": "低", "info": "提示"}
CATEGORY = {"correctness": "错误风险", "redundancy": "冗余", "optimization": "可优化"}
STATUS = {"new": "待处理", "accepted": "已采纳", "dismissed": "已忽略"}
COLORS = {"critical": "#b42d40", "high": "#bb3d4d", "medium": "#ae741e", "low": "#397f9a", "info": "#638096"}

DEMO_CODE = '''"""订单服务示例：在本地审查中探索风险与调用关系。"""

import json
import math


class OrderService:
    def __init__(self, discount=0.1):
        self.discount = discount

    def add_order(self, order, orders=[]):
        assert (order.get("price", 0) >= 0, "价格不能为负数")
        orders.append(order)
        return orders

    def average_price(self, prices):
        return sum(prices) / len(prices)

    def load_orders(self, path):
        try:
            with open(path, encoding="utf-8") as stream:
                return json.load(stream)
        except:
            return []

    def total(self, orders):
        subtotal = 0
        for order in orders:
            subtotal += order["price"]
        return subtotal * (1 - self.discount)
        print("订单汇总完成")


def preview_total(orders):
    service = OrderService()
    return service.total(orders)
'''

STYLE = """
* { font-family: "Microsoft YaHei UI", "Segoe UI"; }
QMainWindow, QWidget#workspace { background: #f3f6fb; color: #25344c; }
QWidget { color: #25344c; font-size: 12px; }
QFrame#sidebar { background: #11213b; border: none; }
QLabel#brand { color: white; font-size: 19px; font-weight: 700; letter-spacing: 1px; }
QLabel#brandSub { color: #96aac8; font-size: 10px; }
QLabel#sideFoot { color: #8fa4c1; font-size: 11px; }
QLabel#sideSection { color: #7189a8; font-size: 10px; font-weight: 700; }
QPushButton#nav { background: transparent; color: #b4c3da; text-align: left; border: none;
  border-radius: 8px; padding: 13px 15px; font-size: 13px; }
QPushButton#nav:hover { background: #1c3151; color: white; }
QPushButton#nav:checked { background: #2b4d7f; color: white; font-weight: 600; }
QLabel#pageTitle { color: #172a46; font-size: 26px; font-weight: 700; }
QLabel#eyebrow { color: #6e83a3; font-size: 10px; font-weight: 700; letter-spacing: 2px; }
QLabel#subtitle, QLabel#muted { color: #78879b; font-size: 11px; }
QLabel#pill { background: #e7f3ee; color: #33886c; padding: 7px 12px; border-radius: 13px; font-size: 11px; }
QFrame#card, QFrame#panel { background: white; border: 1px solid #e1e7f0; border-radius: 11px; }
QFrame#metric { background: white; border: 1px solid #e1e7f0; border-radius: 10px; }
QLabel#metricValue { font-size: 25px; font-weight: 700; color: #233a5b; }
QLabel#metricLabel { color: #7f8ba0; font-size: 11px; }
QLabel#panelTitle { color: #31445e; font-size: 12px; font-weight: 600; }
QPushButton { background: white; color: #425875; border: 1px solid #dce3ee; border-radius: 6px; padding: 8px 13px; }
QPushButton:hover { background: #f0f5ff; border-color: #a4badd; }
QPushButton:pressed { background: #e2ebfc; }
QPushButton:disabled { color: #a7b0c0; background: #f3f5f8; border-color: #e5e9f0; }
QPushButton#primary { background: #346bd5; color: white; border: 1px solid #346bd5; font-weight: 600; }
QPushButton#primary:hover { background: #285ac0; }
QPushButton#primary:disabled { background: #a7bbdf; border-color: #a7bbdf; }
QPushButton#quiet { background: transparent; color: #577190; border: none; padding: 5px 7px; }
QPushButton#danger { color: #b94957; }
QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox { background: white; border: 1px solid #dbe3ef; border-radius: 6px; padding: 7px 9px; min-height: 17px; }
QLineEdit:focus, QComboBox:focus, QSpinBox:focus, QDoubleSpinBox:focus { border-color: #638edb; }
QComboBox::drop-down { border: none; width: 24px; }
QComboBox QAbstractItemView { background: white; selection-background-color: #e8f0ff; color: #2f4361; }
QPlainTextEdit, QTextEdit { background: white; border: 1px solid #e1e7f0; border-radius: 6px; padding: 8px; selection-background-color: #d6e5ff; selection-color: #18395e; }
QPlainTextEdit#codeEditor { background: #fcfdff; border: none; border-top: 1px solid #e8edf5; border-radius: 0; padding: 9px 4px; }
QTreeWidget { background: #fbfcfe; border: none; font-size: 11px; outline: none; }
QTreeWidget::item { padding: 7px 2px; border-radius: 4px; }
QTreeWidget::item:selected { background: #e8effc; color: #2a59a2; }
QTableWidget { background: white; border: none; gridline-color: #f0f3f8; selection-background-color: #edf3ff; selection-color: #244d8a; outline: none; }
QTableWidget::item { padding: 7px; border-bottom: 1px solid #f0f3f8; }
QHeaderView::section { background: #f8fafd; color: #7d8ba0; border: none; border-bottom: 1px solid #e8edf5; padding: 9px 7px; font-size: 10px; }
QSplitter::handle { background: #edf1f7; width: 6px; height: 6px; }
QSplitter::handle:hover { background: #becde4; }
QScrollBar:vertical { background: transparent; width: 8px; margin: 0; }
QScrollBar::handle:vertical { background: #cdd7e5; min-height: 25px; border-radius: 4px; }
QScrollBar:horizontal { background: transparent; height: 8px; margin: 0; }
QScrollBar::handle:horizontal { background: #cdd7e5; min-width: 25px; border-radius: 4px; }
QScrollBar::add-line, QScrollBar::sub-line { width: 0; height: 0; }
QScrollBar::add-page, QScrollBar::sub-page { background: transparent; }
QProgressBar { border: none; background: #e4ebf5; height: 3px; }
QProgressBar::chunk { background: #4c7edd; }
QStatusBar { background: #ffffff; color: #7a8ca3; border-top: 1px solid #e1e7f0; font-size: 10px; }
QGraphicsView { border: 1px solid #dfe7f1; border-radius: 10px; }
QDialog { background: #f5f7fb; }
QCheckBox { spacing: 8px; }
"""


def _label(text: str, name: str = "", wrap: bool = False) -> QLabel:
    label = QLabel(text)
    label.setTextFormat(Qt.TextFormat.PlainText)
    label.setWordWrap(wrap)
    if name:
        label.setObjectName(name)
    return label


def _button(text: str, callback: Callable, name: str = "") -> QPushButton:
    button = QPushButton(text)
    if name:
        button.setObjectName(name)
    button.setCursor(Qt.CursorShape.PointingHandCursor)
    button.clicked.connect(callback)
    return button


def _frame(name: str = "panel") -> tuple[QFrame, QVBoxLayout]:
    frame = QFrame()
    frame.setObjectName(name)
    layout = QVBoxLayout(frame)
    layout.setContentsMargins(16, 14, 16, 14)
    layout.setSpacing(12)
    return frame, layout


class _Worker(QThread):
    succeeded = Signal(object)
    failed = Signal(str)

    def __init__(self, operation: Callable[[], Any], parent: QObject) -> None:
        super().__init__(parent)
        self.operation = operation

    @Slot()
    def run(self) -> None:
        try:
            self.succeeded.emit(self.operation())
        except Exception as exc:
            # Backend exceptions already redact credentials; preserve actionable errors.
            self.failed.emit(str(exc))


@dataclass
class _ReviewOutcome:
    """Keep a completed review available even when local persistence fails."""

    result: ReviewResult
    report_id: int | None
    persistence_error: str = ""


class MainWindow(QMainWindow):
    """A read-only review workspace with explicit paid-send preview."""

    operation_finished = Signal()

    def __init__(self, service=None, credential_store=None) -> None:
        super().__init__()
        if service is None:
            from reviewdesk.service import ReviewService
            service = ReviewService()
        if credential_store is None:
            from reviewdesk.storage import CredentialStore
            credential_store = CredentialStore()
        self.service = service
        self.credentials = credential_store
        self.analysis: Analysis | None = None
        self.result: ReviewResult | None = None
        self.project_name = "未命名代码片段"
        self.project_path: Path | None = None
        self.report_id: int | None = None
        self.current_path = ""
        self._paste_mode = False
        self._setting_code = False
        self._dirty = False
        self._thread: QThread | None = None
        self._worker: _Worker | None = None
        self._operations: deque = deque()
        self._closing = False
        self._visible_findings: list[Finding] = []
        self._selected_finding: Finding | None = None
        self._history_rows: list[dict] = []
        self._busy_buttons: list[QPushButton] = []
        self._temporary_key = ""
        self._success_callback: Callable | None = None
        self._error_callback: Callable | None = None
        self.setWindowTitle("ReviewDesk · 代码审查工作台")
        self.resize(1500, 960)
        self.setMinimumSize(1120, 760)
        self.setStyleSheet(STYLE)
        self._build_ui()
        QShortcut(QKeySequence("Ctrl+O"), self, self.open_project)
        QShortcut(QKeySequence("Ctrl+Return"), self, self.run_local_review)
        QShortcut(QKeySequence("Ctrl+Shift+E"), self, self.export_current)
        self.statusBar().showMessage("就绪   ·   源码只读   ·   仅 Python   ·   本地审查无需网络")

    def _build_ui(self) -> None:
        workspace = QWidget()
        workspace.setObjectName("workspace")
        self.setCentralWidget(workspace)
        outer = QHBoxLayout(workspace)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        sidebar = QFrame()
        sidebar.setObjectName("sidebar")
        sidebar.setFixedWidth(194)
        side = QVBoxLayout(sidebar)
        side.setContentsMargins(19, 29, 19, 22)
        side.setSpacing(7)
        side.addWidget(_label("◈  ReviewDesk", "brand"))
        side.addWidget(_label("代码理解  /  有据可查", "brandSub"))
        side.addSpacing(41)
        side.addWidget(_label("WORKSPACE", "sideSection"))
        side.addSpacing(6)
        self.nav_buttons = []
        for index, title in enumerate(("⌘    代码审查", "◇    代码关系", "▤    历史与用量", "⚙    设置")):
            button = _button(title, lambda checked=False, i=index: self.show_page(i), "nav")
            button.setCheckable(True)
            side.addWidget(button)
            self.nav_buttons.append(button)
        side.addStretch()
        hint = _label("先在本地理解代码\n再把必要上下文交给模型", "sideFoot", True)
        hint.setStyleSheet("line-height: 1.5; padding: 12px 1px;")
        side.addWidget(hint)
        side.addSpacing(20)
        side.addWidget(_label("●  Windows Desktop", "sideFoot"))
        side.addWidget(_label("v0.1  ·  Python / DeepSeek", "sideFoot"))
        outer.addWidget(sidebar)
        body = QWidget()
        body_layout = QVBoxLayout(body)
        body_layout.setContentsMargins(27, 23, 27, 16)
        body_layout.setSpacing(13)
        heading = QHBoxLayout()
        title_area = QVBoxLayout()
        title_area.setSpacing(4)
        title_area.addWidget(_label("CODE INTELLIGENCE WORKSPACE", "eyebrow"))
        self.page_title = _label("代码审查", "pageTitle")
        self.page_subtitle = _label("让每条建议都回到代码，让每次调用都有预算。", "subtitle")
        title_area.addWidget(self.page_title)
        title_area.addWidget(self.page_subtitle)
        heading.addLayout(title_area)
        heading.addStretch()
        self.connection_pill = _label("●  本地引擎就绪", "pill")
        heading.addWidget(self.connection_pill, alignment=Qt.AlignmentFlag.AlignTop)
        body_layout.addLayout(heading)
        self.progress = QProgressBar()
        self.progress.setRange(0, 0)
        self.progress.setTextVisible(False)
        self.progress.setFixedHeight(3)
        self.progress.hide()
        body_layout.addWidget(self.progress)
        self.pages = QStackedWidget()
        self.pages.addWidget(self._build_review_page())
        self.pages.addWidget(self._build_graph_page())
        self.pages.addWidget(self._build_history_page())
        self.pages.addWidget(self._build_settings_page())
        body_layout.addWidget(self.pages, 1)
        self.notice = _label("开始一个项目，或载入示例体验完整的本地审查流程。", "muted", True)
        body_layout.addWidget(self.notice)
        outer.addWidget(body, 1)
        self.nav_buttons[0].setChecked(True)

    def _build_review_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 3, 0, 0)
        layout.setSpacing(14)
        metrics = QHBoxLayout()
        metrics.setSpacing(12)
        self.metric_values = {}
        for key, title, value, hint in (
            ("files", "项目文件", "—", "Python 源码快照"),
            ("findings", "待处理建议", "—", "可定位 · 可追溯"),
            ("risk", "高优先级", "—", "优先检查风险"),
            ("cost", "本次估算费用", "¥ 0.0000", "本地分析不调用 API"),
        ):
            frame, inner = _frame("metric")
            inner.setSpacing(3)
            inner.addWidget(_label(title, "metricLabel"))
            number = _label(value, "metricValue")
            self.metric_values[key] = number
            inner.addWidget(number)
            foot = _label(hint, "muted")
            if key == "cost":
                self.cost_hint = foot
            inner.addWidget(foot)
            metrics.addWidget(frame, 1)
        layout.addLayout(metrics)
        toolbar = QHBoxLayout()
        toolbar.setSpacing(7)
        self.open_button = _button("＋ 打开项目", self.open_project)
        self.paste_button = _button("粘贴代码", self.new_snippet)
        self.demo_button = _button("载入示例", self.load_demo, "quiet")
        self.local_button = _button("本地审查", self.run_local_review)
        self.api_button = _button("DeepSeek 审查  ↗", self.run_api_review, "primary")
        for button in (self.open_button, self.paste_button, self.demo_button):
            toolbar.addWidget(button)
        toolbar.addStretch()
        toolbar.addWidget(self.local_button)
        toolbar.addWidget(self.api_button)
        self._busy_buttons.extend([self.open_button, self.paste_button, self.demo_button, self.local_button, self.api_button])
        layout.addLayout(toolbar)
        self.main_splitter = QSplitter(Qt.Orientation.Horizontal)
        source_panel, source_layout = _frame()
        source_layout.setContentsMargins(0, 0, 0, 0)
        source_header = QHBoxLayout()
        source_header.setContentsMargins(13, 11, 12, 8)
        self.source_label = _label("源码工作区", "panelTitle")
        source_header.addWidget(self.source_label)
        source_header.addStretch()
        self.readonly_label = _label("只读快照", "muted")
        source_header.addWidget(self.readonly_label)
        source_layout.addLayout(source_header)
        self.source_splitter = QSplitter(Qt.Orientation.Horizontal)
        self.file_tree = QTreeWidget()
        self.file_tree.setObjectName("fileTree")
        self.file_tree.setHeaderHidden(True)
        self.file_tree.setRootIsDecorated(False)
        self.file_tree.setMinimumWidth(100)
        self.file_tree.itemClicked.connect(self._file_clicked)
        self.code_editor = CodeEditor()
        self.code_editor.setPlaceholderText("打开 Python 项目或点击“载入示例”\n\n源码在本地建立只读快照。\n运行 DeepSeek 审查前可以预览发送内容。")
        self.code_editor.textChanged.connect(self._code_changed)
        self.source_splitter.addWidget(self.file_tree)
        self.source_splitter.addWidget(self.code_editor)
        self.source_splitter.setSizes([140, 440])
        self.source_splitter.setStretchFactor(1, 1)
        source_layout.addWidget(self.source_splitter, 1)
        source_footer = QHBoxLayout()
        source_footer.setContentsMargins(13, 0, 13, 9)
        self.source_info = _label("尚未载入源码", "muted")
        source_footer.addWidget(self.source_info)
        source_footer.addStretch()
        source_footer.addWidget(_label("UTF-8   Python", "muted"))
        source_layout.addLayout(source_footer)
        self.main_splitter.addWidget(source_panel)
        right_splitter = QSplitter(Qt.Orientation.Vertical)
        findings_panel, findings_layout = _frame()
        findings_layout.setContentsMargins(12, 12, 12, 4)
        findings_layout.setSpacing(8)
        finding_header = QHBoxLayout()
        self.finding_count_label = _label("审查建议", "panelTitle")
        finding_header.addWidget(self.finding_count_label)
        finding_header.addStretch()
        self.retry_save_button = _button("重试保存", self.retry_save_report, "quiet")
        self.retry_save_button.hide()
        self._busy_buttons.append(self.retry_save_button)
        finding_header.addWidget(self.retry_save_button)
        self.export_button = _button("导出 ↗", self.export_current, "quiet")
        finding_header.addWidget(self.export_button)
        findings_layout.addLayout(finding_header)
        filter_row = QHBoxLayout()
        filter_row.setSpacing(5)
        self.search_input = QLineEdit()
        self.search_input.setObjectName("findingSearch")
        self.search_input.setPlaceholderText("搜索建议或文件…")
        self.search_input.setClearButtonEnabled(True)
        self.search_input.textChanged.connect(self._filter_findings)
        self.severity_filter = QComboBox()
        self.severity_filter.addItem("全部级别", "all")
        for key, value in SEVERITY.items():
            self.severity_filter.addItem(value, key)
        self.severity_filter.currentIndexChanged.connect(self._filter_findings)
        self.status_filter = QComboBox()
        self.status_filter.addItem("全部状态", "all")
        for key, value in STATUS.items():
            self.status_filter.addItem(value, key)
        self.status_filter.currentIndexChanged.connect(self._filter_findings)
        filter_row.addWidget(self.search_input, 1)
        filter_row.addWidget(self.severity_filter)
        filter_row.addWidget(self.status_filter)
        findings_layout.addLayout(filter_row)
        self.findings_table = QTableWidget(0, 3)
        self.findings_table.setObjectName("findingsTable")
        self.findings_table.setHorizontalHeaderLabels(["级别", "审查建议", "位置"])
        self.findings_table.verticalHeader().hide()
        self.findings_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.findings_table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self.findings_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.findings_table.setShowGrid(False)
        self.findings_table.setWordWrap(False)
        self.findings_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        self.findings_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.findings_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Interactive)
        self.findings_table.setColumnWidth(2, 115)
        self.findings_table.itemSelectionChanged.connect(self._finding_selected)
        findings_layout.addWidget(self.findings_table, 1)
        self.findings_empty = _label("运行审查后，这里会列出有代码依据的建议。", "muted", True)
        self.findings_empty.setContentsMargins(7, 7, 7, 14)
        findings_layout.addWidget(self.findings_empty)
        right_splitter.addWidget(findings_panel)
        detail_panel, detail_layout = _frame()
        detail_layout.setSpacing(8)
        detail_header = QHBoxLayout()
        detail_header.addWidget(_label("证据与建议", "panelTitle"))
        detail_header.addStretch()
        self.locate_button = _button("定位代码 ↖", self.locate_finding, "quiet")
        detail_header.addWidget(self.locate_button)
        detail_layout.addLayout(detail_header)
        self.detail_title = _label("选择一条建议，查看具体原因", "panelTitle", True)
        self.detail_meta = _label("建议不会自动修改项目中的文件。", "muted", True)
        detail_layout.addWidget(self.detail_title)
        detail_layout.addWidget(self.detail_meta)
        self.detail_text = QPlainTextEdit()
        self.detail_text.setObjectName("findingDetail")
        self.detail_text.setReadOnly(True)
        self.detail_text.setPlaceholderText("证据、改进方向与审查来源将在这里展示。")
        detail_text_font = QFont("Microsoft YaHei UI", 10)
        self.detail_text.setFont(detail_text_font)
        detail_layout.addWidget(self.detail_text, 1)
        detail_buttons = QHBoxLayout()
        self.accept_button = _button("✓ 采纳建议", lambda: self.set_finding_status("accepted"))
        self.dismiss_button = _button("忽略", lambda: self.set_finding_status("dismissed"), "quiet")
        self.reset_button = _button("恢复待处理", lambda: self.set_finding_status("new"), "quiet")
        detail_buttons.addWidget(self.accept_button)
        detail_buttons.addWidget(self.dismiss_button)
        detail_buttons.addStretch()
        detail_buttons.addWidget(self.reset_button)
        detail_layout.addLayout(detail_buttons)
        right_splitter.addWidget(detail_panel)
        right_splitter.setSizes([285, 290])
        self.main_splitter.addWidget(right_splitter)
        self.main_splitter.setSizes([640, 565])
        self.main_splitter.setStretchFactor(0, 6)
        self.main_splitter.setStretchFactor(1, 5)
        layout.addWidget(self.main_splitter, 1)
        self._detail_enabled(False)
        return page

    def _build_graph_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 6, 0, 0)
        controls = QHBoxLayout()
        self.graph_summary = _label("尚未载入项目", "panelTitle")
        controls.addWidget(self.graph_summary)
        controls.addStretch()
        controls.addWidget(_button("－", lambda: self.graph_view.zoom(1 / 1.2)))
        controls.addWidget(_button("＋", lambda: self.graph_view.zoom(1.2)))
        controls.addWidget(_button("适应画布", lambda: self.graph_view.fit_graph()))
        layout.addLayout(controls)
        self.graph_view = GraphView()
        self.graph_view.node_activated.connect(self._graph_node_clicked)
        layout.addWidget(self.graph_view, 1)
        layout.addWidget(_label("深色为模块，浅色为类与函数。拖动平移 · 滚轮缩放 · 点击节点定位源码。\n连线来自静态导入、定义或调用推断；动态分派和运行时依赖可能无法解析。", "muted", True))
        return page

    def _build_history_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 6, 0, 0)
        panel, inner = _frame()
        row = QHBoxLayout()
        self.history_summary = _label("本机审查记录", "panelTitle")
        row.addWidget(self.history_summary)
        row.addStretch()
        row.addWidget(_button("刷新", self.refresh_history))
        self.open_history_button = _button("打开选中报告", self.open_history_report)
        row.addWidget(self.open_history_button)
        self._busy_buttons.append(self.open_history_button)
        inner.addLayout(row)
        self.history_table = QTableWidget(0, 6)
        self.history_table.setObjectName("historyTable")
        self.history_table.setHorizontalHeaderLabels(["时间", "项目", "方式", "建议数", "API token", "估算费用"])
        self.history_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.history_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.history_table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self.history_table.verticalHeader().hide()
        self.history_table.setShowGrid(False)
        self.history_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        self.history_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.history_table.cellDoubleClicked.connect(lambda *_: self.open_history_report())
        inner.addWidget(self.history_table, 1)
        inner.addWidget(_label("历史只保留报告、用量与文件指纹。打开旧报告不会恢复源码；重新打开原项目后可继续定位。\nAPI token 以服务端返回为准；费用按记录时的模型价格估算，缓存命中另计。", "muted", True))
        layout.addWidget(panel, 1)
        self.usage_details = QPlainTextEdit()
        self.usage_details.setReadOnly(True)
        self.usage_details.setMaximumHeight(165)
        self.usage_details.setPlaceholderText("打开一份报告，即可查看实际 token 用量、缓存命中、单价来源与计价日期。")
        layout.addWidget(self.usage_details)
        return page

    def _build_settings_page(self) -> QWidget:
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 6, 0, 0)
        layout.setSpacing(18)
        credentials_panel, credentials_layout = _frame()
        credentials_layout.addWidget(_label("DeepSeek 连接", "panelTitle"))
        credentials_layout.addWidget(_label("密钥保存到 Windows 凭据管理器，跟随当前 Windows 用户。安装到另一台电脑后需要重新配置。", "muted", True))
        key_row = QHBoxLayout()
        self.api_key_input = QLineEdit()
        self.api_key_input.setObjectName("apiKeyInput")
        self.api_key_input.setEchoMode(QLineEdit.EchoMode.Password)
        self.api_key_input.setPlaceholderText("输入 API Key，或使用此电脑已保存的凭据")
        self.api_key_input.setMaxLength(256)
        key_row.addWidget(self.api_key_input, 1)
        self.save_key_button = _button("安全保存", self.save_api_key, "primary")
        self.check_key_button = _button("验证连接", self.check_connection)
        self.delete_key_button = _button("删除凭据", self.delete_api_key, "danger")
        for button in (self.save_key_button, self.check_key_button, self.delete_key_button):
            key_row.addWidget(button)
        self._busy_buttons.extend([self.save_key_button, self.check_key_button, self.delete_key_button])
        credentials_layout.addLayout(key_row)
        self.key_status = _label("连接验证只读取账户信息，不生成审查内容。", "muted", True)
        credentials_layout.addWidget(self.key_status)
        layout.addWidget(credentials_panel)
        review_panel, review_layout = _frame()
        review_layout.addWidget(_label("审查与预算", "panelTitle"))
        form = QFormLayout()
        form.setHorizontalSpacing(25)
        form.setVerticalSpacing(15)
        self.model_combo = QComboBox()
        self.model_combo.addItem("DeepSeek Flash · 快速", "deepseek-flash")
        self.model_combo.addItem("DeepSeek V4 Pro · 深度", "deepseek-v4-pro")
        form.addRow("模型", self.model_combo)
        self.strategy_combo = QComboBox()
        self.strategy_combo.addItem("依赖感知 · 优先风险与关联代码", "dependency")
        self.strategy_combo.addItem("顺序截断 · 基线策略", "truncate")
        form.addRow("上下文选择", self.strategy_combo)
        self.token_budget = QSpinBox()
        self.token_budget.setRange(500, 32000)
        self.token_budget.setSingleStep(500)
        self.token_budget.setValue(6000)
        self.token_budget.setSuffix("  tokens（本地估算）")
        form.addRow("输入上下文预算", self.token_budget)
        self.cost_budget = QDoubleSpinBox()
        self.cost_budget.setRange(0.01, 5.0)
        self.cost_budget.setDecimals(2)
        self.cost_budget.setSingleStep(0.05)
        self.cost_budget.setValue(0.20)
        self.cost_budget.setPrefix("¥ ")
        form.addRow("单次估算费用上限", self.cost_budget)
        self.category_combo = QComboBox()
        for title, key in (("全部维度", "all"), ("错误 / 安全 / 可靠性", "correctness"), ("冗余", "redundancy"), ("可优化项", "optimization")):
            self.category_combo.addItem(title, key)
        form.addRow("模型审查重点", self.category_combo)
        review_layout.addLayout(form)
        review_layout.addWidget(_label("每次发送前会显示实际选中的上下文。token 为保守近似值，并非模型精确分词结果；费用上限用于发送前校验，实际账单以 DeepSeek 为准。", "muted", True))
        layout.addWidget(review_panel)
        about_panel, about_layout = _frame()
        about_layout.addWidget(_label("关于这份工作台", "panelTitle"))
        about_layout.addWidget(_label("ReviewDesk 0.1  /  Python 源码审查\n本地 AST 分析 + 有界上下文选择 + 结构化模型建议 + 可追溯报告。\n只读取被审查项目；“采纳”标记用于管理建议，不会替你写入源码。", "muted", True))
        layout.addWidget(about_panel)
        layout.addStretch()
        scroll.setWidget(page)
        return scroll

    def show_page(self, index: int) -> None:
        """Switch workspace pages; history loads asynchronously on demand."""
        self.pages.setCurrentIndex(index)
        titles = ("代码审查", "代码关系", "历史与用量", "设置")
        subtitles = (
            "让每条建议都回到代码，让每次调用都有预算。",
            "从模块到函数，沿着依赖看懂项目。",
            "留住审查依据，清楚记录每次调用的投入。",
            "连接你的模型，控制上下文与每次审查预算。",
        )
        self.page_title.setText(titles[index])
        self.page_subtitle.setText(subtitles[index])
        for i, button in enumerate(self.nav_buttons):
            button.setChecked(i == index)
        if index == 1:
            QTimer.singleShot(0, self.graph_view.fit_graph)
        elif index == 2:
            self.refresh_history()

    def _run(self, message: str, operation: Callable, success: Callable | None = None,
             error: Callable | None = None) -> None:
        self._operations.append((message, operation, success, error))
        if self._thread is None:
            self._start_next()

    def _start_next(self) -> None:
        if self._closing or not self._operations:
            if self._closing:
                self.close()
            return
        message, operation, success, error = self._operations.popleft()
        self.notice.setText(message)
        self.statusBar().showMessage(message)
        self.progress.show()
        for button in self._busy_buttons:
            button.setEnabled(False)
        worker = _Worker(operation, self)
        thread = worker
        self._success_callback = success
        self._error_callback = error
        worker.succeeded.connect(self._task_succeeded)
        worker.failed.connect(self._task_failed)
        thread.finished.connect(self._task_finished)
        self._thread, self._worker = thread, worker
        self.code_editor.setReadOnly(True)
        self._detail_enabled(False)
        thread.start()

    @Slot(object)
    def _task_succeeded(self, value: Any) -> None:
        if isinstance(value, _ReviewOutcome) and value.persistence_error and self._closing:
            # A paid response cannot be recovered from the UI after it closes.
            # Cancel that close only when the worker could not persist the result.
            self._closing = False
            self.setEnabled(True)
        if self._success_callback and not self._closing:
            try:
                self._success_callback(value)
            except Exception as exc:
                self._error(str(exc))

    @Slot(str)
    def _task_failed(self, message: str) -> None:
        if not self._closing:
            (self._error_callback or self._error)(message)

    @Slot()
    def _task_finished(self) -> None:
        thread = self._thread
        if thread is not None:
            # finished is emitted just before native thread teardown completes.
            thread.wait()
            thread.deleteLater()
        self._thread = None
        self._worker = None
        self.code_editor.setReadOnly(not self._paste_mode or bool(self._operations))
        self.progress.hide()
        for button in self._busy_buttons:
            button.setEnabled(True)
        self._detail_enabled(self._selected_finding is not None)
        self.operation_finished.emit()
        QTimer.singleShot(0, self._start_next)

    def _error(self, message: str) -> None:
        if self._temporary_key:
            message = message.replace(self._temporary_key, "[已隐藏密钥]")
        entered_key = self.api_key_input.text().strip()
        if entered_key:
            message = message.replace(entered_key, "[已隐藏密钥]")
        import re
        message = re.sub(r"sk-[A-Za-z0-9_-]{8,}", "[已隐藏密钥]", message)
        self.notice.setText("操作未完成：" + message[:700])
        self.statusBar().showMessage("操作未完成，请查看提示。")
        self._temporary_key = ""

    def open_project(self) -> None:
        """Select a project folder, then scan it without blocking Qt."""
        if self._thread is not None:
            return
        directory = QFileDialog.getExistingDirectory(self, "选择 Python 项目文件夹")
        if directory:
            self.load_project(Path(directory))

    def load_project(self, path: Path) -> None:
        """Load a folder through the bounded source scanner."""
        self.project_path = Path(path)
        self.project_name = Path(path).name
        self._paste_mode = False
        self._run("正在建立项目源码快照…", lambda: self.service.analyze_project(Path(path)), self._set_analysis)

    def new_snippet(self) -> None:
        """Open an editable in-memory snippet; never write it to the source project."""
        if self._thread is not None:
            return
        self.show_page(0)
        self.project_path = None
        self.project_name = "代码片段"
        self._paste_mode = True
        self.analysis = None
        self.result = None
        self.report_id = None
        self.current_path = "snippet.py"
        self.file_tree.clear()
        self.file_tree.addTopLevelItem(QTreeWidgetItem(["snippet.py"]))
        self.code_editor.setReadOnly(False)
        self.code_editor.setPlainText("")
        self.code_editor.setPlaceholderText("在这里粘贴 Python 代码，然后运行本地审查。")
        self.readonly_label.setText("可编辑片段")
        self.source_label.setText("snippet.py")
        self.source_info.setText("内存片段 · 不写入磁盘")
        self._dirty = True
        self._clear_result()
        self.graph_view.set_analysis(Analysis())
        self.code_editor.setFocus()
        self.notice.setText("粘贴代码后按 Ctrl + Enter 运行本地审查。")

    def load_demo(self) -> None:
        """Load a small, offline sample and immediately review it."""
        if self._thread is not None:
            return
        self.show_page(0)
        self.project_path = None
        self.project_name = "订单服务 · 示例项目"
        self._paste_mode = False
        self._run("正在载入示例并分析代码…", lambda: self.service.analyze_code(DEMO_CODE, "order_service.py"),
                  lambda analysis: self._analysis_then_review(analysis, False))

    def _analysis_then_review(self, analysis: Analysis, use_api: bool) -> None:
        self._set_analysis(analysis)
        if use_api:
            self._prepare_api_preview()
        else:
            self._perform_review(False)

    def _set_analysis(self, analysis: Analysis) -> None:
        self.analysis = analysis
        self.result = None
        self.report_id = None
        self._dirty = False
        self.file_tree.clear()
        for source in analysis.files:
            item = QTreeWidgetItem([source.path])
            item.setData(0, Qt.ItemDataRole.UserRole, source.path)
            item.setToolTip(0, source.path)
            self.file_tree.addTopLevelItem(item)
        self.code_editor.setReadOnly(not self._paste_mode or self._thread is not None or bool(self._operations))
        self.readonly_label.setText("可编辑片段" if self._paste_mode else "只读快照")
        if analysis.files:
            self.file_tree.setCurrentItem(self.file_tree.topLevelItem(0))
            self._display_source(analysis.files[0].path)
        else:
            self._setting_code = True
            self.code_editor.clear()
            self._setting_code = False
            self.source_label.setText("未找到可分析源码")
            self.source_info.setText("0 个 Python 文件")
        self.metric_values["files"].setText(str(len(analysis.files)))
        self._clear_result()
        self.graph_view.set_analysis(analysis)
        self.graph_summary.setText(f"{len(analysis.nodes)} 个节点   /   {len(analysis.edges)} 条关系   ·   {self.project_name}")
        if len(analysis.nodes) > 160 or len(analysis.edges) > 800:
            self.graph_summary.setText(self.graph_summary.text() + "  （画布展示前 160 节点 / 800 关系）")
        note = f"已读取 {len(analysis.files)} 个 Python 文件。"
        if analysis.limitations:
            note += " " + "；".join(analysis.limitations[:3])
        self.notice.setText(note)

    def _code_changed(self) -> None:
        if self._paste_mode and not self._setting_code:
            self._dirty = True
            if self.result:
                self.notice.setText("代码片段已修改，当前报告对应上一次快照；请重新审查。")

    def _display_source(self, path: str, line: int = 1) -> None:
        if not self.analysis:
            return
        source = next((source for source in self.analysis.files if source.path == path), None)
        if source is None:
            self.notice.setText(f"报告中的 {path} 未在当前源码快照中找到。请重新打开对应项目。")
            return
        if not (self._paste_mode and self._dirty and path == self.current_path):
            self._setting_code = True
            self.code_editor.setPlainText(source.content)
            self._setting_code = False
        self.current_path = path
        self.source_label.setText(path)
        self.source_label.setToolTip(path)
        self.source_info.setText(f"{len(source.content.splitlines())} 行 · {'可编辑片段' if self._paste_mode else '只读源码快照'}")
        self.code_editor.go_to_line(line)

    def _file_clicked(self, item: QTreeWidgetItem, column: int) -> None:
        path = item.data(0, Qt.ItemDataRole.UserRole)
        if path:
            self._display_source(path)

    def _ready_to_review(self, use_api: bool) -> None:
        if self._thread is not None:
            return
        if self._paste_mode:
            code = self.code_editor.toPlainText()
            if not code.strip():
                self.notice.setText("请先粘贴 Python 代码。")
                return
            def analyzed(analysis):
                if self.code_editor.toPlainText() != code:
                    self._dirty = True
                    self.notice.setText("片段已变化，已保留新内容。请重新运行审查。")
                    return
                self._analysis_then_review(analysis, use_api)
            self._run("正在分析代码片段…", lambda: self.service.analyze_code(code, "snippet.py"), analyzed)
        elif self.analysis and self.analysis.files:
            if use_api:
                self._prepare_api_preview()
            else:
                self._perform_review(False)
        else:
            self.notice.setText("先打开项目、粘贴代码，或载入示例。")

    def run_local_review(self) -> None:
        """Run the local review with no API traffic or usage charge."""
        self._ready_to_review(False)

    def run_api_review(self) -> None:
        """Prepare a source preview before allowing a paid model call."""
        self._ready_to_review(True)

    def _review_options(self) -> dict:
        return {
            "model": self.model_combo.currentData(),
            "budget": self.token_budget.value(),
            "strategy": self.strategy_combo.currentData(),
            "category": self.category_combo.currentData(),
            "max_cost_cny": f"{self.cost_budget.value():.2f}",
        }

    def _prepare_api_preview(self) -> None:
        analysis = self.analysis
        options = self._review_options()
        entered_key = self.api_key_input.text().strip()

        def prepare():
            from reviewdesk.api import estimate_cost
            from reviewdesk.context import estimate_tokens
            from reviewdesk.service import MAX_OUTPUT_TOKENS, SYSTEM_PROMPT
            key = entered_key or self.credentials.get_key()
            if not key:
                raise ValueError("尚未配置 API Key。请到“设置”安全保存密钥，或输入本次使用的密钥。")
            context = self.service.preview(analysis, budget=options["budget"], strategy=options["strategy"])
            prompt = SYSTEM_PROMPT + ("\n本次仅审查类别：" + options["category"] if options["category"] != "all" else "")
            estimate = estimate_cost(options["model"], estimate_tokens(prompt) + context.estimated_tokens + 128,
                                     MAX_OUTPUT_TOKENS, peak=True)
            return key, context, estimate

        self._run("正在准备发送预览，不消耗 API 额度…", prepare,
                  lambda prepared: self._show_preview(prepared, options))

    def _show_preview(self, prepared: tuple[str, Context, Decimal], options: dict) -> None:
        key, context, estimated_cost = prepared
        self._temporary_key = key
        dialog = QDialog(self)
        dialog.setObjectName("contextPreview")
        dialog.setWindowTitle("发送前预览 · DeepSeek 审查")
        dialog.resize(940, 720)
        layout = QVBoxLayout(dialog)
        layout.setContentsMargins(22, 21, 22, 21)
        layout.addWidget(_label("确认发送上下文", "pageTitle"))
        layout.addWidget(_label(f"{options['model']}  ·  {len(context.included_spans)} 个文件  ·  约 {context.estimated_tokens:,} tokens  ·  单次估算上限 ¥ {options['max_cost_cny']}", "muted", True))
        layout.addWidget(_label(f"按高峰时段、缓存未命中与最多 2,500 输出 tokens 保守估算：¥ {estimated_cost:.6f}。实际费用以平台账单为准。", "muted", True))
        layout.addWidget(_label("以下选中代码将发送到 DeepSeek。可识别的密钥会自动遮蔽；请检查预览中是否仍有不应上传的内容。", "muted", True))
        preview = QPlainTextEdit()
        preview.setObjectName("contextText")
        preview.setReadOnly(True)
        preview.setFont(QFont("Cascadia Code", 9))
        preview.setPlainText(context.text)
        layout.addWidget(preview, 1)
        if context.limitations:
            layout.addWidget(_label("上下文范围：" + "；".join(context.limitations), "muted", True))
        layout.addWidget(_label("API 返回的 token 才是真实用量；当前数字为本地估算。审查包含有限输出预算，生成结果后会保存本机报告。", "muted", True))
        buttons = QDialogButtonBox()
        cancel = buttons.addButton("返回检查", QDialogButtonBox.ButtonRole.RejectRole)
        send = buttons.addButton("发送并审查", QDialogButtonBox.ButtonRole.AcceptRole)
        send.setObjectName("primary")
        if estimated_cost > Decimal(options["max_cost_cny"]) or not context.included_spans:
            send.setEnabled(False)
            layout.addWidget(_label("当前估算超过费用上限，或没有可发送的源码。请返回设置调整预算。", "muted", True))
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self._perform_review(True, key, options)
        else:
            self.notice.setText("已取消发送，没有调用 DeepSeek。")
            self._temporary_key = ""

    def _perform_review(self, use_api: bool, key: str = "", options: dict | None = None) -> None:
        analysis = self.analysis
        name = self.project_name
        options = options or self._review_options()
        def review_and_save():
            # Persistence belongs to the worker so closing the UI cannot discard
            # a paid response whose usage has already been returned by the API.
            result = self.service.review(analysis, name, use_api=use_api, api_key=key, **options)
            return self._persist_result(result)
        self._run("DeepSeek 正在审查选中上下文，请稍候…" if use_api else "正在生成本地审查报告…",
                  review_and_save, self._review_completed)

    def _persist_result(self, result: ReviewResult) -> _ReviewOutcome:
        try:
            report_id = self.service.store.save_report(deepcopy(result))
            return _ReviewOutcome(result, report_id)
        except Exception as exc:
            message = f"本机报告保存失败（{type(exc).__name__}）；报告与用量仍保留在窗口中。请导出报告，或检查磁盘空间、数据目录权限后重试保存。"
            if message not in result.limitations:
                result.limitations.append(message)
            return _ReviewOutcome(result, None, message)

    def _review_completed(self, outcome: _ReviewOutcome) -> None:
        self._set_result(outcome.result, outcome.report_id, save_if_missing=False)
        if outcome.persistence_error:
            self.notice.setText(outcome.persistence_error + " 为防止丢失结果，窗口会保持打开。")
            self.statusBar().showMessage("报告未保存到历史   ·   请导出或重试保存后再关闭")
            self.retry_save_button.show()

    def retry_save_report(self) -> None:
        """Retry a failed history write without repeating the paid API call."""
        if self._thread is not None or self._operations or self.result is None or self.report_id is not None:
            return
        current = self.result
        snapshot = deepcopy(current)
        snapshot.limitations = [item.replace("本机报告保存失败（", "此前本机报告保存失败（", 1)
                                if item.startswith("本机报告保存失败（") else item for item in snapshot.limitations]
        self._run("正在重试保存本机报告，不会再次调用 DeepSeek…", lambda: self._persist_result(snapshot),
                  lambda outcome: self._review_completed(outcome) if self.result is current else None)

    def _set_result(self, result: ReviewResult, report_id: int | None = None, *, save_if_missing: bool = True) -> None:
        self._temporary_key = ""
        self.result = result
        self.report_id = report_id
        self.retry_save_button.setVisible(report_id is None and not save_if_missing)
        source = next((source for source in self.analysis.files if source.path == self.current_path), None) if self.analysis else None
        self._dirty = bool(self._paste_mode and source and self.code_editor.toPlainText() != source.content)
        self._update_metrics()
        self._filter_findings()
        self._update_usage_details()
        summary = f"审查完成 · {result.file_count} 个文件 · {len(result.findings)} 条建议"
        if result.limitations:
            summary += " · " + "；".join(result.limitations[:2])
        self.notice.setText(summary)
        self.notice.setToolTip("\n".join(result.limitations))
        self.statusBar().showMessage("审查完成   ·   报告可导出 Markdown / JSON   ·   原始项目未被修改")
        if report_id is None and save_if_missing:
            snapshot = deepcopy(result)
            self._run("正在保存本机审查报告…", lambda: self.service.store.save_report(snapshot),
                      lambda saved_id: self._report_saved(saved_id, result))

    def _report_saved(self, report_id: int, saved_result: ReviewResult) -> None:
        if self.result is not saved_result:
            return
        self.report_id = report_id
        self.retry_save_button.hide()
        self.notice.setText(f"审查完成 · {self.result.file_count} 个文件 · {len(self.result.findings)} 条建议。" +
                            (" " + "；".join(self.result.limitations[:2]) if self.result.limitations else ""))

    def _clear_result(self) -> None:
        self.findings_table.setRowCount(0)
        self._visible_findings = []
        self._selected_finding = None
        self.retry_save_button.hide()
        self.detail_title.setText("选择一条建议，查看具体原因")
        self.detail_meta.setText("建议不会自动修改项目中的文件。")
        self.detail_text.clear()
        self.finding_count_label.setText("审查建议")
        self.findings_empty.show()
        self.findings_empty.setText("运行审查后，这里会列出有代码依据的建议。")
        self._detail_enabled(False)
        self.metric_values["findings"].setText("—")
        self.metric_values["risk"].setText("—")
        self.metric_values["cost"].setText("¥ 0.0000")
        self.cost_hint.setText("本地分析不调用 API")

    def _update_metrics(self) -> None:
        if not self.result:
            return
        active = [finding for finding in self.result.findings if finding.status == "new"]
        self.metric_values["files"].setText(str(self.result.file_count))
        self.metric_values["findings"].setText(str(len(active)))
        self.metric_values["risk"].setText(str(sum(finding.severity in {"critical", "high"} for finding in active)))
        try:
            cost = Decimal(self.result.usage.estimated_cost_cny)
        except InvalidOperation:
            cost = Decimal(0)
        unknown = self.result.mode != "local" and not self.result.usage.actual
        self.metric_values["cost"].setText("未知" if unknown else f"¥ {cost:.4f}")
        self.cost_hint.setText(f"真实用量 {self.result.usage.prompt_tokens + self.result.usage.completion_tokens:,} tokens" if self.result.usage.actual else "调用用量未知，请查平台账单" if unknown else "本地分析 · 未调用 API")

    def _filter_findings(self, *_args) -> None:
        previous = self._selected_finding
        self.findings_table.blockSignals(True)
        rank = {key: index for index, key in enumerate(SEVERITY)}
        findings = sorted(self.result.findings, key=lambda finding: rank.get(finding.severity, 9)) if self.result else []
        query = self.search_input.text().strip().casefold()
        severity = self.severity_filter.currentData()
        status = self.status_filter.currentData()
        self._visible_findings = [finding for finding in findings if
            (severity == "all" or finding.severity == severity) and
            (status == "all" or finding.status == status) and
            (not query or query in f"{finding.title} {finding.path} {finding.evidence} {finding.recommendation}".casefold())]
        self.findings_table.setRowCount(len(self._visible_findings))
        selected_row = 0
        for row, finding in enumerate(self._visible_findings):
            level = QTableWidgetItem(SEVERITY.get(finding.severity, finding.severity))
            level.setForeground(QColor(COLORS.get(finding.severity, "#638096")))
            title = QTableWidgetItem(("✓ " if finding.status == "accepted" else "○ " if finding.status == "dismissed" else "") + finding.title)
            title.setToolTip(finding.title + "\n" + STATUS.get(finding.status, finding.status))
            path = QTableWidgetItem(f"{Path(finding.path).name}:{finding.start_line}")
            path.setToolTip(finding.path)
            for column, item in enumerate((level, title, path)):
                self.findings_table.setItem(row, column, item)
            self.findings_table.setRowHeight(row, 42)
            if finding is previous:
                selected_row = row
        self.findings_table.blockSignals(False)
        self.finding_count_label.setText(f"审查建议  {len(self._visible_findings)} / {len(findings)}")
        self.findings_empty.setVisible(not self._visible_findings)
        if self.result:
            self.findings_empty.setText("没有匹配筛选条件的建议。" if findings else "本次未发现可报告的问题。静态分析与有限上下文无法证明代码完全无误。")
        if self._visible_findings:
            self.findings_table.selectRow(selected_row)
            self._finding_selected()
        else:
            self._selected_finding = None
            self.detail_title.setText("没有选中的建议")
            self.detail_meta.setText("调整筛选条件，或重新运行审查。")
            self.detail_text.clear()
            self._detail_enabled(False)

    def _finding_selected(self) -> None:
        row = self.findings_table.currentRow()
        if not 0 <= row < len(self._visible_findings):
            return
        finding = self._visible_findings[row]
        self._selected_finding = finding
        self.detail_title.setText(finding.title)
        source = "本地静态分析" if finding.source == "static" else "DeepSeek 模型建议"
        self.detail_meta.setText(f"{SEVERITY.get(finding.severity, finding.severity)} · {CATEGORY.get(finding.category, finding.category)} · {source} · {STATUS.get(finding.status, finding.status)}\n{finding.path}  L{finding.start_line}–{finding.end_line}")
        self.detail_text.setPlainText(f"代码依据\n{finding.evidence}\n\n改进建议\n{finding.recommendation}\n\n规则：{finding.rule_id or '模型推断'}\n置信度：{finding.confidence:.0%}（审查器判断，非统计概率）")
        self._detail_enabled(True)
        self.locate_finding()

    def _detail_enabled(self, enabled: bool) -> None:
        enabled = enabled and self._thread is None and not self._operations
        for button in (self.locate_button, self.accept_button, self.dismiss_button, self.reset_button):
            button.setEnabled(enabled)

    def locate_finding(self) -> None:
        """Navigate to evidence in the loaded source snapshot."""
        finding = self._selected_finding
        if not finding:
            return
        if not self.analysis:
            self.notice.setText("历史报告不保存源码。打开对应项目后才能定位。")
            return
        self._display_source(finding.path, finding.start_line)

    def set_finding_status(self, status: str) -> None:
        """Persist review disposition; accepting never applies a source patch."""
        if self._thread is not None or self._operations:
            return
        if status not in STATUS or not self._selected_finding or not self.result:
            return
        self._selected_finding.status = status
        self._update_metrics()
        self._filter_findings()
        result = deepcopy(self.result)
        selected_result = self.result
        report_id = self.report_id
        if report_id is not None:
            self._run("正在保存建议状态…", lambda: self.service.store.update_report(report_id, result),
                      lambda _: self.notice.setText("建议状态已保存；源码未被修改。"))
        else:
            self._run("正在保存审查报告…", lambda: self.service.store.save_report(result),
                      lambda saved_id: self._status_saved(saved_id, selected_result))

    def _status_saved(self, report_id: int, saved_result: ReviewResult) -> None:
        if self.result is not saved_result:
            return
        self.report_id = report_id
        self.retry_save_button.hide()
        self.notice.setText("建议状态已保存；源码未被修改。")

    def _graph_node_clicked(self, node_id: str) -> None:
        if not self.analysis:
            return
        node = next((node for node in self.analysis.nodes if node.id == node_id), None)
        if node:
            self.show_page(0)
            self._display_source(node.path, node.line)

    def export_current(self) -> None:
        """Export the selected report as plain Markdown or structured JSON."""
        if not self.result:
            self.notice.setText("先完成一次审查，或打开历史报告，再导出。")
            return
        filename, selected_filter = QFileDialog.getSaveFileName(self, "导出审查报告", "ReviewDesk-审查报告.md", "Markdown 报告 (*.md);;JSON 数据 (*.json)")
        if not filename:
            return
        path = Path(filename)
        if path.suffix.lower() not in {".json", ".md"}:
            path = path.with_suffix(".json" if "JSON" in selected_filter else ".md")
        result = self.result
        self._run("正在导出报告…", lambda: self.service.export_report(result, path),
                  lambda _: self.notice.setText(f"报告已导出：{path}"))

    def refresh_history(self) -> None:
        """Read local report metadata in a worker."""
        self._run("正在读取本机审查记录…", self.service.store.history, self._set_history)

    def _set_history(self, records: list[dict]) -> None:
        self._history_rows = records
        self.history_table.setRowCount(len(records))
        cost_total = Decimal(0)
        unknown_count = 0
        for row, record in enumerate(records):
            usage = record.get("usage", {})
            if not isinstance(usage, dict):
                usage = {}
            unknown = record.get("mode") != "local" and not usage.get("actual", False)
            unknown_count += int(unknown)
            cost = record.get("estimated_cost_cny", usage.get("estimated_cost_cny", "0"))
            try:
                if not unknown:
                    cost_total += Decimal(str(cost))
            except InvalidOperation:
                pass
            tokens = record.get("total_tokens", record.get("prompt_tokens", usage.get("prompt_tokens", 0)) + record.get("completion_tokens", usage.get("completion_tokens", 0)))
            created = str(record.get("created_at", ""))
            try:
                created = datetime.fromisoformat(created).astimezone().strftime("%m-%d %H:%M")
            except ValueError:
                created = created[:16]
            mode = record.get("mode")
            unknown = mode != "local" and not usage.get("actual", False)
            values = [created, record.get("project_name", "未命名"), "部分完成" if mode == "partial" else "DeepSeek" if mode in {"api", "deepseek", "hybrid"} else "本地", str(record.get("finding_count", record.get("findings_count", 0))), "未知" if unknown else f"{tokens:,}", "未知" if unknown else f"¥ {cost}"]
            for column, value in enumerate(values):
                self.history_table.setItem(row, column, QTableWidgetItem(str(value)))
            self.history_table.setRowHeight(row, 45)
        self.history_summary.setText(f"最近 {len(records)} 份报告   ·   已知估算费用 ¥ {cost_total:.4f}" + (f"   ·   {unknown_count} 次费用未知" if unknown_count else ""))
        self.notice.setText("已载入本机审查记录。" if records else "还没有审查记录，先载入示例体验一次本地审查。")

    def open_history_report(self) -> None:
        """Open a report without assuming that old code still matches current files."""
        if self._thread is not None or self._operations:
            self.notice.setText("当前操作完成后即可打开历史报告。")
            return
        row = self.history_table.currentRow()
        if not 0 <= row < len(self._history_rows):
            self.notice.setText("请先选中一份历史报告。")
            return
        report_id = int(self._history_rows[row]["id"])

        def loaded(result):
            # Keep source only when hashes establish that it is the same snapshot.
            hashes = {source.path: source.sha256 for source in self.analysis.files} if self.analysis else {}
            if not result.source_hashes or any(hashes.get(path) != value for path, value in result.source_hashes.items()):
                self.analysis = None
                self.file_tree.clear()
                self._setting_code = True
                self.code_editor.clear()
                self._setting_code = False
                self.code_editor.setReadOnly(True)
                self.graph_view.set_analysis(Analysis())
                self.graph_summary.setText("历史报告不保存源码；重新载入项目以查看关系。")
                self.source_label.setText("历史报告 · 未载入源码")
                self.source_info.setText("报告中仅保留证据片段")
                self._paste_mode = False
            self.project_name = result.project_name
            self._set_result(result, report_id)
            self.show_page(0)
        self._run("正在打开历史报告…", lambda: self.service.store.load_report(report_id), loaded)

    def _update_usage_details(self) -> None:
        usage = self.result.usage
        unknown = self.result.mode != "local" and not usage.actual
        quantities = "输入、输出与缓存命中：未知，请检查 DeepSeek 平台账单。" if unknown else f"输入 {usage.prompt_tokens:,} tokens  ·  输出 {usage.completion_tokens:,} tokens  ·  缓存命中 {usage.cache_hit_tokens:,} tokens"
        self.usage_details.setPlainText(
            f"{self.result.project_name}  /  {self.result.created_at}\n"
            f"模型：{usage.model or '本地分析'}    用量来源：{'API 实际返回' if usage.actual else '无 API 调用' if self.result.mode == 'local' else 'API 用量未知'}\n"
            f"{quantities}\n"
            f"估算费用：{'未知' if unknown else '¥ ' + usage.estimated_cost_cny}    价格核对日期：{usage.price_checked_at}\n"
            f"单价来源：{usage.price_source}\n{usage.note}"
        )

    def save_api_key(self) -> None:
        """Save a key in the OS credential vault and clear the text field."""
        key = self.api_key_input.text().strip()
        if not key:
            self.key_status.setText("请先输入 API Key。")
            return
        self._temporary_key = key

        def saved(_):
            self.api_key_input.clear()
            self._temporary_key = ""
            self.key_status.setText("密钥已安全保存到当前 Windows 用户的凭据管理器。")
            self.notice.setText("DeepSeek 密钥已保存，可以验证连接或运行模型审查。")
        self._run("正在安全保存密钥…", lambda: self.credentials.set_key(key), saved)

    def delete_api_key(self) -> None:
        """Remove the saved credential from the current Windows user."""
        def deleted(_):
            self.api_key_input.clear()
            self._temporary_key = ""
            self.key_status.setText("本机已保存的 DeepSeek 凭据已删除。")
            self.connection_pill.setText("●  本地引擎就绪")
            self.notice.setText("已删除当前 Windows 用户保存的凭据。")
        self._run("正在删除已保存凭据…", self.credentials.delete_key, deleted)

    def check_connection(self) -> None:
        """Check API account connectivity without generating a completion."""
        entered_key = self.api_key_input.text().strip()
        self._temporary_key = entered_key

        def check():
            from reviewdesk.api import DeepSeekClient
            key = entered_key or self.credentials.get_key()
            if not key:
                raise ValueError("请先输入或安全保存 API Key。")
            return DeepSeekClient(key).check_connection()

        def connected(response):
            self._temporary_key = ""
            self.connection_pill.setText("●  DeepSeek 已连接")
            balances = response.get("balance_infos", []) if isinstance(response, dict) else []
            balance = " / ".join(f"{entry.get('total_balance', '?')} {entry.get('currency', '')}" for entry in balances)
            self.key_status.setText("DeepSeek 连接验证成功。" + ("账户余额：" + balance if balance else "可进行模型审查。"))
            self.notice.setText("连接成功；本次验证没有请求模型生成。")
        self._run("正在验证 DeepSeek 连接…", check, connected)

    def closeEvent(self, event) -> None:
        """Keep worker objects alive until timeout-bounded work has returned."""
        if self._thread is not None:
            self._closing = True
            self._operations.clear()
            self._thread.requestInterruption()
            self.notice.setText("正在等待当前操作结束后安全关闭…")
            self.setEnabled(False)
            event.ignore()
            return
        self._temporary_key = ""
        self.api_key_input.clear()
        event.accept()
