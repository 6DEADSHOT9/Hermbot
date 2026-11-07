import { createRequire } from 'module';
const ROOT = new URL('../../', import.meta.url).pathname;
const require = createRequire(ROOT + 'tests/browser/package.json');
const { chromium } = require('playwright');
const file = 'file://' + ROOT + 'architecture/index.html';
const VIEWS = ['start','glossary','context','deploy','backend','runtime','er','api','frontend','features','flows','sms','heat','docs','disc','all'];
const browser = await chromium.launch({ channel: 'chrome', headless: true });
const report = { shots: [], views: {}, flows: {}, sms: {}, features: {}, errors: [] };
for (const theme of ['light','dark']) for (const w of [390, 1440]) {
  const ctx = await browser.newContext({ viewport: { width: w, height: w < 600 ? 844 : 900 }, colorScheme: theme });
  const page = await ctx.newPage();
  page.on('pageerror', e => report.errors.push(`${w}/${theme} pageerror ${e.message}`));
  page.on('console', m => { if (m.type() === 'error') report.errors.push(`${w}/${theme} console ${m.text()}`); });
  for (const v of VIEWS) {
    await page.goto('about:blank'); await page.goto(file + '#' + v); await page.waitForTimeout(v === 'all' ? 3000 : 1800);
    const info = await page.evaluate(() => ({ title: document.getElementById('vtitle').textContent, nodes: window.__cy && !document.getElementById('cy').hidden ? window.__cy.nodes().not('.zone,.lane,.anchor').length : null,
      html: document.getElementById('htmlview').hidden ? null : document.querySelectorAll('#htmlview tr, #htmlview .term, #htmlview .parts button').length, overflow: document.documentElement.scrollWidth > innerWidth }));
    const out = `_work/shots/final-${v}-${w}-${theme}.png`;
    await page.screenshot({ path: ROOT + 'architecture/' + out });
    report.shots.push(out);
    report.views[`${v}/${w}/${theme}`] = info;
  }
  if (w === 1440 && theme === 'light') {
    const model = await page.evaluate(() => ({ flows: Object.keys(MODEL.flows).sort(), sms: Object.keys(MODEL.state_machines).sort(), feats: Object.keys(MODEL.features).sort() }));
    for (const f of model.flows) {
      await page.goto('about:blank'); await page.goto(file + '#flows'); await page.waitForTimeout(1200);
      await page.click(`[data-side="${f}"]`); await page.waitForTimeout(1200);
      const total = await page.evaluate(() => +document.querySelector('.stepctl b').textContent.match(/of (\d+)/)[1]);
      let lit = 0, empty = [];
      for (let i = 0; i < total; i++) {
        await page.evaluate(i => document.querySelector(`[data-step="${i}"]`).click(), i); await page.waitForTimeout(60);
        const a = await page.evaluate(() => window.__cy.nodes('.active').length); if (a) lit++; else empty.push(i + 1);
      }
      report.flows[f] = { steps: total, steps_with_highlight: lit, steps_without_graph_nodes: empty };
    }
    for (const s of model.sms) {
      await page.goto('about:blank'); await page.goto(file + '#sms'); await page.waitForTimeout(900);
      await page.click(`[data-side="${s}"]`); await page.waitForTimeout(900);
      report.sms[s] = await page.evaluate(() => ({ states: window.__cy.nodes().length, transitions: window.__cy.edges().length }));
    }
    for (const f of model.feats) {
      await page.goto('about:blank'); await page.goto(file + '#features'); await page.waitForTimeout(900);
      await page.click(`[data-side="${f}"]`); await page.waitForTimeout(1200);
      report.features[f] = await page.evaluate(() => ({ nodes: window.__cy.nodes().length, panel_sections: document.querySelectorAll('#pbody details').length }));
    }
  }
  await ctx.close();
}
await browser.close();
require('fs').writeFileSync(ROOT + 'architecture/_work/verify-report.json', JSON.stringify(report, null, 1));
const bad = Object.entries(report.views).filter(([k, v]) => v.overflow || (v.nodes === 0) || (v.nodes === null && !v.html));
console.log('shots', report.shots.length, 'bad views', bad, 'errors', report.errors.length);
console.log('flows', JSON.stringify(report.flows));
console.log('sms', Object.entries(report.sms).filter(([k, v]) => !v.states).map(([k]) => k), Object.keys(report.sms).length);
console.log('features', Object.entries(report.features).filter(([k, v]) => v.nodes < 2 || v.panel_sections < 10).map(([k]) => k), Object.keys(report.features).length);
