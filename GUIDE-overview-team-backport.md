# AI Agent Guide: Backporting the Team Pokémon Overview into the Stable Addon

> **Target folder (stable addon):** `<ANKI_ADDONS>/1908235722/`
> **Reference folder (experimental):** `ankimon__experimental/src/Ankimon/`
> This guide lists **every file you must touch**, the **exact changes** needed, and the **order** to apply them.

---

## 0. Pre-Requisites & Context

| Concept | Stable (`1908235722/`) | Experimental (`src/Ankimon/`) |
|---|---|---|
| Team overview module | ❌ Does **not** exist as a working feature | ✅ Fully working at `gui_classes/overview_team.py` |
| `pokeball_path` resource | ❌ Missing from `resources.py` | ✅ Defined in `resources.py` |
| `png_to_base64` helper | ❌ Missing from `utils.py` | ✅ Exists at bottom of `utils.py` |
| `get_sprite_path` function | ✅ Already exists at `functions/sprite_functions.py` | ✅ Identical file |
| `gui.team_deck_view` setting | ❌ Missing from all setting files | ✅ In `config.json`, `pyobj/settings.py`, `lang/` files, `pyobj/settings_window.py` |
| `mw.settings_obj` attribute | ✅ Set in `__init__.py` | ✅ Same pattern |
| `overview_team.py` file | ❌ Does **NOT** exist in `gui_classes/` | ✅ Imported via `from .gui_classes.overview_team import *` in `__init__.py` |
| `gui_classes/__init__.py` | ❌ Missing (no package initializer) | ❌ Also missing (uses implicit namespace) |

### Key Insight
The stable addon is missing `gui_classes/overview_team.py` entirely, plus:
1. `gui_classes/__init__.py` doesn't exist — needed for reliable package imports in Anki's loader.
2. Several dependencies it needs (`pokeball_path`, `png_to_base64`) don't exist yet.
3. The setting `gui.team_deck_view` is missing from config/defaults/lang/UI.
4. It is never imported in `__init__.py` → hooks are never registered → feature is invisible.

---

## 1. File Changes — Step by Step

### Step 0a: Create `gui_classes/__init__.py` (package initializer)

**File:** `1908235722/gui_classes/__init__.py`
**Action:** Create an **empty** file. This makes `gui_classes/` a proper Python package so `from .gui_classes.overview_team import *` works reliably.

> **Why this was missed originally:** The experimental build also lacks this file and relies on Python's implicit namespace packages. However, Anki's addon loader can behave differently — an explicit `__init__.py` avoids `ModuleNotFoundError` in edge cases.

### Step 0b: Create `gui_classes/overview_team.py` (the module itself)

**File:** `1908235722/gui_classes/overview_team.py`
**Action:** Copy the module from the experimental build (`src/Ankimon/gui_classes/overview_team.py`) into the stable addon's `gui_classes/` folder.

> **Critical:** The original guide assumed this file was already on disk. It was **not**. The file must be created with the full content from the experimental build. Without it, Python raises `ModuleNotFoundError: No module named '1908235722.gui_classes.overview_team'`.

The file content should be an exact copy of the experimental `overview_team.py`, which contains:
- Imports: `json`, `os`, `gui_hooks`, `mw`, `team_pokemon_path`, `mypokemon_path`, `pokeball_path`, `get_sprite_path`, `png_to_base64`
- `TYPE_COLORS` dict (18 type→color mappings)
- `_bg_style_from_types()` helper
- `POKEBALL_DATA_URI` cached constant
- `load_pokemon_team()` function
- `_build_pokemon_grid()` function
- `deck_browser_will_render()` hook
- `on_overview_will_render_content()` hook
- Hook registration block gated by `mw.settings_obj.get("gui.team_deck_view")`

---

### Step 1: Add `pokeball_path` to `resources.py`

**File:** `1908235722/resources.py`
**Action:** Add a single line defining the pokeball image path.
**Where:** Near the existing `icon_path` definition (around line 52), which already points to the same file but under a different variable name.

```python
# Find this existing line:
icon_path = addon_dir / "addon_files" / "pokeball.png"

# Add immediately AFTER it:
pokeball_path = addon_dir / "addon_files" / "pokeball.png"
```

**Why:** `overview_team.py` imports `pokeball_path` from `..resources`. The variable doesn't exist yet in stable. In the experimental build it's defined at line 15 of `resources.py`. Note that `icon_path` already points to the same file, but the overview module explicitly imports `pokeball_path` by name.

---

### Step 2: Add `png_to_base64` helper to `utils.py`

