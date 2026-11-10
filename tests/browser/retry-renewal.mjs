import {chromium} from 'playwright';
import assert from 'node:assert/strict';
import {createServer} from 'node:http';
import {readFile,writeFile,mkdir} from 'node:fs/promises';
import {resolve,extname} from 'node:path';

// Synthetic work cards only: every API request is intercepted and nothing reaches a runtime.
// Retrying team work keeps its own management permission. When that has expired or expires within the hour,
// Retry asks the owner before renewing it, and sends nothing if they decline.
const root=resolve(process.argv[2]||'test-results/retry-renewal-web');
const out=resolve(process.argv[3]||'test-results/retry-renewal-ui');await mkdir(out,{recursive:true});
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
const errors=[],checks=[],retries=[],dialogs=[];
const bots=['writer','researcher'].map((id,index)=>({id,name:index?'Scout':'Quill',role:'Teammate',color:index?'#cf8d42':'#728eca',avatar:'',capabilities:[],status:'active',desired_state:'running',revision:1,applied_revision:1,readiness:{compatible:true},enabled_tools:[],enabled_skills:[],memory_mb:2048,cpus:1.5}));
const conversations=bots.map((bot,index)=>({id:index?'scout':'quill',title:bot.name,kind:'dm',bot_id:bot.id,bot_ids:[bot.id]}));
const hours=h=>new Date(Date.now()+h*3600000).toISOString();
const grants=[['fresh',20],['lapsing',0.5],['expired',-1]].map(([name,h])=>({id:`grant-${name}`,manager_id:'researcher',targets:['writer'],operations:['delegate','inspect'],kind:'task',expires_at:hours(h),revoked_at:null,max_created_bots:0,created_bots:0,max_memory_mb:2048,max_cpus:1.5}));
const item=(name,title)=>{const task={id:`task-${name}`,root_id:`task-${name}`,parent_id:null,bot_id:'researcher',conversation_id:'scout',input:title,input_hash:'0'.repeat(64),status:'failed',output:null,error:'Model unavailable',grant_id:`grant-${name}`,depth:0,context:'chat',created_at:hours(-2),deadline:null,attempts:1};
  return {id:`item-${name}`,conversation_id:'scout',bot_id:'researcher',title,objective:title,status:'failed',hidden_at:null,legacy:false,outcomes_version:1,outcomes:[],current_task:task,executions:[task],outputs:[],updated_at:hours(-1)};};
const work_items=[item('fresh','Coordinate the weekly report'),item('lapsing','Coordinate the launch notes'),item('expired','Coordinate the client brief')];
const state={bots,retired_bots:[],conversations,tasks:work_items.map(w=>w.current_task),work_items,approvals:[],input_requests:[],artifacts:[],grants,proposals:[],operations:[],controls:[],workspace:{},provider_configured:true,protocol_fixture:false,event_cursor:0};
try{
  const context=await browser.newContext({viewport:{width:1440,height:900},serviceWorkers:'block',timezoneId:'UTC'});
  await context.addInitScript(()=>{window.EventSource=class extends EventTarget{close(){}};});
  await context.route('**/*',async route=>{
    const req=route.request(),url=new URL(req.url());if(url.origin!==origin)return route.abort();
    if(!url.pathname.startsWith('/api/'))return route.continue();
    const retry=url.pathname.match(/^\/api\/v1\/tasks\/([^/]+)\/retry$/);
    if(retry&&req.method()==='POST'){retries.push({task:retry[1],body:JSON.parse(req.postData()||'{}')});return route.fulfill({status:202,json:{id:'replacement-'+retry[1]}});}
    if(req.method()!=='GET')return route.fulfill({status:403,json:{detail:'Fixture rejects writes'}});
    let result={};
    if(url.pathname==='/api/v1/state')result=state;
    else if(url.pathname==='/api/v1/attention')result={unread:0,needs_owner:0,items:[]};
    else if(url.pathname.endsWith('/timeline'))result={entries:[],cursor:0,has_more:false,has_earlier:false,artifacts:[],attachments:[]};
    else if(url.pathname.endsWith('/capabilities'))result={inventory_known:true,running:true,human_control:false,native_tools:[],connector_restricted:false,blocked_tools:[],connections:[]};
    await route.fulfill({json:result});
  });
  const page=await context.newPage();page.on('pageerror',error=>{errors.push(error.message);console.error(error.message);});
  let answer=true;
  page.on('dialog',async dialog=>{dialogs.push(dialog.message());await (answer?dialog.accept():dialog.dismiss());});
  await page.goto(origin);
  await page.getByRole('navigation',{name:'Team conversations'}).getByRole('button',{name:/Scout/}).click();
  const card=name=>page.locator(`[data-work-item-id="item-${name}"]`);
  const retryButton=name=>card(name).getByRole('button',{name:'Retry task',exact:true});
  await page.getByText('Finished work (3)',{exact:true}).click();  // Failed work is listed under finished work.
  await retryButton('fresh').waitFor();
  await page.screenshot({path:`${out}/work-cards-1440.png`});

  await retryButton('fresh').click();
  await page.waitForTimeout(400);
  assert.equal(dialogs.length,0,'Plenty of time left: no question asked');
  assert.deepEqual(retries.at(-1),{task:'task-fresh',body:{}});
  checks.push('A team task with plenty of permission left retries straight away, without renewing anything.');

  answer=false;
  await retryButton('lapsing').click();
  await page.waitForTimeout(400);
  assert.equal(dialogs.at(-1),"Scout's permission to manage Quill for this work expires within the hour. Renew it for 24 hours and retry?");
  assert.equal(retries.length,1,'Declining sends nothing');
  checks.push('When the permission expires within the hour, Retry asks first, names the teammates, and declining sends nothing.');

  answer=true;
  await retryButton('lapsing').click();
  await page.waitForTimeout(400);
  assert.deepEqual(retries.at(-1),{task:'task-lapsing',body:{renew_management:true}});
  checks.push('Confirming renews the same permission as part of the retry.');

  await retryButton('expired').click();
  await page.waitForTimeout(400);
  assert.match(dialogs.at(-1),/^Scout's permission to manage Quill for this work has expired\. Renew it for 24 hours and retry\?$/);
  assert.deepEqual(retries.at(-1),{task:'task-expired',body:{renew_management:true}});
  checks.push('An expired permission is also renewed only on confirmation.');
  assert.deepEqual(errors,[]);
  await context.close();
}finally{
  await browser.close();server.close();
  await writeFile(`${out}/report.json`,JSON.stringify({checks,errors,retries,dialogs},null,2));
}
console.log(JSON.stringify({checks,errors,retries,dialogs},null,2));
