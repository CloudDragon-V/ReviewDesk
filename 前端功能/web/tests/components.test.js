import test from 'node:test'
import assert from 'node:assert/strict'
import fs from 'node:fs'
import { parse, compileScript } from '@vue/compiler-sfc'
import * as Vue from 'vue'
import * as VueSSR from 'vue/server-renderer'
import * as utils from '../src/utils.js'

// Compile the actual SFC templates, so field-name regressions cannot hide in fixtures.
function component(name, ssr = true) {
  const { descriptor } = parse(fs.readFileSync(new URL(`../src/components/${name}.vue`, import.meta.url), 'utf8'))
  let source = compileScript(descriptor, { id: name, inlineTemplate: ssr, templateOptions: { ssr } }).content
  source = source.replace(/import\s*\{([^}]+)\}\s*from\s*['"]([^'"]+)['"]/g, (_, bindings, module) => {
    const target = ({ vue: 'Vue', 'vue/server-renderer': 'VueSSR', '../utils.js': 'utils' })[module]
    assert.ok(target, `unsupported test import: ${module}`)
    return `const {${bindings.replace(/\s+as\s+/g, ':')}} = ${target}`
  }).replace('export default', 'return')
  return new Function('Vue', 'VueSSR', 'utils', source)(Vue, VueSSR, utils)
}

test('finding card renders the real backend evidence and recommendation contract safely', async () => {
  const finding = { path: 'calc.py', start_line: 3, end_line: 3, severity: 'medium', title: '空输入除零', evidence: 'return total / len(numbers) # <script>', recommendation: '先检查空列表并抛出 ValueError。' }
  const html = await VueSSR.renderToString(Vue.createSSRApp(component('FindingCard'), { finding }))
  assert.ok(html.includes('return total / len(numbers) # &lt;script&gt;'))
  assert.ok(html.includes('先检查空列表并抛出 ValueError。'))
  assert.ok(html.includes('calc.py:3'))
  assert.ok(!html.includes('<script>'))
})

test('report export failure keeps download and offers retry without claiming export succeeded', async () => {
  const manual = { id: 'report', revision_id: 'old', markdown: '[源码](reviewdesk-source:main.py?start=1&end=1)', character_count: 5001, export_status: 'failed', export_error: '文件正在占用', auto_exported: false }
  const html = await VueSSR.renderToString(Vue.createSSRApp(component('ManualViewer'), { manual, token: 'session' }))
  assert.ok(html.includes('说明书正文和用量已保存在本机数据库'))
  assert.ok(html.includes('文件正在占用'))
  assert.ok(html.includes('重试保存文件（不调用模型）'))
  assert.ok(html.includes('/api/manuals/report/download?token=session'))
  assert.ok(html.includes('data-source-path="main.py"'))
  assert.ok(!html.includes('后台已自动保存 Markdown 文档。'))
})

test('manual click emits the report revision and rejects references outside the rendered article', () => {
  const emitted = [], scope = Vue.effectScope()
  try {
    const viewer = scope.run(() => component('ManualViewer', false).setup(
      { manual: { id: 'report', revision_id: 'old-report-version', markdown: '' }, token: '' },
      { emit: (...args) => emitted.push(args), expose() {} }))
    const button = { dataset: { sourcePath: 'main.py', sourceStart: '2', sourceEnd: '3' } }
    viewer.locateSource({ target: { closest: () => button }, currentTarget: { contains: () => true } })
    assert.deepEqual(emitted, [['locate', { path: 'main.py', start_line: 2, end_line: 3, revision_id: 'old-report-version' }]])
    viewer.locateSource({ target: { closest: () => button }, currentTarget: { contains: () => false } })
    button.dataset.sourcePath = '../secret.py'
    viewer.locateSource({ target: { closest: () => button }, currentTarget: { contains: () => true } })
    assert.equal(emitted.length, 1)
  } finally { scope.stop() }
})

test('task history displays durable interruption, timestamp, error and unknown billing without zero-cost claims', async () => {
  const jobs = [{ id: 'interrupted123', kind: 'manual', status: 'interrupted', updated_at: '2026-09-29T08:00:00Z', message: '上次运行已中断', error: '请先核对平台用量 <script>', request_status: 'submitted', usage: { actual: false, prompt_tokens: 0, estimated_cost_cny: '0' } }]
  const html = await VueSSR.renderToString(Vue.createSSRApp(component('JobHistory'), { jobs }))
  assert.ok(html.includes('1 条失败 / 中断'))
  assert.ok(html.includes('已中断'))
  assert.ok(html.includes('datetime="2026-09-29T08:00:00Z"'))
  assert.ok(html.includes('请先核对平台用量 &lt;script&gt;'))
  assert.ok(html.includes('请先核对平台账单，不会自动重试'))
  assert.ok(!html.includes('费用估算 ¥0'))
})

test('historical export failure refers to generation time instead of asserting the current export is broken', async () => {
  const jobs = [{ id: 'saved-report', kind: 'manual', status: 'succeeded', result: { export_status: 'failed' }, request_status: 'not_sent' }]
  const html = await VueSSR.renderToString(Vue.createSSRApp(component('JobHistory'), { jobs }))
  assert.ok(html.includes('生成时自动导出失败'))
  assert.ok(html.includes('查看当前保存状态'))
})
