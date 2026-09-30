"""Frozen synthetic evaluation with anonymized results and optional independent grading."""

from __future__ import annotations

import argparse
import ast
import hashlib
import importlib
import json
from pathlib import Path
import random
import re
import sys
import tempfile
from datetime import datetime, timezone

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
BACKEND = ROOT / "后端功能"
FRONTEND = ROOT / "前端功能"
sys.path.insert(0, str(BACKEND))


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def verify() -> dict:
    commitment = read_json(HERE / "COMMITMENT.json")
    for name, digest in commitment["hashes"].items():
        if sha(HERE / name) != digest:
            raise ValueError(f"Commitment mismatch: {name}")
    return commitment


def candidate_hashes() -> dict[str, str]:
    """Record current source paths without rewriting historical commitments."""
    candidates = [*(BACKEND / "reviewdesk").glob("*.py"),
                  *(FRONTEND / "reviewdesk_ui").glob("*.py")]
    return {str(path.relative_to(ROOT)).replace("\\", "/"): sha(path)
            for path in sorted(candidates)}


def normalize(finding: object) -> dict:
    """Remove engine-identifying metadata; preserve exactly the review content."""
    value = finding if isinstance(finding, dict) else vars(finding)
    return {key: value.get(key, "") for key in
            ("path", "start_line", "end_line", "category", "severity", "title", "evidence", "recommendation")}


def pattern_baseline(files: dict[str, str]) -> list[dict]:
    """Transparent cheap comparator: compile plus context-free keyword checks."""
    found: list[dict] = []
    patterns = [
        (r"\beval\s*\(", "Dynamic eval call", "Check whether the input is trusted."),
        (r"\bexec\s*\(", "Dynamic exec call", "Avoid executing external text."),
        (r"except\s*:", "Bare exception handler", "Catch the intended exception type."),
        (r"shell\s*=\s*True", "Shell invocation", "Check command interpolation."),
        (r"\bdef\s+\w+.*=\s*\[\]", "Mutable default argument", "Allocate mutable defaults per call."),
    ]
    for path, code in files.items():
        try:
            ast.parse(code, filename=path)
        except SyntaxError as exc:
            found.append({"path": path, "start_line": exc.lineno or 1, "end_line": exc.lineno or 1,
                          "category": "bug", "severity": "high", "title": "Python syntax error",
                          "evidence": exc.msg, "recommendation": "Correct the syntax."})
            continue
        for number, line in enumerate(code.splitlines(), 1):
            for pattern, title, recommendation in patterns:
                if re.search(pattern, line):
                    found.append({"path": path, "start_line": number, "end_line": number,
                                  "category": "bug", "severity": "medium", "title": title,
                                  "evidence": line.strip(), "recommendation": recommendation})
    return found


def run_probe(case: dict, analysis_module: object, context_module: object, source_type: type) -> dict:
    probe = case["probe"]
    files = [source_type(path=path, content=code) for path, code in case["files"].items()]
    if probe["type"] == "scan_exclusions":
        with tempfile.TemporaryDirectory(prefix="reviewdesk-blind-") as directory:
            root = Path(directory)
            for path, content in case["files"].items():
                target = root / path
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(content, encoding="utf-8")
            analysis = analysis_module.scan_project(root)
            observed = sorted(file.path.replace("\\", "/") for file in analysis.files)
            return {"pass": observed == sorted(probe["expected_paths"]), "observed_paths": observed}
    analysis = analysis_module.analyze_sources(files)
    if probe["type"] == "cross_file_graph":
        paths = {node.id: node.path for node in analysis.nodes}
        edges = [edge for edge in analysis.edges
                 if paths.get(edge.source) and paths.get(edge.target)
                 and paths[edge.source] != paths[edge.target]]
        return {"pass": bool(edges), "cross_file_edges": len(edges),
                "edge_kinds": sorted({edge.kind for edge in edges})}
    budget = probe.get("budget", 1200)
    context = context_module.build_context(analysis, budget=budget, strategy="dependency")
    if probe["type"] == "redaction":
        return {"pass": probe["needle"] not in context.text and bool(context.included_spans),
                "raw_needle_present": probe["needle"] in context.text,
                "source_included": bool(context.included_spans)}
    measured = context_module.estimate_tokens(context.text)
    passed = context.estimated_tokens <= budget and measured <= budget
    spans_valid = True
    for path, spans in context.included_spans.items():
        source = case["files"].get(path)
        for start, end in spans:
            spans_valid &= source is not None and 1 <= start <= end <= len(source.splitlines())
    return {"pass": bool(passed and spans_valid), "reported_tokens": context.estimated_tokens,
            "recomputed_tokens": measured, "budget": budget, "spans_valid": bool(spans_valid)}


