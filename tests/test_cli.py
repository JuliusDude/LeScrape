"""
Unit and boundary tests for ecourts.cli.
Covers Tier 1 (Feature Coverage) and Tier 2 (Boundary & Corner Cases)
for CLI Commands (explore, enqueue, run, export, status) and argument handling.
"""

from __future__ import annotations

import io
import json
import os
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from ecourts.cli import build_cli_parser, main
from ecourts.parser.case_details import CaseDetail
from ecourts.storage.db import Database


class TestCliTier1(unittest.TestCase):
    """Tier 1: Feature Coverage for CLI Commands."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.temp_dir, "cli_test.db")
        self.db = Database(self.db_path)

        # Seed sample case
        self.db.save_case(CaseDetail(cnr_number="KABC0E0001992025", case_status="Pending"))

    def tearDown(self):
        self.db.close()

    def test_cli_parser_registered_commands(self):
        """Verify CLI argument parser registers all 5 core commands."""
        parser = build_cli_parser()
        subparsers_action = [
            action for action in parser._actions if action.dest == "command"
        ][0]
        self.assertIn("explore", subparsers_action.choices)
        self.assertIn("enqueue", subparsers_action.choices)
        self.assertIn("run", subparsers_action.choices)
        self.assertIn("export", subparsers_action.choices)
        self.assertIn("status", subparsers_action.choices)

    def test_cmd_status_displays_counts(self):
        """Verify 'status' command outputs case counts and queue stats."""
        captured = io.StringIO()
        with patch("sys.stdout", captured):
            code = main(["status", "--db", self.db_path])

        self.assertEqual(code, 0)
        output = captured.getvalue()
        self.assertIn("Total Cases:     1", output)
        self.assertIn("Pending:     1", output)
        self.assertIn("Queue Tasks:", output)

    def test_cmd_enqueue_cnr(self):
        """Verify 'enqueue --cnr' enqueues cnr_search task into database."""
        captured = io.StringIO()
        with patch("sys.stdout", captured):
            code = main(["enqueue", "--db", self.db_path, "--cnr", "MHAA010000012025"])

        self.assertEqual(code, 0)
        self.assertIn("Successfully enqueued CNR lookup task", captured.getvalue())

        stats = self.db.get_queue_stats()
        self.assertEqual(stats["queued"], 1)

    def test_cmd_enqueue_case_type(self):
        """Verify 'enqueue' with full search parameters adds case_type_search task."""
        captured = io.StringIO()
        with patch("sys.stdout", captured):
            code = main([
                "enqueue",
                "--db", self.db_path,
                "--state", "3",
                "--district", "20",
                "--complex", "1030135@3@Y",
                "--establishment", "3",
                "--case-type", "12^3",
                "--year", "2025",
            ])

        self.assertEqual(code, 0)
        self.assertIn("Successfully enqueued case_type_search task", captured.getvalue())

        stats = self.db.get_queue_stats()
        self.assertEqual(stats["queued"], 1)

    def test_cmd_export_json_and_csv(self):
        """Verify 'export' command generates JSON and CSV files."""
        json_out = os.path.join(self.temp_dir, "cli_export.json")
        csv_out = os.path.join(self.temp_dir, "cli_export.csv")

        # Export JSON
        c1 = main(["export", "--db", self.db_path, "--format", "json", "--output", json_out])
        self.assertEqual(c1, 0)
        self.assertTrue(os.path.exists(json_out))

        # Export CSV
        c2 = main(["export", "--db", self.db_path, "--format", "csv", "--output", csv_out])
        self.assertEqual(c2, 0)
        self.assertTrue(os.path.exists(csv_out))

    def test_cmd_explore_state_districts(self):
        """Verify 'explore --state 3 --format json' parses and prints districts."""
        mock_resp = MagicMock()
        mock_resp.text = json.dumps({"dist_list": "<option value='20'>BENGALURU</option>"})

        with patch("ecourts.cli._get_session") as mock_get_sess:
            mock_sess = MagicMock()
            mock_sess.post.return_value = mock_resp
            mock_get_sess.return_value = mock_sess

            captured = io.StringIO()
            with patch("sys.stdout", captured):
                code = main(["explore", "--state", "3", "--format", "json"])

            self.assertEqual(code, 0)
            data = json.loads(captured.getvalue())
            self.assertEqual(len(data), 1)
            self.assertEqual(data[0]["name"], "BENGALURU")


class TestCliTier2(unittest.TestCase):
    """Tier 2: Boundary, Missing Arguments, and Error Handling Cases for CLI."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.temp_dir, "cli_boundary.db")

    def test_no_command_prints_help(self):
        """Verify executing CLI without command prints help and returns 0."""
        captured = io.StringIO()
        with patch("sys.stdout", captured):
            code = main([])
        self.assertEqual(code, 0)
        self.assertIn("usage:", captured.getvalue().lower())

    def test_enqueue_missing_required_parameters_fails(self):
        """Verify enqueue without required parameters returns code 1."""
        captured = io.StringIO()
        with patch("sys.stdout", captured):
            code = main(["enqueue", "--state", "3"])  # Missing district, complex, etc.
        self.assertEqual(code, 1)
        self.assertIn("Missing required arguments", captured.getvalue())

    def test_explore_missing_state_fails(self):
        """Verify explore without --state returns code 1."""
        captured = io.StringIO()
        with patch("sys.stdout", captured):
            code = main(["explore"])
        self.assertEqual(code, 1)
        self.assertIn("--state", captured.getvalue())

    def test_invalid_subcommand_raises_system_exit(self):
        """Verify unrecognized command exits with error."""
        with patch("sys.stderr", io.StringIO()):
            with self.assertRaises(SystemExit):
                main(["nonexistent_command"])

    def test_cmd_run_empty_queue_once(self):
        """Verify 'run --once' on empty queue terminates with 0 tasks processed."""
        db = Database(self.db_path)
        db.close()

        captured = io.StringIO()
        with patch("sys.stdout", captured):
            code = main(["run", "--db", self.db_path, "--once", "--captcha", "mock"])
        self.assertEqual(code, 0)
        self.assertIn("Processed 0 task(s)", captured.getvalue())


if __name__ == "__main__":
    unittest.main()
