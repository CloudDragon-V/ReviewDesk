"""Replay accepted v1 findings through current merge_findings, with no network calls."""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import datetime, timezone
import importlib
import json
from pathlib import Path
from unittest.mock import patch

import run_benchmark as harness


def fingerprint(value: object) -> str:
    """Compare all fields preserved by the original anonymous collector."""
    return json.dumps(harness.normalize(value), sort_keys=True, ensure_ascii=False)


def verify_results(root: Path) -> None:
    commitment = harness.read_json(root / "RESULT_COMMITMENT.json")
    for name, digest in commitment["sha256"].items():
        if harness.sha(root / name) != digest:
            raise ValueError(f"Original result changed: {root.name}/{name}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    harness.verify()
    original = harness.HERE / "runs" / "online_v1"
    offline = harness.HERE / "runs" / "offline_v1"
    verify_results(original)
    verify_results(offline)
    original_run = harness.read_json(original / "RUN_COMMITMENT.json")
    # Preserve the original static capability; replay is solely a merge change.
    for filename in ("analysis.py", "context.py"):
        historical_name = f"src/reviewdesk/{filename}"
        current_path = harness.BACKEND / "reviewdesk" / filename
        if harness.sha(current_path) != original_run["candidate_hashes"][historical_name]:
            raise ValueError(f"Static analysis changed since v1: {historical_name}")
    before = harness.candidate_hashes()
    output = Path(args.output).resolve()
    if output.exists():
        raise ValueError("Replay output exists; preserve it and select a new directory.")
    output.mkdir(parents=True)
    mapping = harness.read_json(original / "sealed" / "mapping.json")["mapping"]
    local_label = next(label for label, identity in mapping.items() if identity == "reviewdesk_local")
    hybrid_label = next(label for label, identity in mapping.items() if identity == "reviewdesk_hybrid_deepseek")
    harness.write_json(output / "sealed" / "mapping.json", {"mapping": mapping, "candidate_hashes": before,
                        "note": "Original revealed labels reused; no new blinding."})
    packet = harness.read_json(original / "anonymous_packet.json")
    original_judgments = harness.read_json(original / "adjudication_completed.json")["judgments"]
    details = []
    # Any attempted network connection fails locally. No credentials are read.
    with patch("socket.create_connection", side_effect=AssertionError("Replay must remain offline")), \
         patch("socket.socket.connect", side_effect=AssertionError("Replay must remain offline")):
        analysis_module = importlib.import_module("reviewdesk.analysis")
        service = importlib.import_module("reviewdesk.service")
        models = importlib.import_module("reviewdesk.models")
        for case in packet["cases"]:
            source_files = [models.SourceFile(path=path, content=code) for path, code in case["files"].items()]
            local = analysis_module.analyze_sources(source_files).findings
            saved_local = case["candidates"][local_label]
            if Counter(map(fingerprint, local)) != Counter(map(fingerprint, saved_local)):
                raise ValueError(f"Current local findings differ from saved baseline: {case['case_id']}")
            original_hybrid = case["candidates"][hybrid_label]
            local_to_match = Counter(map(fingerprint, local))
            saved_ai = []
            for finding in original_hybrid:
                key = fingerprint(finding)
                if local_to_match[key]:
                    local_to_match[key] -= 1
                else:
                    saved_ai.append(models.Finding(**harness.normalize(finding), source="api"))
            if any(local_to_match.values()):
                raise ValueError(f"Original hybrid report omitted an original local finding: {case['case_id']}")
            merged, duplicates = service.merge_findings(local, saved_ai)
            available_ids: dict[str, list[str]] = defaultdict(list)
            for finding in original_hybrid:
                available_ids[fingerprint(finding)].append(finding["item_id"])
            replayed = []
            for finding in merged:
                key = fingerprint(finding)
                if not available_ids[key]:
                    raise ValueError("Replay created or changed finding content; prior judgment cannot be reused.")
                value = harness.normalize(finding)
                value["item_id"] = available_ids[key].pop(0)
                replayed.append(value)
            removed = sorted(item_id for ids in available_ids.values() for item_id in ids)
            if duplicates != len(removed):
                raise ValueError("Merge duplicate count does not match retained finding IDs.")
            details.append({"case_id": case["case_id"], "original_local_findings": len(local),
                            "original_hybrid_findings": len(original_hybrid),
                            "retained_model_findings_available_for_replay": len(saved_ai),
                            "replayed_hybrid_findings": len(replayed), "merged_duplicates": duplicates,
                            "removed_item_ids": removed})
            case["candidates"][hybrid_label] = replayed
    if before != harness.candidate_hashes():
        raise ValueError("Candidate source changed during replay; discard this run.")
    packet["evaluation_kind"] = "known_sample_saved_finding_replay"
    packet["instructions"] = "Not a new blind evaluation. Original independent judgments are reused unchanged for retained original item IDs."
    harness.write_json(output / "anonymous_packet.json", packet)
    retained_ids = {finding["item_id"] for case in packet["cases"]
                    for findings in case["candidates"].values() for finding in findings}
    reused_judgments = [row for row in original_judgments if row["item_id"] in retained_ids]
    harness.write_json(output / "adjudication_completed.json", {"judgments": reused_judgments,
                        "provenance": "Unchanged subset of online_v1 independent judgments; no new adjudication."})
    harness.write_json(output / "ADJUDICATION_COMMITMENT.json", {
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "adjudication_sha256": harness.sha(output / "adjudication_completed.json"),
        "original_adjudication_sha256": harness.sha(original / "adjudication_completed.json"),
        "method": "Exact original item IDs and judgments retained after product merge."})
    harness.write_json(output / "sealed" / "probes.json", {"results": []})
    harness.write_json(output / "sealed" / "errors.json", {"errors": []})
    harness.write_json(output / "sealed" / "usage.json", {"usage": []})
    harness.write_json(output / "USAGE_REFERENCE.json", {
        "new_api_requests": 0, "new_api_usage_records": 0, "new_estimated_cost_cny": "0",
        "historical_usage_file": "../online_v1/sealed/usage.json",
        "historical_usage_sha256": harness.sha(original / "sealed" / "usage.json"),
        "historical_summary": harness.read_json(harness.HERE / "online_evidence" / "usage_summary_v1.json"),
        "note": "All token and cost values in historical_summary belong to the original online_v1 run; none are new consumption."})
    harness.write_json(output / "REPLAY_DETAILS.json", {
        "evaluation_kind": "Known-sample replay, not a new blind evaluation",
        "original_packet_sha256": harness.sha(original / "anonymous_packet.json"),
        "replay_script_sha256": harness.sha(Path(__file__)),
        "source_limit": "The original collector saved accepted combined findings, not raw HTTP/model envelopes. Already discarded findings cannot be reconstructed.",
        "restoration_method": "Reanalyze unchanged static source; require exact equality with saved local findings to restore rule_id; remaining saved hybrid findings are retained model candidates.",
        "new_network_connections": 0, "cases": details})
    harness.write_json(output / "RUN_COMMITMENT.json", {
        "schema": 1, "created_utc": datetime.now(timezone.utc).isoformat(), "state": "frozen",
        "mode": "known_sample_replay", "case_count": len(packet["cases"]), "probe_count": 0, "error_count": 0,
        "candidate_hashes": before, "harness_sha256": harness.sha(harness.HERE / "run_benchmark.py"),
        "packet_sha256": harness.sha(output / "anonymous_packet.json"),
        "mapping_sha256": harness.sha(output / "sealed" / "mapping.json"),
        "corpus_commitment_sha256": harness.sha(harness.HERE / "COMMITMENT.json")})
    for reveal in (False, True):
        harness.score(argparse.Namespace(output=str(output), adjudications=str(output / "adjudication_completed.json"), reveal=reveal))
    files = ["RUN_COMMITMENT.json", "ADJUDICATION_COMMITMENT.json", "anonymous_packet.json",
             "adjudication_completed.json", "SCORES_ANONYMOUS.json", "SCORES_REVEALED.json",
             "REPLAY_DETAILS.json", "USAGE_REFERENCE.json"]
    harness.write_json(output / "RESULT_COMMITMENT.json", {
        "schema": 1, "created_utc": datetime.now(timezone.utc).isoformat(), "status": "Known-sample replay; preserve unchanged",
        "sha256": {name: harness.sha(output / name) for name in files}})
    verify_results(original)
    verify_results(offline)
    print(json.dumps({"replay_complete": True, "new_api_requests": 0,
                      "merged_duplicates": sum(row["merged_duplicates"] for row in details)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
