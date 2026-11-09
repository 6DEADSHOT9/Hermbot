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
async function selectProof(page) {
  const button=page.getByRole('navigation',{name:'Group conversations'}).getByRole('button',{name:'Live proof · manager and dependent assignments',exact:false}).last();
  await button.scrollIntoViewIfNeeded();
  await button.click();
  await page.getByRole('heading',{name:'Live proof · manager and dependent assignments',exact:true}).waitFor();
  await page.locator('.task-card').first().waitFor();
}

let {context,page}=await openWorkspace();
const before=await snapshot(page);
assert.equal(before.protocol_fixture,false);
await selectProof(page);
await page.screenshot({path:'/output/desktop.png',fullPage:true});
await context.close();
({context,page}=await openWorkspace());
const after=await snapshot(page);
await selectProof(page);
const proof=before.conversations.filter(item=>item.title==='Live proof · manager and dependent assignments').at(-1);
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
if(await page.getByRole('button',{name:'Close work panel',exact:true}).isVisible())await page.getByRole('button',{name:'Close work panel',exact:true}).click();
await page.getByRole('button',{name:'Open navigation'}).click();
await page.getByLabel('Owner menu').click();const settingsPosition=await page.getByRole('button',{name:'Settings',exact:true}).boundingBox();
assert.ok(settingsPosition&&settingsPosition.y>=0&&settingsPosition.y+settingsPosition.height<=844);
await page.getByRole('navigation',{name:'Group conversations'}).getByRole('button',{name:'Live proof · manager and dependent assignments',exact:false}).last().click();
await page.waitForFunction(()=>document.querySelector('.sidebar').getBoundingClientRect().right<=1);
await page.screenshot({path:'/output/mobile.png',fullPage:true});
assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>window.innerWidth),false);
assert.deepEqual(errors,[]);
await writeFile('/output/browser-reconnect.json',JSON.stringify({live_model_results:true,check:'fresh browser login preserves collaborative tasks and artifact access; desktop/mobile UI',task_ids:ids,artifact_name:artifactName,script_errors:errors},null,2));
console.log('Fresh browser reconnect, durable manager results, artifact preview, theme and mobile layout passed.');
await browser.close();
