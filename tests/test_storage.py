"""
Unit and boundary tests for ecourts.storage.db (Database).
Covers Tier 1 (Feature Coverage) and Tier 2 (Boundary & Corner Cases)
for Normalized SQLite Storage, CNR Deduplication, Foreign Key Cascades, and Schema Integrity.
"""

from __future__ import annotations

import sqlite3
import unittest

from ecourts.parser.case_details import CaseDetail
from ecourts.storage.db import Database


class TestStorageTier1(unittest.TestCase):
    """Tier 1: Feature Coverage for Database Persistence and Schema."""

    def setUp(self):
        self.db = Database(":memory:")

    def tearDown(self):
        self.db.close()

    def test_schema_and_tables_creation(self):
        """Verify all 5 required tables exist with foreign keys enabled."""
        tables = [
            r[0]
            for r in self.db.conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        ]
        self.assertIn("cases", tables)
        self.assertIn("parties", tables)
        self.assertIn("acts", tables)
        self.assertIn("hearings", tables)
        self.assertIn("tasks", tables)

        # Check foreign keys enabled
        fk_status = self.db.conn.execute("PRAGMA foreign_keys").fetchone()[0]
        self.assertEqual(fk_status, 1)

    def test_save_case_and_retrieve_with_relations(self):
        """Verify saving a case record populates master table and child relational tables."""
        case = CaseDetail(
            cnr_number="KABC0E0001992025",
            case_type="Misc",
            case_number="Misc/2/2025",
            filing_date="15-01-2025",
            registration_date="16-01-2025",
            case_status="Pending",
            stage="Evidence",
            court_name="PRL. JUDGE FAMILY COURT",
            coram="Judge 15",
            parties=[
                {"type": "petitioner", "name": "Ram", "advocate": "A. K. Rao"},
                {"type": "respondent", "name": "Sita", "advocate": "P. N. Hegde"},
            ],
            acts=[{"act": "HMA", "sections": "13(1)"}],
            hearings=[
                {
                    "business_date": "16-01-2025",
                    "hearing_date": "10-02-2025",
                    "purpose": "Notice",
                    "judge": "Judge 15",
                }
            ],
        )
        self.assertTrue(self.db.save_case(case))

        retrieved = self.db.get_case_by_cnr("KABC0E0001992025")
        self.assertIsNotNone(retrieved)
        self.assertEqual(retrieved["cnr_number"], "KABC0E0001992025")
        self.assertEqual(retrieved["case_type"], "Misc")
        self.assertEqual(retrieved["case_stage"], "Evidence")
        self.assertEqual(len(retrieved["petitioners"]), 1)
        self.assertEqual(retrieved["petitioners"][0]["name"], "Ram")
        self.assertEqual(len(retrieved["respondents"]), 1)
        self.assertEqual(retrieved["respondents"][0]["name"], "Sita")
        self.assertEqual(len(retrieved["acts"]), 1)
        self.assertEqual(retrieved["acts"][0]["act_name"], "HMA")
        self.assertEqual(len(retrieved["hearings"]), 1)
        self.assertEqual(retrieved["hearings"][0]["purpose"], "Notice")

    def test_cnr_primary_key_deduplication_upsert(self):
        """Verify upserting an updated case with identical CNR replaces/updates without duplicating."""
        case_v1 = CaseDetail(
            cnr_number="KABC0E0001992025",
            case_number="Misc/2/2025",
            stage="Summons",
            case_status="Pending",
        )
        self.db.save_case(case_v1)
        self.assertEqual(self.db.get_cases_count(), 1)

        # Update case stage and add a hearing
        case_v2 = CaseDetail(
            cnr_number="KABC0E0001992025",
            case_number="Misc/2/2025",
            stage="Arguments",
            case_status="Pending",
            hearings=[{"business_date": "10-02-2025", "hearing_date": "24-03-2025", "purpose": "Arguments", "judge": "J1"}],
        )
        self.db.save_case(case_v2)

        # Count must remain exactly 1
        self.assertEqual(self.db.get_cases_count(), 1)
        updated = self.db.get_case_by_cnr("KABC0E0001992025")
        self.assertEqual(updated["case_stage"], "Arguments")
        self.assertEqual(len(updated["hearings"]), 1)

    def test_cascade_delete_removes_children(self):
        """Verify deleting a case removes corresponding child parties, acts, and hearings."""
        case = CaseDetail(
            cnr_number="KABC0E0001992025",
            parties=[{"type": "petitioner", "name": "Ram", "advocate": ""}],
            acts=[{"act": "CPC", "sections": "S9"}],
            hearings=[{"business_date": "01-01-2025", "hearing_date": "02-02-2025", "purpose": "Call", "judge": "J"}],
        )
        self.db.save_case(case)

        # Verify child records exist
        self.assertEqual(self.db.conn.execute("SELECT COUNT(*) FROM parties").fetchone()[0], 1)
        self.assertEqual(self.db.conn.execute("SELECT COUNT(*) FROM acts").fetchone()[0], 1)
        self.assertEqual(self.db.conn.execute("SELECT COUNT(*) FROM hearings").fetchone()[0], 1)

        # Delete case
        self.assertTrue(self.db.delete_case("KABC0E0001992025"))
        self.assertEqual(self.db.get_cases_count(), 0)

        # Children must be automatically deleted by ON DELETE CASCADE
        self.assertEqual(self.db.conn.execute("SELECT COUNT(*) FROM parties").fetchone()[0], 0)
        self.assertEqual(self.db.conn.execute("SELECT COUNT(*) FROM acts").fetchone()[0], 0)
        self.assertEqual(self.db.conn.execute("SELECT COUNT(*) FROM hearings").fetchone()[0], 0)

    def test_list_cases_with_status_filter(self):
        """Verify list_cases filters by status accurately."""
        self.db.save_case(CaseDetail(cnr_number="CNR1", case_status="Pending"))
        self.db.save_case(CaseDetail(cnr_number="CNR2", case_status="Disposed"))
        self.db.save_case(CaseDetail(cnr_number="CNR3", case_status="Pending"))

        pending = self.db.list_cases(status="Pending")
        self.assertEqual(len(pending), 2)
        cnrs = [c["cnr_number"] for c in pending]
        self.assertIn("CNR1", cnrs)
        self.assertIn("CNR3", cnrs)

        disposed = self.db.list_cases(status="Disposed")
        self.assertEqual(len(disposed), 1)
        self.assertEqual(disposed[0]["cnr_number"], "CNR2")


