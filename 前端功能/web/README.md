# ReviewDesk 0.2 Vue 阅读工作台

这个目录只负责界面。代码分析、SQLite 数据库、检索、说明书和 DeepSeek 请求位于 `../../后端功能/reviewdesk/`。浏览器不直接调用 DeepSeek；用户填写密钥时通过本机接口提交，已保存的密钥不会从后台回传。

## 运行与构建

已构建的 `dist/` 由 `../workbench.py` 提供本地 HTTP 服务，安装包运行时不需要 Node.js，也不依赖在线 CDN。不要直接双击 `dist/index.html`，因为工作台需要访问本地 API。

开发时需要 Node.js 20.19+ 或 22.12+：

```powershell
cd "前端功能\web"
npm ci
npm run build
npm test
```

`npm run dev` 使用 Vite；默认把 `/api` 转发到 `http://127.0.0.1:8765`，因此先用 `workbench.py --port 8765 --no-browser` 启动后台。正式运行建议直接使用 Python 入口或安装后的桌面快捷方式。

## 修改入口

- `src/App.vue`：项目导入、任务轮询、视图切换、API 设置、版本对比和模型请求预览。
- `src/components/CallGraph.vue`：按文件分组的可点击调用图；未知目标保留为文字记录。
- `src/components/SourceView.vue`：源码只读定位，使用 Vue 文本节点显示源码。
- `src/components/ManualViewer.vue`：详细说明书、阅读目录、分段显示、原版本源码定位、完整 Markdown 下载及导出重试。
- `src/components/FindingCard.vue`：本地问题的 `evidence` / `recommendation` 与源码定位。
- `src/components/JobHistory.vue`：近期任务、中断/失败状态与实际或未知用量。
- `src/utils.js`：Markdown 安全渲染、图布局、搜索和超长文档分页等可离线测试的逻辑。
- `src/style.css`：界面样式与宽度适配。

目前只导入 Python。后端通过语言适配器扩展，前端未来可从 `/api/bootstrap` 返回的语言列表增加选择器。

## 边界与验证

静态分析不能告诉用户真实运行时数值；调用图的排版不表示执行顺序。界面会显示分支条件、未解析调用和分析限制。

模型请求必须先获取 `/api/manual/preview`，确认时带上 `preview_id`；后台验证版本、发送上下文和费用参数。本地说明不调用收费接口。超过 5000 个非空白字符的自动文档由后台生成；界面显示实际状态和文件位置。超长说明每次只渲染约 60000 字符，读者可以翻页或下载完整文档。

Markdown 关闭 HTML，禁止图片自动加载；源码不使用 HTML 注入。下载令牌仅附加到本机的指定文档下载路径。

`reviewdesk-source:` 是本地源码引用协议，不会访问网络。界面严格校验相对路径/正整数行号，并读取报告 `revision_id` 对应的源码；不能用当前项目同名文件替代旧快照。导出失败保留下载按钮；重试仅调用 `/api/manuals/{id}/export`，不重新生成说明或调用模型。顶部任务历史通过 `/api/jobs?limit=20` 读取。

`npm test` 覆盖安全渲染、调用图正确边界、源码保持、Unicode 长度、下载令牌路径和百万字文档分页。

`tests/browser-smoke.cjs` 是真实浏览器集成测试，需要独立测试后台与 Playwright。默认访问 `http://127.0.0.1:8876`，会创建示例和测试项目，不应指向用户正在使用的数据目录。通过 `REVIEWDESK_TEST_URL`、`PLAYWRIGHT_PATH`、`PLAYWRIGHT_CHANNEL` 配置环境，`REVIEWDESK_TEST_OUTPUT` 指定独立证据目录。测试不发送收费 AI 请求，也不写入或移除密钥；截图与结果默认写入 `../../其它/verification/`。隔离服务应禁止读取实际凭据和调用模型；无密钥时可用 `REVIEWDESK_TEST_KEY_FIXTURE=1` 明确模拟bootstrap的has_key，仅用于进入真实预览界面，不能作为已配置真实密钥的证据。

`tests/bugfix-smoke.cjs` 验证问题卡、说明书源码联动、旧版本以及导出失败/重试，`tests/task-history-smoke.cjs` 验证真实任务列表和明确的中断请求夹具；默认分别输出到 `../../其它/verification/20260929-bugfix-ui/`，不会覆盖早期盲评证据。
