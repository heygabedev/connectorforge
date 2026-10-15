import { expect, test } from '@playwright/test'
import AxeBuilder from '@axe-core/playwright'
import { execFileSync } from 'node:child_process'
import { resolve } from 'node:path'

function loginCode(role: string) {
  const python = resolve(
    process.cwd(),
    process.platform === 'win32' ? '../.venv/Scripts/python.exe' : '../.venv/bin/python',
  )
  return execFileSync(python, ['-m', 'connectorforge.cli', 'login-code', '--role', role], {
    env: process.env,
    encoding: 'utf8',
  }).trim()
}

test('administrator can create and revoke a credential, then sign out', async ({ page }) => {
  await page.goto('/')
  await expect(page.getByRole('heading', { name: 'Open your workspace' })).toBeVisible()
  await page.getByLabel('Login code').fill(loginCode('admin'))
  await page.getByRole('button', { name: 'Continue to workspace' }).click()
  await expect(page.getByRole('heading', { name: 'Workspace overview' })).toBeVisible()
  await page.getByRole('button', { name: 'Access', exact: true }).click()
  await page.getByLabel('Credential name').fill('Browser integration')
  await page.getByRole('button', { name: 'Create credential' }).click()
  await expect(page.getByText('Copy this credential now.', { exact: false })).toBeVisible()
  await page.getByRole('button', { name: 'Dismiss' }).click()
  await page.getByRole('button', { name: 'Revoke Browser integration' }).click()
  await expect(page.getByRole('cell', { name: 'Revoked', exact: true })).toBeVisible()
  await page.getByRole('button', { name: 'Sign out', exact: true }).click()
  await expect(page.getByLabel('Login code')).toBeVisible()
})

test('viewer navigation is accessible and does not expose credential administration', async ({
  page,
}, testInfo) => {
  await page.goto('/')
  expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([])
  await page.getByLabel('Login code').fill(loginCode('viewer'))
  await page.getByRole('button', { name: 'Continue to workspace' }).click()
  await expect(page.getByRole('heading', { name: 'Workspace overview' })).toBeVisible()
  await expect(page.getByRole('button', { name: 'Access', exact: true })).toHaveCount(0)
  await page.keyboard.press('Tab')
  expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([])
  await page.screenshot({ path: testInfo.outputPath('workspace.png'), fullPage: true })
})
