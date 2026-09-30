<script setup>
import { computed, nextTick, onMounted, onUnmounted, ref } from 'vue'
import CallGraph from './components/CallGraph.vue'
import SourceView from './components/SourceView.vue'
import ManualViewer from './components/ManualViewer.vue'
import FindingCard from './components/FindingCard.vue'
import JobHistory from './components/JobHistory.vue'
import { displayDate, matchSymbol, resolveManualSource } from './utils.js'

const token = ref(''), bootstrap = ref({}), projects = ref([]), revisions = ref([]), revision = ref(null)
const tab = ref('overview'), page = ref('workspace'), selectedId = ref(''), sourcePath = ref(''), sourceLine = ref(0), sourceEnd = ref(0)
const error = ref(''), notice = ref(''), connected = ref(false), busy = ref(false), job = ref(null), importOpen = ref(false)
const projectName = ref('我的 Python 项目'), importMode = ref('upload'), uploadFiles = ref([]), folderPath = ref(''), pastedCode = ref(''), pastedPath = ref('main.py')
const symbolQuery = ref(''), symbolFile = ref(''), variableQuery = ref(''), variableScope = ref('')
const manuals = ref([]), manual = ref(null), manualFocus = ref(''), model = ref('deepseek-flash'), maxCost = ref('0.20'), budget = ref(24000), preview = ref(null)
const query = ref(''), strategy = ref('hybrid'), searchResults = ref(null), searchBusy = ref(false)
const baseRevision = ref(''), comparison = ref(null), compareBusy = ref(false), apiKey = ref(''), rememberKey = ref(true), settingsBusy = ref(false)
const confirmDelete = ref(null), showSource = ref(true), alive = ref(true)
const reportSourceRevision = ref(null), exportBusy = ref(false)
const recentJobs = ref([]), jobsBusy = ref(false), jobsError = ref('')
let manualLocationRequest = 0
const tabs = [{ id: 'overview', icon: '◫', label: '阅读总览' }, { id: 'calls', icon: '↗', label: '函数调用' }, { id: 'symbols', icon: 'ƒ', label: '模块与函数' }, { id: 'variables', icon: '⇄', label: '变量变化' }, { id: 'manual', icon: '▤', label: '详细说明书' }, { id: 'knowledge', icon: '⌕', label: '知识检索' }, { id: 'history', icon: '◷', label: '版本对比' }]
const program = computed(() => revision.value?.program || { files: [], symbols: [], calls: [], entry_points: [], limitations: [] })
const symbols = computed(() => program.value.symbols || [])
const selectedSymbol = computed(() => symbols.value.find(s => s.id === selectedId.value))
const sourceRevision = computed(() => tab.value === 'manual' && manual.value
  ? (reportSourceRevision.value?.revision_id === manual.value.revision_id ? reportSourceRevision.value : revision.value?.revision_id === manual.value.revision_id ? revision.value : null)
  : revision.value)
const selectedSource = computed(() => sourceRevision.value?.sources?.find(s => s.path === sourcePath.value))
const hasSourcePanel = computed(() => ['calls', 'symbols', 'variables', 'knowledge', 'manual'].includes(tab.value))
const functions = computed(() => symbols.value.filter(s => !['module', 'class'].includes(s.kind)))
const filteredSymbols = computed(() => symbols.value.filter(s => matchSymbol(s, symbolQuery.value, symbolFile.value)))
const selectedCalls = computed(() => (program.value.calls || []).filter(c => c.source === selectedId.value))
const callers = computed(() => (program.value.calls || []).filter(c => c.target === selectedId.value))
const variables = computed(() => symbols.value.flatMap(symbol => (symbol.variables || []).map(variable => ({ ...variable, symbol }))).filter(v => (!variableScope.value || v.symbol.id === variableScope.value) && (!variableQuery.value || `${v.name} ${v.symbol.name}`.toLowerCase().includes(variableQuery.value.toLowerCase()))))
const hasKey = computed(() => Boolean(bootstrap.value.has_key))
const models = computed(() => (bootstrap.value.models || ['deepseek-flash']).map(m => typeof m === 'string' ? { id: m, name: m } : { ...m, id: m.id || m.name, name: m.label || m.name || m.id }))
const activeProject = computed(() => projects.value.find(p => p.id === revision.value?.project_id))
const localFindings = computed(() => revision.value?.findings || [])
const activeTab = computed(() => tabs.find(t => t.id === tab.value)?.label || '阅读总览')
const jobPercent = computed(() => Math.min(100, Math.max(0, Math.round(Number(job.value?.progress) || 0))))

