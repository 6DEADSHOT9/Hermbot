import { createRequire } from 'module';
const ROOT = new URL('../../', import.meta.url).pathname;
const require = createRequire(ROOT + 'tests/browser/package.json');
const { chromium } = require('playwright');
const file = 'file://' + ROOT + 'architecture/index.html';
const views = (process.argv[2] || 'context').split(',');
const widths = (process.argv[3] || '1440').split(',').map(Number);
const themes = (process.argv[4] || 'light').split(',');
const browser = await chromium.launch({ channel: 'chrome', headless: true });
for (const theme of themes) for (const w of widths) {
  const ctx = await browser.newContext({ viewport: { width: w, height: w < 600 ? 844 : 900 }, colorScheme: theme });
  const page = await ctx.newPage();
  const errs = [];
  page.on('console', m => { if (m.type() === 'error' || m.type() === 'warning') errs.push(m.type() + ': ' + m.text()); });
  page.on('pageerror', e => errs.push('pageerror: ' + e.message));
  for (const v of views) {
    const [view, extra] = v.split('+');
    await page.goto(file + '#' + view);
    await page.waitForTimeout(2500);
    if (extra === 'panel') { await page.evaluate(() => document.getElementById('sdbtn').click()); await page.waitForTimeout(600); }
    if (extra === 'cov') { await page.evaluate(() => document.getElementById('covbadge').click()); await page.waitForTimeout(400); }
    if (extra && extra.startsWith('search')) { await page.fill('#q', extra.slice(6) || 'worker'); await page.waitForTimeout(300); await page.keyboard.press('Enter'); await page.waitForTimeout(2500); }
    const out = `${ROOT}architecture/_work/shots/${view}${extra ? '-' + extra : ''}-${w}-${theme}.png`;
    await page.screenshot({ path: out });
    const info = await page.evaluate(() => ({ title: document.getElementById('vtitle').textContent, overflow: document.documentElement.scrollWidth > innerWidth }));
    console.log(out.split('/').pop(), JSON.stringify(info));
  }
  if (errs.length) console.log('ERRORS', w, theme, [...new Set(errs)].slice(0, 15).join('\n'));
  await ctx.close();
}
await browser.close();
