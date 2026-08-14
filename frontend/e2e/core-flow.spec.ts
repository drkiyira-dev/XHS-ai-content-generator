import { expect, test } from '@playwright/test'


const TEST_PNG = Buffer.from(
  'iVBORw0KGgoAAAANSUhEUgAAACAAAAAYCAIAAAAUMWhjAAAANElEQVR4nGPcImfDQEvARFPTGUYtIAKMxgFBMBpEBMFoEBEEo0FEEIwGEUEwGkQEAc2DCAB5mgE+DOIaaAAAAABJRU5ErkJggg==',
  'base64'
)


test('local account generation and history lifecycle works in a real browser', async ({
  context,
  page
}) => {
  const email = `browser-e2e-${Date.now()}@example.com`
  const password = 'local-browser-e2e-password-2026'

  await page.goto('/register?next=generate')
  await expect(page.getByRole('heading', { name: '注册新账号' })).toBeVisible()
  await page.getByLabel('邮箱', { exact: true }).fill(email)
  await page.getByLabel('密码', { exact: true }).fill(password)
  await page.getByLabel('确认密码', { exact: true }).fill(password)
  await page.getByRole('button', { name: '注册并登录' }).click()

  await expect(page).toHaveURL(/\/app\/generate$/)
  await expect(page.getByText(email, { exact: true })).toBeVisible()
  const sessionCookie = (await context.cookies()).find(
    cookie => cookie.name === 'xhs_session_local'
  )
  expect(sessionCookie).toMatchObject({
    httpOnly: true,
    secure: false,
    sameSite: 'Lax',
    path: '/api/v1'
  })

  await page.reload()
  await expect(page.getByRole('heading', { name: '生成小红书内容初稿' })).toBeVisible()
  await expect(page.getByText(email, { exact: true })).toBeVisible()

  await page.locator('input[type="file"]').setInputFiles({
    name: 'browser-e2e.png',
    mimeType: 'image/png',
    buffer: TEST_PNG
  })
  await expect(page.getByRole('img', { name: '已选择图片预览' })).toBeVisible()

  await page.getByRole('button', { name: '生成初稿' }).click()
  await expect(page.getByRole('heading', { name: '正在分析图片并生成初稿' })).toBeVisible()
  await expect(page.getByLabel('主题或名称')).toBeDisabled()
  await expect(page.getByRole('button', { name: '重新上传' })).toBeDisabled()

  await expect(page.getByRole('heading', { name: '生成结果' })).toBeVisible()
  await expect(
    page.getByRole('heading', { name: '发布前风险提示（非平台审核）' })
  ).toBeVisible()
  await expect(page.locator('.risk-findings li')).toHaveCount(1)
  const editableTitle = page.getByLabel('标题', { exact: true })
  await expect(editableTitle).toHaveValue(/📷|✨/)
  const originalGeneratedTitle = await editableTitle.inputValue()
  const editedTitle = '本地修改标题'

  await page.evaluate(() => {
    Object.defineProperty(navigator, 'clipboard', {
      configurable: true,
      value: {
        writeText: async (text: string) => {
          sessionStorage.setItem('e2e-copied-text', text)
        }
      }
    })
  })
  await editableTitle.fill(editedTitle)
  await expect(page.getByText('内容已在本地修改', { exact: true })).toBeVisible()
  await page.getByRole('button', { name: '复制全部文案', exact: true }).click()
  await expect.poll(
    () => page.evaluate(() => sessionStorage.getItem('e2e-copied-text'))
  ).toContain(`标题：${editedTitle}`)

  await page.getByRole('link', { name: '历史记录' }).click()
  await expect(page.getByRole('heading', { name: '历史记录' })).toBeVisible()
  const historyItem = page.locator('.history-item').first()
  await expect(historyItem).toBeVisible()
  await expect(historyItem.getByRole('heading', { level: 2 })).toHaveText(originalGeneratedTitle)
  await expect(historyItem.getByText(editedTitle, { exact: true })).toHaveCount(0)
  await expect(historyItem.getByText('发布前风险提示 1 项 · 非平台审核')).toBeVisible()
  const preview = historyItem.locator('img')
  await expect(preview).toBeVisible()
  await expect.poll(() => preview.evaluate(image => image.naturalWidth)).toBeGreaterThan(0)

  await page.getByRole('link', { name: '生成文案' }).click()
  await expect(page.getByLabel('标题', { exact: true })).toHaveValue(editedTitle)
  await page.getByRole('button', { name: '使用新设置生成', exact: true }).click()
  await expect(page.getByText('正在生成新版本', { exact: true })).toBeVisible()
  await expect(page.getByText('当前稿件仍可查看和复制；新结果通过检查后才会替换。')).toBeVisible()
  await expect(page.getByLabel('标题', { exact: true })).toHaveValue(editedTitle)
  await expect(page.getByRole('button', { name: '复制全部文案', exact: true })).toBeEnabled()

  const previousVersionDrawer = page.locator('#previous-version-drawer')
  await expect(
    previousVersionDrawer.getByRole('heading', { name: '上一版', exact: true })
  ).toBeVisible()
  await expect(previousVersionDrawer.getByText(editedTitle, { exact: true })).toBeVisible()
  await page.evaluate(() => sessionStorage.removeItem('e2e-copied-text'))
  await previousVersionDrawer.getByRole('button', { name: '复制上一版', exact: true }).click()
  await expect.poll(
    () => page.evaluate(() => sessionStorage.getItem('e2e-copied-text'))
  ).toContain(`标题：${editedTitle}`)

  await page.getByRole('link', { name: '历史记录' }).click()
  await expect(page.locator('.history-item')).toHaveCount(2)
  const historyTitles = await page.locator('.history-item h2').allTextContents()
  expect(historyTitles).toContain(originalGeneratedTitle)
  expect(historyTitles).not.toContain(editedTitle)

  await page.locator('.history-item').first().getByRole('button', { name: /载回工作台/ }).last().click()
  await expect(page).toHaveURL(/\/app\/generate$/)
  await expect(page.getByText('已载入历史生成结果')).toBeVisible()
  await expect(page.getByRole('img', { name: '历史记录图片预览' })).toBeVisible()
  await expect(
    page.getByRole('button', { name: '保留旧稿并重新生成', exact: true })
  ).toBeDisabled()
  await expect(page.getByLabel('标题', { exact: true })).not.toHaveValue(editedTitle)

  await page.getByRole('link', { name: '历史记录' }).click()
  for (let remaining = 2; remaining > 0; remaining -= 1) {
    const itemToDelete = page.locator('.history-item').first()
    await itemToDelete.getByRole('button', { name: /删除历史记录/ }).click()
    const confirmation = page.locator('.el-message-box')
    await expect(confirmation).toBeVisible()
    await confirmation.getByRole('button', { name: '删除', exact: true }).click()
    await expect(page.locator('.history-item')).toHaveCount(remaining - 1)
  }
  await expect(page.getByText('暂无历史记录')).toBeVisible()

  await page.reload()
  await expect(page.getByText('暂无历史记录')).toBeVisible()
  await page.getByRole('button', { name: '退出', exact: true }).click()
  await expect(page).toHaveURL(/\/$/)
  await expect.poll(async () => {
    const cookies = await context.cookies()
    return cookies.some(cookie => cookie.name === 'xhs_session_local')
  }).toBe(false)
})
