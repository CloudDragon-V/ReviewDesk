"""Draw an original, scalable code/inspection application mark."""
from pathlib import Path
from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QImage, QPainter, QPen, QPolygonF
from PySide6.QtWidgets import QApplication

app = QApplication([])
image = QImage(256, 256, QImage.Format.Format_ARGB32)
image.fill(Qt.GlobalColor.transparent)
p = QPainter(image)
p.setRenderHint(QPainter.RenderHint.Antialiasing)
p.setPen(Qt.PenStyle.NoPen)
p.setBrush(QColor("#15243E"))
p.drawRoundedRect(QRectF(8, 8, 240, 240), 54, 54)
p.setPen(QPen(QColor("#B9C9FF"), 16, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
p.drawPolyline(QPolygonF([QPointF(93, 76), QPointF(55, 113), QPointF(93, 150)]))
p.drawPolyline(QPolygonF([QPointF(161, 76), QPointF(199, 113), QPointF(161, 150)]))
p.setPen(QPen(QColor("#5AE0BC"), 17, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
p.drawPolyline(QPolygonF([QPointF(99, 182), QPointF(124, 204), QPointF(172, 164)]))
p.end()
output = Path(__file__).resolve().parents[2] / "前端功能" / "resources"
output.mkdir(exist_ok=True)
assert image.save(str(output / "reviewdesk.ico"))
assert image.save(str(output / "reviewdesk.png"))
print("Application icon created.")
