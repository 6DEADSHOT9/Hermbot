import {chromium} from 'playwright';
import assert from 'node:assert/strict';
import {writeFile} from 'node:fs/promises';

const browser=await chromium.launch({headless:true});
const page=await browser.newPage({baseURL:process.env.HERMBOT_TEST_URL||'http://api:8000',viewport:{width:1440,height:900}});
const errors=[];
page.on('pageerror',error=>errors.push(error.message));
page.setDefaultTimeout(15000);
let fixtureId;
try {
  await page.goto('/');
  await page.getByLabel('Owner token').fill(process.env.OWNER_TOKEN);
  await page.getByRole('button',{name:'Open workspace',exact:true}).click();
  await page.getByRole('navigation',{name:'Team conversations'}).waitFor();
  assert.equal(await page.getByRole('navigation',{name:'Team conversations'}).getByRole('button').count(),3);
  assert.equal(await page.getByRole('button',{name:/^Phase 2 UI (manager|specialist)/}).count(),0);
  const state=await page.evaluate(async()=>{const r=await fetch('/api/v1/state');return r.json();});
  assert.deepEqual(state.conversations.filter(c=>c.kind==='group').map(c=>c.title),['The dawn of ai']);
  const title='Cleanup UI verification '+Date.now();
  const fixture=await page.evaluate(async title=>{
    const r=await fetch('/api/v1/groups',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({title,bot_ids:['researcher','writer']})});
    if(!r.ok)throw new Error('Could not create isolated UI fixture');
    return r.json();
  },title);
  fixtureId=fixture.id;
  await writeFile('/output/group-cleanup-ui.json',JSON.stringify({fixture_id:fixtureId,status:'started'},null,2));
  await page.reload();
  const section=page.getByRole('button',{name:'Groups',exact:true});
  await section.waitFor();
  if(await section.getAttribute('aria-expanded')==='false')await section.click();
  await page.getByRole('navigation',{name:'Group conversations'}).getByRole('button',{name:new RegExp(title)}).click();
  await page.getByRole('button',{name:'Edit group',exact:true}).click();
  const removalRoute='**/api/v1/groups/'+fixtureId;
  await page.route(removalRoute,route=>route.request().method()==='DELETE'?route.fulfill({status:409,contentType:'application/json',body:JSON.stringify({detail:'Stop or settle all conversation work before removing it'})}):route.continue());
  const dialog=page.getByRole('dialog',{name:'Edit group'});
  await dialog.getByRole('button',{name:'Remove group',exact:true}).click();
  await dialog.getByRole('alert').filter({hasText:'Stop or settle all conversation work'}).waitFor();
  await page.unroute(removalRoute);
  await dialog.getByRole('button',{name:'Remove group',exact:true}).click();
  await page.getByRole('dialog').waitFor({state:'hidden'});
  await page.getByRole('navigation',{name:'Group conversations'}).getByRole('button',{name:new RegExp(title)}).waitFor({state:'hidden'});
  const updated=await page.evaluate(async()=>{const r=await fetch('/api/v1/state');return r.json();});
  assert.ok(!updated.conversations.some(c=>c.id===fixtureId));
  assert.deepEqual(updated.conversations.filter(c=>c.kind==='group').map(c=>c.title),['The dawn of ai']);
  await page.getByRole('heading',{name:/^(Scout|Quill|Sage)$/}).waitFor();
  await page.setViewportSize({width:390,height:844});
  await page.getByRole('button',{name:'Open navigation',exact:true}).click();
  assert.equal(await page.getByRole('navigation',{name:'Team conversations'}).getByRole('button').count(),3);
  assert.equal(await page.getByRole('navigation',{name:'Group conversations'}).getByRole('button').count(),1);
  assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false);
  assert.deepEqual(errors,[]);
  await writeFile('/output/group-cleanup-ui.json',JSON.stringify({fixture_id:fixtureId,status:'passed',ui_removal_verified:true,blocked_removal_error_visible:true,regular_group_preserved:true,remaining_teammates:3,desktop_and_mobile_checked:true,script_errors:errors},null,2));
  console.log('Group removal UI passed; three teammates and the regular group remain visible.');
} finally {
  if(fixtureId)await page.evaluate(async id=>{await fetch('/api/v1/groups/'+id,{method:'DELETE'});},fixtureId).catch(()=>{});
  await browser.close();
}