async function api(path, options = {}) {
  const response = await fetch(`/api${path}`, { ...options, headers: { 'Content-Type': 'application/json', ...(token.value ? { 'X-ReviewDesk-Token': token.value } : {}), ...options.headers } })
  let data
  try { data = await response.json() } catch { throw new Error(`后台响应无法读取（HTTP ${response.status}）。请确认本地服务已启动。`) }
  if (!response.ok) throw new Error(typeof data.detail === 'string' ? data.detail : `请求失败（HTTP ${response.status}）`)
  return data
}
function post(path, body = {}) { return api(path, { method: 'POST', body: JSON.stringify(body) }) }
function fail(reason) { error.value = reason?.message || String(reason); busy.value = false; job.value = null }
async function refreshProjects() { projects.value = (await api('/projects')).projects || [] }
async function refreshJobs() {
  if (jobsBusy.value || !token.value) return
  jobsBusy.value = true; jobsError.value = ''
  try { recentJobs.value = (await api('/jobs?limit=20')).jobs || [] }
  catch (e) { jobsError.value = '读取任务记录失败：' + e.message }
  finally { jobsBusy.value = false }
}
async function start() {
  error.value = ''
  try {
    bootstrap.value = await api('/bootstrap')
    token.value = bootstrap.value.token
    connected.value = true
    if (models.value.length) model.value = models.value[0].id
    await Promise.all([refreshProjects(), refreshJobs()])
    if (projects.value[0]?.latest_revision_id) await openRevision(projects.value[0].latest_revision_id)
  } catch (e) { fail(e); connected.value = false }
}
async function openRevision(id) {
  if (!id) return
  error.value = ''
  try {
    const next = await api(`/revisions/${encodeURIComponent(id)}`)
    revision.value = next
    selectedId.value = ''; sourceLine.value = 0; sourceEnd.value = 0
    sourcePath.value = next.sources?.[0]?.path || ''
    symbolFile.value = ''; variableScope.value = ''; comparison.value = null; searchResults.value = null; manual.value = null; preview.value = null; reportSourceRevision.value = null; manualLocationRequest++
    const [history, saved] = await Promise.all([api(`/projects/${encodeURIComponent(next.project_id)}/revisions`), api(`/manuals?revision_id=${encodeURIComponent(id)}`)])
    revisions.value = history.revisions || []
    manuals.value = saved.manuals || []
    baseRevision.value = revisions.value.find(r => (r.id || r.revision_id) !== id)?.id || ''
    if (manuals.value[0]) await loadManual(manuals.value[0].id)
    projectName.value = next.project_name || '我的 Python 项目'
    page.value = 'workspace'
  } catch (e) { fail(e) }
}
async function runJob(request, after) {
  if (busy.value) return
  busy.value = true; error.value = ''; notice.value = ''; preview.value = null
  job.value = { progress: 0, message: '正在准备…' }
  try {
    const started = await request()
    if (!started.job_id) throw new Error('后台未返回任务编号。')
    const deadline = Date.now() + 20 * 60 * 1000
    while (alive.value && Date.now() < deadline) {
      const update = await api(`/jobs/${encodeURIComponent(started.job_id)}`)
      job.value = update
      if (['done', 'completed', 'succeeded', 'success'].includes(update.status)) {
        await after(update.result || {})
        busy.value = false; job.value = null
        return
      }
      if (['failed', 'error', 'cancelled', 'interrupted'].includes(update.status)) throw new Error(update.error?.detail || update.error?.message || update.error || update.message || (update.status === 'interrupted' ? '后台重启后任务已中断，请重新操作。' : '任务执行失败。'))
      await new Promise(resolve => setTimeout(resolve, 600))
    }
    if (alive.value) throw new Error('等待任务超时。后台任务可能仍在运行，可稍后重新打开项目查看。')
  } catch (e) { fail(e) } finally { await refreshJobs() }
}
async function importFinished(result) {
  importOpen.value = false
  await refreshProjects()
  await openRevision(result.revision_id)
  tab.value = 'overview'
  notice.value = '代码已保存为一个独立版本。请从阅读总览开始，再跟着函数和变量逐步深入。'
}
function demo() { runJob(() => post('/demo'), importFinished) }
async function readFiles(event) {
  error.value = ''
  try {
    const files = [...(event.target.files || [])].filter(f => f.name.toLowerCase().endsWith('.py'))
    if (!files.length) throw new Error('请选择至少一个 .py 文件。当前版本仅分析 Python。')
    if (files.length > 300) throw new Error('一次最多导入 300 个 Python 文件，请选择更小的功能目录。')
    if (files.reduce((sum, f) => sum + f.size, 0) > 8 * 1024 * 1024) throw new Error('本次源码超过 8 MB，请缩小导入范围。')
    uploadFiles.value = await Promise.all(files.map(async f => ({ path: f.webkitRelativePath || f.name, content: await f.text() })))
    if (files[0]?.webkitRelativePath) projectName.value = files[0].webkitRelativePath.split('/')[0]
  } catch (e) { fail(e) }
  event.target.value = ''
}
function submitImport() {
  if (!projectName.value.trim()) { error.value = '请填写项目名称。'; return }
  const common = { project_name: projectName.value.trim(), language: 'python' }
  if (importMode.value === 'folder') {
    if (!folderPath.value.trim()) { error.value = '请填写本机目录的完整路径。'; return }
    return runJob(() => post('/import-folder', { ...common, path: folderPath.value.trim() }), importFinished)
  }
  const files = importMode.value === 'paste' ? [{ path: pastedPath.value.trim(), content: pastedCode.value }] : uploadFiles.value
  if (!files.length || (importMode.value === 'paste' && !files[0].content.trim())) { error.value = '请先选择代码文件，或粘贴需要阅读的 Python 源码。'; return }
  return runJob(() => post('/import', { ...common, files }), importFinished)
}
function selectSymbol(symbol, line, navigate = false) {
  if (!symbol) return
  selectedId.value = symbol.id; sourcePath.value = symbol.path; sourceLine.value = line || symbol.start_line; sourceEnd.value = line ? line : symbol.end_line; showSource.value = true
  if (navigate) tab.value = 'symbols'
}
function locate(path, line, endLine = line) { sourcePath.value = path; sourceLine.value = line; sourceEnd.value = endLine; showSource.value = true }
function nameOf(id) { return symbols.value.find(s => s.id === id)?.qualified_name || symbols.value.find(s => s.id === id)?.name || '未确定的调用方' }
function kindName(kind) { return ({ module: '模块', class: '类', function: '函数', method: '方法', async_function: '异步函数' })[kind] || kind || '代码单元' }
function eventName(kind) { return ({ assign: '赋值', assignment: '赋值', parameter: '接收参数', augmented_assign: '更新', augassign: '更新', loop: '循环取值', delete: '删除', return: '返回', annotated_assign: '声明与赋值' })[kind] || kind }
function manualPayload(useAi) { return { revision_id: revision.value.revision_id, use_ai: useAi, model: model.value, max_cost_cny: maxCost.value, focus: manualFocus.value.trim(), budget: Number(budget.value) } }
async function loadManual(id) {
  manual.value = await api(`/manuals/${encodeURIComponent(id)}`)
  reportSourceRevision.value = null; manualLocationRequest++
  sourceLine.value = 0; sourceEnd.value = 0
  sourcePath.value = revision.value?.revision_id === manual.value.revision_id ? revision.value.sources?.[0]?.path || '' : ''
}
async function locateManual(reference) {
  const reportId = manual.value?.id, reportRevisionId = manual.value?.revision_id, request = ++manualLocationRequest
  error.value = ''
  try {
    const resolved = await resolveManualSource(reference, reportRevisionId, reportSourceRevision.value || revision.value,
      id => api(`/revisions/${encodeURIComponent(id)}`))
    if (request !== manualLocationRequest || manual.value?.id !== reportId) return
    reportSourceRevision.value = resolved.snapshot
    locate(resolved.path, resolved.start_line, resolved.end_line)
  } catch (e) { if (request === manualLocationRequest && manual.value?.id === reportId) error.value = e.message }
}
async function retryManualExport(id) {
  if (exportBusy.value || busy.value || id !== manual.value?.id) return
  exportBusy.value = true; error.value = ''
  try {
    const updated = await post(`/manuals/${encodeURIComponent(id)}/export`)
    if (manual.value?.id === id) manual.value = updated
    if (updated.export_status === 'saved') notice.value = 'Markdown 文件已保存；本次没有调用模型。'
  } catch (e) { error.value = e.message } finally { exportBusy.value = false }
}
function generateManual(useAi = false) {
  if (!revision.value) return
  const payload = { ...manualPayload(useAi), ...(useAi ? { preview_id: preview.value?.preview_id } : {}) }
  if (useAi && !payload.preview_id) { error.value = '请重新预览发送范围，再确认生成。'; return }
  runJob(() => post('/manual', payload), async result => {
    manuals.value = (await api(`/manuals?revision_id=${encodeURIComponent(revision.value.revision_id)}`)).manuals || []
    await loadManual(result.manual_id || result.id || manuals.value[0]?.id)
    tab.value = 'manual'
    notice.value = manual.value?.export_status === 'failed' ? '说明书已保存，自动导出文件失败。可在下方重试保存或直接下载。' : '完整说明书已生成。所有说明都应结合右侧对应版本的源码核对。'
  })
}
async function previewAi() {
  if (!hasKey.value) { page.value = 'settings'; notice.value = '请先配置 DeepSeek API Key，再预览本次发送范围。'; return }
  busy.value = true; error.value = ''
  try { preview.value = await post('/manual/preview', manualPayload(true)) } catch (e) { fail(e) } finally { busy.value = false }
}
async function searchKnowledge() {
  if (!query.value.trim()) return
  searchBusy.value = true; error.value = ''
  try { searchResults.value = (await api(`/search?revision_id=${encodeURIComponent(revision.value.revision_id)}&q=${encodeURIComponent(query.value)}&strategy=${encodeURIComponent(strategy.value)}`)).results || [] } catch (e) { fail(e) } finally { searchBusy.value = false }
}
async function compare() {
  compareBusy.value = true; error.value = ''
  try { comparison.value = await api(`/compare?base_id=${encodeURIComponent(baseRevision.value)}&target_id=${encodeURIComponent(revision.value.revision_id)}`) } catch (e) { fail(e) } finally { compareBusy.value = false }
}
async function saveKey() {
  if (!apiKey.value.trim()) { error.value = '请填写 API Key。'; return }
  settingsBusy.value = true; error.value = ''
  try { const result = await post('/settings/key', { key: apiKey.value.trim(), remember: rememberKey.value }); bootstrap.value.has_key = result.has_key; apiKey.value = ''; notice.value = '密钥已配置，可以返回说明书页面预览模型请求。' } catch (e) { fail(e) } finally { settingsBusy.value = false }
}
async function deleteKey() {
  settingsBusy.value = true
  try { await api('/settings/key', { method: 'DELETE' }); bootstrap.value.has_key = false; apiKey.value = ''; notice.value = '已移除保存的密钥。' } catch (e) { fail(e) } finally { settingsBusy.value = false }
}
async function removeProject() {
  if (!confirmDelete.value) return
  const id = confirmDelete.value.id
  try {
    await api(`/projects/${encodeURIComponent(id)}`, { method: 'DELETE' })
    if (revision.value?.project_id === id) { revision.value = null; manuals.value = []; manual.value = null }
    confirmDelete.value = null
    await refreshProjects()
    notice.value = '项目及其知识库记录已删除，原始代码文件不受影响。'
  } catch (e) { fail(e) }
}
async function quit() {
  try { await post('/shutdown'); connected.value = false; notice.value = '本地后台已退出。可以关闭此窗口。' } catch (e) { fail(e) }
}
function keydown(event) { if (event.key === 'Escape') { importOpen.value = false; preview.value = null; confirmDelete.value = null } }
onMounted(() => { start(); window.addEventListener('keydown', keydown) })
onUnmounted(() => { alive.value = false; window.removeEventListener('keydown', keydown) })
</script>