class TestStorageTier2(unittest.TestCase):
    """Tier 2: Boundary, Constraints, and Integrity Cases for Database Storage."""

    def setUp(self):
        self.db = Database(":memory:")

    def tearDown(self):
        self.db.close()

    def test_empty_cnr_raises_value_error(self):
        """Verify save_case raises ValueError when CNR is missing or whitespace."""
        with self.assertRaises(ValueError):
            self.db.save_case(CaseDetail(cnr_number=""))

        with self.assertRaises(ValueError):
            self.db.save_case({"case_number": "Misc/1/2025"})

    def test_foreign_key_violation_direct_insert(self):
        """Verify direct insert into child table without parent case raises IntegrityError."""
        with self.assertRaises(sqlite3.IntegrityError):
            with self.db.conn:
                self.db.conn.execute(
                    "INSERT INTO parties (cnr_number, party_type, party_seq, name, created_at) "
                    "VALUES ('NONEXISTENT_CNR', 'petitioner', 1, 'Ghost', '2026-01-01')"
                )

    def test_special_characters_in_case_and_party_names(self):
        """Verify names containing quotes, apostrophes, emojis, and newlines persist correctly."""
        weird_name = "O'Connor & Sons / Ltd. \"Quoted\" \n\t Test ⚖️"
        case = CaseDetail(
            cnr_number="TEST010000022025",
            case_number="C.C./10'2/2025",
            court_name=weird_name,
            parties=[{"type": "petitioner", "name": weird_name, "advocate": "D'Souza"}],
        )
        self.assertTrue(self.db.save_case(case))
        loaded = self.db.get_case_by_cnr("TEST010000022025")
        self.assertEqual(loaded["court_name"], weird_name)
        self.assertEqual(loaded["petitioners"][0]["name"], weird_name)
        self.assertEqual(loaded["petitioners"][0]["advocate"], "D'Souza")

    def test_get_nonexistent_case_returns_none(self):
        """Verify querying unknown CNR returns None."""
        self.assertIsNone(self.db.get_case_by_cnr("DOESNOTEXIST1234"))

    def test_idempotent_multiple_saves(self):
        """Verify saving the identical object multiple times does not corrupt or duplicate data."""
        case = CaseDetail(
            cnr_number="SAME010000012025",
            parties=[{"type": "petitioner", "name": "Alice"}],
            acts=[{"act": "Act A", "sections": "Sec 1"}],
        )
        for _ in range(5):
            self.db.save_case(case)

        self.assertEqual(self.db.get_cases_count(), 1)
        item = self.db.get_case_by_cnr("SAME010000012025")
        self.assertEqual(len(item["parties"]), 1)
        self.assertEqual(len(item["acts"]), 1)


if __name__ == "__main__":
    unittest.main()
