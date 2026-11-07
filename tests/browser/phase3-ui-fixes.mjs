import {chromium} from 'playwright';
import assert from 'node:assert/strict';
import {writeFile,access,unlink} from 'node:fs/promises';

const browser=await chromium.launch({headless:true});
const context=await browser.newContext({baseURL:process.env.HERMBOT_TEST_URL||'http://api:8000',viewport:{width:1440,height:1000}});
const page=await context.newPage(),errors=[];
page.on('pageerror',e=>errors.push(e.message));
await page.goto('/');
await page.getByLabel('Owner token').fill(process.env.OWNER_TOKEN);
await page.getByRole('button',{name:'Open workspace'}).click();
const team=page.getByRole('navigation',{name:'Team conversations'});
await team.waitFor();

// Hold a real response across closing the old panel and opening another bot's panel.
await team.getByRole('button',{name:/Scout/}).click();
await page.getByRole('button',{name:'Open Scout memory and skills'}).click();
let releaseSkill,skillStarted;
const skillGate=new Promise(resolve=>releaseSkill=resolve),skillSeen=new Promise(resolve=>skillStarted=resolve);
await page.route('**/api/v1/bots/researcher/knowledge/skills/hermbot-phase3-report',async route=>{
  const response=await route.fetch();skillStarted();await skillGate;await route.fulfill({response});
});
await page.getByRole('button',{name:/^hermbot-phase3-report/}).click();
await skillSeen;
await page.getByRole('dialog').getByRole('button',{name:'Close',exact:true}).click();
await team.getByRole('button',{name:/Quill/}).click();
await page.getByRole('button',{name:'Open Quill memory and skills'}).click();
releaseSkill();await page.waitForTimeout(1200);
assert.equal(await page.getByLabel('Knowledge content').count(),0);
await page.getByRole('dialog').getByRole('button',{name:'Close',exact:true}).click();

await page.getByLabel('Owner menu').click();await page.getByRole('button',{name:'File library',exact:true}).click();
await page.getByLabel('Search library').fill('phase3-preview.py');
await page.getByRole('button',{name:/phase3-preview.py/}).click();
let releaseShare,shareStarted;
const shareGate=new Promise(resolve=>releaseShare=resolve),shareSeen=new Promise(resolve=>shareStarted=resolve);
await page.route('**/api/v1/library/*/shares',async route=>{
  if(route.request().method()!=='POST')return route.continue();
  const response=await route.fetch();shareStarted();await shareGate;await route.fulfill({response});
});
await page.getByRole('button',{name:'Use in this conversation',exact:true}).click();
await shareSeen;
await page.getByRole('dialog').getByRole('button',{name:'Close',exact:true}).click();
await team.getByRole('button',{name:/Scout/}).click();
await page.getByLabel('Owner menu').click();await page.getByRole('button',{name:'File library',exact:true}).click();
releaseShare();await page.waitForTimeout(1200);
assert.equal(await page.locator('.attachment-chips').count(),0);
assert.ok(await page.getByRole('dialog').isVisible());
await page.getByRole('dialog').getByRole('button',{name:'Close',exact:true}).click();

await page.getByRole('button',{name:'Open Scout computer'}).click();
if(await page.getByRole('button',{name:'Start computer',exact:true}).count())await page.getByRole('button',{name:'Start computer',exact:true}).click();
await page.getByRole('button',{name:/^(Take over|Reclaim control)$/}).click();
await page.getByText('You are in control',{exact:true}).waitFor();
assert.equal((await context.request.post('/api/v1/bots/researcher/computer/stop')).status(),409);
const done='/output/phase3-stop-desktop-done';
await unlink(done).catch(()=>{});
await writeFile('/output/phase3-ui-signal.json',JSON.stringify({action:'stop-native-desktop',bot_id:'researcher'}));
for(let i=0;i<240;i++){try{await access(done);break;}catch{}await page.waitForTimeout(500);}
await access(done);
await page.getByRole('button',{name:'Start computer',exact:true}).waitFor();
await page.getByRole('button',{name:'Hand back',exact:true}).click();
await page.getByText('Bot is in control',{exact:true}).waitFor();
const state=await(await context.request.get('/api/v1/bots/researcher/computer')).json();
assert.equal(state.lease.holder,'agent');assert.equal(state.admission_paused,false);
await page.getByRole('button',{name:'Start computer',exact:true}).click();
await page.getByRole('button',{name:'Take over',exact:true}).waitFor();
assert.deepEqual(errors,[]);
await writeFile('/output/phase3-ui-fixes.json',JSON.stringify({check:'delayed knowledge and file-use responses stay in their original panel; explicit handback remains usable after real native desktop failure',stale_knowledge_draft_suppressed:true,stale_file_attachment_suppressed:true,stopped_desktop_handed_back:true,script_errors:errors},null,2));
await browser.close();console.log('Phase 3 panel scope and stopped-desktop recovery checks passed.');
