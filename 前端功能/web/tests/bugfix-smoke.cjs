/* Use an isolated Workbench with credentials mocked and paid completions disabled. */
const { chromium } = require(process.env.PLAYWRIGHT_PATH || 'C:/Users/hq/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright')
const fs = require('node:fs')
const path = require('node:path')
const assert = require('node:assert/strict')

async function main() {
  const base = process.env.REVIEWDESK_TEST_URL || 'http://127.0.0.1:8876'
  const output = process.env.REVIEWDESK_TEST_OUTPUT || path.resolve(__dirname, '../../../其它/verification/20260929-bugfix-ui')
  fs.mkdirSync(output, { recursive: true })
  const browser = await chromium.launch({ headless: true, channel: process.env.PLAYWRIGHT_CHANNEL || 'msedge' })
  const page = await browser.newPage({ viewport: { width: 1500, height: 1100 } })
  const actions = [], errors = []
  let paidRequests = 0
  page.on('pageerror', error => errors.push(error.message))
  page.on('request', request => {
    if (request.url().endsWith('/api/manual') && request.method() === 'POST' && request.postDataJSON()?.use_ai) paidRequests++
  })
  try {
    await page.goto(base, { waitUntil: 'networkidle' })
    const bootstrap = await (await page.request.get(`${base}/api/bootstrap`)).json()
    const headers = { 'X-ReviewDesk-Token': bootstrap.token }
    async function api(route, body) {
      const response = body === undefined ? await page.request.get(base + route, { headers }) : await page.request.post(base + route, { headers, data: body })
      assert.equal(response.status(), 200, await response.text())
      return response.json()
    }
    async function finish(started) {
      for (let attempt = 0; attempt < 100; attempt++) {
        const job = await api(`/api/jobs/${started.job_id}`)
        if (job.status === 'succeeded') return job.result
        assert.ok(!['failed', 'interrupted'].includes(job.status), JSON.stringify(job))
        await new Promise(resolve => setTimeout(resolve, 100))
      }
      throw Error('isolated test job timed out')
    }
    const projectName = '源码联动与建议回归'
    const oldCode = 'def collect(items=[]):\n    items.append("OLD_SNAPSHOT_MARKER")\n    return items\n'
    const oldRevision = (await finish(await api('/api/import', { project_name: projectName, files: [{ path: 'main.py', content: oldCode }] }))).revision_id
    const snapshot = await api(`/api/revisions/${oldRevision}`)
    assert.ok(snapshot.findings.length > 0, 'fixture must produce actual static findings')
    await page.reload({ waitUntil: 'networkidle' })
    await page.locator('.finding').first().waitFor()
    const actualFinding = snapshot.findings[0]
    assert.ok((await page.locator('.finding').first().innerText()).includes(actualFinding.recommendation))
    assert.equal((await page.locator('.finding-evidence').first().innerText()).trim(), actualFinding.evidence.trim())
    actions.push('real static finding displays backend evidence and recommendation')

    await page.getByRole('navigation').getByRole('button', { name: '详细说明书', exact: false }).click()
    await page.getByRole('button', { name: '生成本地完整说明', exact: false }).click()
    await page.locator('.source-reference').first().waitFor({ timeout: 30000 })
    await page.locator('.source-reference').first().click()
    assert.ok(await page.locator('.source-panel .code-line.selected').count() > 0)
    assert.ok((await page.locator('.source-panel').innerText()).includes('OLD_SNAPSHOT_MARKER'))
    const saved = (await api(`/api/manuals?revision_id=${oldRevision}`)).manuals[0]
    const oldManual = await api(`/api/manuals/${saved.id}`)
    actions.push('real generated report source citation opens highlighted immutable source')
    await page.screenshot({ path: path.join(output, 'report-source.png') })

    const newRevision = (await finish(await api('/api/import', { project_name: projectName, files: [{ path: 'main.py', content: oldCode.replace('OLD_SNAPSHOT_MARKER', 'NEW_SNAPSHOT_MARKER') }] }))).revision_id
    // Explicit UI fixture: expose an old report while the selected project is its newer revision.
    await page.route('**/api/manuals?revision_id=*', route => route.fulfill({ json: { manuals: [saved] } }))
    await page.reload({ waitUntil: 'networkidle' })
    assert.ok((await page.locator('.project-heading').innerText()).includes(newRevision.slice(0, 8)))
    await page.getByRole('navigation').getByRole('button', { name: '详细说明书', exact: false }).click()
    await page.locator('.source-reference').first().click()
    await page.getByText('与当前项目版本不同', { exact: false }).waitFor()
    await page.locator('.source-panel .code-line.selected').first().waitFor()
    const source = await page.locator('.source-panel').innerText()
    assert.ok(source.includes('OLD_SNAPSHOT_MARKER'))
    assert.ok(!source.includes('NEW_SNAPSHOT_MARKER'))
    actions.push('explicit old-report listing fixture: same-name source resolves report revision, not current revision')

    let retries = 0
    await page.route(`**/api/manuals/${saved.id}`, route => route.fulfill({ json: { ...oldManual, export_status: 'failed', export_error: '测试模拟：文件被占用', auto_exported: false, document_path: null } }))
    await page.route(`**/api/manuals/${saved.id}/export`, route => {
      retries++
      assert.equal(route.request().method(), 'POST')
      assert.deepEqual(route.request().postDataJSON(), {})
      return route.fulfill({ json: { ...oldManual, export_status: 'saved', export_error: null, auto_exported: true, document_path: '隔离测试目录/已恢复说明书.md' } })
    })
    await page.reload({ waitUntil: 'networkidle' })
    await page.getByRole('navigation').getByRole('button', { name: '详细说明书', exact: false }).click()
    await page.getByText('测试模拟：文件被占用', { exact: false }).waitFor()
    assert.ok(await page.getByRole('link', { name: '下载完整 Markdown', exact: false }).count())
    await page.getByRole('button', { name: '重试保存文件（不调用模型）', exact: true }).click()
    await page.getByText('Markdown 文件已保存；本次没有调用模型。', { exact: true }).waitFor()
    assert.equal(retries, 1)
    assert.equal(paidRequests, 0)
    assert.equal(await page.getByRole('button', { name: '重试保存文件（不调用模型）', exact: true }).count(), 0)
    actions.push('explicit export-failure/retry HTTP fixtures: report stays downloadable, one export retry, zero paid requests')
    assert.deepEqual(errors, [])
    fs.writeFileSync(path.join(output, 'results.json'), JSON.stringify({ ok: true, checked_at: new Date().toISOString(), actions, errors, paid_requests: paidRequests }, null, 2))
    console.log(JSON.stringify({ ok: true, actions, paid_requests: paidRequests }, null, 2))
  } catch (error) {
    await page.screenshot({ path: path.join(output, 'failure.png'), fullPage: true })
    fs.writeFileSync(path.join(output, 'results.json'), JSON.stringify({ ok: false, actions, errors, failure: error.stack }, null, 2))
    throw error
  } finally { await browser.close() }
}
main().catch(error => { console.error(error); process.exitCode = 1 })
