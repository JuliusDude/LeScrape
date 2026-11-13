"""
V1.test_import_isolation
=================================
Phase 0: importing parser/db/models (and the JSON/CSV exporters) must not need
openpyxl, and ``from V1 import X`` must keep working for every name in
``__all__``. Isolation checks run in a subprocess so this process is not polluted.
"""

from __future__ import annotations

import importlib
import os
import subprocess
import sys
import textwrap
import unittest

repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if repo_root not in sys.path:
    sys.path.insert(0, repo_root)

SUBPROCESS_SCRIPT = textwrap.dedent(
    """
    import sys, os, tempfile
    sys.modules['openpyxl'] = None   # block openpyxl
    sys.path.insert(0, {root!r})
    import V1.parser, V1.db, V1.models
    from V1 import parse_deeper_case_detail, DeeperDatabase
    assert 'V1.exporter' not in sys.modules, 'exporter imported eagerly'
    assert 'V1.scraper' not in sys.modules, 'scraper imported eagerly'
    from V1 import export_to_json, export_to_csv_flat, export_to_multisheet_excel
    html = open(os.path.join({root!r}, 'V1', 'fixtures', 'target_case_view.html'), encoding='utf-8').read()
    case = parse_deeper_case_detail(html)
    with tempfile.TemporaryDirectory() as tmp:
        db = DeeperDatabase(os.path.join(tmp, 't.db'))
        db.save_case(case)
        export_to_json(db, os.path.join(tmp, 'o.json'))
        export_to_csv_flat(db, os.path.join(tmp, 'o.csv'))
        assert os.path.getsize(os.path.join(tmp, 'o.json')) > 1000
        assert os.path.getsize(os.path.join(tmp, 'o.csv')) > 200
        try:
            export_to_multisheet_excel(db, os.path.join(tmp, 'o.xlsx'))
        except RuntimeError as e:
            assert 'openpyxl' in str(e), str(e)
        else:
            raise SystemExit('expected RuntimeError')
        db.close() if hasattr(db, 'close') else None
    print('OK')
    """
)


class TestImportIsolation(unittest.TestCase):
    def test_no_openpyxl_needed_for_core_imports_and_json_csv(self):
        script = SUBPROCESS_SCRIPT.format(root=repo_root)
        res = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True, timeout=120)
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
        self.assertIn("OK", res.stdout)

    def test_all_names_resolve(self):
        import V1

        lazy_deps = {
            "export_to_json": "bs4",
            "export_to_csv_flat": "bs4",
            "export_to_multisheet_excel": "bs4",
            "DeeperScraper": "requests",
        }
        for name in V1.__all__:
            if name in lazy_deps:
                mod = "V1.scraper" if name == "DeeperScraper" else "V1.exporter"
                try:
                    importlib.import_module(mod)
                except ImportError as exc:
                    self.skipTest(f"{name}: dependency missing ({exc})")
            self.assertTrue(hasattr(V1, name), f"{name} does not resolve")
            self.assertIsNotNone(getattr(V1, name))

    def test_unknown_attribute_raises(self):
        import V1

        with self.assertRaises(AttributeError):
            V1.does_not_exist  # noqa: B018


if __name__ == "__main__":
    unittest.main()
