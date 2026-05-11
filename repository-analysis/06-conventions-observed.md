# Conventions Observed

This document outlines the coding and architectural conventions actually observed in the Ankimon repository, rather than generic best practices.

## 1. Global Singleton Injection
- **Strength:** Strong
- **Evidence:** `singletons.py` instantiates `settings_obj`, `logger`, `main_pokemon`, etc. These are then passed explicitly to the constructors of almost every UI class (e.g., `TestWindow`, `ItemWindow`).
- **Guidance:** Do not rely on importing global variables directly into UI classes. Instead, require them in the `__init__` signature to enforce proper dependency injection and avoid circular imports.

## 2. Resource Pathing
- **Strength:** Strong
- **Evidence:** `resources.py` defines `Path` objects for every directory and major file in the project (e.g., `user_path`, `mypokemon_path`).
- **Guidance:** Never hardcode file paths string literals. Always import the relevant path variable from `resources.py`.

## 3. UI Window Destruction
- **Strength:** Moderate
- **Evidence:** Prompt memory indicates a project convention to use `self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)` on most PyQt windows to prevent memory leaks, with explicit exceptions for highly trafficked windows like `pc_box.py`.
- **Guidance:** When creating a new QDialog or QWidget subclass, add the delete-on-close attribute unless keeping the window in memory is specifically required for performance.

## 4. Heavy Caching of Static JSON
- **Strength:** Strong
- **Evidence:** Prompt memory indicates `moves.json` and `learnsets.json` cause severe bottlenecks if re-read. Functions fetching from these files use `@functools.lru_cache(maxsize=1)`.
- **Guidance:** Any read operations targeting large static data files must be wrapped in an LRU cache or loaded once at startup.

## 5. Explicit Stat Mapping over `**kwargs`
- **Strength:** Moderate
- **Evidence:** Guidelines specify that updating a `PokemonObject` should be done by explicitly mapping attributes (e.g., `main_pokemon.hp = pokemon['current_hp']`) rather than blind `**kwargs` updates.
- **Guidance:** Avoid blind dictionary unpacking when hydrating domain objects to prevent schema drift and unmapped keys.

## 6. Strict Type Checking for Configs
- **Strength:** Strong
- **Evidence:** The codebase distinguishes strongly between integers (1, 0) and booleans (True, False) in settings, often using QButtonGroup IDs.
- **Guidance:** Use strict type checking (`type(val) is int`) rather than `isinstance()` when validating configuration, as Python's `bool` subclasses `int`.

## 7. JSON Library Preference
- **Strength:** Strong
- **Evidence:** Memory explicitly mandates the use of `orjson` instead of standard `json` for performance reasons.
- **Guidance:** If you need to write new JSON parsing logic, `import orjson`.

## 8. Logging over Printing
- **Strength:** Strong
- **Evidence:** The presence of `mw.logger` (an instance of `ShowInfoLogger`) and explicit rules forbidding `print`.
- **Guidance:** Use `logger.log("info", ...)` or `logger.log_and_showinfo(...)` exclusively for debug/output. Do not use print statements.
