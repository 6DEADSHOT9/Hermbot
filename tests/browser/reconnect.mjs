import {chromium} from 'playwright';
import assert from 'node:assert/strict';
import {writeFile} from 'node:fs/promises';

const browser = await chromium.launch({headless:true});
const baseURL = process.env.HERMBOT_TEST_URL || 'http://api:8000';
const errors = [];
async function openWorkspace() {
  const context = await browser.newContext({viewport:{width:1440,height:1000}});
  const page = await context.newPage();
  page.on('pageerror',error=>errors.push(error.message));
  await page.goto(baseURL);
  await page.getByLabel('Owner token').fill(process.env.OWNER_TOKEN);
  await page.getByRole('button',{name:'Open workspace'}).click();
  await page.getByRole('navigation',{name:'Team conversations'}).waitFor();
  const cookies = await context.cookies();
  assert.ok(cookies.some(cookie=>cookie.httpOnly && cookie.sameSite==='Strict'));
  return {context,page};
}
async function snapshot(page) {
  return page.evaluate(async()=>{
    const response=await fetch('/api/v1/state');
    if(!response.ok) throw new Error('Could not read workspace state');
    return response.json();
  });
}
// The workspace's own collaborative history: the group with the most outputs among those with at least three tasks.
function chooseProof(state) {
  const conversationOf=new Map(state.tasks.map(task=>[task.id,task.conversation_id]));
  const outputs=id=>state.artifacts.filter(artifact=>conversationOf.get(artifact.task_id)===id).length;
  const groups=state.conversations.filter(item=>item.kind==='group'&&!item.archived_at&&state.tasks.filter(task=>task.conversation_id===item.id).length>=3&&outputs(item.id)>0);
  assert.ok(groups.length,'Needs a group conversation with at least three tasks and an output');
  return groups.sort((a,b)=>outputs(b.id)-outputs(a.id))[0];
}
async function selectProof(page,title) {
  const button=page.getByRole('navigation',{name:'Group conversations'}).getByRole('button',{name:title,exact:false}).last();
  await button.scrollIntoViewIfNeeded();
  await button.click();
  await page.getByRole('heading',{name:title,exact:true}).waitFor();
  // Each task's result is shown as a response in the conversation timeline.
  await page.locator('[data-response-task-id]').first().waitFor();
}

let {context,page}=await openWorkspace();
const before=await snapshot(page);
assert.equal(before.protocol_fixture,false);
const proof=chooseProof(before);
await selectProof(page,proof.title);
await page.screenshot({path:'/output/desktop.png',fullPage:true});
await context.close();
({context,page}=await openWorkspace());
const after=await snapshot(page);
await selectProof(page,proof.title);
const ids=before.tasks.filter(item=>item.conversation_id===proof.id).map(item=>item.id);
assert.ok(ids.length>=3);
assert.deepEqual(after.tasks.filter(item=>ids.includes(item.id)).map(item=>[item.id,item.status]),before.tasks.filter(item=>ids.includes(item.id)).map(item=>[item.id,item.status]));
await page.getByRole('button',{name:/^Outputs/}).click();
await page.locator('.artifact-card').first().waitFor();
const artifactName=await page.locator('.artifact-card strong').first().innerText();
await page.locator('.artifact-card').first().click();
await page.getByRole('dialog').waitFor();
assert.ok((await page.locator('.artifact-preview').innerText()).length>0);
await page.getByRole('dialog').getByRole('button',{name:'Close',exact:true}).click();
await page.getByLabel('Owner menu').click();await page.getByRole('button',{name:'Switch to dark theme'}).click();
assert.equal(await page.locator('html').getAttribute('data-theme'),'dark');
await page.setViewportSize({width:390,height:844});
// The compact layout closes the work panel itself; close it by hand only if it stays open.
await page.locator('.context-panel').waitFor({state:'detached',timeout:5000}).catch(()=>page.getByRole('button',{name:'Close work panel',exact:true}).click());
await page.getByRole('button',{name:'Open navigation'}).click();
await page.getByLabel('Owner menu').click();const settingsPosition=await page.getByRole('button',{name:'Settings',exact:true}).boundingBox();
assert.ok(settingsPosition&&settingsPosition.y>=0&&settingsPosition.y+settingsPosition.height<=844);
await page.getByRole('navigation',{name:'Group conversations'}).getByRole('button',{name:proof.title,exact:false}).last().click();
await page.waitForFunction(()=>document.querySelector('.sidebar').getBoundingClientRect().right<=1);
await page.screenshot({path:'/output/mobile.png',fullPage:true});
assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>window.innerWidth),false);
assert.deepEqual(errors,[]);
await writeFile('/output/browser-reconnect.json',JSON.stringify({live_model_results:true,conversation:proof.title,check:'fresh browser login preserves collaborative tasks and artifact access; desktop/mobile UI',task_ids:ids,artifact_name:artifactName,script_errors:errors},null,2));
console.log('Fresh browser reconnect, durable manager results, artifact preview, theme and mobile layout passed.');
await browser.close();
