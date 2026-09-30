import MarkdownIt from 'markdown-it'

/** Match Python physical source lines, including CR files and no phantom final line. */
export function sourceLines(text = '') {
  const normalized = text.replace(/\r\n?/g, '\n')
  if (!normalized) return []
  const lines = normalized.split('\n')
  return normalized.endsWith('\n') ? lines.slice(0, -1) : lines
}

/** Only the backend's local source protocol can become a source locator. */
export function parseSourceReference(value) {
  if (typeof value !== 'string' || !value.startsWith('reviewdesk-source:')) return null
  const match = /^reviewdesk-source:([^?#]+)\?start=([1-9]\d*)&end=([1-9]\d*)$/.exec(value)
  if (!match) return null
  let path
  try { path = decodeURIComponent(match[1]) } catch { return null }
  if (!path || path.length > 500 || /[\\:\x00-\x1f\x7f]/u.test(path) || path.split('/').some(part => !part || part === '.' || part === '..')) return null
  const start_line = Number(match[2]), end_line = Number(match[3])
  if (!Number.isSafeInteger(start_line) || !Number.isSafeInteger(end_line) || end_line < start_line) return null
  return { path, start_line, end_line }
}

/** Resolve against the report's immutable revision, never the current version by accident. */
export async function resolveManualSource(reference, reportRevisionId, currentRevision, loadRevision) {
  if (!reportRevisionId || reference?.revision_id !== reportRevisionId) throw new Error('说明书版本信息不一致，请重新打开报告。')
  const location = parseSourceReference(`reviewdesk-source:${encodeURIComponent(reference.path || '')}?start=${reference.start_line}&end=${reference.end_line}`)
  if (!location) throw new Error('说明书的源码位置格式不正确。')
  const snapshot = currentRevision?.revision_id === reportRevisionId ? currentRevision : await loadRevision(reportRevisionId)
  if (snapshot?.revision_id !== reportRevisionId) throw new Error('无法取得这份说明书对应的源码版本。')
  const source = snapshot.sources?.find(item => item.path === location.path)
  if (!source || location.end_line > sourceLines(source.content).length) throw new Error('说明书引用的文件或行号不在该版本源码中。')
  return { snapshot, ...location }
}

/** Treat a report as text: raw HTML and automatic image loading are disabled. */
export function renderManual(text = '') {
  const md = new MarkdownIt({ html: false, linkify: false, typographer: false })
  const headings = []
  md.renderer.rules.heading_open = (tokens, index) => {
    const id = `manual-section-${headings.length}`
    const token = tokens[index]
    headings.push({ id, level: Number(token.tag.slice(1)), text: tokens[index + 1]?.content || '' })
    return `<${token.tag} id="${id}">`
  }
  md.renderer.rules.image = (tokens, index) => `[图片：${md.utils.escapeHtml(tokens[index].content || '已禁用自动加载')}]`
  const defaultLink = md.renderer.rules.link_open || ((tokens, i, options, env, self) => self.renderToken(tokens, i, options))
  const defaultClose = md.renderer.rules.link_close || ((tokens, i, options, env, self) => self.renderToken(tokens, i, options))
  md.renderer.rules.link_open = (tokens, i, options, env, self) => {
    const href = tokens[i].attrGet('href') || ''
    if (/^reviewdesk-/i.test(href)) {
      const location = parseSourceReference(href)
      const closing = tokens.find((token, index) => index > i && token.type === 'link_close')
      if (closing) closing.meta = { sourceTag: location ? 'button' : 'span' }
      if (!location) return '<span>'
      return `<button type="button" class="source-reference" data-source-path="${md.utils.escapeHtml(location.path)}" data-source-start="${location.start_line}" data-source-end="${location.end_line}" title="查看这份说明书对应版本的源码">`
    }
    tokens[i].attrSet('rel', 'noopener noreferrer')
    tokens[i].attrSet('target', '_blank')
    return defaultLink(tokens, i, options, env, self)
  }
  md.renderer.rules.link_close = (tokens, i, options, env, self) => tokens[i].meta?.sourceTag ? `</${tokens[i].meta.sourceTag}>` : defaultClose(tokens, i, options, env, self)
  return { html: md.render(String(text)), headings }
}

export function matchSymbol(symbol, query = '', path = '') {
  const needle = query.trim().toLowerCase()
  return (!path || symbol.path === path) && (!needle || [symbol.name, symbol.qualified_name, symbol.path, symbol.purpose, symbol.signature].join(' ').toLowerCase().includes(needle))
}

/** A deterministic, bounded column layout grouped by file; never claims execution order. */
export function layoutGraph(symbols = [], calls = [], query = '', path = '') {
  const matched = symbols.filter(s => matchSymbol(s, query, path))
  const visible = matched.slice(0, 120)
  const columns = [...new Set(visible.map(s => s.path))]
  const counts = new Map()
  const nodes = visible.map(s => {
    const row = counts.get(s.path) || 0
    counts.set(s.path, row + 1)
    return { ...s, x: 28 + columns.indexOf(s.path) * 310, y: 64 + row * 102, width: 258, height: 66 }
  })
  const nodeMap = new Map(nodes.map(n => [n.id, n]))
  const edges = calls.filter(c => c.resolved && nodeMap.has(c.source) && nodeMap.has(c.target)).map(c => ({ ...c, from: nodeMap.get(c.source), to: nodeMap.get(c.target) }))
  return { nodes, edges, columns, width: Math.max(620, columns.length * 310 + 30), height: Math.max(360, Math.max(0, ...counts.values()) * 102 + 85), hidden: Math.max(0, matched.length - visible.length) }
}

/** Lightweight token colors with Vue text nodes: code never becomes executable HTML. */
export function tokenizePython(line = '') {
  const pattern = /(#.*$)|("(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*')|\b(def|class|return|if|elif|else|for|while|in|import|from|as|try|except|finally|raise|with|yield|async|await|lambda|and|or|not|is|None|True|False|pass|break|continue|global|nonlocal)\b|\b(\d+(?:\.\d+)?)\b/g
  const tokens = []
  let cursor = 0
  for (const match of line.matchAll(pattern)) {
    if (match.index > cursor) tokens.push({ text: line.slice(cursor, match.index), kind: 'plain' })
    tokens.push({ text: match[0], kind: match[1] ? 'comment' : match[2] ? 'string' : match[3] ? 'keyword' : 'number' })
    cursor = match.index + match[0].length
  }
  if (cursor < line.length) tokens.push({ text: line.slice(cursor), kind: 'plain' })
  return tokens
}

export function characterCount(text = '') { return [...text.replace(/\s/gu, '')].length }
/** Unknown or local usage must never be rendered as an actual zero-token API receipt. */
export function describeUsage(usage = {}) {
  if (usage.actual !== true) return usage.note || '未获得 API 返回的实际用量，费用未知。'
  const tokens = `API 实际用量：输入 ${usage.prompt_tokens ?? '未知'} / 输出 ${usage.completion_tokens ?? '未知'} token`
  const cost = usage.estimated_cost_cny != null ? `；费用估算 ¥${usage.estimated_cost_cny}，以平台账单为准。` : '；费用估算未知。'
  return tokens + cost
}
/** A persisted submission with no receipt is an unknown charge, never a free run. */
export function describeJobUsage(job = {}) {
  if (job.usage?.actual === true) return describeUsage(job.usage)
  if (['submitted', 'outcome_unknown'].includes(job.request_status)) return '模型请求已提交，结果或用量尚未确认；请先核对平台账单，不会自动重试。'
  if (job.request_status === 'not_sent') return '本次尚未发送模型请求。'
  return job.usage?.note || '未取得可核验的调用用量；不能按零费用理解。'
}
/** Limit each rendered page while retaining the complete report for navigation/download. */
export function paginateManual(text = '', limit = 60000) {
  if (text.length <= limit) return [text]
  const pages = []
  let current = '', fence = ''
  for (const rawLine of text.split('\n')) {
    const line = rawLine + '\n'
    const match = /^\s*(`{3,}|~{3,})(.*)$/.exec(rawLine)
    if (current.length + line.length > limit && current.trim()) {
      pages.push(current + (fence ? `\n${fence}\n` : ''))
      current = fence ? `${fence}\n` : ''
    }
    if (match) {
      if (!fence) fence = match[1]
      else if (match[1][0] === fence[0] && match[1].length >= fence.length && !match[2].trim()) fence = ''
    }
    // An abnormally long single line is split as inert text rather than building an unbounded DOM node.
    if (line.length > limit) {
      for (let start = 0; start < line.length; start += limit) {
        const part = line.slice(start, start + limit)
        if (current.length + part.length > limit && current.trim()) { pages.push(current); current = '' }
        current += part
      }
    } else current += line
  }
  if (current.trim()) pages.push(current)
  return pages
}
export function displayDate(value) {
  if (!value) return '已保存'
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? String(value) : date.toLocaleString('zh-CN', { hour12: false })
}
export function safeDownloadUrl(path, token) {
  if (typeof path !== 'string' || !/^\/api\/manuals\/[^/?#]+\/download$/.test(path)) return ''
  return `${path}?token=${encodeURIComponent(token)}`
}
