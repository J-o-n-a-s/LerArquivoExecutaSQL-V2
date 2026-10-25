"""REORG-SRC-1: estrutura simples em src/ e entrypoints equivalentes."""

import contextlib
import io
import os
import runpy
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

import preflight
import sql_classifier
from tests import _application_harness
from tests._application_harness import (
    SYNTHETIC_DATABASE,
    SYNTHETIC_DRIVER,
    SYNTHETIC_SERVER,
    Scenario,
    STATEMENT,
)


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
ROOT_MAIN = ROOT / "main.py"
SRC_MAIN = SRC / "main.py"


class SourceLayoutTests(unittest.TestCase):
    def test_production_modules_live_directly_under_src(self):
        self.assertTrue(SRC.is_dir())
        for name in ("main.py", "sql_classifier.py", "preflight.py"):
            with self.subTest(name=name):
                self.assertTrue((SRC / name).is_file())
        self.assertFalse((ROOT / "sql_classifier.py").exists())
        self.assertFalse((ROOT / "preflight.py").exists())
        self.assertFalse((SRC / "lerarquivoexecutasql").exists())
        self.assertFalse((SRC / "__init__.py").exists())

    def test_direct_test_imports_resolve_to_src(self):
        self.assertEqual(Path(sql_classifier.__file__).resolve(), SRC / "sql_classifier.py")
        self.assertEqual(Path(preflight.__file__).resolve(), SRC / "preflight.py")
        self.assertIn(str(SRC), sys.path)

    def test_harness_compiles_the_effective_src_application(self):
        self.assertEqual(_application_harness.SOURCE_PATH, SRC_MAIN)
        self.assertEqual(_application_harness.SOURCE_PATH.read_bytes(), SRC_MAIN.read_bytes())

    def test_root_main_is_a_minimal_guarded_launcher(self):
        source = ROOT_MAIN.read_text(encoding="utf-8")
        self.assertLessEqual(len(source.splitlines()), 25)
        self.assertIn('if __name__ == "__main__":', source)
        self.assertIn("runpy.run_path", source)
        self.assertIn('"src"', source)
        for forbidden in ("pyodbc", "SQLCommandReader", "Driver=", "Digite o caminho", "cursor.execute"):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, source)

    def test_importing_root_launcher_does_not_execute_application(self):
        with patch("builtins.input", side_effect=AssertionError("launcher executou durante import")):
            namespace = runpy.run_path(str(ROOT_MAIN), run_name="source_layout_import")
        self.assertNotEqual(namespace["__name__"], "__main__")

    def test_both_entrypoints_execute_the_same_real_application_synthetically(self):
        for entrypoint in (ROOT_MAIN, SRC_MAIN):
            with self.subTest(entrypoint=entrypoint.name, parent=entrypoint.parent.name):
                scenario = Scenario(text=STATEMENT + "\n")
                output = io.StringIO()
                with (patch.dict(sys.modules, {"pyodbc": scenario.pyodbc}),
                      patch.dict(os.environ, {
                          "LER_SQL_SERVER": SYNTHETIC_SERVER,
                          "LER_SQL_DATABASE": SYNTHETIC_DATABASE,
                          "LER_SQL_DRIVER": SYNTHETIC_DRIVER,
                      }, clear=True),
                      patch("builtins.open", scenario.open),
                      patch("builtins.input", scenario.input),
                      contextlib.redirect_stdout(output),
                      contextlib.redirect_stderr(output)):
                    with self.assertRaises(SystemExit) as stopped:
                        runpy.run_path(str(entrypoint), run_name="__main__")
                self.assertEqual(stopped.exception.code, 0, output.getvalue())
                self.assertEqual(scenario.cursor.statements, [STATEMENT])
                self.assertEqual(scenario.confirmed, [STATEMENT])
                self.assertEqual(scenario.connection.commit_attempts, 1)
                self.assertEqual(scenario.connection.rollback_attempts, 0)


if __name__ == "__main__":
    unittest.main()
