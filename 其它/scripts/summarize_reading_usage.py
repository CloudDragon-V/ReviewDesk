"""Aggregate all charged attempts, including incomplete responses, without secrets."""
from decimal import Decimal
import json
from pathlib import Path

ROOT=Path(__file__).resolve().parents[2]
verification=ROOT/"其它/verification"
base=ROOT/"其它/evaluation/v02_blind"
demo=json.loads((verification/"v02-live-result.json").read_text(encoding="utf-8"))
rows=[{"phase":"demo","case_id":"shopping","mode":demo["mode"],"usage":demo["usage"]}]
for phase in ["enhanced","reasoning","completion"]:
    manifest=json.loads((base/(phase+"_outputs")/"MANIFEST.json").read_text(encoding="utf-8"))
    rows.extend({"phase":phase,"case_id":item["case_id"],"mode":item["mode"],"usage":item["usage"]} for item in manifest["outputs"])
summary={"actual_request_count":len(rows),"complete_manuals":sum(row["mode"]=="ai" for row in rows),
         "partial_manuals":sum(row["mode"]=="partial" for row in rows),"all_usage_returned":all(row["usage"]["actual"] for row in rows),
         "prompt_tokens":sum(row["usage"]["prompt_tokens"] for row in rows),
         "completion_tokens_including_reasoning":sum(row["usage"]["completion_tokens"] for row in rows),
         "estimated_total_cny":str(sum(Decimal(row["usage"]["estimated_cost_cny"]) for row in rows)),
         "note":"Includes the truncated request; CNY is an estimate, not the provider bill. No production automatic retries.","attempts":rows}
(verification/"v02-live-summary.json").write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding="utf-8")
print(json.dumps({key:value for key,value in summary.items() if key!="attempts"},ensure_ascii=False,indent=2))
