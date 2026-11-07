import {chromium} from 'playwright';
import assert from 'node:assert/strict';

const browser=await chromium.launch({headless:true});
try {
  const page=await browser.newPage({baseURL:'http://api:8000'});
  await page.goto('/');
  await page.getByLabel('Owner token').fill(process.env.OWNER_TOKEN);
  await page.getByRole('button',{name:'Open workspace'}).click();
  await page.locator('.sidebar-bottom').getByRole('button',{name:'Routines'}).click();
  await page.getByRole('dialog',{name:'Routines',exact:true}).waitFor();
  for(const width of [1440,768,390]) {
    await page.setViewportSize({width,height:900});
    await page.screenshot({path:`/output/phase52-before-${width}.png`,fullPage:true});
    assert.ok(await page.getByRole('button',{name:'New routine',exact:true}).isVisible());
  }
  console.log('Recorded existing routines before Phase 5.2 UI changes.');
} finally {await browser.close();}
