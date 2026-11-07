import {chromium} from 'playwright';
import assert from 'node:assert/strict';
import {readFile,writeFile} from 'node:fs/promises';

const proof=JSON.parse(await readFile('/output/phase53-live.json','utf8'));
const browser=await chromium.launch({headless:true});
const page=await browser.newPage({viewport:{width:1440,height:900},baseURL:'http://api:8000'});
page.setDefaultTimeout(20000);
const errors=[],observations=[];
let createdGrant;
page.on('pageerror',e=>errors.push(e.message));
try {
  await page.goto('/');
  await page.getByLabel('Owner token').fill(process.env.OWNER_TOKEN);
  const loaded=page.waitForResponse(r=>r.url().endsWith('/api/v1/state')&&r.status()===200);
  await page.getByRole('button',{name:'Open workspace'}).click();
  await loaded;
  await page.locator('.sidebar-bottom').getByRole('button',{name:'Routines'}).click();
  const dialog=page.getByRole('dialog',{name:'Routines'});
  await dialog.getByRole('button',{name:'New routine',exact:true}).click();
  await dialog.getByLabel(/Results conversation/).selectOption(proof.conversation_id);
  await dialog.getByLabel(/^Teammate/).selectOption('reviewer');
  await dialog.getByLabel('Routine name').fill('Phase 5.3 browser draft — not saved');
  await dialog.getByLabel('Routine instructions').fill('Coordinate the selected teammates and return a local report.');
  await dialog.getByRole('checkbox',{name:'Coordinate teammates',exact:true}).check();
  const section=dialog.locator('.recurring-management');
  assert.ok(await dialog.getByRole('button',{name:'Save routine',exact:true}).isDisabled());
  await section.getByRole('checkbox',{name:'Scout',exact:true}).check();
  await section.getByRole('checkbox',{name:'Quill',exact:true}).check();
  await section.getByLabel('Recurring permission duration').fill('1');
  const response=page.waitForResponse(r=>r.url().endsWith('/api/v1/grants')&&r.request().method()==='POST');
  await section.getByRole('button',{name:'Create management permission'}).click();
  createdGrant=await (await response).json();
  assert.ok(createdGrant.id);
  await section.getByText(/Authorized until/).waitFor();
  assert.equal(await section.getByLabel('Recurring management permission').inputValue(),createdGrant.id);
  await dialog.getByRole('button',{name:'Save routine',exact:true}).and(page.locator(':enabled')).waitFor();
  await dialog.getByText('The management permission expires before the next occurrence. Choose a later permission or an earlier start.').waitFor();
  await section.getByLabel('Recurring management permission').selectOption(proof.grant_id);
  assert.equal(await section.getByRole('checkbox',{name:'Scout',exact:true}).isChecked(),false);
  await section.getByRole('checkbox',{name:'Scout',exact:true}).check();
  await section.getByRole('checkbox',{name:'Quill',exact:true}).check();
  await section.getByText('Management operations',{exact:true}).click();
  assert.ok(await section.getByRole('checkbox',{name:'Assign work',exact:true}).isDisabled());
  const reassign=section.getByRole('checkbox',{name:'Reassign work',exact:true});
  await reassign.focus();
  await reassign.press('Space');
  assert.equal(await reassign.isChecked(),false);
  await reassign.press('Space');
  assert.equal(await reassign.isChecked(),true);
  for(const width of [1440,768,390,320]) {
    await page.setViewportSize({width,height:900});
    await section.scrollIntoViewIfNeeded();
    await page.evaluate(()=>document.fonts.ready);
    const sizes=await page.evaluate(()=>({viewport:innerWidth,document:document.documentElement.scrollWidth,dialog:document.querySelector('.modal').scrollWidth,dialogWidth:document.querySelector('.modal').clientWidth}));
    assert.ok(sizes.document<=width+1&&sizes.dialog<=sizes.dialogWidth+1,JSON.stringify(sizes));
    const result={width,sizes};
    if(process.env.TASTE_SCRIPTS) {
      const {probe,layout,app}=await import(process.env.TASTE_SCRIPTS+'/lib/probe.mjs');
      const {judge,judgeLayout,judgeApp,APP_SKIP}=await import(process.env.TASTE_SCRIPTS+'/lib/rules.mjs');
      const background=await page.addStyleTag({content:'#root { display:none !important; }'});
      try {
        const m=await page.evaluate(probe),lay=await page.evaluate(layout),appM=await page.evaluate(app);
        const allow=['heavy-shadow','no-title'];
        result.inspector=[...judge(m,{width,allow}),...judgeLayout(lay,{width,allow})].filter(f=>!APP_SKIP.has(f.id)&&!['no-title','small-title'].includes(f.id));
        result.inspector.push(...judgeApp(appM,m,{width,allow}));
        assert.deepEqual(result.inspector.filter(f=>f.level==='FAIL'),[],`Recurring form at ${width}`);
      } finally {await background.evaluate(el=>el.remove());}
    }
    await page.screenshot({path:`/output/phase53-management-${width}.png`,fullPage:true});
    observations.push(result);
  }
  await page.setViewportSize({width:1440,height:900});
  await dialog.getByLabel(/^Teammate/).selectOption('researcher');
  assert.equal(await dialog.getByRole('checkbox',{name:'Coordinate teammates',exact:true}).isChecked(),false);
  assert.deepEqual(errors,[]);
  await writeFile('/output/phase53-browser.json',JSON.stringify({checks:['Actual owner grant creation in the routine form','Save unavailable until authority and collaborators are selected','Existing permission selection clears collaborator scope for explicit choice','Delegation remains required; other management operations are editable','Changing manager clears old management scope','No horizontal overflow at 1440, 768, 390 or 320px','No browser errors'],created_grant_id:createdGrant.id,routine_saved:false,observations},null,2));
  console.log('Recurring-management browser checks passed. Draft routine was not saved.');
} catch(error) {
  await page.screenshot({path:'/output/phase53-browser-failure.png',fullPage:true});
  throw error;
} finally {
  if(createdGrant?.id) {
    const response=await page.request.post(`/api/v1/grants/${createdGrant.id}/revoke`,{headers:{Authorization:'Bearer '+process.env.OWNER_TOKEN}});
    assert.ok(response.ok(),'Fixture-only browser permission cleanup failed');
  }
  await browser.close();
}
