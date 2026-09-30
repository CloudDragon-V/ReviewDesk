"""Five fixed synthetic RAG checks; isolated data, <=0.20 CNY ceiling per request."""
from datetime import datetime, timezone
import argparse
import hashlib
import json
from pathlib import Path
import sys
import time

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/"后端功能"))
from reviewdesk.workbench import Workbench


def wait(workspace, identifier):
    deadline=time.monotonic()+180
    while time.monotonic()<deadline:
        state=workspace.store.get_job(identifier)
        if state["status"] not in {"queued","running"}:
            if state["status"]!="succeeded": raise RuntimeError(state.get("error") or state["status"])
            return state["result"]
        time.sleep(.2)
    raise RuntimeError("任务超时，禁止自动重试")


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-live",action="store_true")
    parser.add_argument("--round",choices=["enhanced","reasoning","completion"],default="enhanced")
    parser.add_argument("--case",choices=["C01","C02","C03","C04","C05"])
    args=parser.parse_args()
    if not args.run_live:
        raise SystemExit("Explicit --run-live is required for this charged regression evaluation.")
    base=ROOT/"其它/evaluation/v02_blind"
    output=base/(args.round+"_outputs")
    output.mkdir(exist_ok=True)
    guard=ROOT/("其它/verification/v02-"+args.round+"-attempt.json")
    # Exclusive marker prevents accidentally running all five paid calls twice.
    with guard.open("x",encoding="utf-8") as stream:
        json.dump({"started_at":datetime.now(timezone.utc).isoformat(),"max_requests":1 if args.case else 5,"total_ceiling_cny":"0.20" if args.case else "1.00"},stream)
    workspace=Workbench(ROOT/("其它/verification/v02-"+args.round+"-userdata"))
    candidate={"enhanced":"R23","reasoning":"R31","completion":"R37"}[args.round]
    manifest={"candidate":candidate,"evaluation_kind":"known-case enhancement regression after initial scores",
              "sealed_oracle_read_by_generator":False,"source_executed":False,"outputs":[]}
    try:
        if not workspace.key(): raise RuntimeError("未保存密钥，不调用模型")
        cases=json.loads((base/"cases.json").read_text(encoding="utf-8"))["cases"]
        if args.case: cases=[case for case in cases if case["id"]==args.case]
        if args.round=="reasoning": cases=list(reversed(cases))
        for case in cases:
            revision=wait(workspace,workspace.import_files(case["title"],case["files"]))["revision_id"]
            request={"revision_id":revision,"use_ai":True,"model":"deepseek-flash","max_cost_cny":"0.20",
                     "focus":"向第一次看此代码的初学者详细解释程序目标、入口顺序、函数调用与返回、关键变量及对象变化。对给定输入可做有前提的静态推演，明确未执行。"}
            preview=workspace.preview_manual(request)
            request["preview_id"]=preview["preview_id"]
            job=workspace.generate_manual(request)
            generated=wait(workspace,job)
            manual=workspace.store.get_manual(generated["manual_id"])
            target=output/(case["id"]+"_"+candidate+".md")
            target.write_text(manual["markdown"],encoding="utf-8")
            row={"case_id":case["id"],"file":target.name,"sha256":hashlib.sha256(target.read_bytes()).hexdigest(),
                 "mode":manual["mode"],"character_count":manual["character_count"],"usage":manual["usage"],
                 "auto_exported":manual["auto_exported"],"ceiling_cny":"0.20","job_id":job}
            manifest["outputs"].append(row)
            (output/"MANIFEST.json").write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding="utf-8")
            print(json.dumps({"case":case["id"],"mode":row["mode"],"characters":row["character_count"],"estimated_cost_cny":row["usage"]["estimated_cost_cny"]},ensure_ascii=False),flush=True)
    except Exception as exc:
        manifest["error"]=str(exc)[:500]
        raise
    finally:
        workspace.close()
        manifest["frozen_at_utc"]=datetime.now(timezone.utc).isoformat()
        (output/"MANIFEST.json").write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding="utf-8")


if __name__=="__main__":
    main()