**File:** `1908235722/utils.py`
**Action:** Add the `import base64` statement at the top and the function at the bottom.

#### 2a. Add `import base64` to the imports

```python
# Find this block near the top of utils.py:
import os
from pathlib import Path
import requests
import json
import random
import csv
from typing import Optional

# Add `import base64` to it, for example after `import csv`:
import base64
```

#### 2b. Add the function at the END of the file

```python
def png_to_base64(path):
    """Convert a PNG file to a base64 data URI for embedding into HTML.

    Args:
        path (str): absolute or relative filesystem path to a PNG file.

    Returns:
        str: a data URI string like `data:image/png;base64,...` or empty
             string if the file does not exist.
    """
    if not os.path.exists(path):
        return ""
    with open(path, "rb") as f:
        return "data:image/png;base64," + base64.b64encode(f.read()).decode("utf-8")
```

**Why:** `overview_team.py` line 8 does `from ..utils import png_to_base64`. This function converts sprite PNG files into inline base64 data URIs so the HTML grid is self-contained (no external image references needed).

---

### Step 3: Add `gui.team_deck_view` to `config.json`

**File:** `1908235722/config.json`
**Action:** Add the key `"gui.team_deck_view": true` in the `gui.*` section.

```jsonc
// Find this section (around lines 23-25):
    "gui.show_mainpkmn_in_reviewer": 1,
    "gui.view_main_front": true,

// Change to:
    "gui.show_mainpkmn_in_reviewer": 1,
    "gui.team_deck_view": true,
    "gui.view_main_front": true,
```

**Why:** Anki reads `config.json` as the addon's default configuration. Without this key, users who haven't saved custom settings won't have the value defined. The experimental build has this at line 24 of its `config.json`.

---

### Step 4: Add `gui.team_deck_view` to DEFAULT_CONFIG in `pyobj/settings.py`

**File:** `1908235722/pyobj/settings.py`
**Action:** Add the key to the `DEFAULT_CONFIG` dictionary.

```python
# Find this section (around lines 37-39):
    "gui.show_mainpkmn_in_reviewer": 1,
    "gui.view_main_front": True,

# Change to:
    "gui.show_mainpkmn_in_reviewer": 1,
    "gui.team_deck_view": True,
    "gui.view_main_front": True,
```

**Why:** The `Settings` class merges user config with `DEFAULT_CONFIG`. If the key is missing here, `settings_obj.get("gui.team_deck_view")` returns `None` instead of `True`, and the feature won't activate. The experimental build has this at line 32 of its `pyobj/settings.py`.

---

### Step 5: Add the setting to `lang/setting_name.json`

**File:** `1908235722/lang/setting_name.json`
**Action:** Add the display name for the new setting key.

```jsonc
// Find (around lines 23-24):
    "gui.view_main_front": "View Main Pokémon Front",
    "gui.xp_bar_config": "XP Bar Configuration",

// Change to:
    "gui.team_deck_view": "Team Overview in Deck Overview",
    "gui.view_main_front": "View Main Pokémon Front",
    "gui.xp_bar_config": "XP Bar Configuration",
```

**Why:** The settings window looks up human-readable names from this file. Without this entry, the setting would show its raw key name.

---

### Step 6: Add the setting to `lang/setting_description.json`

**File:** `1908235722/lang/setting_description.json`
**Action:** Add the description for the new setting key.

```jsonc
// Find (around lines 23-24):
    "gui.view_main_front": "View front of main Pokémon in reviewer when GIFs are enabled. Set Disabled to show from the back.",
    "gui.xp_bar_config": "Enable XP bar in the reviewer to show XP progression for main Pokémon.",

// Change to:
    "gui.team_deck_view": "Enable a quick overview of your Pokémon team at the bottom of the deck overview.",
    "gui.view_main_front": "View front of main Pokémon in reviewer when GIFs are enabled. Set Disabled to show from the back.",
    "gui.xp_bar_config": "Enable XP bar in the reviewer to show XP progression for main Pokémon.",
```

**Why:** The settings window shows this description as a tooltip or subtitle.

---

### Step 7: Add the setting to the "Styling" group in `pyobj/settings_window.py`

**File:** `1908235722/pyobj/settings_window.py`
**Action:** Add `"Team Overview in Deck Overview"` to the `"Styling"` group's settings list.

