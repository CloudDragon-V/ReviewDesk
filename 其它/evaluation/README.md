# ReviewDesk 模拟双盲测评协议

状态：首次离线与在线模拟双盲测评已完成独立判断、评分和揭盲；见 [测评结果](测评结果.md)。原始题库、候选输出、判断与评分均保留哈希承诺。开发方现可查阅结果，之后对相同题库的测试必须标为“已知样本回归”，不能再称首次盲测。此协议页不列出题库答案。

后续验证：结果合并修复已通过 [已知样本离线回放](已知样本回放.md)，运行目录为 `runs/replay_v2`。复用原已接受发现、oracle 与独立判断，未新增 API 调用；首次盲评分数原样保留。

本流程衡量 Python 代码审查的发现质量及若干工程边界。题库作者在阅读公共数据契约后生成 32 个合成案例，未读取分析、上下文、API 或界面实现；其中 26 个案例用于缺陷发现质量，6 个探针用于结构与边界行为。样例不来自用户项目，不含真实密钥，也不会执行被审查代码。

## 盲法及其局限

1. 题库作者冻结 `sealed/corpus.json`、`sealed/oracle.json` 和作者脚本哈希；开发者仅收到数量及哈希承诺。
2. 开发者完成并冻结候选后，收集器先保存候选源码哈希，再导入并调用公共接口；采集前后哈希必须一致。
3. 收集器随机将两种方法映射为 Candidate-A / Candidate-B。映射仅保存在本轮 `sealed/mapping.json`，承诺保存在 `RUN_COMMITMENT.json`。
4. 独立评分者仅阅读 `anonymous_packet.json` 和判定模板，对每条发现标记 `valid`、`invalid` 或 `uncertain` 并解释。评分者不查看引擎源码、隐藏答案、映射或初次自动评分。
5. 独立判断写入 `adjudications.json` 后先冻结其哈希，再运行评分，最后揭盲。首次结果原样保留；后续修复使用新的运行目录，并明确标注已经接触测试结果。

这是**角色隔离的模拟双盲**，不是招募人类参与者的严格双盲实验。代理共享模型家族与文件系统，权限不能强制隔离；候选语言或措辞可能使身份被推断。哈希属于本地时间的承诺，不是第三方时间戳。我们仅报告实际做到的隐藏、冻结和独立判断，不将其描述为临床式或人类双盲试验。

## 比较对象

- 离线：产品本地分析器与公开写明的轻量基线。基线仅使用 Python AST 语法解析及少量无上下文关键字规则。它用于给出低成本参照，不代表完整的成熟开源分析器。
- 在线：产品本地分析与产品本地加 DeepSeek 的混合审查。预注册 8 个均衡的合成阳性/阴性案例，使用同一组源代码，API 上下文预算 1200，每次费用估算上限 0.05 元，计划最多 8 次，即 0.40 元。模型返回的 token 用量必须是真实 API 值，费用仍是单价计算的估算。
- 在线计划 `ONLINE_PROTOCOL.json` 及其独立承诺 `ONLINE_COMMITMENT.json` 取代原始承诺里预留的 4 例在线选取字段；**原始 32 例题库与 oracle 未改动**。变更发生在读取任何实现或输出之前，为配合 local/hybrid 比较而预注册。
- 在线只能经显式 `--online` 开启。密钥仅调用 Windows 凭据管理器接口获取，不接受命令行明文密钥，不写入结果、日志或环境文件。只有题库合成代码会外发。

## 可复现命令

在项目根目录执行，使用项目 Python：

```powershell
python evaluation/run_benchmark.py verify
python -m unittest discover -s evaluation -p 'test_harness.py' -v
python evaluation/run_benchmark.py collect --output evaluation/runs/offline_v1
```

此时把 `evaluation/runs/offline_v1/anonymous_packet.json` 和 `adjudication_template.json` 提供给独立评分者。评分者另存完整的 `adjudications.json`，每个 `item_id` 恰好一条。完成并冻结判断后：

```powershell
Get-FileHash evaluation/runs/offline_v1/adjudications.json -Algorithm SHA256
python evaluation/run_benchmark.py score --output evaluation/runs/offline_v1 --adjudications evaluation/runs/offline_v1/adjudications.json
python evaluation/run_benchmark.py score --output evaluation/runs/offline_v1 --adjudications evaluation/runs/offline_v1/adjudications.json --reveal
```

经主任务通知可以开展在线测评后，运行：

```powershell
python evaluation/run_benchmark.py collect --output evaluation/runs/online_v1 --online
```

仍先独立判断该目录匿名包，再使用相同评分命令。已有运行目录不会覆盖。若候选在采集过程中发生变化，该轮作废并保留证据，另开目录重跑。`freeze_corpus.py` 在承诺文件存在时拒绝重写，正常验证不应运行它。

## 评分定义

- TP：报告的路径及邻近行覆盖冻结 oracle 中的目标，并且文本匹配事先冻结的缺陷概念。独立评分者判定无效的发现不能计为 TP。一个目标最多命中一次。
- FN：冻结目标未被匹配。不同语言/表达可能导致自动匹配漏计，因此逐例报告会保留未命中概念，必要的人工复核必须另记原因，不能静默改 oracle。
- FP：没有匹配目标、且未被独立评分者认可的发现。未匹配且不确定的项按保守口径计入 FP，并单列数量。
- 合理额外发现：独立评分者认可、但不属于冻结目标的发现单独列为 `valid_extra`，只影响 precision，不修改冻结的 recall 分母。
- Precision = (TP + 合理额外发现) / 所有发现；Recall = TP / (TP + FN)；F1 为两者调和平均；100 分制 = F1 × 100。零发现时 precision 记为 0，以避免无报告获得满分。
- 结构/边界探针按通过数单列，不混入 F1。UI、安装包、迁移运行、费用误差需要另做工程验证；本题库分数不能代替这些验证。

未带 `--adjudications` 的评分只是预定义规则自动评分，不能称为“已完成独立盲评分”。小规模合成题库不代表真实仓库总体性能；训练或调整后再次使用原题应标注为已知样本回归测试。