def collect(args: argparse.Namespace) -> None:
    commitment = verify()
    output = Path(args.output).resolve()
    if output.exists():
        raise ValueError("Run directory already exists; use a new directory to preserve prior evidence.")
    output.mkdir(parents=True)
    identities = ["reviewdesk_local", "keyword_compile_baseline"]
    online_key = ""
    online_plan = None
    if args.online:
        online_plan = read_json(HERE / "ONLINE_PROTOCOL.json")
        online_commitment = read_json(HERE / "ONLINE_COMMITMENT.json")
        if sha(HERE / "ONLINE_PROTOCOL.json") != online_commitment["protocol_sha256"]:
            raise ValueError("Online protocol commitment mismatch.")
        credentials = importlib.import_module("reviewdesk.storage").CredentialStore()
        online_key = credentials.get_key()
        if not online_key:
            raise ValueError("Online mode requires a key in Windows Credential Manager.")
        identities = ["reviewdesk_local", "reviewdesk_hybrid_deepseek"]
    random.SystemRandom().shuffle(identities)
    mapping = dict(zip(("Candidate-A", "Candidate-B"), identities))
    before = candidate_hashes()
    write_json(output / "sealed" / "mapping.json", {"mapping": mapping, "candidate_hashes": before})
    write_json(output / "RUN_COMMITMENT.json", {
        "created_utc": datetime.now(timezone.utc).isoformat(), "mode": "online" if args.online else "offline",
        "corpus_commitment_sha256": sha(HERE / "COMMITMENT.json"),
        "mapping_sha256": sha(output / "sealed" / "mapping.json"),
        "harness_sha256": sha(Path(__file__)), "candidate_hashes": before,
        "online_protocol_sha256": sha(HERE / "ONLINE_PROTOCOL.json") if args.online else None,
        "online_per_call_cap_cny": "0.05" if args.online else "0", "state": "collecting"})
    analysis_module = importlib.import_module("reviewdesk.analysis")
    context_module = importlib.import_module("reviewdesk.context")
    SourceFile = importlib.import_module("reviewdesk.models").SourceFile
    service = importlib.import_module("reviewdesk.service").ReviewService() if args.online else None
    packet: dict = {"schema": 1, "instructions": "Judge whether each reported issue is valid in the supplied synthetic source. Do not infer candidate identity. Preserve item_id. Use valid, invalid or uncertain; give a concise reason.", "cases": []}
    probes, errors, usage = [], [], []
    cases = read_json(HERE / "sealed" / "corpus.json")["cases"]
    for case in cases:
        if args.online and case["id"] not in online_plan["selected_case_ids"]:
            continue
        if case["kind"] == "probe":
            try:
                probes.append({"case_id": case["id"], **run_probe(case, analysis_module, context_module, SourceFile)})
            except Exception as exc:
                probes.append({"case_id": case["id"], "pass": False, "error_type": type(exc).__name__})
            continue
        row = {"case_id": case["id"], "files": case["files"], "candidates": {}}
        for label, identity in mapping.items():
            try:
                if identity == "keyword_compile_baseline":
                    findings = pattern_baseline(case["files"])
                else:
                    sources = [SourceFile(path=path, content=code) for path, code in case["files"].items()]
                    analysis = analysis_module.analyze_sources(sources)
                    if identity == "reviewdesk_hybrid_deepseek":
                        result = service.review(analysis, case["id"], use_api=True,
                                                api_key=online_key,
                                                budget=1200, strategy="dependency", max_cost_cny="0.05")
                        # Combined product findings are evaluated; actual usage remains sealed until reveal.
                        findings = result.findings
                        usage.append({"case_id": case["id"], "candidate": label, "usage": vars(result.usage)})
                    else:
                        findings = analysis.findings
                clean = [normalize(finding) for finding in findings]
                random.SystemRandom().shuffle(clean)
                for index, finding in enumerate(clean, 1):
                    finding["item_id"] = f"{case['id']}-{label[-1]}-{index:03d}"
                row["candidates"][label] = clean
            except Exception as exc:
                row["candidates"][label] = []
                errors.append({"case_id": case["id"], "candidate": label, "error_type": type(exc).__name__})
        packet["cases"].append(row)
    after = candidate_hashes()
    if before != after:
        raise ValueError("Candidate source changed during collection. Freeze source and rerun in a new directory.")
    write_json(output / "anonymous_packet.json", packet)
    write_json(output / "sealed" / "probes.json", {"results": probes})
    write_json(output / "sealed" / "errors.json", {"errors": errors})
    write_json(output / "sealed" / "usage.json", {"usage": usage})
    write_json(output / "adjudication_template.json", {"judgments": [
        {"item_id": finding["item_id"], "verdict": "uncertain", "reason": ""}
        for case in packet["cases"] for findings in case["candidates"].values() for finding in findings]})
    run = read_json(output / "RUN_COMMITMENT.json")
    run.update({"state": "frozen", "packet_sha256": sha(output / "anonymous_packet.json"),
                "case_count": len(packet["cases"]), "probe_count": len(probes), "error_count": len(errors)})
    write_json(output / "RUN_COMMITMENT.json", run)
    print(json.dumps({"state": "frozen", "anonymous_packet": str(output / "anonymous_packet.json"),
                      "case_count": len(packet["cases"]), "probe_count": len(probes), "errors": len(errors)}, ensure_ascii=False))