```python
# Find this line (around line 264):
            "Styling": {"settings": ["Styling in Reviewer", "Animate Time", "HP Bar Thickness", "Reviewer Image as GIF", "View Main Pokémon Front", "Show GIFs in Collection"]},

# Change to:
            "Styling": {"settings": ["Styling in Reviewer", "Team Overview in Deck Overview", "Animate Time", "HP Bar Thickness", "Reviewer Image as GIF", "View Main Pokémon Front", "Show GIFs in Collection"]},
```

**Why:** This controls which settings appear in the "Styling" category of the settings UI. The experimental build has `"Team Overview in Deck Overview"` added right after `"Styling in Reviewer"`.

---

### Step 8: Import `overview_team` in `__init__.py`

**File:** `1908235722/__init__.py`
**Action:** Add the import statement **after** `mw.settings_obj = settings_obj` is set.

This is **critical** — the `overview_team.py` module reads `mw.settings_obj` at module level (line 211: `if mw.settings_obj.get("gui.team_deck_view") is True:`), so the import **must** come after `mw.settings_obj` has been assigned.

```python
# Find this block (around lines 141-143 of __init__.py):
mw.settings_ankimon = settings_window
mw.logger = logger
mw.translator = translator
mw.settings_obj = settings_obj

# Add immediately AFTER those lines:
from .gui_classes.overview_team import *
```

**Why:** In the experimental build, this import appears at line 157 of `__init__.py`, right after the `mw.settings_obj` assignment. The wildcard import causes the module to execute, which:
1. Defines the hook functions.
2. Checks `mw.settings_obj.get("gui.team_deck_view")`.
3. If `True`, registers the hooks with `gui_hooks.deck_browser_will_render_content` and `gui_hooks.overview_will_render_content`.

---

## 2. No-Change Files (Already Correct)

These files already exist in the stable addon and **do not need modification**:

| File | Status |
|---|---|
| `functions/sprite_functions.py` | ✅ `get_sprite_path()` already exists and is identical |
| `resources.py` → `team_pokemon_path` | ✅ Already defined (`addon_dir / "user_files" / "team.json"`) |
| `resources.py` → `mypokemon_path` | ✅ Already defined |

---

## 3. How `overview_team.py` Works (Architecture Reference)

```
__init__.py
  ├── sets mw.settings_obj = settings_obj
  └── from .gui_classes.overview_team import *
        │
        ├── imports from ..resources:
        │     team_pokemon_path   (user_files/team.json)
        │     mypokemon_path      (user_files/mypokemon.json)
        │     pokeball_path       (addon_files/pokeball.png)  ← YOU ADD THIS
        │
        ├── imports from ..functions.sprite_functions:
        │     get_sprite_path()   (already exists)
        │
        ├── imports from ..utils:
        │     png_to_base64()     ← YOU ADD THIS
        │
        ├── TYPE_COLORS dict (18 Pokémon types → hex colors)
        │
        ├── _bg_style_from_types(types) → CSS gradient string
        │
        ├── POKEBALL_DATA_URI = png_to_base64(pokeball_path)
        │     cached once at import time
        │
        ├── load_pokemon_team() → list[dict]
        │     1. Try team.json → resolve individual_ids against mypokemon.json
        │     2. Fallback to full mypokemon.json
        │     3. Return [] on any error
        │
        ├── _build_pokemon_grid(pokemon_list, id_prefix, max_items=6) → HTML string
        │     Builds a flex grid of up to 6 cards, each showing:
        │       - Sprite (base64 embedded, with pokéball background)
        │       - Name / nickname
        │       - Level, HP, type(s)
        │       - Type-colored background (gradient for dual-type)
        │
        ├── deck_browser_will_render(deck_browser, content)
        │     Hook: prepends grid to content.stats
        │
        ├── on_overview_will_render_content(overview, content)
        │     Hook: prepends grid to content.table
        │
        └── Hook registration (at import time):
              if mw.settings_obj.get("gui.team_deck_view") is True:
                  gui_hooks.deck_browser_will_render_content.append(...)
                  gui_hooks.overview_will_render_content.append(...)
```

---

## 4. Data Flow

```
user_files/team.json          user_files/mypokemon.json
        │                              │
        ▼                              ▼
  [individual_ids]    ────────►  {by individual_id}
        │                              │
        └───── ordered merge ──────────┘
                    │
                    ▼
            pokemon_list (max 6)
                    │
                    ▼
         _build_pokemon_grid()
                    │
           ┌────────┴─────────┐
           ▼                   ▼
   get_sprite_path()    _bg_style_from_types()
           │                   │
           ▼                   ▼
    png_to_base64()       CSS gradient
           │                   │
           └────────┬──────────┘
                    ▼
              HTML string
                    │
           ┌────────┴─────────┐
           ▼                   ▼
   content.stats          content.table
  (Deck Browser)        (Deck Overview)
```

