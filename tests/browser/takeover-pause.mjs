import {chromium} from 'playwright';
import assert from 'node:assert/strict';
import {createServer} from 'node:http';
import {readFile,writeFile,mkdir} from 'node:fs/promises';
import {resolve,extname} from 'node:path';

// Synthetic takeover only: every API request and the live-view socket are intercepted; no runtime is touched.
// Covers the pane while a takeover pauses the bot: a reply finishing first, a failed pause, an unpaused
// takeover and what other sessions see while the bot is paused, at desktop, tablet and phone widths.
const root=resolve(process.argv[2]||'test-results/takeover-pause-web');
const out=resolve(process.argv[3]||'test-results/takeover-pause-ui');await mkdir(out,{recursive:true});
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
const errors=[],checks=[],writes=[],sockets=[];
const bots=['writer','researcher'].map((id,index)=>({id,name:index?'Scout':'Quill',role:'Teammate',color:index?'#cf8d42':'#728eca',avatar:'',capabilities:[],status:'active',desired_state:'running',revision:1,applied_revision:1,readiness:{compatible:true},enabled_tools:[],enabled_skills:[],memory_mb:2048,cpus:1.5}));
const conversations=bots.map((bot,index)=>({id:index?'scout':'quill',title:bot.name,kind:'dm',bot_id:bot.id,bot_ids:[bot.id]}));
const state={bots,retired_bots:[],conversations,tasks:[],work_items:[],approvals:[],input_requests:[],artifacts:[],grants:[],proposals:[],operations:[],controls:[],workspace:{},provider_configured:true,event_cursor:0};
const desktop={installed:true,running:true,geometry:'1280x800',blocker:null,memory_available_mb:4000,browser:'chromium'};
const agent={desktop,lease:{holder:'agent',epoch:1,reason:''},mine:false,input_ready:false,admission_paused:false,private_session:false,takeover:null};
const owner=(takeover,session=false)=>({desktop,lease:{holder:'human',epoch:2,reason:'Owner takeover'},mine:true,input_ready:true,admission_paused:true,private_session:session,takeover});
const STATES={
  finishing:()=>({...owner({phase:'finishing',seconds_left:15}),until:Date.now()+15000}),
  pause_failed:()=>owner({phase:'pause_failed'}),
  unpaused:()=>owner({phase:'unpaused'},true),
  watcher:()=>({...owner({phase:'paused'},true),mine:false}),
};
let computer={...agent},onTakeover=null,heldTakeover=null;
async function open(width,height){
  const context=await browser.newContext({viewport:{width,height},serviceWorkers:'block',timezoneId:'UTC'});
  await context.addInitScript(()=>{window.EventSource=class extends EventTarget{close(){}};});
  await context.route('**/*',async route=>{
    const req=route.request(),url=new URL(req.url());if(url.origin!==origin)return route.abort();
    if(!url.pathname.startsWith('/api/'))return route.continue();
    if(req.method()!=='GET'){
      const body=JSON.parse(req.postData()||'{}');
      writes.push(`${req.method()} ${url.pathname} ${body.action||''}`.trim());
      if(url.pathname!=='/api/v1/bots/researcher/computer/control')return route.fulfill({status:403,json:{detail:'Fixture rejects writes'}});
      if(body.action==='takeover'&&onTakeover)return onTakeover(route);
      if(body.action==='takeover_now'){computer=STATES.unpaused();computer.takeover={phase:'paused'};heldTakeover?.({json:computer});return route.fulfill({json:computer});}
      if(body.action==='takeover_unpaused'){computer=STATES.unpaused();return route.fulfill({json:computer});}
      if(body.action==='handback'){computer={...agent};heldTakeover?.({status:409,json:{detail:'Control changed while the bot was finishing its reply. Reload to see who is in control.'}});return route.fulfill({json:computer});}
      return route.fulfill({status:400,json:{detail:'Unexpected action'}});
    }
    let result={};
    if(url.pathname==='/api/v1/state')result=state;
    else if(url.pathname==='/api/v1/attention')result={unread:0,needs_owner:0,items:[]};
    else if(url.pathname.endsWith('/timeline'))result={entries:[],cursor:0,has_more:false,has_earlier:false,artifacts:[],attachments:[]};
    else if(url.pathname==='/api/v1/bots/researcher/computer'){const {until,...current}=computer;result=until?{...current,takeover:{phase:'finishing',seconds_left:Math.max(0,Math.ceil((until-Date.now())/1000))}}:current;}
    else if(url.pathname.endsWith('/computer/thumbnail'))result=computer.takeover?.phase==='paused'?{image:null,paused:true}:{image:null};
    else if(url.pathname.endsWith('/capabilities'))result={inventory_known:true,running:true,human_control:false,native_tools:[],connector_restricted:false,blocked_tools:[],connections:[]};
    await route.fulfill({json:result});
  });
  const page=await context.newPage();page.on('pageerror',error=>{errors.push(error.message);console.error(error.message);});
  page.on('websocket',ws=>{if(ws.url().endsWith('/computer/ws'))sockets.push({at:Date.now(),phase:computer.takeover?.phase,mine:computer.mine});});
  await page.goto(origin);
  await page.getByRole('navigation',{name:'Team conversations'}).waitFor({timeout:10000}).catch(async error=>{await page.screenshot({path:`${out}/failure.png`});throw error;});
  return [context,page];
}
async function openComputer(page,width){
  await page.getByRole('button',{name:'Open navigation'}).click().catch(()=>{});
  await page.getByRole('navigation',{name:'Team conversations'}).getByRole('button',{name:/Scout/}).click();
  if(width<=650){await page.getByLabel('Conversation tools').click();await page.getByRole('button',{name:'Computer',exact:true}).click();}
  else await page.getByRole('button',{name:'Open Scout computer'}).click();
  return page.getByRole('dialog');
}
const overflow=page=>page.evaluate(()=>document.documentElement.scrollWidth>window.innerWidth+1);
const button=(dialog,name)=>dialog.getByRole('button',{name,exact:true});
const text=(dialog,value)=>dialog.getByText(value,{exact:true});
try{
  const [context,page]=await open(1440,900);
  let dialog=await openComputer(page,1440);
  await text(dialog,'Bot is in control').waitFor();

  // 1. A reply in progress finishes first; the takeover request stays open meanwhile, as the API's does.
  onTakeover=route=>{computer=STATES.finishing();heldTakeover=reply=>{heldTakeover=null;route.fulfill(reply);};};
  await button(dialog,'Take over privately').click();
  await text(dialog,'Scout is finishing a reply…').waitFor();
  const first=Number((await dialog.getByText(/s left · Take over now skips the wait$/).innerText()).match(/^(\d+)s/)[1]);
  await page.waitForTimeout(2200);
  const later=Number((await dialog.getByText(/s left · Take over now skips the wait$/).innerText()).match(/^(\d+)s/)[1]);
  assert(first<=15&&later<first,`The countdown runs (${first} → ${later})`);
  assert.equal(await button(dialog,'Take over privately').count(),0,'Only the wait’s own choices are offered');
  await page.screenshot({path:`${out}/finishing-1440.png`});
  checks.push('While a reply finishes, the pane says so with a running countdown and offers Take over now and Hand back.');
  await button(dialog,'Take over now').click();
  await text(dialog,'You are in control · private browser').waitFor();
  assert(writes.includes('POST /api/v1/bots/researcher/computer/control takeover_now'));
  checks.push('Take over now skips the wait and opens the private browser.');
  await button(dialog,'Hand back').click();
  await text(dialog,'Bot is in control').waitFor();

  // 2. Handing back during the wait cancels the takeover without showing its interrupted request as an error.
  await button(dialog,'Take over privately').click();
  await text(dialog,'Scout is finishing a reply…').waitFor();
  await button(dialog,'Hand back').click();
  await text(dialog,'Bot is in control').waitFor();
  await page.waitForTimeout(500);
  assert.equal(await dialog.getByRole('alert').count(),0,'A takeover the owner cancelled is not reported as a failure');
  checks.push('Hand back during the wait cancels the takeover quietly.');

  // 3. Ask on failure: three choices, and no other control actions.
  onTakeover=route=>{computer=STATES.pause_failed();route.fulfill({json:computer});};
  await button(dialog,'Take over privately').click();
  await text(dialog,'Couldn’t pause Scout').waitFor();
  await dialog.getByText(/Scout couldn’t be paused\. Its tools stay blocked while you decide\./).waitFor();
  for(const name of ['Try again','Take over without pausing','Cancel'])assert.equal(await button(dialog,name).count(),1,name);
  for(const name of ['Confirm control','Hand back','Stop computer'])assert.equal(await button(dialog,name).count(),0,`${name} is not offered while deciding`);
  await page.screenshot({path:`${out}/pause-failed-1440.png`});
  checks.push('When Scout can’t be paused, the pane offers exactly Try again, Take over without pausing and Cancel.');
  await button(dialog,'Take over without pausing').click();
  await text(dialog,'You are in control · private browser').waitFor();
  await dialog.getByRole('note').filter({hasText:'Scout couldn’t be paused and keeps working. It still can’t see this browser.'}).waitFor();
  await page.screenshot({path:`${out}/unpaused-1440.png`});
  checks.push('Taking over without pausing shows a banner saying Scout keeps working but can’t see the private browser.');
  await button(dialog,'Hand back').click();
  await text(dialog,'Bot is in control').waitFor();
  await button(dialog,'Take over privately').click();
  await button(dialog,'Cancel').click();
  await text(dialog,'Bot is in control').waitFor();
  assert.equal(writes.at(-1),'POST /api/v1/bots/researcher/computer/control handback','Cancel hands back');
  checks.push('Cancel hands control back.');

  // 4. Refuse on failure: the API's error is shown and the bot stays in control.
  onTakeover=route=>route.fulfill({status:502,json:{detail:'Couldn\'t pause Scout. Try again.'}});
  await button(dialog,'Take over privately').click();
  await dialog.getByRole('alert').filter({hasText:'Couldn\'t pause Scout. Try again.'}).waitFor();
  await text(dialog,'Bot is in control').waitFor();
  checks.push('When pausing is refused, the error says so and the bot keeps control.');

  // 5. Another session while the bot is paused: nothing to watch and no live view.
  await page.keyboard.press('Escape');
  await context.close();
  computer=STATES.watcher();sockets.length=0;
  const [watching,watcher]=await open(1440,900);
  dialog=await openComputer(watcher,1440);
  await text(dialog,'Paused while the owner uses a private browser').waitFor();
  await dialog.getByText('Scout’s screen is hidden while it is paused. It comes back when the owner hands back.').waitFor();
  assert.equal(await button(dialog,'Expand live view').count(),0,'A paused screen offers no live view');
  for(const name of ['Reclaim control','Hand back (force)'])assert.equal(await button(dialog,name).count(),1,name);
  await watcher.waitForTimeout(1500);
  assert.equal(sockets.length,0,'No live view connects to a paused bot');
  await watcher.screenshot({path:`${out}/watcher-1440.png`});
  checks.push('Other sessions see that Scout is paused, with no live view, and can still reclaim or force a handback.');
  await watching.close();

  // 6. Every state at tablet and phone widths.
  for(const [width,height] of [[390,844],[768,1024]]){
    for(const [name,expected] of [['finishing','Scout is finishing a reply…'],['pause_failed','Couldn’t pause Scout'],['unpaused','You are in control · private browser'],['watcher','Paused while the owner uses a private browser']]){
      computer=STATES[name]();
      const [mobile,small]=await open(width,height);
      const pane=await openComputer(small,width);
      await text(pane,expected).waitFor({timeout:10000}).catch(async error=>{await small.screenshot({path:`${out}/failure-${name}-${width}.png`});throw error;});
      assert(!await overflow(small),`No horizontal overflow for ${name} at ${width}px`);
      await small.screenshot({path:`${out}/${name}-${width}.png`});
      await mobile.close();
    }
  }
  checks.push('Every takeover state fits at 390 and 768 px without horizontal overflow.');
  assert.deepEqual(errors,[]);
  assert(writes.every(w=>w.startsWith('POST /api/v1/bots/researcher/computer/control')),`Unexpected writes: ${writes}`);
}finally{
  await browser.close();server.close();
  await writeFile(`${out}/report.json`,JSON.stringify({checks,errors,writes,sockets:sockets.length},null,2));
}
console.log(JSON.stringify({checks,errors,writes},null,2));
