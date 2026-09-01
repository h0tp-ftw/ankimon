"""Backwards-compatible type-name helpers — delegates to :mod:`localized_text`."""
from .localized_text import type_name as format_type_name  # noqa: F401
from .localized_text import type_list as format_type_list  # noqa: F401
