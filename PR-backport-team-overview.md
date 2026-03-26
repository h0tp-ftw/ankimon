# PR: Backport Team Pokémon Overview to Stable Addon

**Date:** 2026-02-06
**Target:** `1908235722/` (stable Anki addon)
**Source:** `ankimon__experimental/src/Ankimon/` (experimental build)
**Feature:** Team Pokémon Overview grid on Deck Browser & Deck Overview pages

---

## Description

This change backports the **Team Pokémon Overview** feature from the experimental Ankimon build into the stable addon. When enabled, a styled grid of the player's current team (up to 6 Pokémon) is injected at the top of Anki's Deck Browser and Deck Overview pages. Each card shows the Pokémon's sprite, name/nickname, level, HP, and types with color-coded backgrounds.

The feature is gated behind a new user-facing setting (`gui.team_deck_view`, default: `true`) in the **Styling** settings group. Toggling it off (requires Anki restart) hides the grid entirely.

---

## Files Changed

### New Files (2)

| File | Purpose |
|---|---|
| `gui_classes/__init__.py` | Empty package initializer — makes `gui_classes/` a proper Python package for reliable imports |
| `gui_classes/overview_team.py` | Core module: team loading, HTML/CSS grid builder, Anki hook registration |

### Modified Files (8)

| File | Change |
|---|---|
| `resources.py` | Added `pokeball_path` variable |
| `utils.py` | Added `import base64` + `png_to_base64()` function |
| `config.json` | Added `"gui.team_deck_view": true` default |
| `pyobj/settings.py` | Added `"gui.team_deck_view": True` to `DEFAULT_CONFIG` |
| `lang/setting_name.json` | Added display name for new setting |
| `lang/setting_description.json` | Added description for new setting |
| `pyobj/settings_window.py` | Added setting to "Styling" group |
| `__init__.py` | Added `from .gui_classes.overview_team import *` |

---

## Detailed Diffs

### `gui_classes/__init__.py` — NEW FILE

```python
# (empty file)
```

> Makes `gui_classes/` a proper Python package. Without this, Anki's addon loader raises `ModuleNotFoundError` when trying `from .gui_classes.overview_team import *`.

---

### `gui_classes/overview_team.py` — NEW FILE (214 lines)

Full module ported from experimental. Key components:

```python
# Imports
import json, os
from aqt import gui_hooks, mw
from ..resources import team_pokemon_path, mypokemon_path, pokeball_path
from ..functions.sprite_functions import get_sprite_path
from ..utils import png_to_base64

# TYPE_COLORS — 18-entry dict mapping type names to hex colors
# _bg_style_from_types(types) — returns CSS gradient for multi-type backgrounds
# POKEBALL_DATA_URI — cached base64 of pokeball.png
# load_pokemon_team() — loads team from team.json → mypokemon.json with fallback
# _build_pokemon_grid(pokemon_list, id_prefix, max_items=6) — returns HTML string
# deck_browser_will_render(deck_browser, content) — hook for Deck Browser
# on_overview_will_render_content(overview, content) — hook for Deck Overview

# Hook registration (runs at import time):
if mw.settings_obj.get("gui.team_deck_view") is True:
    gui_hooks.deck_browser_will_render_content.append(deck_browser_will_render)
    gui_hooks.overview_will_render_content.append(on_overview_will_render_content)
```

---

### `resources.py`

```diff
 icon_path = addon_dir / "addon_files" / "pokeball.png"
+pokeball_path = addon_dir / "addon_files" / "pokeball.png"
 sound_list_path = addon_dir / "addon_files" / "sound_list.json"
```

---

### `utils.py` — imports

```diff
 import csv
+import base64
 from typing import Optional
```

### `utils.py` — new function (appended before `close_anki`)

```diff
+def png_to_base64(path):
+    """Convert a PNG file to a base64 data URI for embedding into HTML.
+
+    Args:
+        path (str): absolute or relative filesystem path to a PNG file.
+
+    Returns:
+        str: a data URI string like ``data:image/png;base64,...`` or empty
+             string if the file does not exist.
+    """
+    if not os.path.exists(path):
+        return ""
+    with open(path, "rb") as f:
+        return "data:image/png;base64," + base64.b64encode(f.read()).decode("utf-8")
+
 def close_anki():
     mw.close()
```