<template>
  <div class="app-shell">
    <aside class="sidebar">
      <a class="brand" href="#" @click.prevent="page = 'workspace'; tab = 'overview'"><span class="brand-mark">R<span>·</span></span><span>ReviewDesk<small>代码阅读工作台</small></span></a>
      <button class="button primary import-button" :disabled="!connected || busy" @click="importOpen = true"><span>＋</span> 导入 Python 项目</button>
      <div class="sidebar-label">我的代码知识库 <span>{{ projects.length }}</span></div>
      <div class="project-list">
        <div v-for="project in projects" :key="project.id" class="project-row" :class="{ active: revision?.project_id === project.id && page === 'workspace' }"><button class="project-select" :disabled="busy" @click="openRevision(project.latest_revision_id)"><span class="project-icon">▱</span><span><strong>{{ project.name }}</strong><small>{{ project.revision_count || 1 }} 个版本 · Python</small></span></button><button class="project-delete" :disabled="busy" :aria-label="`删除项目 ${project.name}`" @click="confirmDelete = project">×</button></div>
        <p v-if="!projects.length" class="sidebar-empty">还没有项目。<br>导入代码后，版本与阅读资料会保存在本机。</p>
      </div>
      <button class="sidebar-action" :disabled="busy || !connected" @click="demo"><span>◇</span> 打开示例项目 <small>免费</small></button>
      <div class="sidebar-guide"><span class="guide-icon">i</span><div><strong>先看全貌，再读细节</strong><p>从入口出发，跟随函数调用，理解变量如何改变。</p></div></div>
      <div class="sidebar-bottom"><button class="sidebar-action" :class="{ current: page === 'settings' }" @click="page = 'settings'"><span>⚙</span> 模型与设置 <i class="status-dot" :class="{ ready: hasKey }"></i></button><div class="local-status"><span class="status-dot" :class="{ ready: connected }"></span>{{ connected ? '本地服务已连接' : '本地服务未连接' }}<small>v{{ bootstrap.version || '0.2' }}</small></div></div>
    </aside>

    <main class="main-shell">
      <header class="topbar"><div class="breadcrumb">工作台 <span>/</span> {{ page === 'settings' ? '模型与设置' : revision?.project_name || '开始阅读' }} <template v-if="revision && page !== 'settings'"><span>/</span><strong>{{ activeTab }}</strong></template></div><div class="top-actions"><span class="pill"><span class="status-dot ready"></span> Python 首发</span><button class="button subtle" @click="page = page === 'settings' ? 'workspace' : 'settings'">{{ page === 'settings' ? '返回工作台' : '设置' }}</button></div></header>
      <div v-if="error" class="notice error global-notice" role="alert"><span>{{ error }}</span><button v-if="!connected" class="text-button" @click="start">重新连接</button><button aria-label="关闭错误提示" @click="error = ''">×</button></div>
      <div v-if="notice" class="notice success global-notice" role="status"><span>{{ notice }}</span><button aria-label="关闭提示" @click="notice = ''">×</button></div>
      <div v-if="job" class="task-progress" role="status"><div><span class="spinner"></span><strong>{{ job.message || '正在分析代码…' }}</strong><small>{{ jobPercent }}%</small></div><progress :value="jobPercent" max="100"></progress></div>
      <JobHistory v-if="connected" :jobs="recentJobs" :busy="jobsBusy" :error="jobsError" @refresh="refreshJobs"/>

      <div v-if="page === 'settings'" class="content settings-page">
        <div class="page-title"><span class="eyebrow">YOUR LOCAL WORKSPACE</span><h1>模型与设置</h1><p>本地分析帮助你读懂结构；大模型结合代码证据，把解释写得更完整。</p></div>
        <section class="card settings-card"><div class="card-heading"><div><h2>DeepSeek API</h2><p>只在你确认发送范围后调用。导入、调用图、变量分析和本地说明书不消耗 API 额度。</p></div><span class="badge" :class="{ green: hasKey }">{{ hasKey ? '已配置密钥' : '尚未配置' }}</span></div><label>API Key<input v-model="apiKey" type="password" placeholder="输入 DeepSeek API Key" autocomplete="off" spellcheck="false"></label><label class="checkbox-label"><input v-model="rememberKey" type="checkbox">使用 Windows 安全凭据存储；取消勾选仅当前会话使用</label><div class="button-row"><button class="button primary" :disabled="settingsBusy || !connected" @click="saveKey">保存密钥</button><button v-if="hasKey" class="button" :disabled="settingsBusy" @click="deleteKey">移除密钥</button></div></section>
        <section class="card settings-card"><h2>分析与存储</h2><div class="setting-fact"><span>当前支持语言</span><strong>Python（通过语言适配器保留扩展入口）</strong></div><div class="setting-fact"><span>项目知识库</span><strong>本机 SQLite · 按项目和代码版本隔离</strong></div><div class="setting-fact"><span>变量与调用关系</span><strong>静态分析，不执行导入的代码</strong></div><div class="setting-fact"><span>说明书自动文档</span><strong>超过 5000 个非空白字符，自动保存 Markdown</strong></div><div class="setting-fact"><span>前端运行方式</span><strong>本地 Vue 工作台，无在线 CDN 依赖</strong></div><button class="button" :disabled="busy || !connected" @click="quit">退出本地后台</button></section>
      </div>

      <div v-else-if="!revision" class="welcome content">
        <span class="eyebrow">FROM CODE TO UNDERSTANDING</span><h1>把陌生的代码，<br>读成清楚的说明书<span>。</span></h1><p class="welcome-subtitle">先看功能全貌，再跟随函数调用，最后理解变量变化。<br>从第一次打开项目，到说清它如何运行。</p><div class="button-row"><button class="button primary large" :disabled="busy || !connected" @click="importOpen = true">＋ 导入你的 Python 代码</button><button class="button large" :disabled="busy || !connected" @click="demo">先用示例体验 →</button></div>
        <div class="welcome-flow"><div><span>01</span><strong>看见全貌</strong><p>每个文件做什么，程序从哪里开始。</p></div><i>→</i><div><span>02</span><strong>跟随调用</strong><p>谁调用谁，输入怎样成为输出。</p></div><i>→</i><div><span>03</span><strong>读懂变化</strong><p>变量何时赋值，哪些条件改变结果。</p></div><i>→</i><div><span>04</span><strong>带走说明书</strong><p>可定位、可检索、可下载的中文解释。</p></div></div>
        <div class="welcome-foot"><span>✓ 源码保存在本机</span><span>✓ 不执行被审查代码</span><span>✓ 没有 API Key 也能开始</span></div>
      </div>

      <template v-else>
        <div class="project-heading"><div><span class="eyebrow">PYTHON READING ROOM</span><h1>{{ revision.project_name }} <span class="badge">Python</span></h1><p>{{ program.files?.length || 0 }} 个文件 · {{ functions.length }} 个函数 / 方法 · 当前版本 {{ String(revision.revision_id).slice(0, 8) }}</p></div><div class="button-row"><select :value="revision.revision_id" :disabled="busy" aria-label="切换代码版本" @change="openRevision($event.target.value)"><option v-for="(item, index) in revisions" :key="item.id || item.revision_id" :value="item.id || item.revision_id">{{ index === 0 ? '最新版本' : '历史版本' }} · {{ displayDate(item.created_at || item.created) }}</option></select><button class="button" :disabled="busy" @click="importOpen = true">导入新版本</button></div></div>
        <nav class="tabbar" aria-label="代码阅读功能"><button v-for="item in tabs" :key="item.id" :class="{ active: tab === item.id }" @click="tab = item.id"><span>{{ item.icon }}</span>{{ item.label }}</button></nav>
        <div class="workspace-content" :class="{ 'with-source': showSource && hasSourcePanel }">
          <div class="primary-content">
            <div v-if="tab === 'overview'" class="overview-page">
              <section class="overview-hero"><div><span class="eyebrow">先用一分钟，建立阅读地图</span><h2>先找到入口，再沿着调用读下去。</h2><p>下面的结论来自当前版本的 Python 源码。你可以对照代码逐步核实，动态行为会明确保留边界。</p><button class="button primary" @click="tab = 'manual'; if (!manual && !busy) generateManual(false)">生成完整中文说明书 <span>→</span></button></div><div class="hero-illustration" aria-hidden="true"><div class="mini-code">main.py <span>入口</span></div><div class="mini-connector"></div><div class="mini-branches"><div>读取输入</div><div>处理逻辑</div></div><div class="mini-connector"></div><div class="mini-code end">返回结果 <span>输出</span></div></div></section>
              <div class="metric-grid"><div class="metric-card"><span>代码文件</span><strong>{{ program.files?.length || 0 }}<small>个模块</small></strong><p>按职责理解项目组成</p></div><div class="metric-card"><span>函数与方法</span><strong>{{ functions.length }}<small>个代码单元</small></strong><p>输入、过程与返回结果</p></div><div class="metric-card"><span>项目内调用</span><strong>{{ program.calls?.filter(c => c.resolved).length || 0 }}<small>条关系</small></strong><p>可追溯到具体源码位置</p></div><div class="metric-card"><span>待核实风险</span><strong>{{ localFindings.length }}<small>项本地发现</small></strong><p>审查建议需要人工确认</p></div></div>
              <div class="overview-columns"><section class="card"><div class="card-heading"><div><span class="eyebrow">START HERE</span><h2>程序从哪里开始？</h2></div><button class="text-button" @click="tab = 'calls'">查看调用图 →</button></div><div v-if="!program.entry_points?.length" class="empty-small">未找到明确的启动入口。这个项目可能作为库被其他程序导入；先从文件的顶层代码与公开函数读起。</div><button v-for="(entry, index) in program.entry_points" :key="`${entry.path}:${entry.line}`" class="entry-item" @click="selectSymbol(symbols.find(s => s.id === entry.symbol_id), entry.line, true)"><span class="step-number">{{ String(index + 1).padStart(2, '0') }}</span><span><strong>{{ entry.path }} <small>L{{ entry.line }}</small></strong><p>{{ entry.description }}</p></span><span>↗</span></button></section><section class="card"><div class="card-heading"><div><span class="eyebrow">A GOOD READING ORDER</span><h2>建议这样读</h2></div></div><div class="reading-step"><span>1</span><div><strong>先知道每个模块的用途</strong><p>把文件名和职责对应起来，建立项目地图。</p></div></div><div class="reading-step"><span>2</span><div><strong>再看函数如何交接</strong><p>沿箭头查看调用关系，对照输入和返回值。</p></div></div><div class="reading-step"><span>3</span><div><strong>最后核对变量和分支</strong><p>看赋值位置与触发条件，不把可能路径当作实际执行。</p></div></div></section></div>
              <section class="card"><div class="card-heading"><div><span class="eyebrow">PROJECT MAP</span><h2>每个模块，负责哪件事？</h2></div><button class="text-button" @click="tab = 'symbols'">深入阅读 →</button></div><div class="module-grid"><button v-for="file in program.files" :key="file.path" class="module-card" @click="symbolFile = file.path; tab = 'symbols'; selectSymbol(symbols.find(s => s.path === file.path && s.kind === 'module'))"><span class="file-dot">py</span><strong>{{ file.path }}</strong><p>{{ file.purpose || '请结合模块内的函数、导入和顶层步骤理解用途。' }}</p><small>{{ file.line_count }} 行源码 <span>打开模块 →</span></small></button></div></section>
              <section v-if="localFindings.length" class="card"><div class="card-heading"><h2>阅读时需要留意的地方</h2><span class="badge amber">本地审查建议</span></div><FindingCard v-for="(finding, index) in localFindings" :key="index" :finding="finding" @locate="(path, start, end) => { tab = 'symbols'; locate(path, start, end) }"/></section>
              <details class="card boundaries" open><summary>这份分析能确定什么，不能确定什么？</summary><ul><li v-for="(item, index) in program.limitations" :key="index">{{ item }}</li><li>代码结构是静态读取的结果；运行时输入、真实数值和实际经过的分支，需要测试或调试才能确定。</li></ul></details>
            </div>

            <template v-if="tab === 'calls'"><CallGraph :program="program" :selected="selectedId" @select="selectSymbol"/><section v-if="selectedSymbol" class="card compact-details"><div class="card-heading"><h2>{{ selectedSymbol.qualified_name || selectedSymbol.name }}</h2><button class="text-button" @click="tab = 'symbols'">阅读完整函数说明 →</button></div><p>{{ selectedSymbol.purpose }}</p><code>{{ selectedSymbol.signature }}</code><div class="call-summary"><span>调用了 {{ selectedCalls.length }} 处</span><span>被项目内 {{ callers.length }} 处调用</span><button class="text-button" @click="locate(selectedSymbol.path, selectedSymbol.start_line, selectedSymbol.end_line)">定位源码 L{{ selectedSymbol.start_line }}</button></div></section><section v-else class="card empty-small">点击图中的任意模块或函数，右侧会打开对应源码，下方会显示它的职责与调用情况。</section></template>

            <template v-if="tab === 'symbols'">
              <div class="section-intro"><h2>把一个函数，拆开来读</h2><p>先看它要解决什么，再看接收什么、经过哪些步骤、最终交出什么。</p></div><div class="filter-row"><input v-model="symbolQuery" type="search" placeholder="搜索函数、名称或用途" aria-label="搜索模块与函数"><select v-model="symbolFile" aria-label="筛选函数文件"><option value="">全部文件</option><option v-for="file in program.files" :key="file.path">{{ file.path }}</option></select></div>
              <div class="symbol-picker"><button v-for="symbol in filteredSymbols" :key="symbol.id" :class="{ active: selectedId === symbol.id }" @click="selectSymbol(symbol)"><span class="symbol-kind">{{ symbol.kind === 'module' ? 'M' : symbol.kind === 'class' ? 'C' : 'ƒ' }}</span><span>{{ symbol.qualified_name || symbol.name }}<small>{{ symbol.path }} · L{{ symbol.start_line }}</small></span></button><p v-if="!filteredSymbols.length" class="empty-small">没有符合条件的代码单元。试试文件名或更短的关键词。</p></div>
              <section v-if="selectedSymbol" class="card symbol-details"><div class="card-heading"><div><span class="eyebrow">{{ kindName(selectedSymbol.kind) }} · {{ selectedSymbol.path }}</span><h2>{{ selectedSymbol.qualified_name || selectedSymbol.name }}</h2></div><button class="text-button" @click="locate(selectedSymbol.path, selectedSymbol.start_line, selectedSymbol.end_line)">L{{ selectedSymbol.start_line }}–{{ selectedSymbol.end_line }} ↗</button></div><p class="purpose-text">{{ selectedSymbol.purpose }}</p><pre class="signature">{{ selectedSymbol.signature }}</pre><blockquote v-if="selectedSymbol.docstring">{{ selectedSymbol.docstring }}</blockquote>
                <h3><span class="section-number">01</span> 接收什么输入</h3><div v-if="selectedSymbol.parameters?.length" class="table-scroll"><table><thead><tr><th>参数</th><th>类型 / 默认值</th><th>如何理解</th></tr></thead><tbody><tr v-for="parameter in selectedSymbol.parameters" :key="parameter.name"><td><code>{{ parameter.name }}</code></td><td>{{ parameter.annotation || '未注明类型' }}<small v-if="parameter.default != null">默认：{{ parameter.default }}</small></td><td>{{ parameter.description }}</td></tr></tbody></table></div><p v-else class="muted">没有显式形参；仍可能读取模块变量、外部文件或对象状态，请结合下方步骤判断。</p>
                <h3><span class="section-number">02</span> 内部怎样一步步处理</h3><ol class="step-timeline"><li v-for="(step, index) in selectedSymbol.steps" :key="index"><button class="line-link" @click="locate(selectedSymbol.path, step.line)">L{{ step.line }}</button><div><p>{{ step.text }}</p><span v-if="step.condition" class="condition">条件：{{ step.condition }}</span></div></li></ol><p v-if="!selectedSymbol.steps?.length" class="muted">没有可展开的处理步骤。</p>
                <h3><span class="section-number">03</span> 调用了谁，又由谁调用</h3><div class="calls-table"><div v-for="call in selectedCalls" :key="call.id"><span class="badge" :class="{ green: call.resolved }">{{ call.resolved ? '项目内' : '目标未确定' }}</span><button v-if="call.resolved" class="text-button" @click="selectSymbol(symbols.find(s => s.id === call.target))">{{ call.callee }} →</button><code v-else>{{ call.callee }}</code><button class="line-link" @click="locate(call.path, call.line)">L{{ call.line }}</button><p>{{ call.description }}</p></div><p v-if="!selectedCalls.length" class="muted">当前代码单元没有发现直接函数调用。</p></div><div v-if="callers.length" class="caller-list"><span>调用它的地方：</span><button v-for="call in callers" :key="call.id" class="text-button" @click="selectSymbol(symbols.find(s => s.id === call.source), call.line)">{{ nameOf(call.source) }} · L{{ call.line }}</button></div>
                <h3><span class="section-number">04</span> 最后返回什么</h3><div v-for="(item, index) in selectedSymbol.returns" :key="index" class="return-item"><button class="line-link" @click="locate(selectedSymbol.path, item.line)">L{{ item.line }}</button><div><code>{{ item.expression || 'None' }}</code><p>{{ item.description }}</p><span v-if="item.condition" class="condition">条件：{{ item.condition }}</span></div></div><p v-if="!selectedSymbol.returns?.length" class="muted">没有显式 return。普通函数执行到末尾会隐式返回 None；模块和类定义不按普通函数返回值理解。</p>
                <button class="button" @click="variableScope = selectedSymbol.id; tab = 'variables'">继续看这个代码单元的变量变化 →</button>
              </section><div v-else class="card empty-large"><span>ƒ</span><h3>选择一个模块或函数</h3><p>它的用途、输入、处理步骤、调用关系和返回值会在这里展开。</p></div>
            </template>

            <template v-if="tab === 'variables'"><div class="section-intro"><h2>变量，是怎样一步步改变的？</h2><p>这里展示赋值表达式与触发条件，帮助你追踪数据。它不是执行日志，也不显示猜测的运行时数值。</p></div><div class="notice neutral">同一变量可能在不同分支中改变。下列顺序依据源码行号，实际执行顺序和次数取决于输入、循环与调用。</div><div class="filter-row"><input v-model="variableQuery" type="search" placeholder="搜索变量名或所属函数" aria-label="搜索变量"><select v-model="variableScope" aria-label="筛选变量作用域"><option value="">全部作用域</option><option v-for="symbol in symbols.filter(s => s.variables?.length)" :key="symbol.id" :value="symbol.id">{{ symbol.qualified_name || symbol.name }} · {{ symbol.path }}</option></select></div><section v-for="(variable, index) in variables" :key="`${variable.symbol.id}:${variable.name}:${index}`" class="card variable-card"><div class="card-heading"><div><span class="eyebrow">{{ variable.symbol.path }} · {{ variable.scope || variable.symbol.qualified_name }}</span><h2><span class="variable-mark">x</span>{{ variable.name }}</h2></div><button class="text-button" @click="selectSymbol(variable.symbol, null, true)">所在{{ kindName(variable.symbol.kind) }} ↗</button></div><ol class="variable-timeline"><li v-for="(event, eventIndex) in variable.events" :key="eventIndex"><span class="timeline-dot"></span><div class="event-heading"><span class="badge">{{ eventName(event.kind) }}</span><button class="line-link" @click="selectSymbol(variable.symbol, event.line)">L{{ event.line }}</button></div><code>{{ event.expression }}</code><p>{{ event.explanation }}</p><span v-if="event.condition" class="condition">只在此条件下：{{ event.condition }}</span></li></ol></section><p v-if="!variables.length" class="card empty-large">没有符合条件的变量记录。可以清空筛选，或选择包含赋值语句的函数。</p></template>

            <template v-if="tab === 'manual'"><div class="section-intro"><h2>一份可以慢慢读的代码说明书</h2><p>包含模块职责、运行入口、函数输入与输出、调用关系、变量变化，以及需要核实的边界。</p></div><section class="card manual-controls"><label class="focus-label">本次重点关注什么？<input v-model="manualFocus" placeholder="例如：从 main 开始讲清订单总价如何计算，说明异常分支"></label><div class="manual-options"><label>模型<select v-model="model"><option v-for="item in models" :key="item.id" :value="item.id">{{ item.name }}</option></select></label><label>费用上限（元）<input v-model="maxCost" type="number" min="0.01" max="5" step="0.01"></label><label>上下文预算<input v-model="budget" type="number" min="2000" max="64000" step="1000"></label></div><div class="button-row"><button class="button primary" :disabled="busy" @click="generateManual(false)">生成本地完整说明 <small>免费</small></button><button class="button" :disabled="busy" @click="previewAi">DeepSeek 深入讲解 · 先预览</button><select v-if="manuals.length" :value="manual?.id" aria-label="切换已生成的说明书" @change="loadManual($event.target.value).catch(fail)"><option v-for="item in manuals" :key="item.id" :value="item.id">{{ item.mode === 'ai' || item.mode === 'deepseek' ? '模型说明' : '本地说明' }} · {{ displayDate(item.created_at || item.created) }}</option></select></div><p class="field-help">本地说明基于静态证据生成。DeepSeek 只接收预览列出的代码范围；长说明超过 5000 个非空白字符时自动生成 Markdown 文档。</p></section><ManualViewer v-if="manual" :manual="manual" :token="token" :export-busy="exportBusy || busy" @locate="locateManual" @retry-export="retryManualExport"/><div v-else class="card empty-large"><span>▤</span><h3>让代码逻辑，成为可交接的文档</h3><p>点击“生成本地完整说明”即可开始，无需配置密钥。<br>模型讲解会在你确认发送范围之后生成。</p></div></template>

            <template v-if="tab === 'knowledge'"><div class="section-intro"><h2>带着问题，在代码证据里找答案</h2><p>检索范围固定在当前项目的当前版本，结果保留文件与行号，方便回到源码核实。</p></div><form class="card search-form" @submit.prevent="searchKnowledge"><label>你想了解什么？<div class="search-input-row"><input v-model="query" type="search" placeholder="例如：订单金额、异常处理、calculate_total" aria-label="知识库查询"><button class="button primary" :disabled="searchBusy || !query.trim()">{{ searchBusy ? '检索中…' : '检索代码证据' }}</button></div></label><div class="search-options"><label><input v-model="strategy" type="radio" value="hybrid"> 组合检索</label><label><input v-model="strategy" type="radio" value="keyword"> 关键词检索</label><span>本地检索不消耗模型额度</span></div></form><p class="inline-note">这是代码证据检索，不是模型回答。组合检索按后端提供的符号、关键词或相似度信号排序，具体依据见每条结果。</p><div v-if="searchResults !== null" class="results-heading">找到 {{ searchResults.length }} 条相关代码片段</div><section v-for="(result, index) in searchResults || []" :key="result.id" class="card search-result"><div class="card-heading"><div><span class="eyebrow">证据 {{ String(index + 1).padStart(2, '0') }} · {{ result.path }}</span><h3>{{ result.name }}</h3></div><button class="text-button" @click="locate(result.path, result.start_line, result.end_line)">L{{ result.start_line }}–{{ result.end_line }} ↗</button></div><p class="retrieval-reason">{{ typeof result.reason === 'string' ? result.reason : JSON.stringify(result.reason) }} <small>相关性得分 {{ typeof result.score === 'number' ? result.score.toFixed(3) : result.score }}</small></p><pre class="snippet">{{ result.content }}</pre><button class="text-button" @click="manualFocus = query; tab = 'manual'">围绕这个问题生成说明书 →</button></section><div v-if="searchResults?.length === 0" class="card empty-large"><h3>当前版本没有找到相关片段</h3><p>试试代码中的函数名、变量名或更具体的关键词。中文描述与英文标识符不一定能直接对应。</p></div></template>

            <template v-if="tab === 'history'"><div class="section-intro"><h2>同一个项目，不同版本发生了什么？</h2><p>每次导入保存独立的代码快照。相同名称与相同内容会复用版本，不修改你原来的文件。</p></div><section class="card"><div class="compare-controls"><label>之前的版本<select v-model="baseRevision"><option value="">选择对比基准</option><option v-for="item in revisions.filter(r => (r.id || r.revision_id) !== revision.revision_id)" :key="item.id || item.revision_id" :value="item.id || item.revision_id">{{ displayDate(item.created_at || item.created) }} · {{ String(item.id || item.revision_id).slice(0, 8) }}</option></select></label><span>→</span><div><label>当前查看版本</label><strong>{{ String(revision.revision_id).slice(0, 8) }}</strong></div><button class="button primary" :disabled="!baseRevision || compareBusy" @click="compare">{{ compareBusy ? '对比中…' : '查看代码变化' }}</button></div><p v-if="revisions.length < 2" class="field-help">这个项目还只有一个版本。使用相同项目名称导入修改后的代码，就可以在这里比较。</p></section><div v-if="comparison" class="results-heading">{{ comparison.files?.length || 0 }} 个文件记录</div><section v-for="file in comparison?.files || []" :key="file.path" class="card diff-card"><div class="card-heading"><h3>{{ file.path }}</h3><span class="badge" :class="{ green: ['added', '新增'].includes(file.status), amber: ['modified', '修改'].includes(file.status) }">{{ ({ added: '新增', deleted: '删除', removed: '删除', modified: '修改', unchanged: '未变化' })[file.status] || file.status }}</span></div><pre class="diff-code"><span v-for="(line, index) in (file.diff || '').split('\n')" :key="index" :class="{ addition: line.startsWith('+'), deletion: line.startsWith('-'), hunk: line.startsWith('@@') }">{{ line || ' ' }}
