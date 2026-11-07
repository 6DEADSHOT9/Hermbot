import { createRequire } from 'module';
const ROOT = new URL('../../', import.meta.url).pathname;
const require = createRequire(ROOT + 'tests/browser/package.json');
const { chromium } = require('playwright');
const file = 'file://' + ROOT + 'architecture/index.html';
const browser = await chromium.launch({ channel: 'chrome', headless: true });
for (const [w, theme] of [[1440, 'dark'], [390, 'light']]) {
  const page = await (await browser.newContext({ viewport: { width: w, height: w < 600 ? 844 : 900 }, colorScheme: theme })).newPage();
  await page.goto(file + '#start'); await page.waitForTimeout(1500);
  await page.evaluate(() => document.querySelector('.diagram').scrollIntoView()); await page.waitForTimeout(300);
  await page.screenshot({ path: `${ROOT}architecture/_work/shots/p-start-diagram-${w}-${theme}.png` });
  await page.goto(file + '#features'); await page.waitForTimeout(1500);
  await page.evaluate(() => window.__cy.getElementById('feature:outcomes').emit('tap') && null); await page.waitForTimeout(1500);
  await page.screenshot({ path: `${ROOT}architecture/_work/shots/p-feature-${w}-${theme}.png` });
}
await browser.close();