---

### `config.json`

```diff
     "gui.show_mainpkmn_in_reviewer": 1,
+    "gui.team_deck_view": true,
     "gui.view_main_front": true,
```

---

### `pyobj/settings.py`

```diff
     "gui.show_mainpkmn_in_reviewer": 1,
+    "gui.team_deck_view": True,
     "gui.view_main_front": True,
```

---

### `lang/setting_name.json`

```diff
+    "gui.team_deck_view": "Team Overview in Deck Overview",
     "gui.view_main_front": "View Main Pokémon Front",
```

---

### `lang/setting_description.json`

```diff
+    "gui.team_deck_view": "Enable a quick overview of your Pokémon team at the bottom of the deck overview.",
     "gui.view_main_front": "View front of main Pokémon in reviewer when GIFs are enabled. Set Disabled to show from the back.",
```

---

### `pyobj/settings_window.py`

```diff
-            "Styling": {"settings": ["Styling in Reviewer", "Animate Time", "HP Bar Thickness", "Reviewer Image as GIF", "View Main Pokémon Front", "Show GIFs in Collection"]},
+            "Styling": {"settings": ["Styling in Reviewer", "Team Overview in Deck Overview", "Animate Time", "HP Bar Thickness", "Reviewer Image as GIF", "View Main Pokémon Front", "Show GIFs in Collection"]},
```

---

### `__init__.py`

```diff
 mw.settings_obj = settings_obj

+from .gui_classes.overview_team import *
+
 # Log an startup message
```

> **Import order is critical.** The module reads `mw.settings_obj` at module level, so this line must appear **after** `mw.settings_obj = settings_obj`.

---

## Bug Fix During Implementation

### `ModuleNotFoundError: No module named '1908235722.gui_classes.overview_team'`

**Problem:** The initial guide assumed `overview_team.py` already existed on disk in `gui_classes/`. It did not — the file had never been created in the stable addon.

**Root Cause:** Two files were missing:
1. `gui_classes/__init__.py` — no package initializer meant Python couldn't resolve subpackage imports reliably
2. `gui_classes/overview_team.py` — the module itself was never placed on disk

**Fix:** Created both files. The `__init__.py` is empty; `overview_team.py` is a full copy from the experimental build.

---

## Testing Checklist

- [ ] **Startup:** Anki loads without `ImportError` or `AttributeError`
- [ ] **Deck Browser:** Team grid appears at top of stats area (with Pokémon data)
- [ ] **Deck Overview:** Team grid appears at top of table area (click into a deck)
- [ ] **Setting ON (default):** Grid visible after fresh install
- [ ] **Setting OFF:** Disable in Settings → Styling → restart → grid hidden
- [ ] **Empty data:** Empty `mypokemon.json` → no crash, grid simply doesn't render
- [ ] **Missing sprites:** Removed sprite file → substitute image shown, no crash
- [ ] **No team.json:** Falls back to showing all Pokémon from `mypokemon.json`

---

## Dependencies (no changes needed)

These files were already present and correct in the stable addon:

| File | Status |
|---|---|
| `functions/sprite_functions.py` | ✅ `get_sprite_path()` identical to experimental |
| `resources.py` → `team_pokemon_path` | ✅ Already defined |
| `resources.py` → `mypokemon_path` | ✅ Already defined |
| `addon_files/pokeball.png` | ✅ Already on disk (also aliased as `icon_path`) |

---

## Notes

- Hook registration happens at **import time** (module level). Toggling the setting requires an Anki restart — this matches the experimental build's behavior.
- Sprites are embedded as base64 data URIs to keep the HTML self-contained. For 6 Pokémon this adds ~50–100 KB to the page — acceptable.
- The `pokeball_path` variable points to the same file as `icon_path`. Both are kept for import compatibility across modules.
