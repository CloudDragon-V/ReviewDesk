"""Small, self-contained Qt widgets for source and static graph navigation."""

from __future__ import annotations

import keyword
import re

from PySide6.QtCore import QRect, QRectF, QSize, Qt, Signal
from PySide6.QtGui import (
    QColor, QFont, QPainter, QPainterPath, QPen, QPolygonF,
    QSyntaxHighlighter, QTextCharFormat, QTextCursor, QTextFormat,
)
from PySide6.QtCore import QPointF
from PySide6.QtWidgets import (
    QGraphicsItem, QGraphicsObject, QGraphicsPathItem, QGraphicsPolygonItem,
    QGraphicsScene, QGraphicsView, QPlainTextEdit, QTextEdit, QWidget,
)

from reviewdesk.models import Analysis, GraphNode


class PythonHighlighter(QSyntaxHighlighter):
    """Apply visual syntax hints only; source text is never evaluated."""

    def __init__(self, document) -> None:
        super().__init__(document)
        self.rules: list[tuple[re.Pattern, QTextCharFormat]] = []
        for pattern, color, bold in (
            (r"\b(?:" + "|".join(keyword.kwlist) + r")\b", "#8955b3", True),
            (r"\b(?:print|len|range|str|int|float|list|dict|set|tuple|open|super|self)\b", "#197d8c", False),
            (r"\b\d+(?:\.\d+)?\b", "#a16816", False),
            (r"(?<=def )\w+|(?<=class )\w+", "#2359a8", True),
            (r"(?:\"(?:[^\"\\]|\\.)*\"|'(?:[^'\\]|\\.)*')", "#31734e", False),
            (r"#.*$", "#8a97ab", False),
        ):
            style = QTextCharFormat()
            style.setForeground(QColor(color))
            if bold:
                style.setFontWeight(QFont.Weight.DemiBold)
            self.rules.append((re.compile(pattern), style))

    def highlightBlock(self, text: str) -> None:
        """Color one displayed block without interpreting the code."""
        for pattern, style in self.rules:
            for match in pattern.finditer(text):
                # Qt positions are UTF-16 code units, not Python code points.
                start = len(text[:match.start()].encode("utf-16-le")) // 2
                length = len(match.group().encode("utf-16-le")) // 2
                self.setFormat(start, length, style)


class _LineArea(QWidget):
    def __init__(self, editor: "CodeEditor") -> None:
        super().__init__(editor)
        self.editor = editor

    def sizeHint(self) -> QSize:
        return QSize(self.editor.line_area_width(), 0)

    def paintEvent(self, event) -> None:
        self.editor.paint_line_area(event)


