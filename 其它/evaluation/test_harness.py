"""Scorer checks use separate toy examples, never product implementation."""

import argparse
from contextlib import redirect_stdout
import io
import json
from pathlib import Path
import tempfile
import unittest

import run_benchmark as harness


class HarnessTests(unittest.TestCase):
    def test_frozen_corpus_integrity_and_size(self) -> None:
        commitment = harness.verify()
        self.assertGreaterEqual(commitment["total_cases"], 20)
        corpus = harness.read_json(harness.HERE / "sealed" / "corpus.json")["cases"]
        self.assertEqual(len(corpus), commitment["total_cases"])
        self.assertEqual(len({row["id"] for row in corpus}), len(corpus))

    def test_normalize_removes_identity_metadata(self) -> None:
        result = harness.normalize({"path": "a.py", "source": "secret-engine", "rule_id": "engine-id"})
        self.assertNotIn("source", result)
        self.assertNotIn("rule_id", result)

    def test_baseline_parses_but_does_not_execute(self) -> None:
        result = harness.pattern_baseline({"a.py": "raise RuntimeError('must never execute')\n"})
        self.assertEqual(result, [])

    def test_balanced_scoring_and_adjudication_coverage(self) -> None:
        original_here = harness.HERE
        try:
            with tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                harness.HERE = root
                harness.write_json(root / "COMMITMENT.json", {"hashes": {}})
                harness.write_json(root / "sealed" / "oracle.json", {"expectations": {
                    "one": [{"id": "d1", "concept": "toy", "path": "a.py", "line": 2, "pattern": "toy"},
                            {"id": "d2", "concept": "other", "path": "a.py", "line": 10, "pattern": "other"}]}})
                run = root / "run"
                valid = {"item_id": "i1", "path": "a.py", "start_line": 2, "end_line": 2, "title": "toy"}
                spurious = {"item_id": "i2", "path": "a.py", "start_line": 8, "end_line": 8, "title": "unrelated"}
                harness.write_json(run / "anonymous_packet.json", {"cases": [
                    {"case_id": "one", "candidates": {"Candidate-A": [valid, spurious], "Candidate-B": []}}]})
                harness.write_json(run / "RUN_COMMITMENT.json", {
                    "state": "frozen", "packet_sha256": harness.sha(run / "anonymous_packet.json")})
                harness.write_json(run / "sealed" / "probes.json", {"results": []})
                harness.write_json(run / "sealed" / "errors.json", {"errors": []})
                args = argparse.Namespace(output=str(run), adjudications=None, reveal=False)
                with redirect_stdout(io.StringIO()):
                    harness.score(args)
                scores = harness.read_json(run / "SCORES_ANONYMOUS.json")["scores"]["Candidate-A"]
                self.assertEqual((scores["tp"], scores["fp"], scores["fn"]), (1, 1, 1))
                self.assertEqual(scores["f1"], 0.5)
                adjudications = run / "adjudications.json"
                harness.write_json(adjudications, {"judgments": [{"item_id": "i1", "verdict": "valid"}]})
                args.adjudications = str(adjudications)
                with self.assertRaisesRegex(ValueError, "cover every"):
                    harness.score(args)
        finally:
            harness.HERE = original_here


if __name__ == "__main__":
    unittest.main()
