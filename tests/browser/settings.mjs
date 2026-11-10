import {chromium} from 'playwright';
import assert from 'node:assert/strict';
import {createServer} from 'node:http';
import {readFile,writeFile,mkdir} from 'node:fs/promises';
import {resolve,extname} from 'node:path';

// Synthetic Settings window only. Every API request is intercepted; only the two settings saves are accepted.
const root=resolve(process.argv[2]||'test-results/settings-web');
const out=resolve(process.argv[3]||'test-results/settings-ui');await mkdir(out,{recursive:true});
const server=createServer(async(req,res)=>{try{
  const path=resolve(root,'.'+new URL(req.url,'http://fixture').pathname.replace(/\/$/,'/index.html'));
  if(!path.startsWith(root+'/')){res.writeHead(403);return res.end();}
  const body=await readFile(path);
  res.writeHead(200,{'content-type':{'.html':'text/html','.js':'text/javascript','.css':'text/css','.woff2':'font/woff2'}[extname(path)]||'application/octet-stream'});res.end(body);
}catch{res.writeHead(404);res.end();}});
server.on('upgrade',(req,socket)=>socket.destroy());
await new Promise(r=>server.listen(0,'127.0.0.1',r));
const origin=`http://127.0.0.1:${server.address().port}`;
const browser=await chromium.launch({headless:true,channel:'chrome'});
const errors=[],checks=[],writes=[];
const bots=['writer','researcher'].map((id,index)=>({id,name:index?'Scout':'Quill',role:'Teammate',color:index?'#cf8d42':'#728eca',avatar:'',capabilities:[],status:'active',desired_state:'running',revision:1,applied_revision:1,readiness:{compatible:true},enabled_tools:[],enabled_skills:[],memory_mb:2048,cpus:1.5,model_name:'m'}));
const conversations=bots.map((bot,index)=>({id:index?'scout':'quill',title:bot.name,kind:'dm',bot_id:bot.id,bot_ids:[bot.id]}));
const state={bots,retired_bots:[],conversations,tasks:[],work_items:[],approvals:[],input_requests:[],artifacts:[],grants:[],proposals:[],operations:[],controls:[],workspace:{},provider_configured:true,event_cursor:0};
const settings={workspace:{timezone:'UTC',default_provider_id:'provider',permitted_tools:['file','terminal'],max_bots:10,max_active:3,active_budget_seconds:3600,decision_provider_id:null,decision_model:'',decision_mode:'disabled',decision_actions:false,external_action_mode:'requested'},
  providers:[{id:'provider',name:'Fixture provider',base_url:'https://models.invalid/v1',models:['fixture-model'],default_model:'fixture-model',revision:1,key_configured:true,auth_kind:'api_key'}],model_broker_online:false};
