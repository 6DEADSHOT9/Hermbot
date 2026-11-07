import {chromium} from 'playwright';
import assert from 'node:assert/strict';
import {writeFile} from 'node:fs/promises';

const browser=await chromium.launch({headless:true});
try {
  for(const width of [1440,768,390,320]) {
    const page=await browser.newPage({baseURL:'http://api:8000',viewport:{width,height:900}});
    await page.goto('/');
    await page.getByLabel('Owner token').fill(process.env.OWNER_TOKEN);
    const loaded=page.waitForResponse(r=>r.url().endsWith('/api/v1/state')&&r.status()===200);
    await page.getByRole('button',{name:'Open workspace'}).click();
    await loaded;
    await page.locator('.sidebar .avatar').first().waitFor({state:'attached'});
    if(width<=950) {
      await page.getByRole('button',{name:'Open navigation',exact:true}).click();
      await page.locator('.sidebar.open').waitFor();
    }
    await page.locator('.sidebar-bottom').getByRole('button',{name:'Routines'}).click();
    const dialog=page.getByRole('dialog',{name:'Routines',exact:true});
    await dialog.getByRole('button',{name:'Events',exact:true}).click();
    await dialog.getByText('Archived event triggers',{exact:true}).click();
    await dialog.getByRole('button',{name:'Phase 5.2 arithmetic event proof · History',exact:true}).click();
    await dialog.getByText('For the matching event, 173 + 268 = 441.',{exact:true}).waitFor();
    await dialog.getByRole('heading',{name:'Routines',exact:true}).waitFor();
    await page.evaluate(()=>document.fonts.ready);
    await page.screenshot({path:`/output/phase52-history-final-${width}.png`,fullPage:true});
    assert.equal(await dialog.getByRole('button',{name:'Stop current work'}).count(),0);
    await page.close();
  }
  await writeFile('/output/phase52-archived-browser.json',JSON.stringify({checks:['Real completed occurrence remains readable after source/trigger/group archive','Required report remains visible','No active Stop action for settled work','Fresh desktop/tablet/phone viewport captures'],widths:[1440,768,390,320]},null,2));
  console.log('Archived event history passed in fresh desktop/tablet/phone viewports.');
} finally {await browser.close();}
