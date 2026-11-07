import {chromium} from 'playwright';
import assert from 'node:assert/strict';
import {writeFile} from 'node:fs/promises';

const browser=await chromium.launch({headless:true});
const context=await browser.newContext({baseURL:process.env.HERMBOT_TEST_URL||'http://api:8000',viewport:{width:1440,height:900},reducedMotion:'reduce'});
const page=await context.newPage(),errors=[],checks=[],measurements=[];
page.on('pageerror',e=>errors.push(e.message));
page.setDefaultTimeout(15000);
const section=name=>page.getByRole('button',{name,exact:true});
async function expanded(name,value){if((await section(name).getAttribute('aria-expanded'))!==String(value))await section(name).click();}
async function selectBot(name){if(page.viewportSize().width<=950&&!(await page.locator('.sidebar').getAttribute('class')).includes('open'))await section('Open navigation').click();await expanded('Your teammates',true);await page.getByRole('navigation',{name:'Team conversations'}).getByRole('button',{name:new RegExp(name)}).click();await page.getByRole('heading',{name,exact:true}).waitFor();}
async function closeDialog(){await page.getByRole('dialog').getByRole('button',{name:'Close',exact:true}).click();}
async function noHorizontalOverflow(label){
  const result=await page.evaluate(()=>{
    const modal=document.querySelector('.modal');
    return {viewport:innerWidth,document:document.documentElement.scrollWidth,modal:modal?{width:modal.clientWidth,scroll:modal.scrollWidth}:null};
  });
  assert.ok(result.document<=result.viewport+1,`${label}: document overflow`);
  if(result.modal)assert.ok(result.modal.scroll<=result.modal.width+1,`${label}: dialog overflow ${JSON.stringify(result)}`);
  measurements.push({label,...result});
}
async function footerVisible(height){for(const item of [section('Routines'),page.getByLabel('Owner menu')]){const box=await item.boundingBox();assert.ok(box&&box.y>=0&&box.y+box.height<=height,'Sidebar footer clipped');}}
await page.goto('/');await page.getByLabel('Owner token').fill(process.env.OWNER_TOKEN);await section('Open workspace').click();await page.getByRole('navigation',{name:'Team conversations'}).waitFor();
await selectBot('Scout');await page.locator('.message').first().waitFor();
assert.equal(await section('Past teammates').getAttribute('aria-expanded'),'false');
for(const name of ['Your teammates','Past teammates','Groups']){await expanded(name,false);assert.equal(await section(name).getAttribute('aria-expanded'),'false');await expanded(name,true);assert.equal(await section(name).getAttribute('aria-expanded'),'true');}
await expanded('Past teammates',false);await expanded('Groups',false);
await page.reload();await section('Groups').waitFor();assert.equal(await section('Groups').getAttribute('aria-expanded'),'false');
await page.getByLabel('Find a teammate or group').fill('proof');assert.equal(await section('Groups').getAttribute('aria-expanded'),'true');await page.getByLabel('Find a teammate or group').fill('');assert.equal(await section('Groups').getAttribute('aria-expanded'),'false');
await expanded('Groups',true);await selectBot('Scout');await page.locator('.message').first().waitFor();
checks.push('All three sections collapse, persist across reload, and reveal search results.');

