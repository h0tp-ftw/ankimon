"""Compact, full-width evolution requirements for the collection details UI."""

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import (
    QApplication,
    QFrame,
    QGridLayout,
    QLabel,
    QSizePolicy,
    QToolButton,
    QVBoxLayout,
    QWidget,
)
from aqt.theme import theme_manager


class EvolutionRequirements(QFrame):
    """Keep the primary requirements visible and disclose item alternatives."""

    def __init__(self, details, parent=None):
        super().__init__(parent)
        self.setObjectName("evolutionRequirements")
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        body_font = QFont(QApplication.font())
        body_font.setPointSizeF(max(11.0, body_font.pointSizeF()))
        self.setFont(body_font)
        dark = theme_manager.night_mode
        ink = "#e3f0ff" if dark else "#123e67"
        muted = "#b4cbdf" if dark else "#4e6780"
        accent = "#f3a1cc" if dark else "#a52c72"
        border = "#426482" if dark else "#bfd5e9"
        background = "#123759" if dark else "#f2f8ff"
        self.setStyleSheet(f"""
            QFrame#evolutionRequirements {{
                background: {background}; border: 1px solid {border}; border-radius: 8px;
            }}
            QFrame#evolutionRequirements QLabel {{ color: {ink}; background: transparent; }}
            QFrame#evolutionRequirements QLabel[role="summary"] {{ color: {accent}; }}
            QFrame#evolutionRequirements QLabel[role="condition"] {{ color: {muted}; }}
            QFrame#evolutionRequirements QToolButton {{
                color: {ink}; background: transparent; border: none; padding: 4px 0;
                text-align: left;
            }}
        """)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 10, 14, 10)
        layout.setSpacing(6)
        title = self._label("Evolution")
        title_font = title.font()
        title_font.setBold(True)
        title.setFont(title_font)
        layout.addWidget(title)
        if details["status_text"]:
            layout.addWidget(self._label(details["status_text"]))
        if details["summary"]:
            layout.addWidget(self._label(details["summary"], "summary"))
        if details["friendship_routes"]:
            layout.addWidget(self._routes(details["friendship_routes"]))
        if details["item_routes"]:
            items = self._routes(details["item_routes"])
            items.setObjectName("itemEvolutionRoutes")
            # A species with only an item route should show its requirement
            # immediately. Collapse items only when another route is visible.
            if details["friendship_routes"] or details["status_text"]:
                toggle = QToolButton(self)
                toggle.setObjectName("itemEvolutionToggle")
                count = len(details["item_routes"])
                toggle.setText(
                    f"Item evolutions · {count} {'option' if count == 1 else 'options'}"
                )
                toggle.setCheckable(True)
                toggle.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
                toggle.setArrowType(Qt.ArrowType.RightArrow)
                toggle.setSizePolicy(
                    QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed
                )
                items.hide()

                def reveal(checked):
                    items.setVisible(checked)
                    toggle.setArrowType(
                        Qt.ArrowType.DownArrow if checked else Qt.ArrowType.RightArrow
                    )

                toggle.toggled.connect(reveal)
                layout.addWidget(toggle)
            layout.addWidget(items)

    def _label(self, text, role=None):
        label = QLabel(text, self)
        label.setTextFormat(Qt.TextFormat.PlainText)
        label.setWordWrap(True)
        if role:
            label.setProperty("role", role)
        return label

    def _routes(self, routes):
        widget = QWidget(self)
        grid = QGridLayout(widget)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(16)
        grid.setVerticalSpacing(4)
        grid.setColumnStretch(0, 1)
        grid.setColumnStretch(1, 2)
        for row, route in enumerate(routes):
            grid.addWidget(self._label(route["name"]), row, 0)
            condition = self._label(route["condition"], "condition")
            condition.setAlignment(
                Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
            )
            grid.addWidget(condition, row, 1)
        return widget
