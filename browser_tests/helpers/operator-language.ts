import { expect, type Page } from '@playwright/test';

const MACHINE_TOKEN = /\b[a-z][a-z0-9]*(?:_[a-z0-9]+)+\b/g;

export async function expectNoVisibleMachineTokens(page: Page) {
  const tokens = await page.evaluate(source => {
    const pattern = new RegExp(source, 'g');
    const found = new Set<string>();
    const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
    while (walker.nextNode()) {
      const node = walker.currentNode as Text;
      const parent = node.parentElement;
      if (!parent || parent.closest('details:not([open]), code, input, script, style')) continue;
      if (parent.getClientRects().length === 0) continue;
      for (const token of node.data.match(pattern) ?? []) found.add(token);
    }
    return [...found].sort();
  }, MACHINE_TOKEN.source);
  expect(tokens, 'visible machine tokens').toEqual([]);
}

export async function expectTechnicalEvidence(page: Page, exactCode: string) {
  const details = page.locator('details.operator-evidence').filter({ hasText: exactCode }).first();
  await expect(details.getByText(exactCode, { exact: true })).toBeHidden();
  await details.locator('summary').click();
  await expect(details.getByText(exactCode, { exact: true })).toBeVisible();
}
