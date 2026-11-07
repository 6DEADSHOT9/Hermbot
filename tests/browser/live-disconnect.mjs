import {chromium} from 'playwright';
import assert from 'node:assert/strict';
import {writeFile} from 'node:fs/promises';

const baseURL=process.env.HERMBOT_TEST_URL || 'http://api:8000';
const title='Live browser · disconnect during execution';
const browser=await chromium.launch({headless:true});
const errors=[];
async function openWorkspace() {
  const context=await browser.newContext({viewport:{width:1440,height:1000}});
  const page=await context.newPage();
  page.on('pageerror',error=>errors.push(error.message));
  await page.goto(baseURL);
  await page.getByLabel('Owner token').fill(process.env.OWNER_TOKEN);
  await page.getByRole('button',{name:'Open workspace'}).click();
  await page.getByRole('navigation',{name:'Team conversations'}).waitFor();
  return {context,page};
}
async function state() {
  const response=await fetch(baseURL+'/api/v1/state',{headers:{Authorization:'Bearer '+process.env.OWNER_TOKEN}});
  assert.ok(response.ok);
  return response.json();
}
async function waitTask(id,predicate,timeout=180000) {
  const deadline=Date.now()+timeout;
  while(Date.now()<deadline) {
    const task=(await state()).tasks.find(t=>t.id===id);
    if(predicate(task)) return task;
    assert.ok(!['failed','cancelled','uncertain','interrupted'].includes(task.status),`Task settled unexpectedly: ${task.status}`);
    await new Promise(resolve=>setTimeout(resolve,500));
  }
  throw new Error('Timed out waiting for task');
}
assert.equal((await state()).protocol_fixture,false);
let {context,page}=await openWorkspace();
await page.getByRole('button',{name:'New group conversation'}).click();
await page.getByLabel('Conversation name').fill(title);
// The owner-managed roster is dynamic; explicitly select the two native proof bots.
for(const member of await page.locator('.member-list label').all()) {
  const name=await member.innerText();
  await member.getByRole('checkbox').setChecked(name.startsWith('Scout')||name.startsWith('Quill'));
}
const created=page.waitForResponse(response=>response.url().endsWith('/api/v1/groups')&&response.request().method()==='POST');
await page.getByRole('button',{name:'Create conversation',exact:true}).click();
assert.ok((await created).ok(),'Could not create the disconnect proof group');
await page.getByRole('heading',{name:title,exact:true}).waitFor();
await page.locator('.target-picker select').selectOption('writer');
await page.getByLabel('Message your teammate').fill('Immediately call terminal with sleep 20. Then publish browser-recovery.txt containing BROWSER_CLOSED_EXECUTION_CONTINUED. Call hermbot_finish and end. This is a controlled local browser disconnect test; do not delegate or ask for review.');
const submitted=page.waitForResponse(response=>response.url().endsWith('/api/v1/tasks') && response.request().method()==='POST');
await page.getByRole('button',{name:'Send message',exact:true}).click();
const response=await submitted;
assert.ok(response.ok());
const task=await response.json();
await waitTask(task.id,t=>t.status==='running');
await context.close();
console.log(JSON.stringify({check:'browser closed during live execution',task_id:task.id}));
const completed=await waitTask(task.id,t=>t.status==='completed');
assert.equal(completed.attempts,1);
({context,page}=await openWorkspace());
await page.getByRole('navigation',{name:'Group conversations'}).getByRole('button',{name:title,exact:false}).last().click();
await page.getByRole('heading',{name:title,exact:true}).waitFor();
await page.getByRole('button',{name:/^Outputs/}).click();
await page.locator('.artifact-card').filter({hasText:'browser-recovery.txt'}).click();
await page.getByRole('dialog').waitFor();
assert.ok((await page.locator('.artifact-preview').innerText()).includes('BROWSER_CLOSED_EXECUTION_CONTINUED'));
assert.deepEqual(errors,[]);
await writeFile('/output/browser-live-disconnect.json',JSON.stringify({live_model_acceptance:true,check:'real UI submission, browser closed during execution, fresh login and artifact recovery',task_id:task.id,status:completed.status,attempts:completed.attempts,script_errors:errors},null,2));
console.log('Real UI submission, active browser disconnect, continued execution and artifact recovery passed.');
await browser.close();
