"""
reloader.py — developer hot-reload for the Ankimon add-on.

Ctrl+Shift+R (and the developer-only "Restart Ankimon" menu entry) call
:func:`restart_ankimon`, which tears the add-on down and re-imports it in place
so code edits take effect without restarting Anki.

Seam-aware teardown
-------------------
Ankimon's live state lives in the ``services`` registry (:mod:`Ankimon.services`),
not on ``mw``. So unlike a naive "delete every submodule" reload, this one
**preserves ``<addon>.services``** across the purge: the registry singleton keeps
the database, settings, tracker, Pokemon and trainer card, which is what lets
``singletons`` re-execute down its ``_core_is_populated()``-is-True branch (reuse
the core, rebind module globals) instead of rebuilding — and is what preserves
the active database file (e.g. ``ankimonDEV.db``) across a reload.

Every hook-registering module already records its handlers on that surviving
registry and removes the previous record before re-appending on re-import, so
re-registration is self-deduplicating. Teardown's remaining job is therefore to:

* remove the add-on's ``gui_hooks`` handlers (belt-and-suspenders, and the only
  way the *unrecorded* ``webview_will_set_content`` handler gets cleared),
* restore the pristine ``Reviewer`` methods that ``reviewer_ui`` wraps (the
  ``Reviewer._ankimon_orig_*`` contract),
* delete the ``Ctrl+Shift+R`` ``QShortcut`` objects (the one UI handle with no
  self-dedup),
* close and delete every add-on Qt widget / QWebEngine web shell, and null the
  registry's UI slots (windows + reviewer manager) so ``singletons`` rebuilds
  them — while leaving the core *data* services untouched,
* drop the cached DB singleton (``database_manager.reset_db``), and finally
* purge the add-on's own submodules from ``sys.modules`` (keeping ``services``
  and any native extension modules) so the re-import loads fresh code.

Headless-import contract (integrity test): Qt classes are imported directly from
``PyQt6`` (not the possibly-mocked ``aqt.qt``), aqt-only symbols are imported
lazily inside the functions, and nothing constructs a Qt object at import time.
"""

import importlib
import sys
import traceback

from PyQt6.QtCore import QCoreApplication, QEvent
from PyQt6.QtWidgets import QApplication, QWidget
from aqt import gui_hooks, mw


# Longest the reload blocks the GUI thread waiting for an in-flight startup
# QueryOp. Generous enough for a slow first boot (legacy backup + dev-mode disk
# copy + DB migration on a large collection), bounded so a wedged startup costs
# the developer one tooltip instead of a frozen Anki that needs force-quitting.
_STARTUP_WAIT_TIMEOUT_SECONDS = 30.0


def restart_ankimon():
    """Tear the add-on down and re-import it in place (developer hot-reload)."""
    import time

    from aqt.utils import tooltip

    from .services import services

    # Re-entrancy guard. The wait loop below pumps the Qt event queue while the
    # Ctrl+Shift+R QShortcut and the "Restart Ankimon" menu action are both
    # still live and enabled, so a second trigger can land *inside* this call
    # and start a concurrent teardown of half-purged modules. The flag lives on
    # the registry, not at module scope, because _purge_addon_modules() drops
    # this module while the reload is still running.
    if getattr(services, "_reload_in_progress", False):
        return
    services._reload_in_progress = True
    try:
        # Wait for the active startup QueryOp before purging modules. Otherwise
        # its worker can hold a database connection lock while waiting for
        # Python's import lock, as teardown waits for that same database lock.
        deadline = time.monotonic() + _STARTUP_WAIT_TIMEOUT_SECONDS
        while getattr(services, "_startup_in_progress", False):
            if time.monotonic() >= deadline:
                # Purging now is exactly what deadlocks, so abort the reload
                # rather than trade a slow startup for a hung Anki.
                print(
                    "Ankimon reload aborted: startup still running after "
                    f"{_STARTUP_WAIT_TIMEOUT_SECONDS:.0f}s."
                )
                try:
                    tooltip("Ankimon reload skipped — startup still running.")
                except Exception:
                    pass
                return
            QApplication.processEvents()
            time.sleep(0.02)

        addon_package = __name__.split(".")[0]
        services._is_reloading = True
        try:
            teardown_ankimon(addon_package)
            importlib.import_module(addon_package)
            tooltip("Ankimon reloaded.")
        except Exception as exc:  # surface any reload failure to the developer
            # A successful re-import starts a fresh startup QueryOp, and *its*
            # callbacks own clearing _is_reloading. If we failed before that
            # (teardown raised, or the edited module has a syntax error) nothing
            # else will clear the flag, and every later startup in this session
            # would keep silently skipping its backups.
            if not getattr(services, "_startup_in_progress", False):
                services._is_reloading = False
            message = f"Ankimon reload failed: {exc}\n{traceback.format_exc()}"
            print(message)
            try:
                tooltip("Ankimon reload failed — see console.")
            except Exception:
                pass
    finally:
        services._reload_in_progress = False


def _handler_belongs_to_addon(handler, addon_package):
    """True if a gui_hooks callback was defined inside this add-on."""
    module = getattr(handler, "__module__", "") or ""
    # Bound methods proxy __module__ to __func__; be explicit for robustness.
    if not module and hasattr(handler, "__func__"):
        module = getattr(handler.__func__, "__module__", "") or ""
    return module == addon_package or module.startswith(addon_package + ".")


