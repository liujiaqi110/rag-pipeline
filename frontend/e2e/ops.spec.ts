// P5 full-chain demo on the REAL stack (Edge → Vue → FastAPI → SQLite/Alembic → jobs → BM25 + bge-m3 → Ollama):
// upload → ACL → frequent questions → FAQ clustering → human review → publish → FAQ hit → ACL change → immediate
// invalidation → knowledge gap → supplement document → verification. Fictional company "云帆科技", synthetic data.
import { expect, test, type Browser, type Locator, type Page } from '@playwright/test'
import { writeFileSync, mkdirSync } from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const __dirname = path.dirname(fileURLToPath(import.meta.url))
const ADMIN = { u: 'e2e_admin', p: process.env.KB_E2E_ADMIN_PASSWORD! }
const PW = process.env.KB_E2E_USER_PASSWORD!
const KB = '云帆运营知识库'
const TMP = path.resolve(__dirname, '..', '.e2e', 'ops-files')

const TRAVEL = `# 差旅住宿标准

本文是云帆科技（虚构公司）销售部的合成示例文档。标记 YF-TRAVEL-4410。

## 住宿标准

销售部员工出差时，一线城市住宿标准为每晚六百元以内，其他城市为每晚四百元以内。超出标准的部分需要部门负责人事先审批，否则不予报销。标记 YF-TRAVEL-4410。

## 报销时限

差旅费用应在出差结束后十个工作日内提交报销，逾期需要说明原因。
`
const SHUTTLE = `# 班车时刻表

本文是云帆科技（虚构公司）行政部的合成示例文档。标记 YF-SHUTTLE-2025。

## 班车发车时间

公司班车每个工作日早上七点半从地铁站发车，晚上六点十分从园区返程。节假日停运。标记 YF-SHUTTLE-2025。
`

async function login(page: Page, u: string, p: string) {
  await page.goto('/login')
  await page.getByTestId('login-username').fill(u)
  await page.getByTestId('login-password').fill(p)
  await page.getByTestId('login-submit').click()
  await page.waitForURL(/\/chat/)
}
async function newPage(browser: Browser) { return (await browser.newContext()).newPage() }
async function pickOption(page: Page, testId: string, text: string) {
  await page.getByTestId(testId).click()
  await page.locator('.el-select-dropdown__item:visible', { hasText: new RegExp(`^${text}$`) }).first().click()
  await page.keyboard.press('Escape')
}
async function newChat(page: Page) {
  await page.getByTestId('chat-new').click()
  await pickOption(page, 'chat-kb-select', KB)
  await page.getByTestId('chat-create').click()
  await page.waitForURL(/\/chat\/[0-9a-f]{32}/)
}
async function ask(page: Page, q: string): Promise<Locator> {
  const turns = page.getByTestId('turn')
  const before = await turns.count()
  const resp = page.waitForResponse((r) => r.url().includes('/ask/stream'))
  await page.getByTestId('chat-input').fill(q)
  await page.getByTestId('chat-send').click()
  expect((await resp).headers()['content-type']).toContain('text/event-stream')
  await expect(page.getByTestId('chat-send')).not.toHaveClass(/is-loading/, { timeout: 300_000 })
  const t = turns.nth(before)
  await expect(t.locator('.streaming')).toHaveCount(0)
  return t
}
async function shot(page: Page, name: string) {
  await page.waitForTimeout(600)
  await page.screenshot({ path: `test-results/shots/p5-${name}.png`, animations: 'disabled', fullPage: false })
}

