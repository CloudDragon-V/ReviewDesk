"""Produce a concise acceptance record from completed verification artifacts."""
from pathlib import Path
from decimal import Decimal
import json
import hashlib

root = Path(__file__).resolve().parents[1]
v = root / "verification"
load = lambda path: json.loads((root / path).read_text(encoding="utf-8-sig"))
installed = load("verification/installer-verification.json")
bundle = load("verification/bundle-inspection.json")
performance = load("verification/performance.json")
live = load("verification/live-smoke.json")["result"]["usage"]
benchmark = load("evaluation/online_evidence/usage_summary_v1.json")
balance = load("verification/security-and-balance.json")["official_balance"]["balance_infos"][0]["total_balance"]
assert installed["smoke"]["ok"] and installed["smoke"]["frozen"]
assert installed["application_removed"] and installed["uninstall_exit"] == 0
assert bundle["foreign_icu_absent"] and not bundle["credential_matches"]
total_cost = Decimal(live["estimated_cost_cny"]) + Decimal(benchmark["estimated_cost_cny"])
installer = root / "安装包" / "ReviewDesk-0.1.0-Setup-win-x64.exe"
size = installer.stat().st_size / 1024**2
checksum = hashlib.sha256(installer.read_bytes()).hexdigest()
text = f'''# ReviewDesk 0.1.0 验收报告

日期：2026-09-12。结论：已完成可安装的 Windows Python 代码审查桌面首版，源码、打包程序与隔离安装流程均已实际运行。当前版本聚焦 Python，包含本地检查、DeepSeek审查、关系图、上下文预算、费用记录、历史与报告导出。

## 交付物

- 安装包：`release/ReviewDesk-0.1.0-Setup-win-x64.exe`，{size:.2f} MiB。
- 便携版：`release/ReviewDesk-0.1.0-Portable-win-x64.zip`，解压整个目录后运行 ReviewDesk.exe。
- 源码与测试：`release/ReviewDesk-0.1.0-Source.zip`；本工作区也保留完整源码。
- 安装包 SHA-256：`{checksum}`。其余校验值见 `release/SHA256SUMS.txt`。
- 使用、代码流程、修改入口及理解练习：根目录 `README.md`。

## 已执行的验证

| 验证 | 结果和实际边界 |
|---|---|
| 产品离线测试 | 72/72通过，包含本地扫描/上下文/API错误/模型校验/费用/存储/Qt交互；见 independent-final-tests.txt |
| 测评工具自测 | 4/4通过；见 evaluation-harness-tests.txt |
| 语法检查 | src、app.py、scripts、tests的compileall通过 |
| 独立工程复验 | 93/100；所有已复现P1/P2已关闭，评分不包括未测的异机性能；见 independent-engineering-review.md |
| Windows系统凭据 | 隔离假凭据的写入、读取、删除实际通过；真实凭据未写进源码或安装包 |
| 数据库锁定 | 真实SQLite排他锁后保留已付费报告，可导出；解锁重试保存不重复调用API |
| 原生界面 | 已检查审查/关系/历史/设置四页及1366×768布局；源截图在 artifacts |
| 打包运行 | frozen=true；Python与Qt自带；将PATH缩至Windows系统目录并将PYTHONPATH指向不存在目录，仍完成示例审查 |
| 安装后运行 | 安装器退出0，应用退出0，1文件/4建议/8节点/8关系，SQLite持久化与MD/JSON导出全部成功 |
| 卸载 | 卸载器退出0，安装目录应用已移除，用户报告数据保留；见 installer-verification.json |
| 构建成分 | 当前合并修复已在PYZ中核对，Qt Windows平台插件和python314.dll存在，外来ICU未混入；见 bundle-inspection.json |

真实运行系统是 Windows 11 家庭中文版 64位、build 26200。目标为 Windows 10 1809+ / Windows 11 x64；**没有可用的第二台实体 Windows 电脑或Windows Sandbox，因此没有宣称完成异机实测**。这里通过的是同一台Windows上的新安装路径、独立用户数据目录、清理后的运行环境；不能替代完整干净系统兼容矩阵。安装包未购买代码签名证书。

## 盲评成绩与迭代

32个合成案例在实现前冻结，其中26检测、6探针。独立评分者只看匿名源码/发现，不看实现、oracle或方法映射；逐项判断先冻结再揭盲。代理共享模型家族与文件系统，所以准确名称是角色隔离的模拟双盲，而非严格人类双盲。

| 阶段 | 成绩 |
|---|---|
| 首次26例本地审查 | Precision100%、Recall76.92%、F1 **86.96分**；轻量基线F1 63.16分 |
| 首次结构/边界探针 | 6/6，其中极小预算返回空上下文的探针仅证明未超预算 |
| 首次8例在线同组本地 | F1 85.71分 |
| 首次8例混合审查 | F1 **75.00分**，一处同根因重复；没有新增独立检出 |
| 修复后离线回放 | F1 **85.71分**，重复已消除；这是已知样本回归，没有再次盲测或付费调用 |

首次结果原样保留，见 `evaluation/测评结果.md`；回放见 `evaluation/已知样本回放.md`。没有为了满分删除争议目标，或把同一题重跑当成新盲测。仍保留静默OSError、shell字符串拼接、硬编码合成密钥三项本地漏提示；OSError忽略是否有错还依赖业务契约。本轮证据没有证明增加模型调用一定提升检测效果。

## 真实API消耗

全部外发内容都是合成代码。共9次收费请求：1次独立集成验证、8次预注册测评；无自动重试。累计输入 {live['prompt_tokens'] + benchmark['prompt_tokens']:,} token、输出 {live['completion_tokens'] + benchmark['completion_tokens']:,} token、缓存命中 {live['cache_hit_tokens'] + benchmark['cache_hit_tokens']:,} token（属于输入总量）。按API返回用量和[2026-09-12官方单价](https://api-docs.deepseek.com/zh-cn/quick_start/pricing/)估算 **¥{total_cost}**，不是平台扣费收据。

余额最后查询显示 **¥{balance}**。平台显示精度有限，前后显示相同不代表零消费。最初在线验证确实成功返回，但随后的临时数据库清理曾因未关闭连接失败；该问题已修复并在Windows上验证。原始记录保留于 live-smoke.json，而不是伪称首次端到端无错误。

## 实際修复过的问题

独立复验推动修复了任务结束覆盖编辑、历史ID串写、关闭时丢失付费记录、数据库锁/满时丢结果、畸形JSON及超大数值逃过校验、Unicode转义绕过凭据遮蔽、网络重置和超时分类、未知费用导出误显示零，以及重复合并误吞不同根因。

打包首跑另发现DLL冲突：PyInstaller从宿主Poppler收集了同名ICU78，其导出与Qt期望的Windows系统ICU不一致。构建脚本改为最小PATH并干净重建后，打包运行和安装运行均通过。这里修复的是可复现构建问题，不是要求用户手工安装开发环境。

## 本机性能样例与限制

一次合成测量：100文件、4000函数，AST分析 {performance['analysis_seconds']}秒，4100节点/4000边。在相同6000字节预算代理下，顺序截断涉及3文件，依赖/风险优先涉及16文件；只说明选择覆盖差异，不能当作检出率提升。详见 performance.json，属于一次本机测量，不代表冷启动、大仓库或其他机器性能。

源码默认只读，项目不会被自动修改。模型建议的路径、行号和摘录通过校验仍需人工复核。图不执行源码、不推断全部动态调用；自动脱敏不是任意秘密识别保证。现版不含Git差异、多语言、向量检索、模型训练或自动补丁应用。
'''
(v / "验收报告.md").write_text(text, encoding="utf-8")
print("Acceptance report written from completed artifacts.")