def _remove_addon_gui_hooks(addon_package):
    """Remove every ``gui_hooks`` handler that belongs to this add-on.

    Anki's generated hooks expose their callback list as ``_hooks``; the headless
    fake hooks *are* lists. Handle both. ``remove()`` tolerates already-absent
    callbacks on both, so this stays safe even though each module also removes
    its own recorded handlers on re-import.
    """
    for attr_name in dir(gui_hooks):
        attr = getattr(gui_hooks, attr_name, None)
        inner = getattr(attr, "_hooks", None)
        if isinstance(inner, list):
            handlers = list(inner)
        elif isinstance(attr, list):
            handlers = list(attr)
        else:
            continue
        for handler in handlers:
            if not _handler_belongs_to_addon(handler, addon_package):
                continue
            try:
                attr.remove(handler)
            except Exception:
                pass


def _restore_reviewer_wraps():
    """Undo ``reviewer_ui``'s Reviewer method wrapping (the _ankimon_orig_* contract)."""
    from aqt.reviewer import Reviewer

    for method in ("_shortcutKeys", "_linkHandler", "_bottomHTML"):
        orig_attr = "_ankimon_orig_" + method
        if hasattr(Reviewer, orig_attr):
            setattr(Reviewer, method, getattr(Reviewer, orig_attr))
            delattr(Reviewer, orig_attr)


def _delete_reload_shortcuts(services):
    """Delete the Ctrl+Shift+R QShortcut objects recorded on the registry."""
    for shortcut in list(getattr(services, "_reload_shortcuts", ()) or ()):
        try:
            shortcut.setEnabled(False)
            shortcut.deleteLater()
        except Exception:
            pass
    services._reload_shortcuts = []


def _close_widget(obj):
    if obj is None:
        return
    try:
        if hasattr(obj, "close"):
            obj.close()
    except Exception:
        pass
    try:
        if isinstance(obj, QWidget):
            obj.deleteLater()
    except Exception:
        pass


def _teardown_windows(addon_package, services):
    """Close/delete the add-on's Qt windows and null the registry UI slots."""
    # Remove the add-on menu from the menubar (rebuilt by create_menu_actions).
    pokemenu = getattr(mw, "pokemenu", None)
    if pokemenu is not None:
        try:
            mw.form.menubar.removeAction(pokemenu.menuAction())
        except Exception:
            pass

    # Close + delete the registry-held windows, then null the slots so the
    # singletons factories rebuild them. (Their is_alive() checks would rebuild
    # a dead one anyway, but nulling avoids ever handing out a deleted C++ object.)
    for slot in ("test_window", "evo_window", "pokemon_pc"):
        _close_widget(getattr(services, slot, None))
        setattr(services, slot, None)
    # The reviewer manager is not a widget; its gui_hooks were just removed, so
    # null it and let singletons rebuild it (re-registering its hooks).
    services.reviewer = None

    # Catch every other add-on widget (item/settings windows, QWebEngine web
    # shells, Ankidex SPA, encounter-simulator dialog, …) by module ownership.
    for widget in QApplication.allWidgets():
        cls_module = getattr(widget.__class__, "__module__", "") or ""
        if cls_module == addon_package or cls_module.startswith(addon_package + "."):
            _close_widget(widget)

    # Drop dangling UI references parked on mw so the re-import rebuilds them.
    for attr in ("pokemenu", "settings_ankimon", "_encounter_simulator_dialog"):
        if hasattr(mw, attr):
            try:
                delattr(mw, attr)
            except Exception:
                pass


def _flush_deferred_widget_deletes():
    """Destroy closed Qt/WebEngine objects before their modules are purged.

    ``deleteLater()`` only schedules destruction. Purging and re-importing the
    add-on while old QWebEngine pages are still alive can release their profile
    first and crash Qt during shutdown or the next reload.
    """
    app = QApplication.instance()
    if app is None:
        return
    for _ in range(2):
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        app.processEvents()


def _purge_addon_modules(addon_package):
    """Drop the add-on's submodules from ``sys.modules`` so the re-import is fresh.

    Preserves two things:

    * ``<addon>.services`` — the live registry singleton that holds the reused
      core state. Keeping it is what makes ``singletons`` re-execute its reuse
      branch and is what preserves the active DB file across the reload.
    * Any already-loaded native extension modules — re-initialising a compiled
      (C/Rust) extension a second time can crash the interpreter.
    """
    services_module = addon_package + ".services"
    for name in list(sys.modules):
        if name != addon_package and not name.startswith(addon_package + "."):
            continue
        if name == services_module:
            continue
        module = sys.modules.get(name)
        origin = ""
        spec = getattr(module, "__spec__", None)
        if spec is not None and getattr(spec, "origin", None):
            origin = spec.origin
        elif getattr(module, "__file__", None):
            origin = module.__file__
        if origin.endswith((".so", ".pyd", ".dylib")):
            continue
        del sys.modules[name]


def teardown_ankimon(addon_package):
    """Undo everything the add-on installed, then purge its modules.

    Preserves the ``services`` registry's core data objects (db / settings /
    tracker / pokemon / trainer card) so the re-import reuses them — which is
    what preserves the active database file across the reload.
    """
    from .services import services

    _remove_addon_gui_hooks(addon_package)
    _restore_reviewer_wraps()
    _delete_reload_shortcuts(services)
    _teardown_windows(addon_package, services)
    _flush_deferred_widget_deletes()

    # Drop the cached DB singleton (closes its connection). The registry still
    # holds the same AnkimonDB instance with its db_path intact, so the reused
    # core reopens the SAME file (e.g. ankimonDEV.db) lazily on next access.
    try:
        from .pyobj.database_manager import reset_db

        reset_db()
    except Exception:
        pass

    # Fresh code on re-import (services + native extensions preserved).
    _purge_addon_modules(addon_package)
