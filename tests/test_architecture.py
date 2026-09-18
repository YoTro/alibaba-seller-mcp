"""Architecture guards that `lint-imports` cannot express.

The layers contract in ``pyproject.toml`` checks *which* module imports which; this
one checks *what* it imports: a leading underscore means "not part of my interface",
so a name that crosses a module boundary must be public in the module it comes from.
"""

from __future__ import annotations

import ast
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src" / "alibaba_seller_mcp"


def _private_cross_module_imports(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    out = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.ImportFrom):
            continue
        # relative imports, or absolute ones back into this package
        own = node.level > 0 or (node.module or "").startswith("alibaba_seller_mcp")
        if not own:
            continue
        where = ("." * node.level) + (node.module or "")
        for alias in node.names:
            if alias.name.startswith("_") and not alias.name.startswith("__"):
                out.append(f"{path.name}:{node.lineno} imports {alias.name} from {where}")
    return out


def test_no_module_reaches_into_another_modules_privates():
    leaks = [leak for f in sorted(SRC.rglob("*.py")) for leak in _private_cross_module_imports(f)]
    assert leaks == [], (
        "underscore names are module-internal; promote the helper (and document it) "
        "or keep the caller out of it:\n  " + "\n  ".join(leaks)
    )