---

## 5. Diff Summary (What Experimental Has That Stable Doesn't)

### `gui_classes/__init__.py` (NEW FILE)
```diff
+ (empty file — makes gui_classes a proper Python package)
```

### `gui_classes/overview_team.py` (NEW FILE)
```diff
+ (full module copied from experimental build — see Step 0b)
```

### `resources.py`
```diff
+ pokeball_path = addon_dir / "addon_files" / "pokeball.png"
```

### `utils.py`
```diff
+ import base64      # (add to existing imports)
  ...
+ def png_to_base64(path):
+     if not os.path.exists(path):
+         return ""
+     with open(path, "rb") as f:
+         return "data:image/png;base64," + base64.b64encode(f.read()).decode("utf-8")
```

### `config.json`
```diff
      "gui.show_mainpkmn_in_reviewer": 1,
+     "gui.team_deck_view": true,
      "gui.view_main_front": true,
```

### `pyobj/settings.py`
```diff
      "gui.show_mainpkmn_in_reviewer": 1,
+     "gui.team_deck_view": True,
      "gui.view_main_front": True,
```

### `lang/setting_name.json`
```diff
+     "gui.team_deck_view": "Team Overview in Deck Overview",
```

### `lang/setting_description.json`
```diff
+     "gui.team_deck_view": "Enable a quick overview of your Pokémon team at the bottom of the deck overview.",
```

### `pyobj/settings_window.py`
```diff
-     "Styling": {"settings": ["Styling in Reviewer", "Animate Time", ...
+     "Styling": {"settings": ["Styling in Reviewer", "Team Overview in Deck Overview", "Animate Time", ...
```

### `__init__.py`
```diff
  mw.settings_obj = settings_obj
+
+ from .gui_classes.overview_team import *
```

---

## 6. Verification Checklist

After all changes:

1. **Startup test:** Launch Anki — addon must load without `ImportError` or `AttributeError`.
2. **Deck Browser:** Navigate to the main deck list — a 6-Pokémon grid should appear at the top of the stats area (if the user has Pokémon).
3. **Deck Overview:** Click into any deck's overview — the grid should appear at the top of the table area.
4. **Setting OFF:** Open Ankimon Settings → Styling → disable "Team Overview in Deck Overview" → restart Anki → grid should NOT appear.
5. **Empty data:** Delete/empty `mypokemon.json` → no crash, grid simply doesn't render.
6. **Missing sprites:** Remove a sprite file → the substitute image should appear instead.

---

## 7. Potential Pitfalls

| Pitfall | Mitigation |
|---|---|
| `gui_classes/overview_team.py` not on disk | Causes `ModuleNotFoundError`. Must create the file first (copy from experimental). |
| `gui_classes/__init__.py` missing | Can cause `ModuleNotFoundError` in Anki's addon loader. Create an empty `__init__.py`. |
| Importing `overview_team` **before** `mw.settings_obj` is set | Causes `AttributeError`. Always import **after** the assignment line. |
| Missing `pokeball_path` in `resources.py` | Causes `ImportError` on startup. Must add before the `overview_team` import runs. |
| Missing `png_to_base64` in `utils.py` | Causes `ImportError`. Must add before the import. |
| `gui.team_deck_view` missing from `DEFAULT_CONFIG` | `settings_obj.get()` returns `None`, which is not `True`, so hooks never register. Feature silently disabled. |
| `config.json` missing the key | New installs won't have the default. Feature off by default unexpectedly. |
| Hook registration is at import time | Toggling the setting requires an Anki restart. This is expected/known behavior. |
| `pokeball.png` file missing from `addon_files/` | `png_to_base64` returns `""`. The grid still works, just without the pokéball background. No crash. |

---

## 8. File Modification Order (Recommended)

Apply changes in this order to avoid import errors if you test incrementally:

0a. `gui_classes/__init__.py` — create empty file (package initializer)
0b. `gui_classes/overview_team.py` — create the module (copy from experimental)
1. `resources.py` — add `pokeball_path`
2. `utils.py` — add `import base64` + `png_to_base64()`
3. `config.json` — add `gui.team_deck_view`
4. `pyobj/settings.py` — add to `DEFAULT_CONFIG`
5. `lang/setting_name.json` — add display name
6. `lang/setting_description.json` — add description
7. `pyobj/settings_window.py` — add to Styling group
8. `__init__.py` — add the import (LAST, after all dependencies exist)