const notifications={revision:1,push_enabled:false,categories:['questions','reviews','results','problems'],timezone:'Asia/Kolkata',quiet_enabled:false,quiet_start:'22:00',quiet_end:'08:00',worker_error:null,worker_healthy:true,push:{configured:false,public_key:'',key_id:''},devices:[]};
const sections=['General','Model providers','Teammates','Permissions','Notifications'];
const advancedDefaults={takeover_unpausable:'ask',takeover_reply_wait_seconds:0};
const advanced={values:{...advancedDefaults},defaults:{...advancedDefaults},history:[]};
function applyAdvanced(next){for(const [setting,to] of Object.entries(next))if(advanced.values[setting]!==to){advanced.history.unshift({setting,from:advanced.values[setting],to,changed_at:new Date().toISOString()});advanced.values[setting]=to;}return advanced;}
async function open(width,height){
  const context=await browser.newContext({viewport:{width,height},serviceWorkers:'block',timezoneId:'UTC'});
  await context.addInitScript(()=>{window.EventSource=class extends EventTarget{close(){}};});
  await context.route('**/*',async route=>{
    const req=route.request(),url=new URL(req.url());if(url.origin!==origin)return route.abort();
    if(!url.pathname.startsWith('/api/'))return route.continue();
    if(req.method()!=='GET'){
      writes.push(`${req.method()} ${url.pathname} ${req.postData()||''}`);
      if(req.method()==='PUT'&&url.pathname==='/api/v1/settings')return route.fulfill({json:settings.workspace});
      if(req.method()==='PUT'&&url.pathname==='/api/v1/settings/advanced')return route.fulfill({json:applyAdvanced(JSON.parse(req.postData()))});
      if(req.method()==='POST'&&url.pathname==='/api/v1/settings/advanced/reset')return route.fulfill({json:applyAdvanced(advancedDefaults)});
      if(req.method()==='PUT'&&url.pathname==='/api/v1/notifications/settings')return route.fulfill({json:{...notifications,...JSON.parse(req.postData()),revision:2}});
      return route.fulfill({status:403,json:{detail:'Fixture rejects writes'}});
    }
    let result={};
    if(url.pathname==='/api/v1/state')result=state;
    else if(url.pathname==='/api/v1/attention')result={unread:0,needs_owner:0,items:[]};
    else if(url.pathname.endsWith('/timeline'))result={entries:[],cursor:0,has_more:false,has_earlier:false,artifacts:[],attachments:[]};
    else if(url.pathname==='/api/v1/settings')result=settings;
    else if(url.pathname==='/api/v1/settings/advanced')result=advanced;
    else if(url.pathname==='/api/v1/notifications/settings')result=notifications;
    await route.fulfill({json:result});
  });
  const page=await context.newPage();page.on('pageerror',error=>{errors.push(error.message);console.error(error.message);});
  await page.goto(origin);
  await page.getByRole('navigation',{name:'Team conversations'}).waitFor({timeout:10000}).catch(async error=>{await page.screenshot({path:`${out}/failure.png`});throw error;});
  return [context,page];
}
async function openSettings(page){
  if(await page.getByRole('button',{name:'Open navigation'}).isVisible())await page.getByRole('button',{name:'Open navigation'}).click();
  await page.getByLabel('Owner menu').click();
  const items=await page.locator('.owner-menu button, details[open] button').allInnerTexts();
  await page.getByRole('button',{name:'Settings',exact:true}).click();
  const dialog=page.getByRole('dialog',{name:'Settings',exact:true});await dialog.waitFor();
  return [dialog,items.map(text=>text.trim()).filter(Boolean)];
}
const overflow=page=>page.evaluate(()=>{const modal=document.querySelector('.modal');return {document:document.documentElement.scrollWidth>innerWidth+1,modal:!!modal&&modal.scrollWidth>modal.clientWidth+1};});
try{
  const [context,page]=await open(1440,900);
  const [dialog,menu]=await openSettings(page);
  assert(!menu.some(item=>/^Notifications$|Workspace settings/.test(item)),`Owner menu still lists old entries: ${menu}`);
  assert(['Settings','Connections','File library','Sign out'].every(item=>menu.some(text=>text.includes(item))),`Owner menu: ${menu}`);
  checks.push('The owner menu offers Settings, Connections, File library and Sign out; Notifications moved into Settings.');
  assert.deepEqual(await dialog.getByRole('tab').allInnerTexts(),sections);
  assert.equal(await dialog.getByRole('tab',{name:'General'}).getAttribute('aria-selected'),'true');
  await dialog.getByLabel('Active work budget').waitFor();
  const box=await dialog.boundingBox();assert(box.width>=900&&Math.abs(box.x+box.width/2-720)<2,'A wide, centered window');
  checks.push('Settings opens as a centered window with the five sections in a sidebar, starting at General.');
  const contents={'Model providers':'Add OpenAI-compatible provider','Teammates':'Quill','Permissions':'Create standing grant','Notifications':'Registered browsers'};
  for(const [section,text] of Object.entries(contents)){
    await dialog.getByRole('tab',{name:section}).click();
    await dialog.getByRole('heading',{name:section,exact:true}).waitFor();
    await dialog.getByText(text,{exact:false}).first().waitFor();
    assert.equal(await dialog.getByRole('tab',{name:section}).getAttribute('aria-selected'),'true');
  }
  checks.push('Each section shows its own heading, description and existing content.');
  await dialog.getByRole('tab',{name:'General'}).click();await dialog.getByRole('tab',{name:'General'}).focus();
  const selected=async()=>(await dialog.locator('[role=tab][aria-selected=true]').innerText()).trim();
  await page.keyboard.press('ArrowDown');assert.equal(await selected(),'Model providers');
  await page.keyboard.press('End');assert.equal(await selected(),'Notifications');
  await page.keyboard.press('ArrowDown');assert.equal(await selected(),'General');
  await page.keyboard.press('ArrowUp');assert.equal(await selected(),'Notifications');
  await page.keyboard.press('Home');assert.equal(await selected(),'General');
  assert.equal(await page.evaluate(()=>document.activeElement?.textContent),'General','Focus follows the selected section');
  checks.push('Arrow keys, Home and End move between sections, and focus follows.');
  await dialog.getByLabel('Active work budget').fill('90');
  await dialog.getByRole('button',{name:'Save workspace settings'}).click();
  await page.waitForTimeout(300);
  const saved=writes.find(w=>w.startsWith('PUT /api/v1/settings '));assert(saved&&JSON.parse(saved.slice('PUT /api/v1/settings '.length)).active_budget_seconds===5400,'General saves exactly as before');
  await dialog.getByRole('tab',{name:'Notifications'}).click();
  await dialog.getByRole('checkbox',{name:'Pause all browser notifications during quiet hours'}).check();
  await dialog.getByRole('button',{name:'Save preferences'}).click();
  await dialog.getByText('Notification preferences saved.').waitFor();
  assert(writes.some(w=>w.startsWith('PUT /api/v1/notifications/settings ')&&JSON.parse(w.slice('PUT /api/v1/notifications/settings '.length)).quiet_enabled===true));
  checks.push('General and Notifications save through the same API calls as before.');
  await page.screenshot({path:`${out}/settings-1440.png`});
  assert((await dialog.locator('.settings-content').evaluate(node=>node.scrollTop))>0,'The fixture scrolls Notifications');
  await dialog.getByRole('tab',{name:'Teammates'}).click();
  assert.equal(await dialog.locator('.settings-content').evaluate(node=>node.scrollTop),0,'A new section starts at its top');
  await dialog.getByRole('tab',{name:'Notifications'}).click();
  checks.push('Switching sections starts the new section at its top.');
  await page.keyboard.press('Escape');await dialog.waitFor({state:'hidden'});
  const [reopened]=await openSettings(page);
  assert.equal(await reopened.getByRole('tab',{name:'Notifications'}).getAttribute('aria-selected'),'true','Reopens at the last section');
  checks.push('Escape closes Settings, and it reopens at the last section used.');
  // Advanced: hidden until revealed in General, and the reveal belongs to this browser.
  await reopened.getByRole('tab',{name:'General'}).click();
  const reveal=reopened.getByRole('switch',{name:'Show advanced settings'});
  assert.equal(await reveal.isChecked(),false);assert.deepEqual(await reopened.getByRole('tab').allInnerTexts(),sections);
  await reveal.check();
  assert.deepEqual(await reopened.getByRole('tab').allInnerTexts(),[...sections,'Advanced']);
  await reopened.getByRole('tab',{name:'Advanced'}).click();
  await reopened.getByText('These change how Hermbot behaves for the whole workspace. The defaults are recommended.').waitFor();
  assert.equal(await reopened.getByText(/takeover pausing ships/).count(),0,'The settings are in effect, so no pending-effect note');
  await reopened.getByText('Show “Couldn’t pause the bot” with Try again, Take over without pausing, or Cancel.').waitFor();
  assert.equal(await reopened.getByRole('radio',{name:/^Ask me · default/}).isChecked(),true);
  assert.equal(await reopened.getByRole('radio',{name:/^Pause immediately · default/}).isChecked(),true);
  assert(await reopened.getByRole('button',{name:'Save advanced settings'}).isDisabled(),'Nothing to save yet');
  assert(await reopened.getByRole('button',{name:'Reset to defaults'}).isDisabled(),'Already at the defaults');
  await reopened.getByText('No changes yet.').waitFor();
  checks.push('Advanced is hidden until revealed in General, then appears last with a warning and both defaults selected; the ask option names all three choices.');
  await reopened.getByRole('radio',{name:/^Always refuse/}).check();
  await reopened.getByRole('radio',{name:/^Wait up to 30 seconds/}).check();
  await reopened.getByRole('button',{name:'Save advanced settings'}).click();
  await reopened.getByText('Advanced settings saved.').waitFor();
  const put=writes.find(w=>w.startsWith('PUT /api/v1/settings/advanced '));
  assert.deepEqual(JSON.parse(put.slice('PUT /api/v1/settings/advanced '.length)),{takeover_unpausable:'refuse',takeover_reply_wait_seconds:30});
  const recent=await reopened.locator('.advanced-history li').allInnerTexts();
  assert(recent.length===2&&recent.some(t=>t.includes('Ask me → Always refuse'))&&recent.some(t=>t.includes('Pause immediately → Wait up to 30 seconds')),`Recent changes: ${recent}`);
  await reopened.getByRole('button',{name:'Reset to defaults'}).click();
  await reopened.getByText('Advanced settings reset to their defaults.').waitFor();
  assert.equal(await reopened.getByRole('radio',{name:/^Ask me · default/}).isChecked(),true);
  assert.equal(await reopened.locator('.advanced-history li').count(),4);
  checks.push('Saving sends exactly the chosen values, each change appears under Recent changes, and Reset restores the defaults.');
  await page.keyboard.press('Escape');await reopened.waitFor({state:'hidden'});
  await page.reload();await page.getByRole('navigation',{name:'Team conversations'}).waitFor();
  const [remembered]=await openSettings(page);
  assert.equal(await remembered.getByRole('tab',{name:'Advanced'}).getAttribute('aria-selected'),'true','The reveal and last section survive a reload');
  await remembered.getByRole('tab',{name:'General'}).click();
  await remembered.getByRole('switch',{name:'Show advanced settings'}).uncheck();
  assert.deepEqual(await remembered.getByRole('tab').allInnerTexts(),sections);
  await page.keyboard.press('Escape');await remembered.waitFor({state:'hidden'});
  const [hidden]=await openSettings(page);
  assert.equal(await hidden.getByRole('tab',{name:'General'}).getAttribute('aria-selected'),'true','A hidden Advanced section is never reopened');
  checks.push('The reveal is remembered in this browser; hiding Advanced removes it and Settings never reopens there.');
  await context.close();
  for(const [width,height] of [[768,1024],[390,844],[320,568]]){
    const [small,phone]=await open(width,height);
    const [settingsWindow]=await openSettings(phone);
    assert(!(await overflow(phone)).document&&!(await overflow(phone)).modal,`No overflow at ${width}px`);
    if(width<=650){
      const box=await settingsWindow.boundingBox();assert(box.width>=width-1&&box.height>=height-1,'Full screen on phones');
      assert.equal(await settingsWindow.locator('.settings-content').isVisible(),false,'Phones start at the section list');
      await phone.screenshot({path:`${out}/settings-list-${width}.png`});
      await settingsWindow.getByRole('tab',{name:'Teammates'}).click();
      await settingsWindow.getByRole('heading',{name:'Teammates',exact:true}).waitFor();
      assert.equal(await settingsWindow.locator('.settings-nav').isVisible(),false,'A section replaces the list');
      assert(!(await overflow(phone)).document&&!(await overflow(phone)).modal,`No overflow in a section at ${width}px`);
      await phone.screenshot({path:`${out}/settings-section-${width}.png`});
      await settingsWindow.getByRole('button',{name:'All settings'}).click();
      await settingsWindow.getByRole('tab',{name:'General'}).waitFor();
      assert.equal(await settingsWindow.locator('.settings-content').isVisible(),false);
      await settingsWindow.getByRole('tab',{name:'General'}).click();await settingsWindow.getByRole('switch',{name:'Show advanced settings'}).check();
      await settingsWindow.getByRole('button',{name:'All settings'}).click();await settingsWindow.getByRole('tab',{name:'Advanced'}).click();
      await settingsWindow.getByRole('button',{name:'Reset to defaults'}).waitFor();
      assert(!(await overflow(phone)).document&&!(await overflow(phone)).modal,`No overflow in Advanced at ${width}px`);
      await phone.screenshot({path:`${out}/settings-advanced-${width}.png`});
    }else await phone.screenshot({path:`${out}/settings-${width}.png`});
    await small.close();
  }
  checks.push('At 768 px the sidebar stays; at 390 and 320 px Settings fills the screen as a list that opens one section, with All settings to go back, and nothing overflows.');
  assert.deepEqual(errors,[]);
  assert(writes.every(w=>/^(PUT \/api\/v1\/(settings|settings\/advanced|notifications\/settings)|POST \/api\/v1\/settings\/advanced\/reset) /.test(w)),`Unexpected writes: ${writes}`);
}finally{
  await browser.close();server.close();
  await writeFile(`${out}/report.json`,JSON.stringify({checks,errors,writes:writes.map(w=>w.split(' ').slice(0,2).join(' '))},null,2));
}
console.log(JSON.stringify({checks,errors},null,2));
