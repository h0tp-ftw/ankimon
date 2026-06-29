"""
harness/screenshot.py — capture how a real (Tier-2) window looks, offscreen.

Even under QT_QPA_PLATFORM=offscreen, Qt renders widgets into an offscreen
buffer, so ``widget.grab()`` returns a real QPixmap. That lets an agent (or you)
*see* the genuine Ankimon UI — real sprites, real layout — saved to a PNG,
without any display. Only meaningful in Tier 2 (real windows).
"""

from __future__ import annotations

import pathlib


def grab(widget, path, size=None):
    """Render a real Qt widget to a PNG. Returns the saved path, or None.

    ``size`` is an optional (w, h) to resize before grabbing; otherwise the
    widget's sizeHint (or a sane default) is used.
    """
    try:
        from PyQt6.QtWidgets import QApplication
        from PyQt6.QtCore import QSize

        if size is not None:
            widget.resize(int(size[0]), int(size[1]))
        elif widget.size().isEmpty() or widget.width() < 2 or widget.height() < 2:
            hint = widget.sizeHint()
            if hint.isValid() and not hint.isEmpty():
                widget.resize(hint)
            else:
                widget.resize(QSize(900, 650))

        widget.show()
        QApplication.processEvents()  # let layout + paint happen offscreen
        pixmap = widget.grab()
        QApplication.processEvents()

        out = pathlib.Path(path)
        out.parent.mkdir(parents=True, exist_ok=True)
        ok = pixmap.save(str(out), "PNG")
        return str(out) if ok else None
    except Exception:
        return None
