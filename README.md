# ReviewDesk 中文代码审查与阅读工作台

基于大语言模型与 RAG 的 Python 代码审查系统，帮助阅读陌生项目的模块职责、函数调用、变量变化和潜在问题，并生成带源码引用的中文说明书。

## 功能

- Python AST 静态分析、函数调用关系和变量事件展示。
- Vue 3 界面与 FastAPI 本机服务，SQLite 保存项目版本、索引、任务和报告。
- BM25、标识符匹配与调用关系扩展检索；可选 DeepSeek 生成详细说明。
- 离线说明、历史报告、版本对比、Markdown 导出；超过 5000 个非空白字符时自动保存。
- API 密钥可保存至 Windows 凭据管理器，发送前预览代码与费用估算。

系统不执行被审查的代码。调用与变量分析属于静态推断，无法确定的动态行为需要人工核查；模型引用通过校验也不代表结论一定正确。目前仅支持 Python。

## 从源码运行

推荐 Windows 10/11 x64，安装 Python 3.14。首次在项目根目录打开 PowerShell：

```powershell
py -3.14 -m venv "其它/.venv"
& "其它/.venv/Scripts/python.exe" -m pip install -r "其它/requirements.txt"
& "其它/.venv/Scripts/python.exe" "前端功能/workbench.py"
```

之后可双击 `前端功能/启动软件.cmd`。程序打开本机浏览器页面；DeepSeek 功能需要用户自己的 API Key，离线阅读不需要密钥。

仓库包含前端静态构建文件。修改 Vue 源码后的构建方式见 [前端开发说明](前端功能/web/README.md)。

## 当前发布状态

此仓库上传的是包含 2026-09-29 修补的源码。现有本地 0.2.0 安装包尚未包含这些修补，因此本次未上传旧安装包，也未发布新版 Release。下载源码 ZIP 不等于下载可安装软件。

## 目录

| 目录 | 用途 |
|---|---|
| 前端功能 | Vue 界面、桌面启动入口、经典 Qt 界面 |
| 后端功能 | 分析器、知识库、检索、模型接口与本机 API |
| 其它 | 测试、评估样本、构建脚本、许可证与技术文档 |

详细用法见 [项目说明](其它/README.md)，架构见 [已实现架构与运行说明](其它/需求规则目录/已实现架构与运行说明.md)。部分历史验证记录描述的是当时版本，不代表当前提交的新测试结果。评估中的匿名角色评分属于模拟盲评，不是真人双盲实验。

## 离线测试

```powershell
$env:PYTHONPATH = "$PWD/后端功能;$PWD/前端功能"
& "其它/.venv/Scripts/python.exe" -m unittest discover -s "其它/tests" -v
```

## 许可证

项目许可证见 [LICENSE](其它/LICENSE)，第三方组件声明见 [工作台第三方声明](其它/WORKBENCH_THIRD_PARTY_NOTICES.md) 与 [第三方声明](其它/THIRD_PARTY_NOTICES.md)。
