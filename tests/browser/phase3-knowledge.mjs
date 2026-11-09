import {chromium} from 'playwright';
import assert from 'node:assert/strict';
import {writeFile} from 'node:fs/promises';
const browser=await chromium.launch({headless:true});const context=await browser.newContext({baseURL:process.env.HERMBOT_TEST_URL||'http://api:8000',viewport:{width:1440,height:1000}}),page=await context.newPage(),errors=[];
page.on('pageerror',e=>errors.push(e.message));await page.goto('/');await page.getByLabel('Owner token').fill(process.env.OWNER_TOKEN);await page.getByRole('button',{name:'Open workspace'}).click();await page.getByRole('navigation',{name:'Team conversations'}).getByRole('button',{name:/Quill/}).click();await page.getByRole('button',{name:'Open Quill memory and skills'}).click();
const dialog=page.getByRole('dialog');
let name=null,previous=null,marked=0;
try{
await dialog.getByRole('button').filter({hasText:'Personal notes'}).click();
previous=await page.getByLabel('Knowledge content').inputValue();await page.getByLabel('Knowledge content').fill(previous+'\nPHASE3_UI_MEMORY: Review before enabling.');await mark();await dialog.getByRole('button',{name:'Save reviewed content',exact:true}).click();await page.getByText('Saved. The runtime is refreshing so its next run sees the new knowledge.').waitFor();
// A save returns before it bumps the runtime revision; wait for a newer revision to be applied, not just for "active".
async function writer(){return (await (await context.request.get('/api/v1/state')).json()).bots.find(b=>b.id==='writer');}
async function mark(){marked=(await writer()).revision;}
async function ready(){for(let i=0;i<200;i++){const b=await writer();if(b.revision>marked&&b.status==='active'&&b.applied_revision===b.revision)return;await page.waitForTimeout(750);}throw new Error('Knowledge runtime refresh timed out');}
await ready();await dialog.getByRole('button').filter({hasText:'Personal notes'}).click();await page.getByLabel('Knowledge content').fill(previous);await mark();await dialog.getByRole('button',{name:'Save reviewed content',exact:true}).click();await ready();
name='hermbot-ui-'+Date.now();await dialog.getByLabel('Import skill Markdown').setInputFiles({name:'SKILL.md',mimeType:'text/markdown',buffer:Buffer.from(`---\nname: ${name}\ndescription: Review a local UI acceptance report\n---\n\nVerify source values and publish a concise report.\n`)});assert.equal(await page.getByLabel('Knowledge name').inputValue(),name);await page.getByRole('checkbox',{name:'Enable this skill after saving'}).check();await mark();await dialog.getByRole('button',{name:'Save reviewed content',exact:true}).click();await ready();
await page.getByLabel('Find installed skill').fill(name);await dialog.getByRole('button').filter({hasText:name}).click();await page.getByLabel('Knowledge content').fill((await page.getByLabel('Knowledge content').inputValue())+'\nVersion two: check provenance.\n');await mark();await dialog.getByRole('button',{name:'Save reviewed content',exact:true}).click();await ready();await dialog.getByRole('button').filter({hasText:name}).click();await page.getByLabel('Knowledge version').waitFor();await page.waitForFunction(()=>document.querySelector('[aria-label="Knowledge version"]')?.querySelectorAll('option').length===3);await page.screenshot({path:'/output/phase3-knowledge-desktop.png',fullPage:true});
await page.setViewportSize({width:390,height:844});assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));await page.screenshot({path:'/output/phase3-knowledge-mobile.png',fullPage:true});await mark();await dialog.getByRole('button',{name:'Remove skill',exact:true}).click();await ready();
const skills=await (await context.request.get('/api/v1/bots/writer/knowledge')).json();assert.ok(!skills.skills.some(s=>s.name===name));const finalMemory=skills.memory.find(m=>m.name==='memory');assert.equal(finalMemory.content,previous);assert.deepEqual(errors,[]);
await writeFile('/output/phase3-knowledge.json',JSON.stringify({check:'real UI native memory edit/removal, imported skill, explicit enable, immutable version history, skill removal, responsive forms',skill:name,version_count:2,script_errors:errors},null,2));
}catch(error){
  await page.screenshot({path:'/output/phase3-knowledge-failure.png',fullPage:true}).catch(()=>{});
  console.error((await page.locator('body').innerText().catch(()=>'')).slice(0,2000));throw error;
}finally{
  // Never leave the temporary skill or the test memory line in the live workspace, even after a failure.
  const state=await (await context.request.get('/api/v1/bots/writer/knowledge')).json(),key=()=>({'Idempotency-Key':crypto.randomUUID()});
  if(name&&state.skills.some(s=>s.name===name)){const skill=await (await context.request.get(`/api/v1/bots/writer/knowledge/skills/${name}`)).json();await context.request.put(`/api/v1/bots/writer/knowledge/skill/${name}`,{headers:key(),data:{content:'',expected_hash:skill.sha256,enable:false}});}
  const memory=state.memory.find(m=>m.name==='memory');
  if(previous!==null&&memory&&memory.content!==previous)await context.request.put('/api/v1/bots/writer/knowledge/memory/memory',{headers:key(),data:{content:previous,expected_hash:memory.sha256}});
  await browser.close();
}
console.log('Phase 3 memory and skills UI acceptance passed.');
