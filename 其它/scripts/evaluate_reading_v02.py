"""Freeze anonymous reading outputs without loading the sealed reference answers."""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "后端功能"))
from reviewdesk.languages import get_adapter
from reviewdesk.workbench import validated_sources
from reviewdesk.manual import build_manual, count_characters
from reviewdesk.analysis import analyze_sources
from dataclasses import asdict


def main():
    base = ROOT / "其它/evaluation/v02_blind"
    cases = json.loads((base / "cases.json").read_text(encoding="utf-8"))["cases"]
    packet = base / "anonymous_outputs"
    packet.mkdir(exist_ok=True)
    outputs = []
    for case in cases:
        sources = validated_sources(case["files"])
        program = get_adapter("python").analyze(sources)
        findings = [asdict(f) for f in analyze_sources(sources).findings]
        manual = build_manual(program, findings, case["title"])
        filename = case["id"] + "_R17.md"
        path = packet / filename
        if path.exists():
            raise SystemExit("Frozen candidate exists; use a new regression folder, never overwrite.")
        path.write_text(manual, encoding="utf-8")
        outputs.append({"case_id":case["id"],"candidate":"R17","file":filename,
                        "sha256":hashlib.sha256(path.read_bytes()).hexdigest(),
                        "character_count":count_characters(manual)})
    manifest = {"frozen_at_utc":datetime.now(timezone.utc).isoformat(),
                "case_sha256":hashlib.sha256((base/"cases.json").read_bytes()).hexdigest(),
                "sealed_reference_read_by_generator":False,"source_executed":False,
                "external_api_called":False,"outputs":outputs}
    (packet/"MANIFEST.json").write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding="utf-8")
    print(json.dumps(manifest,ensure_ascii=False,indent=2))


if __name__ == "__main__":
    main()
