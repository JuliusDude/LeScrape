"""
V1.test_golden_parse
=============================
Golden regression test for the parser.

``fixtures/snapshots/golden_target_case.json`` is
``parse_deeper_case_detail(target_case_view.html).to_dict()`` produced by the
UNMODIFIED Phase-0 parser. Later phases may ADD keys but must not change or drop
any existing key, nor change list lengths.

``fixtures/snapshots/synthetic_baseline/<name>.json`` documents the pre-fix output
for each synthetic fixture. It is NOT asserted (later phases intentionally change
some of it); it is only regenerated on request.

Regenerate (only from known-good code!):
    DEEPER_REGEN_SNAPSHOTS=1 python -m unittest V1.test_golden_parse
"""

from __future__ import annotations

import glob
import json
import os
import sys
import unittest

repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if repo_root not in sys.path:
    sys.path.insert(0, repo_root)

from V1.parser import parse_deeper_case_detail

HERE = os.path.dirname(os.path.abspath(__file__))
FIX = os.path.join(HERE, "fixtures")
GOLDEN = os.path.join(FIX, "snapshots", "golden_target_case.json")
SYN_DIR = os.path.join(FIX, "synthetic")
SYN_BASE = os.path.join(FIX, "snapshots", "synthetic_baseline")
REGEN = os.environ.get("DEEPER_REGEN_SNAPSHOTS") == "1"


def _read(path):
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def _dump(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        json.dump(obj, f, indent=2, ensure_ascii=False, sort_keys=False)
        f.write("\n")


def assert_superset(test, golden, current, path="$"):
    """Every key/item in golden must be present and equal in current (new keys allowed)."""
    if isinstance(golden, dict):
        test.assertIsInstance(current, dict, f"{path}: expected dict")
        for k, v in golden.items():
            test.assertIn(k, current, f"{path}.{k} disappeared")
            assert_superset(test, v, current[k], f"{path}.{k}")
    elif isinstance(golden, list):
        test.assertIsInstance(current, list, f"{path}: expected list")
        test.assertEqual(len(golden), len(current), f"{path}: list length changed")
        for i, (g, c) in enumerate(zip(golden, current)):
            assert_superset(test, g, c, f"{path}[{i}]")
    else:
        test.assertEqual(golden, current, f"{path}: value changed")


class TestGoldenParse(unittest.TestCase):
    def test_target_case_matches_golden(self):
        current = parse_deeper_case_detail(_read(os.path.join(FIX, "target_case_view.html"))).to_dict()
        if REGEN:
            _dump(GOLDEN, current)
        with open(GOLDEN, "r", encoding="utf-8") as f:
            golden = json.load(f)
        self.assertEqual(len(golden["hearings"]), 32)
        assert_superset(self, golden, current)

    def test_superset_helper_detects_changes(self):
        with self.assertRaises(AssertionError):
            assert_superset(self, {"a": 1}, {"a": 2})
        with self.assertRaises(AssertionError):
            assert_superset(self, {"a": 1}, {})
        with self.assertRaises(AssertionError):
            assert_superset(self, {"a": [{"x": 1}]}, {"a": [{"x": 1}, {"x": 2}]})
        assert_superset(self, {"a": [{"x": 1}]}, {"a": [{"x": 1, "new": 5}], "b": 1})

    def test_synthetic_baselines_exist_and_regenerate(self):
        """Documentation snapshots of pre-fix behaviour; only (re)written on request."""
        names = sorted(glob.glob(os.path.join(SYN_DIR, "*.html")))
        self.assertGreaterEqual(len(names), 11)
        for p in names:
            name = os.path.splitext(os.path.basename(p))[0]
            out = os.path.join(SYN_BASE, name + ".json")
            if REGEN:
                _dump(out, parse_deeper_case_detail(_read(p)).to_dict())
            self.assertTrue(os.path.exists(out), f"missing baseline snapshot for {name}")


if __name__ == "__main__":
    unittest.main()
