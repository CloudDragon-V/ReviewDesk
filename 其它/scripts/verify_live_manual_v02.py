"""One explicitly requested synthetic demo call; never retries a paid request."""
import argparse
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
            if state["status"]!="succeeded":
                raise RuntimeError(state.get("error") or state["status"])
            return state["result"]
        time.sleep(.2)
    raise RuntimeError("等待超时；禁止自动重试，先核对用量记录。")


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-live",action="store_true")
    args=parser.parse_args()
    if not args.run_live:
        raise SystemExit("Use --run-live only for an authorized, charged API test.")
    verification=ROOT/"其它/verification"
    guard=verification/"v02-live-attempt.json"
    if guard.exists():
        raise SystemExit("已有实测记录，禁止重复收费调用。")
    workspace=Workbench(verification/"v02-live-userdata")
    result={"api_called":False,"model":"deepseek-flash","max_cost_cny":"0.20","synthetic_demo_only":True}
    try:
        if not workspace.key():
            raise RuntimeError("Windows凭据中没有可用密钥；未提交请求。")
        revision=wait(workspace,workspace.import_demo())["revision_id"]
        request={"revision_id":revision,"use_ai":True,"model":"deepseek-flash","max_cost_cny":"0.20",
                 "focus":"给第一次看代码的小白完整解释入口、模块职责、函数调用、金额变量的变化，使用具体输入作推演时明确不是实际运行。"}
        preview=workspace.preview_manual(request)
        result.update(estimated_upper_cny=preview["estimated_cost_cny"],files=preview["files"],evidence_count=len(preview["evidence"]))
        guard.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding="utf-8")
        request["preview_id"]=preview["preview_id"]
        result["api_called"]=True
        job=workspace.generate_manual(request)
        result["job_id"]=job
        generated=wait(workspace,job)
        manual=workspace.store.get_manual(generated["manual_id"])
        result.update(mode=manual["mode"],usage=manual["usage"],character_count=manual["character_count"],
                      auto_exported=manual["auto_exported"],limitations=manual["limitations"],
                      ai_section_present="## DeepSeek 辅助讲解" in manual["markdown"])
        target=ROOT/"其它/示例说明书/购物订单_详细代码说明书.md"
        target.parent.mkdir(exist_ok=True)
        target.write_text(manual["markdown"],encoding="utf-8")
        result["example_path"]=str(target)
    except Exception as exc:
        result["error"]=str(exc)[:500]
    finally:
        workspace.close()
        (verification/"v02-live-result.json").write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding="utf-8")
    print(json.dumps(result,ensure_ascii=False,indent=2))


if __name__=="__main__":
    main()
