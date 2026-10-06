from __future__ import annotations

import ast
from pathlib import Path
import sys
import tomllib
import unittest


class ArchitectureTests(unittest.TestCase):
    def test_g1_core_has_no_mandatory_third_party_runtime_dependency(self) -> None:
        root = Path(__file__).parents[1]
        project = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
        self.assertEqual(project["project"].get("dependencies"), [])

        unexpected: set[str] = set()
        for source_path in sorted((root / "src" / "nexus_workbench").glob("*.py")):
            tree = ast.parse(source_path.read_text(encoding="utf-8"), filename=str(source_path))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    roots = [alias.name.split(".", 1)[0] for alias in node.names]
                elif isinstance(node, ast.ImportFrom):
                    if node.level:
                        continue
                    roots = [str(node.module).split(".", 1)[0]]
                else:
                    continue
                for module_root in roots:
                    if module_root not in sys.stdlib_module_names and module_root != "nexus_workbench":
                        unexpected.add(module_root)

        self.assertEqual(sorted(unexpected), [])


if __name__ == "__main__":
    unittest.main()
