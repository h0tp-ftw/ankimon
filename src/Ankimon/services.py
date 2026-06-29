"""
services.py — the addon's own service registry.

Historically Ankimon hung its own objects (database, logger, translator,
settings) on Anki's global ``mw`` (the main window). That coupled every module
to Anki: importing anything pulled in ``aqt``, and the only way to substitute a
fake in a test was to monkeypatch a global. This module is the replacement — a
small registry that holds those services and, deliberately, does **not** import
``aqt`` or ``anki``.

This is a *registry* pattern, not full dependency injection. It is still a
global, but one the tests own and one that does not drag in Anki. Constructor
injection can be layered on top later, module by module; until then this is the
seam that makes the addon's own logic testable without an Anki runtime.

Usage
-----
Populate it **once** at the composition root (see ``singletons.py``)::

    from .services import services
    services.populate(db=ankimon_db, logger=logger,
                      settings=settings_obj, translator=translator)

Read it anywhere instead of reaching into ``mw``::

    from .services import services
    services.db.get_pokemon(...)

In a test, assign fakes — no Anki, no ``sys.modules`` surgery::

    from Ankimon.services import services
    services.db = FakeDB()

Author: Ankimon contributors
Created: 2026-05-21
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    # Type-checker-only imports. Guarded by TYPE_CHECKING so they never execute
    # at runtime — that is what keeps this module free of any aqt/anki import.
    from .pyobj.database_manager import AnkimonDB
    from .pyobj.InfoLogger import ShowInfoLogger
    from .pyobj.settings import Settings
    from .pyobj.translator import Translator
    from .pyobj.ankimon_tracker import AnkimonTracker
    from .pyobj.pokemon_obj import PokemonObject
    from .pyobj.trainer_card import TrainerCard


class Services:
    """Holds the addon's own services and shared game state. No Anki dependency.

    The registry holds three kinds of thing:

    * **Core services** (``db``/``logger``/``settings``/``translator``) — already
      aqt-free helper objects.
    * **Core game state** (``tracker``/``main_pokemon``/``enemy_pokemon``/
      ``trainer_card``/``achievements``) — the live objects the battle loop
      mutates. Code reads them from here (often via :func:`module_getattr`)
      instead of importing them from ``singletons``, which is what lets that
      code import without pulling in Anki.
    * **UI ports** (``ui`` presenter, and the window objects ``test_window`` /
      ``evo_window`` / ``pokemon_pc`` / ``reviewer``) — swapped between the real
      Qt implementations (production) and recording fakes (the agent harness).
    """

    def __init__(self) -> None:
        # Core, aqt-free services.
        self.db: Optional["AnkimonDB"] = None
        self.logger: Optional["ShowInfoLogger"] = None
        self.settings: Optional["Settings"] = None
        self.translator: Optional["Translator"] = None

        # Core game state (also aqt-free objects).
        self.tracker: Optional["AnkimonTracker"] = None
        self.main_pokemon: Optional["PokemonObject"] = None
        self.enemy_pokemon: Optional["PokemonObject"] = None
        self.trainer_card: Optional["TrainerCard"] = None
        self.achievements: Optional[dict] = None

        # UI port. Defaults to a GUI-less presenter so core logic can always
        # call ``services.ui.*`` safely; the production root swaps in a Qt one.
        from .ui_port import HeadlessPresenter
        self.ui = HeadlessPresenter()

        # GUI window objects, injected by the composition root: real Qt windows
        # in production, recording fakes in the harness. Core logic reaches them
        # through ``services`` so either works.
        self.test_window = None
        self.evo_window = None
        self.pokemon_pc = None
        self.reviewer = None

        # The Anki collection (``mw.col``) when running inside Anki; None headless.
        self.col = None

    def populate(
        self,
        *,
        db: Optional["AnkimonDB"] = None,
        logger: Optional["ShowInfoLogger"] = None,
        settings: Optional["Settings"] = None,
        translator: Optional["Translator"] = None,
        tracker: Optional["AnkimonTracker"] = None,
        main_pokemon: Optional["PokemonObject"] = None,
        enemy_pokemon: Optional["PokemonObject"] = None,
        trainer_card: Optional["TrainerCard"] = None,
        achievements: Optional[dict] = None,
        ui=None,
        test_window=None,
        evo_window=None,
        pokemon_pc=None,
        reviewer=None,
        col=None,
    ) -> None:
        """Wire up services/state at the composition root.

        Keyword-only and skip-if-None so the call site reads clearly and a
        partial re-populate never clobbers an already-set value with ``None``.
        """
        if db is not None:
            self.db = db
        if logger is not None:
            self.logger = logger
        if settings is not None:
            self.settings = settings
        if translator is not None:
            self.translator = translator
        if tracker is not None:
            self.tracker = tracker
        if main_pokemon is not None:
            self.main_pokemon = main_pokemon
        if enemy_pokemon is not None:
            self.enemy_pokemon = enemy_pokemon
        if trainer_card is not None:
            self.trainer_card = trainer_card
        if achievements is not None:
            self.achievements = achievements
        if ui is not None:
            self.ui = ui
        if test_window is not None:
            self.test_window = test_window
        if evo_window is not None:
            self.evo_window = evo_window
        if pokemon_pc is not None:
            self.pokemon_pc = pokemon_pc
        if reviewer is not None:
            self.reviewer = reviewer
        if col is not None:
            self.col = col

    def reset(self) -> None:
        """Clear every service/state. Intended for test isolation."""
        self.db = None
        self.logger = None
        self.settings = None
        self.translator = None
        self.tracker = None
        self.main_pokemon = None
        self.enemy_pokemon = None
        self.trainer_card = None
        self.achievements = None
        from .ui_port import HeadlessPresenter
        self.ui = HeadlessPresenter()
        self.test_window = None
        self.evo_window = None
        self.pokemon_pc = None
        self.reviewer = None
        self.col = None


# The single shared registry instance. Import this, not the class.
services = Services()
