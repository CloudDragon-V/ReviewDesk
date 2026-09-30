"""Freeze final known-case regression documents and retain their attempt lineage."""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re

ROOT=Path(__file__).resolve().parents[2]
base=ROOT/"其它/evaluation/v02_blind"
target=base/"final_packet"
target.mkdir(exist_ok=True)
rows=[]
lineage=[]
for index in range(1,6):
    case=f"C{index:02}"
    source=base/("completion_outputs" if index==1 else "reasoning_outputs")/(case+("_R37.md" if index==1 else "_R31.md"))
    text=source.read_text(encoding="utf-8").replace("DeepSeek 辅助讲解","连贯讲解")
    text=re.sub(r"^- (?:项目版本|生成方式|API 用量|输入 / 输出 token|估算费用)：.*\n?","",text,flags=re.M)
    destination=target/(case+"_R41.md")
    with destination.open("x",encoding="utf-8") as stream: stream.write(text)
    rows.append({"case_id":case,"file":destination.name,"sha256":hashlib.sha256(destination.read_bytes()).hexdigest()})
    lineage.append({"case_id":case,"original":str(source.relative_to(base)),"original_sha256":hashlib.sha256(source.read_bytes()).hexdigest(),
                    "note":"C01 separately regenerated after a documented 12000-token truncation; other four successful outputs retained" if index==1 else "unchanged successful explanation from reasoning round"})
manifest={"candidate":"R41","frozen_at_utc":datetime.now(timezone.utc).isoformat(),"evaluation_kind":"known-case final regression, not fresh unseen evaluation",
          "transform":"Only provider heading and billing/version metadata removed; all explanatory claims retained.","outputs":rows}
with (target/"MANIFEST.json").open("x",encoding="utf-8") as stream: json.dump(manifest,stream,ensure_ascii=False,indent=2)
with (base/"final_lineage.json").open("x",encoding="utf-8") as stream: json.dump(lineage,stream,ensure_ascii=False,indent=2)
print(json.dumps({"packet_count":len(rows)},ensure_ascii=False))
