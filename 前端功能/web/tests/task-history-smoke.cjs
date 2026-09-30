/* Browser regression against an isolated server; one explicit interrupted-job HTTP fixture. */
const { chromium } = require(process.env.PLAYWRIGHT_PATH || 'C:/Users/hq/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright')
const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')

async function main() {
  const base = process.env.REVIEWDESK_TEST_URL || 'http://127.0.0.1:62516'
  const output = process.env.REVIEWDESK_TEST_OUTPUT || path.resolve(__dirname, '../../../其它/verification/20260929-bugfix-ui')
  fs.mkdirSync(output, { recursive: true })
  const browser = await chromium.launch({ headless: true, channel: process.env.PLAYWRIGHT_CHANNEL || 'msedge' })
  const page = await browser.newPage({ viewport: { width: 1500, height: 1100 } })
  const actions = [], errors = []
  let paidRequests = 0, historyRequests = 0
  page.on('pageerror', error => errors.push(error.message))
  page.on('request', request => {
    if (request.url().includes('/api/jobs?limit=20')) historyRequests++
    if (request.url().endsWith('/api/manual') && request.method() === 'POST' && request.postDataJSON()?.use_ai) paidRequests++
  })
  try {
    await page.goto(base, { waitUntil: 'networkidle' })
    const bootstrap = await (await page.request.get(`${base}/api/bootstrap`)).json()
    const response = await page.request.get(`${base}/api/jobs?limit=20`, { headers: { 'X-ReviewDesk-Token': bootstrap.token } })
    assert.equal(response.status(), 200)
    const realJobs = (await response.json()).jobs
    assert.ok(realJobs.length > 0, 'the isolated server must have previous smoke-test jobs')
    const history = page.locator('.job-history')
    await history.locator('summary').click()
    await history.locator('.job-history-list > li').first().waitFor()
    assert.equal(await history.locator('.job-history-list > li').count(), realJobs.length)
    assert.ok((await history.innerText()).includes(realJobs[0].id.slice(0, 8)))
    const beforeRefresh = historyRequests
    const refreshed = page.waitForResponse(reply => reply.url().endsWith('/api/jobs?limit=20'))
    await history.getByRole('button', { name: '刷新任务记录', exact: true }).click()
    assert.equal((await refreshed).status(), 200)
    assert.ok(historyRequests > beforeRefresh)
    await history.screenshot({ path: path.join(output, 'task-history-real.png') })
    actions.push({ check: 'actual persisted jobs load, expand and refresh', records: realJobs.length })

    const fixture = { id: 'browser-history-interrupted', kind: 'manual', status: 'interrupted', progress: 55,
      message: '测试模拟：上次说明书任务已中断', error: '测试模拟：模型请求期间中断，请先核对平台用量。',
      created_at: '2026-09-29T07:55:00Z', updated_at: '2026-09-29T08:00:00Z', result: null,
      request_status: 'submitted', usage: { actual: false, prompt_tokens: 0, completion_tokens: 0, estimated_cost_cny: '0', note: '模型请求已提交，结果或用量尚未返回。' } }
    await page.route('**/api/jobs?limit=20', route => route.fulfill({ json: { jobs: [fixture] } }))
    await history.getByRole('button', { name: '刷新任务记录', exact: true }).click()
    await history.getByText(fixture.error, { exact: true }).waitFor()
    assert.ok((await history.locator('summary').innerText()).includes('1 条失败 / 中断'))
    assert.equal(await history.locator('time').getAttribute('datetime'), fixture.updated_at)
    const text = await history.innerText()
    assert.ok(text.includes('模型请求已提交，结果或用量尚未确认；请先核对平台账单，不会自动重试。'))
    assert.ok(!text.includes('费用估算 ¥0'))
    assert.ok(!text.includes('输入 0'))
    await page.reload({ waitUntil: 'networkidle' })
    await history.locator('summary').click()
    await history.getByText(fixture.error, { exact: true }).waitFor()
    await history.screenshot({ path: path.join(output, 'task-history-interrupted-fixture.png') })
    actions.push({ check: 'explicit interrupted/submitted HTTP fixture survives reload, shows error and unknown billing without zero-cost claim', fixture: true })
    assert.equal(paidRequests, 0)
    assert.deepEqual(errors, [])
    const result = { ok: true, checked_at: new Date().toISOString(), base, actions, errors, paid_requests: paidRequests, history_requests: historyRequests }
    fs.writeFileSync(path.join(output, 'task-history-results.json'), JSON.stringify(result, null, 2))
    console.log(JSON.stringify(result, null, 2))
  } catch (error) {
    await page.screenshot({ path: path.join(output, 'task-history-failure.png'), fullPage: true })
    fs.writeFileSync(path.join(output, 'task-history-results.json'), JSON.stringify({ ok: false, actions, errors, failure: error.stack, paid_requests: paidRequests }, null, 2))
    throw error
  } finally { await browser.close() }
}
main().catch(error => { console.error(error); process.exitCode = 1 })
