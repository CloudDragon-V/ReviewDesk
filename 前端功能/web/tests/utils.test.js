import test from 'node:test'
import assert from 'node:assert/strict'
import { renderManual, matchSymbol, layoutGraph, tokenizePython, characterCount, safeDownloadUrl, paginateManual, describeUsage, parseSourceReference, resolveManualSource, sourceLines, describeJobUsage } from '../src/utils.js'

test('untrusted report HTML, scripts, image requests and dangerous links remain inert', () => {
  const output = renderManual('# 说明\n<script>alert(1)</script>\n<img src=x onerror=alert(1)>\n[危险](javascript:alert(1))\n![隐私](https://example.com/collect)')
  assert.ok(!output.html.includes('<script'))
  assert.ok(!output.html.includes('<img'))
  assert.ok(!output.html.includes('href="javascript:'))
  assert.ok(output.html.includes('&lt;script&gt;'))
  assert.ok(output.html.includes('[图片：隐私]'))
})

test('TOC ids are generated locally and duplicate titles remain distinct', () => {
  const { html, headings } = renderManual('# 总览\n## 输入\n## 输入')
  assert.deepEqual(headings.map(h => h.id), ['manual-section-0', 'manual-section-1', 'manual-section-2'])
  assert.ok(html.includes('id="manual-section-2"'))
})

test('symbol filtering combines text and file scope', () => {
  const symbol = { name: 'calculate', qualified_name: 'Order.calculate', path: 'order.py', purpose: '计算金额' }
  assert.equal(matchSymbol(symbol, '金额', 'order.py'), true)
  assert.equal(matchSymbol(symbol, 'CALCULATE'), true)
  assert.equal(matchSymbol(symbol, '金额', 'other.py'), false)
})

test('call graph never draws an unresolved or missing target as a known relation', () => {
  const symbols = [{ id: 'a', name: 'main', path: 'main.py' }, { id: 'b', name: 'calculate', path: 'calc.py' }]
  const calls = [{ id: '1', source: 'a', target: 'b', resolved: true }, { id: '2', source: 'a', target: 'b', resolved: false }, { id: '3', source: 'a', target: 'missing', resolved: true }]
  const graph = layoutGraph(symbols, calls)
  assert.equal(graph.edges.length, 1)
  assert.equal(graph.nodes.length, 2)
  assert.ok(graph.nodes[0].x < graph.nodes[1].x)
  assert.equal(layoutGraph(symbols, calls, '', 'main.py').edges.length, 0)
})

test('graph bounding reports hidden nodes rather than silently dropping them', () => {
  const graph = layoutGraph(Array.from({ length: 125 }, (_, i) => ({ id: `${i}`, name: `f${i}`, path: 'a.py' })), [])
  assert.equal(graph.nodes.length, 120)
  assert.equal(graph.hidden, 5)
})

test('Python tokenization preserves exact source text including HTML characters', () => {
  const input = '    return "<script>" + value  # 说明'
  const tokens = tokenizePython(input)
  assert.equal(tokens.map(t => t.text).join(''), input)
  assert.equal(tokens.find(t => t.text === 'return').kind, 'keyword')
})

test('count includes unicode codepoints, excludes whitespace and is strict above 5000', () => {
  assert.equal(characterCount(' 中 文\n🐍 '), 3)
  assert.equal(characterCount('文'.repeat(5001)), 5001)
})

test('download token can only be attached to the dedicated same-origin API route', () => {
  assert.equal(safeDownloadUrl('/api/manuals/abc/download', 'a&b'), '/api/manuals/abc/download?token=a%26b')
  assert.equal(safeDownloadUrl('https://evil.test/api/manuals/abc/download', 'secret'), '')
  assert.equal(safeDownloadUrl('//evil.test/api/manuals/abc/download', 'secret'), '')
  assert.equal(safeDownloadUrl('/api/manuals/abc/download?redirect=evil', 'secret'), '')
})

test('a million-character manual is paginated with bounded rendering and complete retained content', () => {
  const text = Array.from({ length: 20000 }, (_, i) => `变量 ${i} 的变化需要结合当前分支条件判断，不猜测真实的运行时数值。`).join('\n')
  const pages = paginateManual(text)
  assert.ok(pages.length > 5)
  assert.ok(pages.every(page => page.length <= 60000))
  assert.equal(pages.join('').trimEnd(), text)
  const codePages = paginateManual('```python\n' + 'x = 1\n'.repeat(100) + '```', 70)
  for (const page of codePages) assert.ok(!renderManual(page).html.includes('<script'))
})

test('usage displays actual tokens only with an API receipt and reads estimated_cost_cny', () => {
  assert.equal(describeUsage({ actual: false, prompt_tokens: 0, completion_tokens: 0, estimated_cost_cny: '0', note: '离线静态讲解，未调用模型 API。' }), '离线静态讲解，未调用模型 API。')
  assert.equal(describeUsage({ actual: false, prompt_tokens: 0 }).includes('输入 0'), false)
  assert.match(describeUsage({ actual: true, prompt_tokens: 120, completion_tokens: 40, estimated_cost_cny: '0.012' }), /输入 120 \/ 输出 40 token；费用估算 ¥0.012/)
  assert.match(describeUsage({}), /未知/)
})