def score(args: argparse.Namespace) -> None:
    verify()
    output = Path(args.output).resolve()
    run = read_json(output / "RUN_COMMITMENT.json")
    if run["state"] != "frozen" or sha(output / "anonymous_packet.json") != run["packet_sha256"]:
        raise ValueError("Run packet not frozen or changed since collection.")
    packet = read_json(output / "anonymous_packet.json")
    oracle = read_json(HERE / "sealed" / "oracle.json")["expectations"]
    judgments = {}
    if args.adjudications:
        for row in read_json(Path(args.adjudications))["judgments"]:
            if row["verdict"] not in ("valid", "invalid", "uncertain"):
                raise ValueError("Invalid adjudication verdict")
            if row["item_id"] in judgments:
                raise ValueError("Duplicate adjudication item_id")
            judgments[row["item_id"]] = row
        observed_ids = {finding["item_id"] for case in packet["cases"]
                        for findings in case["candidates"].values() for finding in findings}
        if set(judgments) != observed_ids:
            raise ValueError("Adjudications must cover every packet item exactly once, with no unknown IDs.")
    scores = {label: {"tp": 0, "fp": 0, "fn": 0, "valid_extra": 0,
                      "uncertain": 0, "predictions": 0, "cases": []}
              for label in ("Candidate-A", "Candidate-B")}
    for case in packet["cases"]:
        expected = oracle[case["case_id"]]
        for label, findings in case["candidates"].items():
            score_row = scores[label]
            consumed: set[str] = set()
            case_tp, case_fp, extras, uncertain = 0, 0, 0, 0
            for finding in findings:
                judgment = judgments.get(finding["item_id"], {})
                verdict = judgment.get("verdict", "unreviewed")
                text = " ".join(str(finding.get(key, "")) for key in ("title", "evidence", "recommendation"))
                all_matches = [item for item in expected if item["path"] == finding["path"]
                           and max(1, int(finding["start_line"]) - 1) <= item["line"] <= int(finding["end_line"]) + 1
                           and re.search(item["pattern"], text, flags=re.I)]
                matches = [item for item in all_matches if item["id"] not in consumed]
                if verdict == "invalid":
                    case_fp += 1
                elif matches:
                    consumed.add(matches[0]["id"])
                    case_tp += 1
                elif all_matches:
                    # Repeating one oracle issue is not an additional valid issue.
                    case_fp += 1
                elif verdict == "valid":
                    extras += 1
                else:
                    case_fp += 1
                    if verdict == "uncertain":
                        uncertain += 1
            missed = len(expected) - len(consumed)
            score_row["tp"] += case_tp
            score_row["fp"] += case_fp
            score_row["fn"] += missed
            score_row["valid_extra"] += extras
            score_row["uncertain"] += uncertain
            score_row["predictions"] += len(findings)
            score_row["cases"].append({"case_id": case["case_id"], "tp": case_tp, "fp": case_fp,
                                       "fn": missed, "valid_extra": extras,
                                       "missed_concepts": [item["concept"] for item in expected if item["id"] not in consumed]})
    for row in scores.values():
        valid = row["tp"] + row["valid_extra"]
        row["precision"] = valid / (valid + row["fp"]) if valid + row["fp"] else 0.0
        row["recall"] = row["tp"] / (row["tp"] + row["fn"]) if row["tp"] + row["fn"] else 0.0
        p, r = row["precision"], row["recall"]
        row["f1"] = 2 * p * r / (p + r) if p + r else 0.0
        row["score_100"] = round(row["f1"] * 100, 2)
    probes = read_json(output / "sealed" / "probes.json")["results"]
    report = {"schema": 1, "independent_adjudication": bool(args.adjudications),
              "grading_note": "Location plus frozen concept-pattern matching. Independent valid extras count toward precision but do not alter frozen recall targets. Uncertain unmatched findings count conservatively as FP. This is a small synthetic evaluation, not a population estimate.",
              "scores": scores, "probe_passed": sum(bool(row["pass"]) for row in probes),
              "probe_total": len(probes), "probes": probes,
              "errors": read_json(output / "sealed" / "errors.json")["errors"]}
    if args.reveal:
        mapping_path = output / "sealed" / "mapping.json"
        if sha(mapping_path) != run["mapping_sha256"]:
            raise ValueError("Identity mapping changed.")
        report["identities"] = read_json(mapping_path)["mapping"]
        report["usage"] = read_json(output / "sealed" / "usage.json")["usage"]
    filename = "SCORES_REVEALED.json" if args.reveal else "SCORES_ANONYMOUS.json"
    write_json(output / filename, report)
    print(json.dumps({"report": str(output / filename), "scores": {
        label: {key: row[key] for key in ("tp", "fp", "fn", "precision", "recall", "f1", "score_100")}
        for label, row in scores.items()}, "probe_passed": report["probe_passed"], "probe_total": len(probes)}, ensure_ascii=False))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("verify")
    collector = commands.add_parser("collect")
    collector.add_argument("--output", required=True)
    collector.add_argument("--online", action="store_true", help="Paid, explicit opt-in; synthetic cases only.")
    scorer = commands.add_parser("score")
    scorer.add_argument("--output", required=True)
    scorer.add_argument("--adjudications")
    scorer.add_argument("--reveal", action="store_true")
    args = parser.parse_args()
    if args.command == "verify":
        commitment = verify()
        print(json.dumps({"verified": True, "total_cases": commitment["total_cases"],
                          "commitment_sha256": sha(HERE / "COMMITMENT.json")}))
    elif args.command == "collect":
        collect(args)
    else:
        score(args)


if __name__ == "__main__":
    main()