test('云帆科技：高频问题 → FAQ 审核发布 → 命中 → 撤权失效 → 知识缺口 → 补充文档 → 验证解决', async ({ browser }) => {
  mkdirSync(TMP, { recursive: true })
  writeFileSync(path.join(TMP, '差旅住宿标准.md'), TRAVEL, 'utf-8')
  writeFileSync(path.join(TMP, '班车时刻表.md'), SHUTTLE, 'utf-8')
  const admin = await newPage(browser)
  let kbUrl = ''

  await test.step('上传业务文档、创建用户与部门、配置权限、指定 FAQ 审核人', async () => {
    await login(admin, ADMIN.u, ADMIN.p)
    await admin.goto('/acl/departments')
    for (const [n, d] of [['sales', '销售部'], ['support', '客服部']]) {
      await admin.getByTestId('dept-name').fill(n)
      await admin.getByTestId('dept-desc').fill(d)
      await admin.getByTestId('dept-create').click()
      await expect(admin.getByTestId('dept-tree')).toContainText(n)
    }
    await admin.goto('/acl/users')
    for (const [u, dept] of [['sales_zhang', 'sales'], ['sales_li', 'sales'], ['support_wu', 'support'], ['ops_rev', 'sales']]) {
      await admin.getByTestId('user-create-open').click()
      await admin.getByTestId('user-username').fill(u)
      await admin.getByTestId('user-password').fill(PW)
      await pickOption(admin, 'user-departments', dept)
      await admin.getByTestId('user-save').click()
      await expect(admin.getByTestId('user-table')).toContainText(u)
    }
    await admin.goto('/knowledge')
    await admin.getByTestId('kb-create-open').click()
    await admin.getByTestId('kb-name').fill(KB)
    await admin.getByTestId('kb-create').click()
    await admin.getByTestId('kb-card').filter({ hasText: KB }).click()
    await admin.waitForURL(/\/knowledge\/[0-9a-f]{32}/)
    kbUrl = admin.url()
    await admin.getByTestId('open-upload').click()
    await admin.getByTestId('file-input').setInputFiles(path.join(TMP, '差旅住宿标准.md'))
    await admin.getByTestId('upload-start').click()
    await expect(admin.getByTestId('upload-table').locator('tr', { hasText: '差旅住宿标准.md' })).toContainText('已索引', { timeout: 300_000 })
    await admin.keyboard.press('Escape')
    await admin.reload()
    await admin.getByTestId('acl-差旅住宿标准.md').click()
    await pickOption(admin, 'acl-allow-departments', 'sales')
    await admin.getByTestId('acl-save').click()
    await expect(admin.locator('.el-message--success').last()).toBeVisible()
    await pickOption(admin, 'kb-manager-add', 'ops_rev')      // reviewer: manages the KB and can read the sales doc
  })

  const zhang = await newPage(browser)
  await test.step('用户高频提问（同义问法）', async () => {
    await login(zhang, 'sales_zhang', PW)
    await newChat(zhang)
    for (const q of ['出差住宿标准是多少？', '差旅住宿的标准是多少', '出差的住宿标准是多少钱？']) {
      const t = await ask(zhang, q)
      await expect(t.getByTestId('citation-card').filter({ hasText: '差旅住宿标准.md' })).toHaveCount(1)
    }
  })

  const rev = await newPage(browser)
  await test.step('FAQ 聚类挖掘 → 管理员审核、编辑并发布', async () => {
    await login(rev, 'ops_rev', PW)
    await rev.goto('/ops/faq')
    await pickOption(rev, 'faq-kb', KB)
    await rev.getByTestId('faq-mine-open').click()
    await rev.getByTestId('mine-start').click()
    await expect(rev.getByTestId('mine-result')).toContainText('新增候选 1', { timeout: 300_000 })
    const row = rev.getByTestId('faq-candidates').locator('tbody tr').first()
    await expect(row).toContainText('3')
    await row.getByTestId('faq-review').click()
    await expect(rev.getByTestId('faq-members')).toContainText('差旅住宿的标准是多少')
    await expect(rev.getByTestId('faq-editor')).not.toContainText('sales_zhang')        // askers are never shown
    const ans = rev.getByTestId('faq-answer')
    await ans.fill((await ans.inputValue()) + '\n\n（本答案已由运营审核：超标住宿须事先审批。）')
    await shot(rev, 'faq-review')
    await rev.getByTestId('faq-publish').click()
    await expect(rev.locator('.el-message--success').last()).toContainText('已发布')
  })

  const li = await newPage(browser)
  await test.step('用户再次提问 → FAQ 命中（不调用模型）', async () => {
    await login(li, 'sales_li', PW)
    await newChat(li)
    const t = await ask(li, '出差住宿的标准是多少？')
    await expect(t.getByTestId('faq-badge')).toBeVisible()
    await expect(t.getByTestId('answer-text')).toContainText('本答案已由运营审核')
    await expect(t.getByTestId('citation-card').filter({ hasText: '差旅住宿标准.md' })).toHaveCount(1)
    await shot(li, 'faq-hit')
    const wu = await newPage(browser)                           // other department: cannot even see the KB
    await login(wu, 'support_wu', PW)
    await wu.goto('/knowledge')
    await expect(wu.locator('.kb-page')).toContainText('暂无可访问的知识库')
    await expect(wu.getByTestId('kb-card').filter({ hasText: KB })).toHaveCount(0)
    await wu.context().close()
  })

  await test.step('修改文档权限 → FAQ 缓存立即失效', async () => {
    await admin.goto(kbUrl)
    await admin.getByTestId('acl-差旅住宿标准.md').click()
    await admin.getByTestId('acl-allow-departments').locator('.el-tag__close').first().click()
    await admin.keyboard.press('Escape')
    await pickOption(admin, 'acl-allow-users', 'sales_zhang')
    await admin.getByTestId('acl-save').click()
    await expect(admin.locator('.el-message--success').last()).toBeVisible()
    const t = await ask(li, '出差住宿的标准是多少？')               // identical question: FAQ and answer cache must not leak
    await expect(t.getByTestId('faq-badge')).toHaveCount(0)
    await expect(t).not.toContainText('YF-TRAVEL-4410')
    await expect(t).not.toContainText('本答案已由运营审核')
    await expect(t.getByTestId('citation-card').filter({ hasText: '差旅住宿标准' })).toHaveCount(0)
    const z = await ask(zhang, '出差住宿的标准是多少呢？')          // still authorised (personal grant): FAQ hits
    await expect(z.getByTestId('faq-badge')).toBeVisible()
  })

  await test.step('发现知识缺口 → 转为补充任务', async () => {
    for (const q of ['公司班车几点发车？', '公司班车每天几点发车']) {
      const t = await ask(zhang, q)
      await expect(t.getByTestId('citation-card')).toHaveCount(0)
    }
    await rev.goto('/ops/gaps')
    const row = rev.getByTestId('gap-table').locator('tbody tr', { hasText: '班车' }).first()
    await expect(row).toContainText('2 次', { timeout: 30_000 })
    await row.getByTestId('gap-task').click()
    await rev.getByTestId('task-create').click()
    await expect(rev.locator('.el-message--success').last()).toBeVisible()
    await shot(rev, 'gaps')
  })

  await test.step('补充文档 → 关联任务 → 验证缺口已解决', async () => {
    await admin.goto(kbUrl)
    await admin.getByTestId('open-upload').click()
    await admin.getByTestId('file-input').setInputFiles(path.join(TMP, '班车时刻表.md'))
    await admin.getByTestId('upload-start').click()
    await expect(admin.getByTestId('upload-table').locator('tr', { hasText: '班车时刻表.md' })).toContainText('已索引', { timeout: 300_000 })
    await admin.keyboard.press('Escape')
    await admin.reload()
    await admin.getByTestId('acl-班车时刻表.md').click()
    await admin.getByTestId('acl-global').click()
    await admin.getByTestId('acl-save').click()
    await expect(admin.locator('.el-message--success').last()).toBeVisible()
    await rev.goto('/ops/tasks')
    const row = rev.getByTestId('task-table').locator('tbody tr', { hasText: '班车' }).first()
    await row.getByTestId('task-doc').click()
    await rev.locator('.el-select-dropdown__item:visible', { hasText: '班车时刻表.md' }).first().click()
    await expect(rev.locator('.el-message--success').last()).toContainText('已关联')
    await row.getByTestId('task-done').click()
    await expect(rev.locator('.el-message--success').last()).toContainText('缺口已解决', { timeout: 300_000 })
    await expect(rev.getByTestId('task-table').locator('tbody tr', { hasText: '班车' }).first()).toContainText('已验证解决')
    await rev.goto('/ops/gaps')
    await expect(rev.getByTestId('gap-table').locator('tbody tr', { hasText: '班车' }).first()).toContainText('已解决')
  })

  await test.step('运营看板展示真实统计', async () => {
    await admin.goto('/ops/dashboard')
    await expect(admin.getByTestId('kpi-faq')).toContainText('命中 2 次')
    await expect(admin.getByTestId('kpi-llm')).not.toContainText('模型调用次数\n0\n')
    await expect(admin.getByTestId('top-questions')).toContainText('住宿')
    await expect(admin.getByTestId('top-cited')).toContainText('差旅住宿标准')
    await shot(admin, 'dashboard')
  })
})
