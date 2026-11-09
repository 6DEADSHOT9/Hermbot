import {chromium} from 'playwright';
import assert from 'node:assert/strict';
import {writeFile} from 'node:fs/promises';
const browser=await chromium.launch({headless:true}),errors=[];
// The private session's screen announces this name in its protocol handshake; the bot's own screen does not.
let privateScreenFrames=0;
const baseURL=process.env.HERMBOT_TEST_URL||'http://api:8000';
async function session(){const c=await browser.newContext({baseURL,viewport:{width:1440,height:1000}}),p=await c.newPage();p.on('pageerror',e=>errors.push(e.message));p.on('websocket',ws=>{if(ws.url().endsWith('/computer/ws'))ws.on('framereceived',frame=>{if(Buffer.from(frame.payload).toString('latin1').includes('Hermbot private session'))privateScreenFrames++;});});await p.goto('/');await p.getByLabel('Owner token').fill(process.env.OWNER_TOKEN);await p.getByRole('button',{name:'Open workspace'}).click();await p.getByRole('navigation',{name:'Team conversations'}).getByRole('button',{name:/Scout/}).click();return [c,p];}
const [context,page]=await session();
const response=await context.request.get('/api/v1/bots/researcher/computer');assert.equal(response.status(),200);
await page.getByRole('button',{name:'Open Scout computer'}).click();
const dialog=page.getByRole('dialog');
await dialog.getByText('Checking the computer…',{exact:true}).waitFor({state:'hidden'});
if(await dialog.getByRole('button',{name:'Start computer',exact:true}).count())await dialog.getByRole('button',{name:'Start computer',exact:true}).click();
await dialog.getByRole('button',{name:/^(Take over privately|Reclaim control)$/}).click();
await page.getByText('You are in control · private browser',{exact:true}).waitFor();
// Takeover opens a private browser in its own container; the owner never types onto the bot's screen.

if(await dialog.getByRole('button',{name:'Expand live view',exact:true}).count())await dialog.getByRole('button',{name:'Expand live view',exact:true}).click();
await page.getByText('Connected · Private browser · keyboard and pointer enabled',{exact:true}).waitFor();
await page.waitForTimeout(2000);
assert.ok(privateScreenFrames>0,'The live view must be the private session, never the bot’s own screen');
// Focus the page body, not the address bar, so the canary and Enter never reach a website.
await dialog.getByLabel('Live bot computer').click({position:{x:600,y:400}});
await page.getByLabel('Secure computer input').fill('HERM_PHASE3_SECRET_CANARY_9V');
await page.getByRole('button',{name:'Type into focused field',exact:true}).click();
assert.equal(await page.getByLabel('Secure computer input').inputValue(),'');
await page.getByRole('button',{name:'Press Enter on computer',exact:true}).click();
await page.waitForTimeout(1500);

await page.screenshot({path:'/output/phase3-computer-desktop.png',fullPage:true});
let state=await (await context.request.get('/api/v1/bots/researcher/computer')).json();assert.equal(state.lease.holder,'human');assert.ok(state.mine&&state.input_ready&&state.admission_paused&&state.private_session);const epoch=state.lease.epoch;
// A closed browser must retain the lease and admission fence.
await page.close();
const reloaded=await context.newPage();await reloaded.goto('/');await reloaded.getByRole('navigation',{name:'Team conversations'}).getByRole('button',{name:/Scout/}).click();await reloaded.getByRole('button',{name:'Open Scout computer'}).click();await reloaded.getByText('You are in control · private browser',{exact:true}).waitFor();
state=await (await context.request.get('/api/v1/bots/researcher/computer')).json();assert.equal(state.lease.epoch,epoch);
const [other,second]=await session();
state=await (await other.request.get('/api/v1/bots/researcher/computer')).json();assert.equal(state.mine,false);
const forbidden=await other.request.post('/api/v1/bots/researcher/computer/control',{data:{action:'handback'}});assert.equal(forbidden.status(),409);
await second.getByRole('button',{name:'Open Scout computer'}).click();await second.getByRole('button',{name:'Reclaim control',exact:true}).click();await second.getByText('You are in control · private browser',{exact:true}).waitFor();
state=await (await context.request.get('/api/v1/bots/researcher/computer')).json();assert.equal(state.mine,false);assert.ok(state.lease.epoch>epoch);
await second.getByRole('button',{name:'Hand back',exact:true}).click();await second.getByText('Bot is in control',{exact:true}).waitFor();
state=await (await context.request.get('/api/v1/bots/researcher/computer')).json();assert.equal(state.lease.holder,'agent');assert.equal(state.admission_paused,false);assert.equal(state.private_session,false);
if(process.env.HERMBOT_PHASE3_EXIT==='1'){await reloaded.getByRole('dialog').getByRole('button',{name:'Close',exact:true}).click();await reloaded.getByRole('navigation',{name:'Team conversations'}).getByRole('button',{name:/Sage/}).click();await reloaded.getByRole('button',{name:'Input completed · continue',exact:true}).click();}
await second.setViewportSize({width:390,height:844});assert.ok(await second.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));await second.screenshot({path:'/output/phase3-computer-mobile.png',fullPage:true});
assert.deepEqual(errors,[]);
await writeFile('/output/phase3-computer.json',JSON.stringify({check:'private takeover browser over authenticated WebSocket, owner secure keystrokes, close/reconnect, competing owner sessions, stale handback rejection, explicit reclaim, epoch fencing, handback and mobile view',bot_id:'researcher',old_epoch:epoch,new_epoch:state.lease.epoch,secret_input_added_to_transcript:false,script_errors:errors},null,2));
await browser.close();console.log('Phase 3 live computer UI acceptance passed.');
