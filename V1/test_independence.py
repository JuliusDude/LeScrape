"""V1 must not depend on the repo's ``ecourts`` package (owner request, 2026-11-13)."""
import os
import re
import subprocess
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

MODULES = ["V1", "V1.models", "V1.parser", "V1.db", "V1.exporter", "V1.captcha", "V1.search", "V1.scraper"]


class TestV1IsIndependentOfEcourts(unittest.TestCase):
    def test_no_ecourts_imports_in_source(self):
        pat = re.compile(r"^\s*(from|import)\s+ecourts\b", re.MULTILINE)
        offenders = []
        for dirpath, _, files in os.walk(HERE):
            if "__pycache__" in dirpath:
                continue
            for f in files:
                if f.endswith(".py") and f != os.path.basename(__file__):
                    with open(os.path.join(dirpath, f), encoding="utf-8") as fh:
                        if pat.search(fh.read()):
                            offenders.append(os.path.relpath(os.path.join(dirpath, f), ROOT))
        self.assertEqual(offenders, [], f"V1 files import ecourts: {offenders}")

    def test_every_module_imports_with_ecourts_blocked(self):
        # Fresh interpreter in which any attempt to import ecourts raises ImportError.
        code = (
            "import sys, importlib\n"
            "class Block:\n"
            "    def find_spec(self, name, path=None, target=None):\n"
            "        if name == 'ecourts' or name.startswith('ecourts.'):\n"
            "            raise ImportError('ecourts is blocked: ' + name)\n"
            "        return None\n"
            "sys.meta_path.insert(0, Block())\n"
            f"for m in {MODULES!r}:\n"
            "    importlib.import_module(m)\n"
            "from V1.scraper import DeeperScraper\n"
            "from V1.search import parse_case_results, WrongCaptchaError\n"
            "from V1.captcha import LocalOcrSolver\n"
            "assert not any(k == 'ecourts' or k.startswith('ecourts.') for k in sys.modules)\n"
            "print('ok')\n"
        )
        r = subprocess.run([sys.executable, "-c", code], cwd=ROOT, capture_output=True, text=True, timeout=120)
        self.assertEqual(r.returncode, 0, r.stderr[-2000:])
        self.assertIn("ok", r.stdout)

    def test_works_from_a_copy_without_ecourts_folder(self):
        # Copy only V1/ into an empty directory and parse the golden fixture there.
        import shutil
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            shutil.copytree(HERE, os.path.join(td, "V1"), ignore=shutil.ignore_patterns("__pycache__", "*.db"))
            code = (
                "from V1.parser import parse_deeper_case_detail\n"
                "from V1.search import parse_case_results\n"
                "c = parse_deeper_case_detail(open('V1/fixtures/target_case_view.html', encoding='utf-8').read())\n"
                "assert c.cnr_number == 'KABC010342242022', c.cnr_number\n"
                "import V1.scraper\n"
                "print('ok', len(c.hearings))\n"
            )
            r = subprocess.run([sys.executable, "-c", code], cwd=td, capture_output=True, text=True, timeout=120)
            self.assertEqual(r.returncode, 0, r.stderr[-2000:])
            self.assertIn("ok 32", r.stdout)


if __name__ == "__main__":
    unittest.main()
