"""Prepare shareable synthetic examples from real responses; makes no API call."""
import json
from dataclasses import asdict
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/"后端功能"))
from reviewdesk.demo_project import DEMO_FILES
from reviewdesk.workbench import validated_sources
from reviewdesk.languages import get_adapter
from reviewdesk.manual import build_manual, count_characters
from reviewdesk.analysis import analyze_sources

directory=ROOT/"其它/示例说明书"
directory.mkdir(exist_ok=True)
example=directory/"购物订单_详细代码说明书.md"
original=ROOT/"其它/verification/v02-live-original-manual.md"
if not original.exists(): original.write_bytes(example.read_bytes())
raw=original.read_text(encoding="utf-8")
prose=raw.split("## DeepSeek 辅助讲解",1)[1].split("## 本次生成记录",1)[0].strip()
prose=prose.split("### ",1)[1]
prose="### "+prose
record="## 本次生成记录"+raw.split("## 本次生成记录",1)[1]
sources=validated_sources([{"path":path,"content":content} for path,content in DEMO_FILES.items()])
static=build_manual(get_adapter("python").analyze(sources),[asdict(f) for f in analyze_sources(sources).findings],"购物订单 · 三模块教学示例")
title,_,body=static.partition("\n")
text=(title+"\n\n## DeepSeek 辅助讲解\n\n先读业务流程，再查后面的静态结构与源码索引。模型引用经过编号检查，但文字仍需对照代码。\n\n"
      +prose+"\n\n---\n\n## 静态结构与逐项源码索引\n"+body+"\n\n"+record
      +"\n\n本示例沿用首次真实请求的模型正文，按新版顺序重新编排并补充静态语义说明；本次整理没有再次调用 API，上述用量属于原请求。\n")
example.write_text(text,encoding="utf-8")
advanced=ROOT/"其它/evaluation/v02_blind/reasoning_outputs/C05_R31.md"
(directory/"协程调度_详细代码说明书.md").write_bytes(advanced.read_bytes())
cases=json.loads((ROOT/"其它/evaluation/v02_blind/cases.json").read_text(encoding="utf-8"))["cases"]
asynchronous=next(case for case in cases if case["id"]=="C05")
rows=["# 两份说明书对应的完整示例源码","","以下均为本项目合成的教学/测评源码。生成说明时只静态解析，没有执行。", "","## 购物订单（三文件）",""]
for path,content in DEMO_FILES.items(): rows.extend(["### "+path,"","```python",content.rstrip(),"```",""])
rows.extend(["## 协程调度（两文件）",""])
for file in asynchronous["files"]: rows.extend(["### "+file["path"],"","```python",file["content"].rstrip(),"```",""])
(directory/"示例源码索引.md").write_text("\n".join(rows),encoding="utf-8")
print(json.dumps({"api_called":False,"shopping_characters":count_characters(text),"async_characters":count_characters(advanced.read_text(encoding="utf-8"))},ensure_ascii=False))
