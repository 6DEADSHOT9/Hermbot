import {chromium} from 'playwright';
import assert from 'node:assert/strict';
import {writeFile} from 'node:fs/promises';

const browser=await chromium.launch({headless:true});
const page=await browser.newPage({viewport:{width:1440,height:900},baseURL:'http://api:8000'});
const errors=[];
page.on('pageerror',error=>errors.push(error.message));
page.setDefaultTimeout(20000);
try{
  await page.goto('/');
  await page.getByLabel('Owner token').fill(process.env.OWNER_TOKEN);
  await page.getByRole('button',{name:'Open workspace'}).click();
  await page.locator('.sidebar-bottom').getByRole('button',{name:'Routines'}).click();
  const dialog=page.getByRole('dialog',{name:'Routines'});
  await dialog.waitFor();
  const card=dialog.locator('.task-card').filter({hasText:'Native routine acceptance 84dcb22a'}).first();
  await card.waitFor();
  await card.getByRole('button',{name:'History'}).click();
  await dialog.getByText('Run history').waitFor();
  await dialog.getByRole('button',{name:'Open run and results'}).first().waitFor();
  assert.equal(await dialog.getByRole('button',{name:'Open run and results'}).count(),2);
  await dialog.getByRole('button',{name:'New routine'}).click();
  await dialog.getByLabel('Routine name').fill('Browser form preview');
  await dialog.getByLabel('Routine instructions').fill('Write a local report.');
  await dialog.getByText('Next occurrences').waitFor();
  await dialog.locator('.routine-preview li').first().waitFor();
  for(const size of [{width:1440,height:900},{width:390,height:844},{width:320,height:568}]){
    await page.setViewportSize(size);
    const metrics=await page.evaluate(()=>({document:document.documentElement.scrollWidth,viewport:innerWidth,dialog:document.querySelector('.modal').scrollWidth,dialogWidth:document.querySelector('.modal').clientWidth}));
    assert.ok(metrics.document<=metrics.viewport+1&&metrics.dialog<=metrics.dialogWidth+1,`Horizontal overflow at ${size.width}: ${JSON.stringify(metrics)}`);
    await page.screenshot({path:`/output/phase5-routines-${size.width}.png`,fullPage:true});
  }
  assert.deepEqual(errors,[]);
  await writeFile('/output/phase5-routines-browser.json',JSON.stringify({checks:['Existing native scheduled history lists both completed runs','Create form previews next occurrences','No horizontal overflow at desktop and 390px/320px phones','No browser errors'],saved_form:false,external_effects:false},null,2));
  console.log('Routine browser checks passed; draft form was not saved.');
}finally{await browser.close();}
