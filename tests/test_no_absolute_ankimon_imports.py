"""Guard against absolute ``Ankimon`` imports in the add-on source.

The add-on ships as package ``1908235722`` (see ``src/Ankimon/manifest.json``),
so ``import Ankimon.x`` only resolves in this test suite and the harness, which
load the tree under the ``Ankimon`` name. In a real install it raises
``ModuleNotFoundError``. Production code must use relative imports.
"""
import ast
import pathlib

SRC = pathlib.Path(__file__).resolve().parent.parent / "src" / "Ankimon"


def test_no_absolute_ankimon_imports():
    bad = []
    for path in SRC.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.level == 0:
                names = [node.module or ""]
            else:
                continue
            if any(n == "Ankimon" or n.startswith("Ankimon.") for n in names):
                bad.append(f"{path.relative_to(SRC.parent)}:{node.lineno}")
    assert not bad, "absolute 'Ankimon' imports break real installs:\n" + "\n".join(bad)
