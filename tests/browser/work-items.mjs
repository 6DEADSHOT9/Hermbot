import {chromium} from 'playwright';
import assert from 'node:assert/strict';
import {readFile,writeFile} from 'node:fs/promises';

const proof=JSON.parse(await readFile('/output/work-items-native.json','utf8'));
assert.equal(proof.complete,true);
const browser=await chromium.launch({headless:true});
const context=await browser.newContext({viewport:{width:1440,height:1000}});
const page=await context.newPage(),errors=[],turns=[];
const terminal=new Set(['completed','failed','cancelled','interrupted','uncertain']);
let restored=false;
page.on('pageerror',e=>errors.push(e.message));
const call=(path,body,method)=>page.evaluate(async({path,body,method})=>{
  const r=await fetch(path,{method:method||(body?'POST':'GET'),...(body?{headers:{'Content-Type':'application/json'},body:JSON.stringify(body)}:{})});
  if(!r.ok)throw Error(`Request failed ${r.status}`);
  return r.json();
},{path,body,method});
async function state(){return call('/api/v1/state');}
async function until(test,timeout=90000){const end=Date.now()+timeout;while(Date.now()<end){const s=await state();if(test(s))return s;await page.waitForTimeout(300);}throw Error('Work items did not reach expected state');}
const visible=s=>s.work_items.filter(w=>w.conversation_id===proof.conversation_id&&!w.hidden_at&&w.current_task);
async function noContextControls(){
  assert.equal(await page.getByLabel('Message intent',{exact:true}).count(),0);
  assert.equal(await page.locator('select[aria-label^="Attach message "]').count(),0);
  assert.equal(await page.getByRole('button',{name:'Create work item',exact:true}).count(),0);
  assert.equal(await page.getByRole('button',{name:'Keep as discussion',exact:true}).count(),0);
}
async function send(prompt,intent,workItemId=null){
  await page.getByLabel('Message your teammate').fill(prompt);
  const sent=page.waitForResponse(r=>r.request().method()==='POST'&&r.url().endsWith('/api/v1/messages'));
  await page.getByRole('button',{name:'Send message',exact:true}).click();
  const reply=await sent;
  assert.equal(reply.status(),202);
  const turn=await reply.json();
  turns.push({task_id:turn.id});
  const payload=reply.request().postDataJSON();
  assert.equal(payload.intent,'auto');
  assert.equal(Object.hasOwn(payload,'work_item_id'),false);
  const final=await until(s=>terminal.has(s.tasks.find(t=>t.id===turn.id)?.status),300000);
  const completed=final.tasks.find(t=>t.id===turn.id);
  assert.equal(completed.status,'completed');
  assert.equal(completed.message_intent,intent);
  assert.equal(completed.work_item_id,workItemId);
  assert.equal(visible(final).length,2);
  Object.assign(turns.at(-1),{intent:completed.message_intent,work_item_id:completed.work_item_id,status:completed.status});
  return completed;
}
try{
await page.goto(process.env.HERMBOT_TEST_URL||'http://api:8000');
await page.getByLabel('Owner token').fill(process.env.OWNER_TOKEN);
await page.getByRole('button',{name:'Open workspace'}).click();
await page.getByRole('navigation',{name:'Team conversations'}).waitFor();
await call('/api/v1/conversations/'+proof.conversation_id+'/restore',{});
restored=true;
const original=await state();
const group=original.conversations.find(g=>g.id===proof.conversation_id);
assert.ok(group?.title.startsWith('Work item acceptance '),'Only the recorded acceptance fixture may be used');
assert.equal(visible(original).length,2);
await page.reload();
await page.getByRole('navigation',{name:'Group conversations'}).getByRole('button',{name:new RegExp(group.title)}).click();
const panel=page.getByRole('complementary',{name:'Work and outputs'});
await panel.locator('summary').filter({hasText:'Finished work (2)'}).click();
await panel.locator('.task-card').first().waitFor();
assert.equal(await panel.locator('.task-card:visible').count(),2);
await noContextControls();
await send('Thanks for walking me through this. No changes needed.','discussion');
const report=proof.tasks.find(t=>t.expected_intent==='update').work_item_id;
await send('Revise the team organization report in team-organization.md: add one closing sentence about keeping related revisions together. Read the previous published report and publish the updated report.','update',report);
const beforeStatus=await state();
const assignmentsBefore=beforeStatus.tasks.filter(t=>t.work_item_id===report&&['assignment','update'].includes(t.message_intent)).length;
const currentBefore=beforeStatus.work_items.find(w=>w.id===report).current_task.id;
await send('What is the current progress of the team organization report?','status',report);
const afterStatus=await state();
assert.equal(afterStatus.tasks.filter(t=>t.work_item_id===report&&['assignment','update'].includes(t.message_intent)).length,assignmentsBefore);
assert.equal(afterStatus.work_items.find(w=>w.id===report).current_task.id,currentBefore);
await noContextControls();
const first=panel.locator(`[data-work-item-id="${proof.items[0].id}"]`);
await first.getByRole('button',{name:'Remove from work list',exact:true}).click();
await until(s=>!!s.work_items.find(w=>w.id===proof.items[0].id)?.hidden_at);
await panel.locator('summary').filter({hasText:'Removed work (1)'}).click();
await panel.getByRole('button',{name:'Restore to work list',exact:true}).click();
await until(s=>!s.work_items.find(w=>w.id===proof.items[0].id)?.hidden_at);
await page.reload();
await page.getByRole('navigation',{name:'Team conversations'}).waitFor();
await page.getByRole('navigation',{name:'Group conversations'}).getByRole('button',{name:new RegExp(group.title)}).click();
assert.equal((await state()).work_items.filter(w=>w.conversation_id===proof.conversation_id&&!w.hidden_at&&w.current_task).length,2);
assert.equal(await panel.locator('summary').filter({hasText:'Finished work (2)'}).count(),1);
const overflow=[];
for(const width of [1440,390]){
  await page.setViewportSize({width,height:900});
  await page.waitForTimeout(200);
  if(width===390)await page.getByRole('button',{name:'Toggle work panel',exact:true}).click();
  await panel.locator('summary').filter({hasText:'Finished work (2)'}).click();
  assert.equal(await panel.locator('.task-card:visible').count(),2);
  await noContextControls();
  const over=await page.evaluate(()=>({document:document.documentElement.scrollWidth>window.innerWidth+1,panel:(()=>{const e=document.querySelector('.context-scroll');return e.scrollWidth>e.clientWidth+1;})()}));
  assert.equal(over.document,false);assert.equal(over.panel,false);overflow.push({width,...over});
  await page.screenshot({path:`/output/work-items-${width}.png`,fullPage:true});
  await panel.locator('summary').filter({hasText:'Finished work (2)'}).click();
}
assert.deepEqual(errors,[]);
await writeFile('/output/work-items-ui.json',JSON.stringify({complete:true,conversation_id:proof.conversation_id,visible_work_items:2,manual_context_controls_removed:true,automatic_routes:turns,composer_discussion_created_no_work:true,revision_automatically_related_to_existing_work:true,status_did_not_restart_work:true,hide_restore_preserved_execution:true,reload_preserved_grouping:true,overflow,script_errors:errors},null,2));
console.log('Automatic discussion, revision and status routing, hide/restore and desktop/mobile checks passed.');
}finally{
  try{
    if(restored){
      const current=await state();
      for(const turn of turns){
        if(!terminal.has(current.tasks.find(t=>t.id===turn.task_id)?.status))await call('/api/v1/tasks/'+turn.task_id+'/stop',{});
      }
      await until(s=>turns.every(turn=>terminal.has(s.tasks.find(t=>t.id===turn.task_id)?.status)));
      for(const item of proof.items)if((await state()).work_items.find(w=>w.id===item.id)?.hidden_at)await call('/api/v1/work-items/'+item.id+'/restore',{});
      await call('/api/v1/groups/'+proof.conversation_id,null,'DELETE');
      assert.equal((await state()).conversations.some(g=>g.id===proof.conversation_id),false);
    }
  }finally{await browser.close();}
}