for(const size of [{width:1440,height:900},{width:390,height:844},{width:320,height:568},{width:844,height:390}]){
  await page.setViewportSize(size);
  if(size.width<=950){await section('Open navigation').click();}
  await expanded('Your teammates',false);await expanded('Past teammates',false);await expanded('Groups',true);
  await footerVisible(size.height);
  const groups=page.getByRole('navigation',{name:'Group conversations'}).getByRole('button');
  assert.ok(await groups.count()>1,'Need multiple real groups to verify scrolling');
  await groups.last().scrollIntoViewIfNeeded();await footerVisible(size.height);
  const lastBox=await groups.last().boundingBox(),scrollBox=await page.locator('.sidebar-scroll').boundingBox();
  assert.ok(lastBox.y>=scrollBox.y-1&&lastBox.y+lastBox.height<=scrollBox.y+scrollBox.height+1,`Last group unreachable at ${size.width}×${size.height}: ${JSON.stringify({lastBox,scrollBox})}`);
  await selectBot('Scout');
  if(size.width<=950)assert.equal(await page.locator('.context-panel').count(),0);
  if(size.width<=650){await page.getByLabel('Conversation tools').click();await section('Memory and skills').click();}else await section('Open Scout memory and skills').click();await page.getByRole('button',{name:/^Personal notes/}).waitFor();
  await page.getByLabel('Completed workflow').scrollIntoViewIfNeeded();
  assert.ok(await page.getByLabel('Completed workflow').locator('option').count()>1,'Need real completed workflows');
  await noHorizontalOverflow(`Memory & skills ${size.width}×${size.height}`);
  await page.screenshot({path:`/output/ui-fixed-knowledge-${size.width}.png`,fullPage:true});
  await page.getByRole('button',{name:/^Personal notes/}).click();await page.getByLabel('Knowledge content').waitFor();await noHorizontalOverflow(`Memory editor ${size.width}`);await closeDialog();
  if(size.width<=950)await section('Open navigation').click();
  await page.getByLabel('Owner menu').click();await section('File library').click();await page.getByLabel('Search library').waitFor();await noHorizontalOverflow(`File library ${size.width}`);await closeDialog();
  if(size.width<=650)await selectBot('Scout');
  if(size.width<=950){await section('Toggle work panel').click();await page.locator('.context-panel').waitFor();await section('Close work panel backdrop').click({position:{x:10,y:10}});assert.equal(await page.locator('.context-panel').count(),0);await section('Toggle work panel').click();await page.keyboard.press('Escape');assert.equal(await page.locator('.context-panel').count(),0);}
  if(size.width<=650){await section('Open navigation').click();await expanded('Your teammates',false);await page.screenshot({path:`/output/ui-fixed-navigation-${size.width}.png`,fullPage:true});await selectBot('Scout');}
  await noHorizontalOverflow(`Workspace ${size.width}`);
}
checks.push('Dialogs have no horizontal overflow at desktop, phone, small phone, and landscape sizes; last group and footer remain reachable.');
checks.push('Compact work panel starts closed, closes on resize, and supports backdrop/Escape dismissal.');
const mobile=await browser.newContext({baseURL:process.env.HERMBOT_TEST_URL||'http://api:8000',viewport:{width:390,height:844},storageState:await context.storageState()});
const mobilePage=await mobile.newPage();mobilePage.on('pageerror',e=>errors.push(e.message));await mobilePage.goto('/');await mobilePage.getByRole('button',{name:'Open navigation'}).waitFor();assert.equal(await mobilePage.locator('.context-panel').count(),0);await mobile.close();

// Controlled delayed responses test client races without executing model tasks.
await page.setViewportSize({width:1440,height:900});await selectBot('Scout');
const state=await page.evaluate(async()=>{const r=await fetch('/api/v1/state');return r.json();});
const scout=state.conversations.find(c=>c.bot_id==='researcher'),quill=state.conversations.find(c=>c.bot_id==='writer'),sage=state.conversations.find(c=>c.bot_id==='reviewer');
assert.ok(scout&&quill&&sage);
let releaseLoad,loadSeen;
const loadGate=new Promise(resolve=>releaseLoad=resolve),loadStarted=new Promise(resolve=>loadSeen=resolve);
await page.route(`**/api/v1/conversations/${sage.id}`,async route=>{loadSeen();await loadGate;await route.continue();});
// Reload clears the query cache so the loading assertion is deterministic even if Sage was initially selected.
await page.reload();await page.getByRole('navigation',{name:'Team conversations'}).waitFor();
await selectBot('Sage');await loadStarted;await page.getByText('Loading conversation…',{exact:true}).waitFor();assert.equal(await page.locator('.welcome').count(),0);releaseLoad();await page.getByText('Loading conversation…',{exact:true}).waitFor({state:'hidden'});await page.unroute(`**/api/v1/conversations/${sage.id}`);
checks.push('Pending conversation loads show a loading notice instead of an empty welcome screen.');

