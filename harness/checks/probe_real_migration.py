"""Tier 2: click through a failed migration and Retry with real Qt and SQLite.

Run: python3 -m harness.checks.probe_real_migration [screenshot-directory]
All saves and screenshots use disposable directories outside the shipped add-on.
"""

import json
from pathlib import Path
import sys
import tempfile
from unittest.mock import patch

from harness.real_env import start_real_session


def main():
    session = start_real_session()
    from Ankimon.pyobj.database_manager import AnkimonDB
    from Ankimon.pyobj.migration_dialog import MigrationDialog
    from harness.fixtures import build_pokemon

    output = (
        Path(sys.argv[1])
        if len(sys.argv) > 1
        else Path(tempfile.mkdtemp(prefix="ankimon-migration-qa-"))
    )
    output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="ankimon-legacy-save-") as directory:
        root = Path(directory)
        box = []
        for i in range(158):
            pokemon = build_pokemon(
                {"species": "Pikachu", "level": i % 80 + 5}
            ).to_dict()
            pokemon.pop("individual_id", None)
            box.append(pokemon)
        sources = {
            "mypokemon": box,
            "mainpokemon": [box[0]],
            "items": ["pass-orb", "old-gateau", "pass-orb"],
            "badges": list(range(1, 15)),
            "team": [box[0], box[1]],
        }
        paths = {}
        for name, data in sources.items():
            path = root / f"{name}.json"
            path.write_text(json.dumps(data), encoding="utf-8")
            paths[f"{name}_path"] = path
        db = AnkimonDB(db_path=root / "ankimon.db")
        dialog = MigrationDialog(db, **paths)
        dialog.show()
        session.app.processEvents()
        original = db.add_item

        def fail_item(name, *args, **kwargs):
            if name == "old-gateau":
                raise RuntimeError("Injected inventory write failure")
            return original(name, *args, **kwargs)

        with patch.object(db, "add_item", side_effect=fail_item):
            dialog.start_button.click()
        assert not dialog.migration_successful
        assert db.get_pokemon_count() == 158
        assert not db.is_migrated()
        assert db.get_item("pass-orb") is None
        assert paths["mypokemon_path"].is_file()
        assert dialog.start_button.isEnabled()
        assert not dialog.continue_button.isVisible()
        session.app.processEvents()
        assert dialog.grab().save(str(output / "migration-incomplete.png"))

        dialog.start_button.click()
        assert dialog.migration_successful, dialog.log_area.toPlainText()
        assert db.get_pokemon_count() == 158
        assert db.is_migrated()
        assert db.get_item("pass-orb")["quantity"] == 2
        assert len(db.get_team()) == 2
        assert json.loads((root / "json/mypokemon.json").read_text()) == box
        assert dialog.continue_button.isVisible()
        assert not dialog.start_button.isVisible()
        session.app.processEvents()
        assert dialog.grab().save(str(output / "migration-complete.png"))
        dialog.continue_button.click()
        assert dialog.result() == dialog.DialogCode.Accepted
        db.close()
    print(
        f"probe_real_migration: OK — 158 Pokemon preserved after failure/retry; screenshots: {output}"
    )


if __name__ == "__main__":
    main()
