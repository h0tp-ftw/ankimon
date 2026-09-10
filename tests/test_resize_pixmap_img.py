"""``business.resize_pixmap_img`` must survive a pixmap that failed to load.

Issue #101: a sprite the user never downloaded yields a NULL QPixmap, whose
``width()`` is 0. The aspect-ratio maths then raised ``ZeroDivisionError:
integer division or modulo by zero`` and took the Ankimon window down with it.

This helper is the single place every window scales a sprite through, so the
guard is pinned here in Tier 1 — no Qt required, it only needs the
``width()``/``height()``/``scaled()`` trio that QPixmap provides.
"""

import importlib.util
import sys
from pathlib import Path

import pytest

_SRC = Path(__file__).parent.parent / "src"
sys.path.insert(0, str(_SRC))


@pytest.fixture
def resize_pixmap_img():
    """Load the REAL ``business`` module, ignoring any stub in sys.modules.

    Several test files replace ``Ankimon.business`` with a MagicMock, so a
    plain module-level import would bind a mock when the suite runs as a whole.
    Executing the file directly — the trick ``conftest.py`` uses to restore
    ``Ankimon.resources`` — sidesteps that, and never registers the module, so
    this test cannot perturb anyone else's stubs either.
    """
    spec = importlib.util.spec_from_file_location(
        "Ankimon.business", _SRC / "Ankimon" / "business.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)  # relative imports resolve via spec.parent
    return module.resize_pixmap_img


class _FakePixmap:
    """The slice of the QPixmap surface ``resize_pixmap_img`` touches."""

    def __init__(self, width, height):
        self._width = width
        self._height = height
        self.scaled_to = None

    def width(self):
        return self._width

    def height(self):
        return self._height

    def scaled(self, width, height):
        self.scaled_to = (width, height)
        return _FakePixmap(width, height)


def test_null_pixmap_is_returned_untouched_instead_of_raising(resize_pixmap_img):
    null = _FakePixmap(0, 0)

    result = resize_pixmap_img(null, 150)

    assert result is null  # handed back as-is
    assert null.scaled_to is None  # never scaled


def test_zero_width_with_nonzero_height_also_survives(resize_pixmap_img):
    """Qt can report an odd null pixmap; the width is what divides."""
    odd = _FakePixmap(0, 40)

    assert resize_pixmap_img(odd, 150) is odd


@pytest.mark.parametrize(
    ("width", "height", "max_width", "expected"),
    [
        (100, 200, 150, (150, 300)),  # scales up, ratio kept
        (200, 100, 150, (150, 75)),  # scales down, ratio kept
        (96, 96, 150, (150, 150)),  # square
        (150, 60, 150, (150, 60)),  # already at max_width
    ],
)
def test_valid_pixmap_still_scales_by_aspect_ratio(
    resize_pixmap_img, width, height, max_width, expected
):
    """The guard must not disturb the normal path."""
    pixmap = _FakePixmap(width, height)

    result = resize_pixmap_img(pixmap, max_width)

    assert pixmap.scaled_to == expected
    assert (result.width(), result.height()) == expected
