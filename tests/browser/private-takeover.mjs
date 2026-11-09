import {chromium} from 'playwright';
import assert from 'node:assert/strict';
import {createServer} from 'node:http';
import {readFile,writeFile,mkdir} from 'node:fs/promises';
import {resolve,extname} from 'node:path';

// Synthetic takeover only. Every API request and the live-view socket are intercepted; no runtime is touched.
const root=resolve(process.argv[2]||'test-results/private-takeover-web');
const out=resolve(process.argv[3]||'test-results/private-takeover-ui');await mkdir(out,{recursive:true});
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
const computer={desktop:{installed:true,running:true,geometry:'1280x800',blocker:null,memory_available_mb:4000,browser:'chromium'},lease:{holder:'agent',epoch:1,reason:''},mine:false,input_ready:false,admission_paused:false,private_session:false};
let releaseSession=null,holdStatus=null;
async function open(width,height){
  const context=await browser.newContext({viewport:{width,height},serviceWorkers:'block',timezoneId:'UTC'});
  await context.addInitScript(()=>{window.EventSource=class extends EventTarget{close(){}};});
  await context.route('**/*',async route=>{
    const req=route.request(),url=new URL(req.url());if(url.origin!==origin)return route.abort();
    if(!url.pathname.startsWith('/api/'))return route.continue();
    if(req.method()!=='GET'){
      writes.push(`${req.method()} ${url.pathname}`);
      if(url.pathname==='/api/v1/bots/researcher/computer/control'){
        const body=JSON.parse(req.postData()||'{}');
        if(body.action==='takeover'){Object.assign(computer,{lease:{holder:'human',epoch:2,reason:'Owner takeover'},mine:true,input_ready:true,admission_paused:true});releaseSession=()=>{computer.private_session=true;};}
        else Object.assign(computer,{lease:{holder:'agent',epoch:3,reason:''},mine:false,input_ready:false,admission_paused:false,private_session:false});
        return route.fulfill({json:computer});
      }
      return route.fulfill({status:403,json:{detail:'Fixture rejects writes'}});
    }
    let result={};
    if(url.pathname==='/api/v1/state')result=state;
    else if(url.pathname==='/api/v1/attention')result={unread:0,needs_owner:0,items:[]};
    else if(url.pathname.endsWith('/timeline'))result={entries:[],cursor:0,has_more:false,has_earlier:false,artifacts:[],attachments:[]};
    else if(url.pathname==='/api/v1/bots/researcher/computer'){if(holdStatus)await holdStatus;result=computer;}
    else if(url.pathname.endsWith('/computer/thumbnail'))result={image:null};
    else if(url.pathname.endsWith('/knowledge'))result={memory:[{name:'memory',content:'Prefers short summaries.',sha256:'0'.repeat(64),limit:2200}],skills:[],versions:[]};
    else if(url.pathname.endsWith('/capabilities'))result={inventory_known:true,running:true,human_control:false,native_tools:[],connector_restricted:false,blocked_tools:[],connections:[]};
    await route.fulfill({json:result});
  });
  const page=await context.newPage();page.on('pageerror',error=>{errors.push(error.message);console.error(error.message);});
  // The fixture server refuses the live-view socket, as an unreachable screen would; record each attempt.
  page.on('websocket',ws=>{if(ws.url().endsWith('/computer/ws'))sockets.push({at:Date.now(),private_session:computer.private_session});});
  await page.goto(origin);
  await page.getByRole('navigation',{name:'Team conversations'}).waitFor({timeout:10000}).catch(async error=>{await page.screenshot({path:`${out}/failure.png`});throw error;});
  return [context,page];
}
const overflow=page=>page.evaluate(()=>document.documentElement.scrollWidth>window.innerWidth+1);
try{
  const [context,page]=await open(1440,900);
  await page.getByRole('navigation',{name:'Team conversations'}).getByRole('button',{name:/Scout/}).click();
  let release;holdStatus=new Promise(r=>release=r);
  await page.getByRole('button',{name:'Open Scout computer'}).click();
  await page.getByText('Checking the computer…',{exact:true}).waitFor();
  assert.equal(await page.getByRole('dialog').getByRole('button',{name:/Start computer|Take over/}).count(),0,'No computer action is offered before its state loads');
  holdStatus=null;release();
  checks.push('While the computer’s state loads, the pane says so and offers no action.');
  await page.getByText('Bot is in control',{exact:true}).waitFor();
  await page.getByText('Watch this teammate’s desktop, or take over in a private browser and hand it back when you finish.').waitFor();
  assert.equal(await page.getByRole('button',{name:'Open browser'}).count(),0,'The bot’s own browser is not offered to the owner');
  assert.equal(await page.getByText(/recording|teaching/i).count(),0,'No Teaching or recording controls remain');
  checks.push('The computer pane offers a private takeover, no bot-browser shortcut and no recording controls.');
  await page.getByRole('button',{name:'Take over privately'}).click();
  await page.getByText('Opening your private browser…',{exact:true}).waitFor();
  await page.waitForTimeout(2500);
  assert.equal(sockets.length,0,'No live view connects before the private browser is ready');
  checks.push('While the private browser starts, no live view connects, so input can never reach the bot’s screen.');
  releaseSession();
  await page.getByText('You are in control · private browser',{exact:true}).waitFor();
  await page.getByText(/This is a private browser\. The bot can’t see this screen or what you type\./).waitFor();
  await page.getByLabel('Live bot computer').waitFor();
  await page.waitForTimeout(1000);
  assert(sockets.length>=1&&sockets.every(s=>s.private_session),'The live view connects only once the private session exists');
  checks.push('Once ready, the live view opens by itself and the privacy note explains cookies, passwords and the address bar.');
  await page.screenshot({path:`${out}/desktop-private-session.png`});
  await page.getByRole('button',{name:'Hand back',exact:true}).click();
  await page.getByText('Bot is in control',{exact:true}).waitFor();
  checks.push('Hand back returns control to the bot.');
  await page.keyboard.press('Escape');
  await page.getByRole('navigation',{name:'Team conversations'}).getByRole('button',{name:/Quill/}).click();
  await page.getByRole('button',{name:'Open Quill memory and skills'}).click();
  await page.getByText('Prefers short summaries.').waitFor();
  assert.equal(await page.getByText(/teaching|recording|demonstrat/i).count(),0,'The knowledge pane has no Teaching section');
  checks.push('The memory and skills pane opens without any Teaching section.');
  await context.close();
  Object.assign(computer,{lease:{holder:'human',epoch:4,reason:'Owner takeover'},mine:true,input_ready:true,admission_paused:true,private_session:true});
  for(const [width,height] of [[390,844],[768,1024]]){
    const [mobile,small]=await open(width,height);
    await small.getByRole('button',{name:'Open navigation'}).click().catch(()=>{});
    await small.getByRole('navigation',{name:'Team conversations'}).getByRole('button',{name:/Scout/}).click();
    // Below 650px the header tools collapse into the conversation tools menu.
    if(width<=650){await small.getByLabel('Conversation tools').click();await small.getByRole('button',{name:'Computer',exact:true}).click();}
    else await small.getByRole('button',{name:'Open Scout computer'}).click();
    await small.getByText('You are in control · private browser',{exact:true}).waitFor({timeout:10000}).catch(async error=>{await small.screenshot({path:`${out}/failure-${width}.png`});console.error((await small.locator('body').innerText()).slice(0,1500));throw error;});
    assert(!await overflow(small),`No horizontal overflow at ${width}px`);
    await small.screenshot({path:`${out}/private-session-${width}.png`});
    await mobile.close();
  }
  checks.push('The private-session pane has no horizontal overflow at 390 and 768 px.');
  assert.deepEqual(errors,[]);
  assert(writes.every(w=>w==='POST /api/v1/bots/researcher/computer/control'),`Unexpected writes: ${writes}`);
}finally{
  await browser.close();server.close();
  await writeFile(`${out}/report.json`,JSON.stringify({checks,errors,writes,sockets:sockets.length},null,2));
}
console.log(JSON.stringify({checks,errors,writes},null,2));
