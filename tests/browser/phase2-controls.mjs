import {chromium} from 'playwright';
import assert from 'node:assert/strict';
import {writeFile} from 'node:fs/promises';

const browser=await chromium.launch({headless:true});
const context=await browser.newContext({viewport:{width:1440,height:1000}});
const page=await context.newPage(),errors=[];
page.on('pageerror',e=>errors.push(e.message));
await page.goto(process.env.HERMBOT_TEST_URL||'http://api:8000');
await page.getByLabel('Owner token').fill(process.env.OWNER_TOKEN);
await page.getByRole('button',{name:'Open workspace'}).click();
await page.getByRole('navigation',{name:'Team conversations'}).waitFor();
async function state(){return page.evaluate(async()=>{const r=await fetch('/api/v1/state');if(!r.ok)throw Error('State unavailable');return r.json();});}
async function until(test){const end=Date.now()+180000;while(Date.now()<end){const value=test(await state());if(value)return value;await page.waitForTimeout(1000);}throw Error('Lifecycle control timed out');}
async function teammates(){await page.getByLabel('Owner menu').click();await page.getByRole('button',{name:'Settings',exact:true}).click();await page.getByRole('dialog').getByRole('tab',{name:'Teammates'}).click();}
const source=(await state()).bots.find(b=>b.name==='Phase 2 UI manager');
assert.ok(source);
await page.getByLabel('Owner menu').click();await page.getByRole('button',{name:'Settings',exact:true}).click();await page.getByRole('dialog').getByRole('tab',{name:'General'}).click();
const saved=page.waitForResponse(r=>r.url().endsWith('/api/v1/settings')&&r.request().method()==='PUT');
await page.getByRole('button',{name:'Save workspace settings',exact:true}).click();
assert.equal((await saved).status(),200);
await page.getByRole('dialog').getByRole('tab',{name:'Teammates'}).click();
await page.locator('.settings-bot').filter({hasText:'Phase 2 UI manager'}).getByRole('button',{name:'Clone',exact:true}).click();
await page.getByLabel('Teammate name').fill('Phase 2 UI lifecycle copy');
for(const name of ['Memories','Installed skill files','Workspace files','Native conversation history'])await page.getByRole('dialog').getByLabel(name,{exact:true}).check();
await page.getByRole('button',{name:'Create copy',exact:true}).click();
let clone=await until(s=>s.bots.find(b=>b.name==='Phase 2 UI lifecycle copy'&&b.status==='active'&&b.applied_revision===b.revision&&b.readiness.compatible));
await teammates();
await page.locator('.settings-bot').filter({hasText:clone.name}).getByRole('button',{name:'Edit',exact:true}).click();
await page.getByLabel('Teammate avatar').fill('🛠️');
await page.getByRole('button',{name:'Save teammate',exact:true}).click();
clone=await until(s=>s.bots.find(b=>b.id===clone.id&&b.status==='active'&&b.applied_revision===b.revision&&b.revision>clone.revision&&b.readiness.compatible));
assert.equal(clone.avatar,'🛠️');
for(const action of ['Archive','Restore']){
  await teammates();
  await page.locator('.settings-bot').filter({hasText:clone.name}).getByRole('button',{name:action,exact:true}).click();
  await page.getByRole('button',{name:`Confirm ${action.toLowerCase()}`,exact:true}).click();
  await until(s=>s.bots.find(b=>b.id===clone.id&&(action==='Archive'?b.status==='archived':b.status==='active'&&b.readiness.compatible)));
}
await teammates();
await page.locator('.settings-bot').filter({hasText:clone.name}).getByRole('button',{name:'Delete',exact:true}).click();
await page.getByLabel('Confirm teammate deletion').fill('wrong');
assert.equal(await page.getByRole('button',{name:'Confirm delete',exact:true}).isEnabled(),false);
await page.getByLabel('Confirm teammate deletion').fill(clone.name);
await page.getByRole('button',{name:'Confirm delete',exact:true}).click();
await until(s=>!s.bots.some(b=>b.id===clone.id));
assert.deepEqual(errors,[]);
await writeFile('/output/phase2-controls.json',JSON.stringify({check:'workspace settings save, explicit four-option clone, edit avatar, archive, restore and typed-name deletion through UI',source_bot_id:source.id,deleted_clone_id:clone.id,script_errors:errors},null,2));
console.log('Settings and complete UI lifecycle controls passed.');
await browser.close();