class CodeEditor(QPlainTextEdit):
    """Source editor with stable one-based line navigation and a gutter."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("codeEditor")
        font = QFont("Cascadia Code", 10)
        font.setStyleHint(QFont.StyleHint.Monospace)
        self.setFont(font)
        self.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self.setTabStopDistance(self.fontMetrics().horizontalAdvance(" ") * 4)
        self.setReadOnly(True)
        self._gutter = _LineArea(self)
        self._highlighter = PythonHighlighter(self.document())
        self.blockCountChanged.connect(self._update_margins)
        self.updateRequest.connect(self._update_gutter)
        self.cursorPositionChanged.connect(self._highlight_line)
        self._update_margins()
        self._highlight_line()

    def line_area_width(self) -> int:
        """Width needed for the visible one-based line labels."""
        return 25 + self.fontMetrics().horizontalAdvance("9") * len(str(self.blockCount()))

    def _update_margins(self, *_args) -> None:
        self.setViewportMargins(self.line_area_width(), 0, 0, 0)

    def _update_gutter(self, rect: QRect, dy: int) -> None:
        if dy:
            self._gutter.scroll(0, dy)
        else:
            self._gutter.update(0, rect.y(), self._gutter.width(), rect.height())
        if rect.contains(self.viewport().rect()):
            self._update_margins()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        rect = self.contentsRect()
        self._gutter.setGeometry(QRect(rect.left(), rect.top(), self.line_area_width(), rect.height()))

    def paint_line_area(self, event) -> None:
        """Draw only the visible gutter lines."""
        painter = QPainter(self._gutter)
        painter.fillRect(event.rect(), QColor("#f7f9fc"))
        painter.setFont(self.font())
        block = self.firstVisibleBlock()
        number = block.blockNumber()
        top = int(self.blockBoundingGeometry(block).translated(self.contentOffset()).top())
        while block.isValid() and top <= event.rect().bottom():
            height = int(self.blockBoundingRect(block).height())
            if block.isVisible() and top + height >= event.rect().top():
                selected = number == self.textCursor().blockNumber()
                painter.setPen(QColor("#4265b7" if selected else "#9aa6b7"))
                painter.drawText(0, top, self._gutter.width() - 12,
                                 self.fontMetrics().height(), Qt.AlignmentFlag.AlignRight, str(number + 1))
            top += height
            block = block.next()
            number += 1

    def _highlight_line(self) -> None:
        selection = QTextEdit.ExtraSelection()
        selection.format.setBackground(QColor("#edf3ff"))
        selection.format.setProperty(QTextFormat.Property.FullWidthSelection, True)
        selection.cursor = self.textCursor()
        selection.cursor.clearSelection()
        self.setExtraSelections([selection])
        self._gutter.update()

    def go_to_line(self, number: int) -> None:
        """Select a valid line and keep it visible."""
        block = self.document().findBlockByNumber(max(0, min(number - 1, self.blockCount() - 1)))
        cursor = QTextCursor(block)
        self.setTextCursor(cursor)
        self.centerCursor()
        self.setFocus(Qt.FocusReason.OtherFocusReason)


class _GraphNodeItem(QGraphicsObject):
    activated = Signal(str)

    def __init__(self, node: GraphNode) -> None:
        super().__init__()
        self.node = node
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable, True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip(f"{node.label}\n{node.path}:{node.line}\n点击跳转源码")

    def boundingRect(self) -> QRectF:
        return QRectF(0, 0, 218, 67)

    def paint(self, painter: QPainter, option, widget=None) -> None:
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        is_module = self.node.kind == "module"
        selected = self.isSelected()
        painter.setPen(QPen(QColor("#4675d4" if selected else "#dce4f0"), 2 if selected else 1))
        painter.setBrush(QColor("#142746" if is_module else "#ffffff"))
        painter.drawRoundedRect(self.boundingRect().adjusted(1, 1, -1, -1), 10, 10)
        font = QFont("Microsoft YaHei UI", 9)
        font.setWeight(QFont.Weight.DemiBold)
        painter.setFont(font)
        painter.setPen(QColor("#ffffff" if is_module else "#253650"))
        label = painter.fontMetrics().elidedText(self.node.label, Qt.TextElideMode.ElideMiddle, 184)
        painter.drawText(QRectF(16, 10, 185, 23), label)
        painter.setFont(QFont("Microsoft YaHei UI", 8))
        painter.setPen(QColor("#a9bbd4" if is_module else "#7a8ba2"))
        kind = {"module": "模块", "function": "函数", "class": "类", "method": "方法"}.get(self.node.kind, self.node.kind)
        painter.drawText(QRectF(16, 36, 185, 19), f"{kind}   ·   L{self.node.line}")

    def mouseReleaseEvent(self, event) -> None:
        super().mouseReleaseEvent(event)
        if event.button() == Qt.MouseButton.LeftButton:
            self.activated.emit(self.node.id)


class GraphView(QGraphicsView):
    """Bounded, pannable static source graph with clickable nodes."""

    node_activated = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("graphView")
        self.setScene(QGraphicsScene(self))
        self.setRenderHints(QPainter.RenderHint.Antialiasing | QPainter.RenderHint.TextAntialiasing)
        self.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self.setBackgroundBrush(QColor("#f5f8fc"))
        self.node_items: dict[str, _GraphNodeItem] = {}
        self.shown_edges = 0

    def set_analysis(self, analysis: Analysis) -> None:
        """Lay out a bounded subset by source file; all code remains read-only."""
        scene = self.scene()
        scene.clear()
        self.node_items.clear()
        self.shown_edges = 0
        nodes = analysis.nodes[:160]
        groups: dict[str, list[GraphNode]] = {}
        for node in nodes:
            groups.setdefault(node.path, []).append(node)
        group_items = list(groups.items())
        group_levels = []
        for _, members in group_items:
            levels: list[list[GraphNode]] = [[], [], []]
            for node in members:
                levels[0 if node.kind == "module" else 1 if node.kind == "class" else 2].append(node)
            group_levels.append(levels)
        row_offsets = [0]
        for start in range(0, len(group_items), 2):
            row_height = max(max(len(level) for level in levels) for levels in group_levels[start:start + 2]) * 94 + 125
            row_offsets.append(row_offsets[-1] + row_height)
        for index, (path, members) in enumerate(groups.items()):
            x, y = (index % 2) * 1000, row_offsets[index // 2]
            label = scene.addText(path, QFont("Microsoft YaHei UI", 9))
            label.setDefaultTextColor(QColor("#73849a"))
            label.setPos(x, y - 35)
            levels = group_levels[index]
            height = max(len(level) for level in levels) * 94
            for column, level in enumerate(levels):
                for offset, node in enumerate(level):
                    item = _GraphNodeItem(node)
                    item.setPos(x + column * 290, y + offset * 94 + (height - len(level) * 94) / 2)
                    item.activated.connect(self.node_activated)
                    item.setZValue(2)
                    scene.addItem(item)
                    self.node_items[node.id] = item
        for edge in analysis.edges[:800]:
            source, target = self.node_items.get(edge.source), self.node_items.get(edge.target)
            if not source or not target or source is target:
                continue
            start = source.pos() + QPointF(218, 33)
            end = target.pos() + QPointF(0, 33)
            path = QPainterPath(start)
            curve = max(60, abs(end.x() - start.x()) / 2)
            path.cubicTo(start + QPointF(curve, 0), end - QPointF(curve, 0), end)
            item = QGraphicsPathItem(path)
            color = QColor("#7698c6" if edge.kind == "calls" else "#b0c1d7")
            item.setPen(QPen(color, 1.4))
            item.setToolTip(edge.kind)
            scene.addItem(item)
            arrow = QGraphicsPolygonItem(QPolygonF([end, end + QPointF(-8, -4), end + QPointF(-8, 4)]))
            arrow.setBrush(color)
            arrow.setPen(Qt.PenStyle.NoPen)
            scene.addItem(arrow)
            self.shown_edges += 1
        scene.setSceneRect(scene.itemsBoundingRect().adjusted(-70, -65, 70, 65))
        self.fit_graph()

    def fit_graph(self) -> None:
        """Fit the graph while preventing unreadably enlarged small graphs."""
        self.resetTransform()
        if self.scene().items():
            self.fitInView(self.scene().sceneRect(), Qt.AspectRatioMode.KeepAspectRatio)
            if self.transform().m11() > 1.15:
                self.resetTransform()
                self.scale(1.15, 1.15)

    def zoom(self, factor: float) -> None:
        """Zoom within a useful and finite range."""
        current = self.transform().m11()
        target = min(3.0, max(0.12, current * factor))
        self.scale(target / current, target / current)

    def wheelEvent(self, event) -> None:
        self.zoom(1.15 if event.angleDelta().y() > 0 else 1 / 1.15)
        event.accept()
