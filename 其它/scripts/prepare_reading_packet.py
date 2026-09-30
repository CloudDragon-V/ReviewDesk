"""Remove provider/billing metadata, preserving all explanatory claims for grading."""
import hashlib
import json
from pathlib import Path
import re

ROOT=Path(__file__).resolve().parents[2]
base=ROOT/"其它/evaluation/v02_blind"
source=base/"enhanced_outputs"
target=base/"regression_packet"
target.mkdir(exist_ok=True)
rows=[]
for file in sorted(source.glob("C*_R23.md")):
    text=file.read_text(encoding="utf-8").replace("DeepSeek 辅助讲解","连贯讲解")
    text=re.sub(r"^- (?:项目版本|生成方式|API 用量|输入 / 输出 token|估算费用)：.*\n?","",text,flags=re.M)
    destination=target/file.name
    with destination.open("x",encoding="utf-8") as stream: stream.write(text)
    rows.append({"file":file.name,"sha256":hashlib.sha256(destination.read_bytes()).hexdigest()})
manifest={"candidate":"R23","transform":"Only provider heading and billing/version metadata removed; explanatory claims and limitations retained.","outputs":rows}
with (target/"MANIFEST.json").open("x",encoding="utf-8") as stream:
    json.dump(manifest,stream,ensure_ascii=False,indent=2)
print(json.dumps({"packet_count":len(rows)},ensure_ascii=False))
