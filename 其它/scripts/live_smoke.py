"""Explicit opt-in integration check against synthetic code; no paid retries."""
import argparse
import json
from pathlib import Path
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "后端功能"))
from reviewdesk.api import DeepSeekClient
from reviewdesk.service import ReviewService
from reviewdesk.storage import CredentialStore, ReportStore

parser = argparse.ArgumentParser()
parser.add_argument("--allow-paid", action="store_true")
args = parser.parse_args()
if not args.allow_paid:
    raise SystemExit("This check makes one paid request. Pass --allow-paid explicitly.")
source = '''def average(values):
    """Return arithmetic mean; empty input should return zero."""
    return sum(values) / len(values)

def append_event(event, events=[]):
    events.append(event)
    return events
'''
key = CredentialStore().get_key()
client = DeepSeekClient(key)
before = client.check_connection()
with tempfile.TemporaryDirectory() as directory:
    service = ReviewService(ReportStore(Path(directory) / "test.db"))
    analysis = service.analyze_code(source, "synthetic_demo.py")
    result = service.review(analysis, "合成样例在线验证", True, key, budget=2500, max_cost_cny="0.10")
    after = client.check_connection()
    output = Path(__file__).resolve().parents[1] / "verification"
    output.mkdir(exist_ok=True)
    evidence = {"synthetic_code_only": True, "requests": 1, "balance_before": before,
                "balance_after": after, "result": result.to_dict()}
    (output / "live-smoke.json").write_text(json.dumps(evidence, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"mode": result.mode, "findings": len(result.findings),
                      "llm_findings": sum(f.source == "llm" for f in result.findings),
                      "usage": result.to_dict()["usage"], "limitations": result.limitations}, ensure_ascii=False))
