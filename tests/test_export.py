"""
Unit and boundary tests for ecourts.export.exporter.
Covers Tier 1 (Feature Coverage) and Tier 2 (Boundary & Corner Cases)
for JSON Export, Relational CSV Export, Flat CSV Export, and Exporter Facade.
"""

from __future__ import annotations

import csv
import json
import os
import tempfile
import unittest

from ecourts.export.exporter import (
    Exporter,
    export_to_csv_flat,
    export_to_csv_relational,
    export_to_json,
)
from ecourts.parser.case_details import CaseDetail
from ecourts.storage.db import Database


class TestExportTier1(unittest.TestCase):
    """Tier 1: Feature Coverage for Export Pipelines."""

    def setUp(self):
        self.db = Database(":memory:")
        self.temp_dir = tempfile.mkdtemp()

        # Seed sample cases
        c1 = CaseDetail(
            cnr_number="KABC0E0001992025",
            case_type="Misc",
            case_number="Misc/2/2025",
            case_status="Pending",
            stage="Evidence",
            court_name="PRL. JUDGE FAMILY COURT",
            parties=[
                {"type": "petitioner", "name": "Ram", "advocate": "A. Rao"},
                {"type": "respondent", "name": "Sita", "advocate": "P. Hegde"},
            ],
            acts=[{"act": "HMA", "sections": "13(1)"}],
            hearings=[
                {"business_date": "16-01-2025", "hearing_date": "10-02-2025", "purpose": "Notice", "judge": "J1"},
                {"business_date": "10-02-2025", "hearing_date": "24-03-2025", "purpose": "Hearing", "judge": "J1"},
            ],
        )
        c2 = CaseDetail(
            cnr_number="DLHC010005552024",
            case_type="OS",
            case_number="OS/555/2024",
            case_status="Disposed",
            stage="Disposed",
            court_name="DELHI HIGH COURT",
            parties=[{"type": "petitioner", "name": "Govt of NCT", "advocate": ""}],
            acts=[],
            hearings=[],
        )
        self.db.save_case(c1)
        self.db.save_case(c2)

    def tearDown(self):
        self.db.close()

    def test_export_to_json_nested_structure(self):
        """Verify export_to_json generates valid JSON array with embedded child relations."""
        json_path = os.path.join(self.temp_dir, "cases.json")
        count = export_to_json(self.db, json_path)
        self.assertEqual(count, 2)
        self.assertTrue(os.path.exists(json_path))

        with open(json_path, "r", encoding="utf-8") as f:
            data = json.load(f)

        self.assertIsInstance(data, list)
        self.assertEqual(len(data), 2)

        # Locate first case
        c1 = next(item for item in data if item["cnr_number"] == "KABC0E0001992025")
        self.assertEqual(len(c1["parties"]), 2)
        self.assertEqual(len(c1["acts"]), 1)
        self.assertEqual(len(c1["hearings"]), 2)
        self.assertEqual(c1["acts"][0]["act_name"], "HMA")

    def test_export_to_csv_relational(self):
        """Verify export_to_csv_relational outputs 4 individual CSV files with proper headers."""
        rel_dir = os.path.join(self.temp_dir, "relational")
        counts = export_to_csv_relational(self.db, rel_dir)

        self.assertEqual(counts["cases"], 2)
        self.assertEqual(counts["parties"], 3)  # 2 in c1 + 1 in c2
        self.assertEqual(counts["acts"], 1)
        self.assertEqual(counts["hearings"], 2)

        for filename in ("cases.csv", "parties.csv", "acts.csv", "hearings.csv"):
            fpath = os.path.join(rel_dir, filename)
            self.assertTrue(os.path.exists(fpath), f"Missing {filename}")
            with open(fpath, "r", newline="", encoding="utf-8") as f:
                reader = csv.reader(f)
                rows = list(reader)
                self.assertGreaterEqual(len(rows), 1, f"Empty CSV {filename}")

    def test_export_to_csv_flat_denormalized(self):
        """Verify export_to_csv_flat outputs aggregated parties, acts, and hearing summaries."""
        flat_path = os.path.join(self.temp_dir, "flat_cases.csv")
        count = export_to_csv_flat(self.db, flat_path)
        self.assertEqual(count, 2)

        with open(flat_path, "r", newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            rows = list(reader)

        self.assertEqual(len(rows), 2)
        c1 = next(r for r in rows if r["cnr_number"] == "KABC0E0001992025")
        self.assertIn("Ram", c1["petitioners"])
        self.assertIn("Sita", c1["respondents"])
        self.assertIn("HMA", c1["acts_sections"])
        self.assertEqual(c1["total_hearings"], "2")

    def test_export_filter_by_status(self):
        """Verify export methods filter by case status ('Pending' vs 'Disposed')."""
        json_pending = os.path.join(self.temp_dir, "pending.json")
        count = export_to_json(self.db, json_pending, status="Pending")
        self.assertEqual(count, 1)

        with open(json_pending, "r", encoding="utf-8") as f:
            data = json.load(f)
        self.assertEqual(data[0]["cnr_number"], "KABC0E0001992025")

    def test_exporter_facade_routing(self):
        """Verify Exporter facade correctly dispatches format='json', 'csv' flat and relational."""
        exporter = Exporter(self.db)

        # JSON
        p_json = os.path.join(self.temp_dir, "facade.json")
        c_json = exporter.export(format="json", output_path=p_json)
        self.assertEqual(c_json, 2)

        # CSV flat
        p_flat = os.path.join(self.temp_dir, "facade_flat.csv")
        c_flat = exporter.export(format="csv", mode="flat", output_path=p_flat)
        self.assertEqual(c_flat, 2)

        # CSV relational
        p_rel = os.path.join(self.temp_dir, "facade_rel")
        c_rel = exporter.export(format="csv", mode="relational", output_path=p_rel)
        self.assertEqual(c_rel["cases"], 2)


class TestExportTier2(unittest.TestCase):
    """Tier 2: Boundary & Corner Cases for Export Pipelines."""

    def setUp(self):
        self.db = Database(":memory:")
        self.temp_dir = tempfile.mkdtemp()

    def tearDown(self):
        self.db.close()

    def test_export_empty_database(self):
        """Verify exporting an empty database generates valid empty files without errors."""
        empty_json = os.path.join(self.temp_dir, "empty.json")
        count = export_to_json(self.db, empty_json)
        self.assertEqual(count, 0)
        with open(empty_json, "r", encoding="utf-8") as f:
            self.assertEqual(json.load(f), [])

        empty_flat = os.path.join(self.temp_dir, "empty.csv")
        count_flat = export_to_csv_flat(self.db, empty_flat)
        self.assertEqual(count_flat, 0)

    def test_exporter_unsupported_format_raises(self):
        """Verify Exporter.export raises ValueError for unsupported formats."""
        exporter = Exporter(self.db)
        with self.assertRaises(ValueError) as ctx:
            exporter.export(format="xml")
        self.assertIn("Unsupported export format", str(ctx.exception))

    def test_export_by_cnr_list_subset(self):
        """Verify exporting with cnr_list only exports specified records."""
        self.db.save_case(CaseDetail(cnr_number="CNR_A"))
        self.db.save_case(CaseDetail(cnr_number="CNR_B"))
        self.db.save_case(CaseDetail(cnr_number="CNR_C"))

        out_path = os.path.join(self.temp_dir, "subset.json")
        count = export_to_json(self.db, out_path, cnr_list=["CNR_A", "CNR_C"])
        self.assertEqual(count, 2)

        with open(out_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        cnrs = [item["cnr_number"] for item in data]
        self.assertIn("CNR_A", cnrs)
        self.assertIn("CNR_C", cnrs)
        self.assertNotIn("CNR_B", cnrs)

    def test_csv_escaping_special_characters(self):
        """Verify CSV writers escape quotes, semicolons, and commas properly."""
        tricky_case = CaseDetail(
            cnr_number="TRICKY0000000001",
            case_number="Misc,2\"Special\"/2025",
            court_name="Court; with, quotes \"and\" semicolons",
            parties=[{"type": "petitioner", "name": "Doe, John; Jr.", "advocate": "Smith & \"Wesson\""}],
        )
        self.db.save_case(tricky_case)

        flat_path = os.path.join(self.temp_dir, "tricky.csv")
        export_to_csv_flat(self.db, flat_path)

        # Re-read and verify no CSV format corruption
        with open(flat_path, "r", newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            row = next(reader)
            self.assertEqual(row["case_number"], "Misc,2\"Special\"/2025")
            self.assertEqual(row["court_name"], "Court; with, quotes \"and\" semicolons")

    def test_nested_directory_creation_auto_mkdir(self):
        """Verify export creates non-existent parent directory automatically."""
        nested_path = os.path.join(self.temp_dir, "sub1", "sub2", "nested.json")
        self.db.save_case(CaseDetail(cnr_number="TEST010000000001"))
        count = export_to_json(self.db, nested_path)
        self.assertEqual(count, 1)
        self.assertTrue(os.path.exists(nested_path))


if __name__ == "__main__":
    unittest.main()
