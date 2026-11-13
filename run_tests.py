#!/usr/bin/env python3
"""
run_tests.py
============
Standalone offline test runner for eCourts India scraper test suite.
Uses Python standard library unittest with socket-level network isolation
guaranteeing 100% offline self-containment without third-party test runners.
"""

from __future__ import annotations

import argparse
import os
import socket
import sys
import time
import unittest
from typing import Optional


def install_socket_guard() -> None:
    """Monkeypatches socket connection methods to strictly forbid outbound

    network calls during test execution.
    """
    orig_connect = socket.socket.connect

    def blocked_connect(self, address):
        raise RuntimeError(
            f"Network access strictly forbidden in offline test mode! Attempted connect to {address}"
        )

    def blocked_create_connection(*args, **kwargs):
        raise RuntimeError(
            "Network access strictly forbidden in offline test mode! Attempted socket.create_connection"
        )

    socket.socket.connect = blocked_connect
    socket.create_connection = blocked_create_connection


def main() -> int:
    parser = argparse.ArgumentParser(description="Run eCourts offline test suite")
    parser.add_argument(
        "-v", "--verbose", action="store_true", default=True, help="Verbose test execution output"
    )
    parser.add_argument(
        "-q", "--quiet", action="store_true", help="Quiet test execution output"
    )
    parser.add_argument(
        "-p", "--pattern", default="test_*.py", help="Pattern to match test files (default: test_*.py)"
    )
    parser.add_argument(
        "-s", "--start-dir", default="tests", help="Directory to start test discovery (default: tests)"
    )
    parser.add_argument(
        "--allow-network", action="store_true", help="Disable strict offline socket blocking guard"
    )
    args = parser.parse_args()

    project_root = os.path.dirname(os.path.abspath(__file__))
    if project_root not in sys.path:
        sys.path.insert(0, project_root)

    print("=" * 75)
    print(" eCourts India Production Scraper - Offline Test Suite Runner")
    print("=" * 75)

    if not args.allow_network:
        install_socket_guard()
        print("[+] Socket-level network isolation: ACTIVE (Outbound requests blocked)")
    else:
        print("[!] Socket-level network isolation: DISABLED")

    verbosity = 1
    if args.quiet:
        verbosity = 0
    elif args.verbose:
        verbosity = 2

    start_dir = os.path.join(project_root, args.start_dir)
    print(f"[*] Discovering tests in: {start_dir} (pattern: {args.pattern})")

    loader = unittest.TestLoader()
    suite = loader.discover(start_dir=start_dir, pattern=args.pattern, top_level_dir=project_root)

    # Default run also covers the V1 and V2 packages (top-level dir = repo root).
    if args.start_dir == "tests":
        deeper_dir = os.path.join(project_root, "V1")
        if os.path.isdir(deeper_dir):
            print(f"[*] Discovering tests in: {deeper_dir} (pattern: {args.pattern})")
            suite.addTests(
                loader.discover(start_dir=deeper_dir, pattern=args.pattern, top_level_dir=project_root)
            )
        v2_dir = os.path.join(project_root, "V2", "tests")
        if os.path.isdir(v2_dir):
            print(f"[*] Discovering tests in: {v2_dir} (pattern: {args.pattern})")
            suite.addTests(
                loader.discover(start_dir=v2_dir, pattern=args.pattern, top_level_dir=project_root)
            )

    total_tests = suite.countTestCases()
    print(f"[*] Discovered {total_tests} test cases across test modules.")
    print("-" * 75)

    start_time = time.time()
    runner = unittest.TextTestRunner(verbosity=verbosity)
    result = runner.run(suite)
    duration = time.time() - start_time

    print("=" * 75)
    print(" TEST EXECUTION SUMMARY")
    print("=" * 75)
    print(f" Total Tests Run : {result.testsRun}")
    print(f" Successful      : {result.testsRun - len(result.failures) - len(result.errors) - len(result.skipped)}")
    print(f" Failures        : {len(result.failures)}")
    print(f" Errors          : {len(result.errors)}")
    print(f" Skipped         : {len(result.skipped)}")
    print(f" Elapsed Time    : {duration:.2f} seconds")
    print("=" * 75)

    if result.wasSuccessful():
        print(" [PASSED] ALL TEST CASES COMPLETED SUCCESSFULLY (100% OFFLINE)")
        print("=" * 75)
        return 0
    else:
        print(" [FAILED] SOME TEST CASES FAILED OR ERRORED")
        print("=" * 75)
        return 1


if __name__ == "__main__":
    sys.exit(main())