test('source references validate decoded relative paths and integer line ranges', () => {
  assert.deepEqual(parseSourceReference('reviewdesk-source:%E6%A8%A1%E5%9D%97%2Fcalc.py?start=2&end=4'), { path: '模块/calc.py', start_line: 2, end_line: 4 })
  for (const href of [
    'https://example.com/main.py?start=1&end=2', 'reviewdesk-source:..%2Fmain.py?start=1&end=2',
    'reviewdesk-source:%2Fmain.py?start=1&end=2', 'reviewdesk-source:C%3A%5Cmain.py?start=1&end=2',
    'reviewdesk-source:a%2F.%2Fmain.py?start=1&end=2', 'reviewdesk-source:a%2F%2Fmain.py?start=1&end=2',
    'reviewdesk-source:%00main.py?start=1&end=2', 'reviewdesk-source:%ZZ?start=1&end=2',
    'reviewdesk-source:main.py?start=0&end=2', 'reviewdesk-source:main.py?start=3&end=2',
    'reviewdesk-source:main.py?start=1.5&end=2', 'reviewdesk-source:main.py?start=1&end=9007199254740992',
    'reviewdesk-source:main.py?start=1&end=2&revision=other', 'reviewdesk-source:main.py?start=1&end=2#fragment',
  ]) assert.equal(parseSourceReference(href), null, href)
})

test('only valid local references render source buttons; plain text and model-disabled links stay inert', () => {
  const { html } = renderManual('[源码](reviewdesk-source:main.py?start=2&end=3)\n[越界路径](reviewdesk-source:..%2Fsecret.py?start=1&end=2)\n[模型自造](reviewdesk-disabled:main.py?start=1&end=2)\n[E1] main.py:L1\n[网页](https://example.com/)')
  assert.equal((html.match(/data-source-path=/g) || []).length, 1)
  assert.match(html, /data-source-path="main.py" data-source-start="2" data-source-end="3"/)
  assert.ok(!html.includes('href="reviewdesk-'))
  assert.ok(html.includes('href="https://example.com/"'))
  assert.ok(!renderManual('`[源码](reviewdesk-source:main.py?start=1&end=1)`').html.includes('<button'))
  const escaped = renderManual('[源码](reviewdesk-source:a%22%20onclick%3D%22bad.py?start=1&end=1)').html
  assert.ok(!escaped.includes(' onclick="'))
})

test('old report source lookup uses its revision despite a current same-name file', async () => {
  const current = { revision_id: 'new', sources: [{ path: 'main.py', content: 'new-value' }] }
  const old = { revision_id: 'old', sources: [{ path: 'main.py', content: 'old-value\nreturn old-value' }] }
  const requested = []
  const resolved = await resolveManualSource({ revision_id: 'old', path: 'main.py', start_line: 2, end_line: 2 }, 'old', current, async id => { requested.push(id); return old })
  assert.deepEqual(requested, ['old'])
  assert.equal(resolved.snapshot.sources[0].content, old.sources[0].content)
  const same = await resolveManualSource({ revision_id: 'old', path: 'main.py', start_line: 1, end_line: 1 }, 'old', old, () => { throw Error('should reuse same snapshot') })
  assert.equal(same.snapshot, old)
})

test('missing, mismatched or invalid report evidence never falls back to current source', async () => {
  const reference = { revision_id: 'old', path: 'main.py', start_line: 1, end_line: 1 }
  const current = { revision_id: 'new', sources: [{ path: 'main.py', content: 'new-value' }] }
  await assert.rejects(resolveManualSource(reference, 'old', current, async () => { throw Error('old version missing') }), /old version missing/)
  await assert.rejects(resolveManualSource(reference, 'old', current, async () => current), /对应的源码版本/)
  await assert.rejects(resolveManualSource(reference, 'another-report', current, async () => current), /版本信息不一致/)
  await assert.rejects(resolveManualSource({ ...reference, end_line: 99 }, 'old', null, async () => ({ ...current, revision_id: 'old' })), /行号不在/)
  await assert.rejects(resolveManualSource({ ...reference, path: '../main.py' }, 'old', current, async () => current), /位置格式/)
})

test('source viewer and report locator use physical Python lines without a phantom trailing line', async () => {
  assert.deepEqual(sourceLines('a\r\nb\rc\n'), ['a', 'b', 'c'])
  assert.deepEqual(sourceLines(''), [])
  assert.deepEqual(sourceLines('a\u2028b\fc'), ['a\u2028b\fc'])
  const snapshot = { revision_id: 'old', sources: [{ path: 'main.py', content: 'x = 1\r' }] }
  await assert.rejects(resolveManualSource({ revision_id: 'old', path: 'main.py', start_line: 2, end_line: 2 }, 'old', snapshot), /行号不在/)
})

test('interrupted submissions distinguish unknown charges, real usage and requests not sent', () => {
  const unknown = describeJobUsage({ status: 'interrupted', request_status: 'submitted', usage: { actual: false, prompt_tokens: 0, estimated_cost_cny: '0' } })
  assert.match(unknown, /请先核对平台账单/)
  assert.ok(!unknown.includes('0'))
  assert.match(describeJobUsage({ request_status: 'outcome_unknown' }), /用量尚未确认/)
  assert.match(describeJobUsage({ request_status: 'not_sent' }), /尚未发送模型请求/)
  assert.match(describeJobUsage({ request_status: 'failed_with_usage', usage: { actual: true, prompt_tokens: 120, completion_tokens: 40, estimated_cost_cny: '0.012' } }), /输入 120 \/ 输出 40/)
})
