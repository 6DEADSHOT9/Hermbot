import {chromium} from 'playwright';
import assert from 'node:assert/strict';
import {writeFile} from 'node:fs/promises';
const browser=await chromium.launch({headless:true});
const page=await browser.newPage({baseURL:'http://api:8000',viewport:{width:390,height:844}});
const errors=[];page.on('pageerror',e=>errors.push(e.message));
try{
  await page.goto('/');
  await page.getByLabel('Owner token').fill(process.env.OWNER_TOKEN);
  await page.getByRole('button',{name:'Open workspace'}).click();
  await page.getByRole('button',{name:'Open navigation'}).click();
  await page.locator('.sidebar-bottom').getByRole('button',{name:'Routines'}).click();
  const dialog=page.getByRole('dialog',{name:'Routines'});
  await dialog.getByText('Archived routines').click();
  await dialog.getByRole('button',{name:'Native routine acceptance 84dcb22a · History'}).click();
  await dialog.getByText('Run history').waitFor();
  await dialog.getByText(/routine proof, 6 multiplied by 7 is 42/i).first().waitFor();
  assert.equal(await dialog.getByRole('button',{name:'Open run and results'}).count(),0);
  const metrics=await page.evaluate(()=>({document:document.documentElement.scrollWidth,viewport:innerWidth,dialog:document.querySelector('.modal').scrollWidth,dialogWidth:document.querySelector('.modal').clientWidth}));
  assert.ok(metrics.document<=metrics.viewport+1&&metrics.dialog<=metrics.dialogWidth+1,JSON.stringify(metrics));
  await dialog.getByRole('button',{name:'New routine'}).click();
  await dialog.locator('.routine-preview li').first().waitFor();
  assert.equal(await dialog.getByLabel('Routine name').inputValue(),'');
  assert.equal(await dialog.getByLabel('Routine instructions').inputValue(),'');
  assert.deepEqual(errors,[]);
  await writeFile('/output/phase5-routines-archived-browser.json',JSON.stringify({archived_history_readable:true,completed_results_visible:true,no_broken_conversation_link:true,mobile_no_horizontal_overflow:true,blank_form_previews_schedule:true},null,2));
  console.log('Archived routine history is readable on a phone.');
}finally{await browser.close();}
