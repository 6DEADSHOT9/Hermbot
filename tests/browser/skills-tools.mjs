import {chromium} from 'playwright';
import assert from 'node:assert/strict';
import {readFile,writeFile} from 'node:fs/promises';

const proof=JSON.parse(await readFile('/output/skills-tools-native.json','utf8'));
assert.equal(proof.complete,true);
assert.equal(proof.fixture_archived,true);
const browser=await chromium.launch({headless:true});
const page=await browser.newPage({viewport:{width:1440,height:1000}}),errors=[];
page.on('pageerror',e=>errors.push(e.message));
const call=(path,method='GET',body)=>page.evaluate(async({path,method,body})=>{
  const response=await fetch(path,{method,...(body?{headers:{'Content-Type':'application/json'},body:JSON.stringify(body)}:{})});
  if(!response.ok)throw Error('Request failed '+response.status);
  return response.json();
},{path,method,body});
let restored=false;
try{
  await page.goto(process.env.HERMBOT_TEST_URL||'http://api:8000');
  await page.getByLabel('Owner token').fill(process.env.OWNER_TOKEN);
  await page.getByRole('button',{name:'Open workspace'}).click();
  await page.getByRole('navigation',{name:'Team conversations'}).waitFor();
  await call('/api/v1/conversations/'+proof.conversation_id+'/restore','POST',{});
  restored=true;
  await page.reload();
  const state=await call('/api/v1/state');
  const group=state.conversations.find(c=>c.id===proof.conversation_id);
  assert.ok(group.title.startsWith('Skills and tools acceptance '));
  await page.getByRole('navigation',{name:'Group conversations'}).getByRole('button',{name:new RegExp(group.title)}).click();
  assert.equal(await page.getByLabel('Message intent',{exact:true}).count(),0);
  assert.equal(await page.getByLabel('Reference skill',{exact:true}).count(),0);
  assert.equal(await page.locator('select[aria-label^="Attach message "]').count(),0);
  const panel=page.getByRole('complementary',{name:'Work and outputs'});
  await panel.locator('summary').filter({hasText:'Finished work (3)'}).click();
  assert.equal(await panel.locator('.task-card:visible').count(),3);
  const brief=proof.tasks.find(t=>t.case==='brief');
  await panel.locator(`[data-work-item-id="${brief.work_item_id}"]`).getByRole('button',{name:'Inspect work',exact:true}).click();
  let dialog=page.getByRole('dialog',{name:'Inspect the work',exact:true});
  await dialog.getByText('Work preparation',{exact:true}).waitFor();
  assert.match(await dialog.locator('.task-preparation').innerText(),/hermbot-brief-/);
  await dialog.getByRole('button',{name:'Close',exact:true}).click();
  const handoff=proof.tasks.find(t=>t.case==='handoff');
  await panel.locator(`[data-work-item-id="${handoff.work_item_id}"]`).getByRole('button',{name:'Inspect work',exact:true}).click();
  dialog=page.getByRole('dialog',{name:'Inspect the work',exact:true});
  await dialog.getByText('Teammate brief',{exact:true}).waitFor();
  assert.match(await dialog.innerText(),/1 shared attachments/);
  await dialog.getByRole('button',{name:'Close',exact:true}).click();
  await page.getByRole('button',{name:'Open Scout memory and skills',exact:true}).click();
  dialog=page.getByRole('dialog',{name:'Scout · Memory & skills',exact:true});
  await dialog.locator('summary').filter({hasText:'Tools & application access'}).click();
  await dialog.getByText('Run local commands',{exact:true}).waitFor();
  assert.match(await dialog.locator('.capability-summary').innerText(),/App connections keep enabled tools available/);
  await dialog.getByText('Your teammate automatically consults relevant enabled skills for each assignment.',{exact:false}).waitFor();
  const overflow=[];
  for(const width of [1440,390]){
    await page.setViewportSize({width,height:900});
    await page.waitForTimeout(200);
    const dimensions=await dialog.evaluate(e=>({scroll:e.scrollWidth,client:e.clientWidth}));
    assert.ok(dimensions.scroll<=dimensions.client+1);
    assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>window.innerWidth+1),false);
    await page.screenshot({path:`/output/skills-tools-${width}.png`,fullPage:true});
    overflow.push({width,horizontal:false});
  }
  assert.deepEqual(errors,[]);
  await writeFile('/output/skills-tools-ui.json',JSON.stringify({complete:true,manual_skill_or_context_controls:false,capability_restrictions_explained:true,consulted_skills_visible:true,handoff_expected_output_and_shared_inputs_visible:true,overflow,script_errors:errors},null,2));
  console.log('Automatic composer, capability diagnostics, preparation/handoff inspection and desktop/mobile checks passed.');
}finally{
  try{if(restored){await call('/api/v1/groups/'+proof.conversation_id,'DELETE');assert.equal((await call('/api/v1/state')).conversations.some(c=>c.id===proof.conversation_id),false);}}finally{await browser.close();}
}