</span></pre></section><div v-if="comparison && !comparison.files?.length" class="card empty-large">这两个版本没有文件差异。</div></template>
          </div>
          <aside v-if="hasSourcePanel" class="source-panel" :class="{ collapsed: !showSource }"><div class="source-panel-top"><strong>{{ tab === 'manual' ? '对照说明书版本源码' : '对照原始代码' }}</strong><button class="text-button" @click="showSource = !showSource">{{ showSource ? '收起' : '展开' }}</button></div><template v-if="showSource"><p v-if="tab === 'manual' && manual" class="source-version">报告版本 {{ String(manual.revision_id || '未知').slice(0, 8) }}<span v-if="manual.revision_id !== revision.revision_id"> · 与当前项目版本不同</span></p><select v-model="sourcePath" aria-label="选择源码文件" @change="sourceLine = 0; sourceEnd = 0"><option v-for="source in sourceRevision?.sources || []" :key="source.path" :value="source.path">{{ source.path }}</option></select><SourceView :source="selectedSource" :line="sourceLine" :end-line="sourceEnd"/><p class="source-footer">只读视图 · 不执行或修改导入的代码</p></template></aside>
        </div>
      </template>
    </main>

    <div v-if="importOpen" class="modal-backdrop" @click.self="!busy && (importOpen = false)"><section class="modal import-modal" role="dialog" aria-modal="true" aria-labelledby="import-title"><div class="modal-header"><div><span class="eyebrow">START WITH YOUR CODE</span><h2 id="import-title">导入 Python 项目</h2></div><button class="icon-button" aria-label="关闭导入窗口" :disabled="busy" @click="importOpen = false">×</button></div><p>代码会保存为本地版本，用于调用分析、知识检索和说明书生成。</p><label>项目名称<input v-model="projectName" placeholder="为这个项目取一个名字"></label><div class="segmented"><button :class="{ active: importMode === 'upload' }" @click="importMode = 'upload'">选择文件 / 文件夹</button><button :class="{ active: importMode === 'folder' }" @click="importMode = 'folder'">本机目录路径</button><button :class="{ active: importMode === 'paste' }" @click="importMode = 'paste'">粘贴源码</button></div><template v-if="importMode === 'upload'"><div class="upload-zone"><span>↥</span><strong>选择需要读懂的 Python 代码</strong><p>支持多个 .py 文件，或选择整个项目文件夹</p><div class="button-row"><label class="button primary file-input">选择文件<input type="file" accept=".py" multiple @change="readFiles"></label><label class="button file-input">选择文件夹<input type="file" webkitdirectory multiple @change="readFiles"></label></div></div><div v-if="uploadFiles.length" class="upload-list"><strong>已选择 {{ uploadFiles.length }} 个 Python 文件</strong><p v-for="file in uploadFiles.slice(0, 20)" :key="file.path">{{ file.path }}</p><small v-if="uploadFiles.length > 20">以及另外 {{ uploadFiles.length - 20 }} 个文件</small></div></template><template v-if="importMode === 'folder'"><label>本机目录的完整路径<input v-model="folderPath" placeholder="例如 D:\我的项目\订单计算"></label><p class="field-help">由本机后台读取该目录中的 Python 文件。请选项目源码目录，避免整个磁盘或开发环境目录。</p></template><template v-if="importMode === 'paste'"><label>文件名<input v-model="pastedPath" placeholder="main.py"></label><label>Python 源码<textarea v-model="pastedCode" class="code-textarea" rows="12" placeholder="在这里粘贴需要分析的 Python 代码" spellcheck="false"></textarea></label></template><p v-if="error" class="notice error" role="alert">{{ error }}</p><div class="modal-footer"><small>静态读取，不执行代码 · 暂仅支持 Python</small><button class="button primary" :disabled="busy" @click="submitImport">{{ busy ? '正在导入…' : '导入并建立阅读地图 →' }}</button></div></section></div>

    <div v-if="preview" class="modal-backdrop" @click.self="preview = null"><section class="modal preview-modal" role="dialog" aria-modal="true" aria-labelledby="preview-title"><div class="modal-header"><div><span class="eyebrow">REVIEW BEFORE SENDING</span><h2 id="preview-title">确认本次发送范围</h2></div><button class="icon-button" aria-label="关闭发送预览" @click="preview = null">×</button></div><p>确认后，下面的代码上下文会发送到 DeepSeek，用来生成详细中文说明。</p><div class="preview-metrics"><div><span>本次费用估算</span><strong>¥{{ preview.estimated_cost_cny ?? '未知' }}</strong></div><div><span>预计 token</span><strong>{{ typeof preview.estimated_tokens === 'object' ? JSON.stringify(preview.estimated_tokens) : preview.estimated_tokens ?? '未知' }}</strong></div><div><span>费用上限</span><strong>¥{{ maxCost }}</strong></div></div><p class="field-help">上面是请求前估算，实际 token 以 API 返回用量为准；模型价格与计算细节由后台记录。</p><h3>涉及文件</h3><div class="preview-files"><span v-for="(file, index) in preview.files || []" :key="index">{{ typeof file === 'string' ? file : file.path || file.name }}</span></div><details open><summary>查看实际发送的代码上下文</summary><pre class="preview-context">{{ typeof preview.context === 'string' ? preview.context : JSON.stringify(preview.context, null, 2) }}</pre></details><ul v-if="preview.limitations?.length" class="preview-limits"><li v-for="(limit, index) in preview.limitations" :key="index">{{ limit }}</li></ul><div class="modal-footer"><button class="button" @click="preview = null">返回调整</button><button class="button primary" :disabled="busy" @click="generateManual(true)">确认范围并发送给 DeepSeek</button></div></section></div>

    <div v-if="confirmDelete" class="modal-backdrop" @click.self="confirmDelete = null"><section class="modal small-modal" role="dialog" aria-modal="true" aria-labelledby="delete-title"><h2 id="delete-title">删除“{{ confirmDelete.name }}”？</h2><p>会删除本地知识库中这个项目的版本、代码快照和说明书。原始项目目录里的文件不会修改。</p><div class="modal-footer"><button class="button" @click="confirmDelete = null">保留项目</button><button class="button danger" @click="removeProject">删除本地记录</button></div></section></div>
  </div>
</template>