let releaseEarlier,earlierSeen;
const earlierGate=new Promise(resolve=>releaseEarlier=resolve),earlierStarted=new Promise(resolve=>earlierSeen=resolve);
const marker='UI_REGRESSION_OLDER_SCOUT_MESSAGE';
await page.route(`**/api/v1/conversations/${scout.id}*`,async route=>{
  const url=new URL(route.request().url());
  if(url.searchParams.has('before')){
    earlierSeen();await earlierGate;
    await route.fulfill({status:200,contentType:'application/json',body:JSON.stringify({...scout,messages:[{id:-10001,role:'user',author:'owner',content:marker,created_at:new Date().toISOString(),attachment_ids:[],mentions:[],reply_count:0,reactions:[]}],artifacts:[],attachments:[],has_earlier:false})});
  }else{const response=await route.fetch(),body=await response.json();await route.fulfill({response,json:{...body,has_earlier:true}});}
});
await page.reload();await selectBot('Scout');await section('Load earlier messages').click();await earlierStarted;await selectBot('Quill');releaseEarlier();await page.waitForTimeout(400);assert.equal(await page.getByText(marker,{exact:true}).count(),0);await page.unroute(`**/api/v1/conversations/${scout.id}*`);
checks.push('Delayed older messages cannot leak into another conversation.');

// Thread pagination must not hide the main conversation's earlier messages.
const scoutSnapshot=await(await context.request.get(`/api/v1/conversations/${scout.id}`)).json();
await page.route(`**/api/v1/conversations/${scout.id}*`,async route=>{
  const url=new URL(route.request().url());
  if(url.searchParams.has('thread')){
    await route.fulfill({status:200,json:{...scoutSnapshot,messages:scoutSnapshot.messages.slice(0,1),has_earlier:!url.searchParams.has('before')}});
  }else await route.fulfill({status:200,json:{...scoutSnapshot,has_earlier:true}});
});
await page.reload();await selectBot('Scout');await page.locator('.message-actions').getByRole('button',{name:/^Reply/}).first().click();await section('Close thread').waitFor();
await section('Load earlier messages').click();await section('Load earlier messages').waitFor({state:'hidden'});await section('Close thread').click();await section('Load earlier messages').waitFor();await page.unroute(`**/api/v1/conversations/${scout.id}*`);
checks.push('Closing a thread resets its pagination state before returning to main history (controlled GET responses).');

let releaseSend,sendSeen;
const sendGate=new Promise(resolve=>releaseSend=resolve),sendStarted=new Promise(resolve=>sendSeen=resolve);
await page.route('**/api/v1/messages',async route=>{assert.equal(route.request().method(),'POST');sendSeen();await sendGate;await route.fulfill({status:202,contentType:'application/json',body:'{"id":"ui-only-intercepted-message"}'});});
await selectBot('Scout');await page.getByLabel('Message your teammate').fill('Intercepted UI request; no model task is created.');await page.getByLabel('Message your teammate').press('Enter');await sendStarted;
await selectBot('Quill');await page.getByLabel('Message your teammate').fill('Keep this draft in the newly selected conversation.');
const sendResponse=page.waitForResponse(r=>r.url().endsWith('/api/v1/messages')&&r.request().method()==='POST');releaseSend();await sendResponse;await page.waitForTimeout(250);assert.equal(await page.getByLabel('Message your teammate').inputValue(),'Keep this draft in the newly selected conversation.');
await page.unroute('**/api/v1/messages');checks.push('A delayed send cannot erase a draft in another conversation (intercepted POST; no model execution).');
assert.deepEqual(errors,[]);
await writeFile('/output/ui-regressions.json',JSON.stringify({checks,measurements,script_errors:errors},null,2));
await browser.close();console.log('UI regressions passed: collapsible sidebar, responsive dialogs, footer, overlays, loading, pagination and draft races.');
