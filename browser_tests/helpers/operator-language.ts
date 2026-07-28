import { expect, type Page } from '@playwright/test';

const MACHINE_TOKEN = /\b[a-z][a-z0-9]*(?:_[a-z0-9]+)+\b/g;

export async function expectNoVisibleMachineTokens(page: Page) {
  const findings = await page.evaluate(source => {
    const pattern = new RegExp(source, 'g');
    const found = new Set<string>();
    const record = (channel: string, value: string | null) => {
      for (const token of value?.match(pattern) ?? []) found.add(`${channel}: ${token}`);
    };
    const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
    while (walker.nextNode()) {
      const node = walker.currentNode as Text;
      const parent = node.parentElement;
      if (
        !parent ||
        parent.closest(
          'details:not([open]), details.operator-evidence, code[lang="en"][dir="ltr"], input, script, style',
        )
      )
        continue;
      if (parent.getClientRects().length === 0) continue;
      record('visible text', node.data);
    }
    for (const element of document.body.querySelectorAll<HTMLElement>(
      '[aria-label], [aria-labelledby], [alt], [title], input, button, select, textarea',
    )) {
      if (
        element.closest(
          'details:not([open]), details.operator-evidence, code[lang="en"][dir="ltr"], script, style',
        )
      )
        continue;
      if (element.getClientRects().length === 0) continue;
      record('aria-label', element.getAttribute('aria-label'));
      record('alt', element.getAttribute('alt'));
      record('title', element.getAttribute('title'));
      const labelledBy = element.getAttribute('aria-labelledby');
      if (labelledBy) {
        const label = labelledBy
          .split(/\s+/)
          .map(id => document.getElementById(id)?.textContent ?? '')
          .join(' ');
        record('aria-labelledby', label);
      }
    }
    return [...found].sort();
  }, MACHINE_TOKEN.source);
  expect(findings, 'visible or accessible-name machine tokens').toEqual([]);
}

export async function expectTechnicalEvidence(page: Page, exactCode: string) {
  const details = page.locator('details.operator-evidence').filter({ hasText: exactCode }).first();
  await expect(details.getByText(exactCode, { exact: true })).toBeHidden();
  await details.locator('summary').click();
  await expect(details.getByText(exactCode, { exact: true })).toBeVisible();
}
