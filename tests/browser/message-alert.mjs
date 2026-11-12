import {chromium} from 'playwright';
import assert from 'node:assert/strict';
import {createServer} from 'node:http';
import {readFile,writeFile,mkdir} from 'node:fs/promises';
import {resolve,extname} from 'node:path';

// Synthetic attention items only: every API request is intercepted and nothing reaches a runtime.
// A teammate message that couldn't be delivered always offers Resend and Done while its alert is open. Nothing
// predicts whether Resend will work: a resend that can't be delivered comes back with its reason and both actions,
// and a refused action (already handled) shows the server's reason. Each action names the delivery revision the
// alert showed; when another tab has moved on, the server refuses it and the page reloads the alert.
const root=resolve(process.argv[2]||'test-results/message-alert-web');
const out=resolve(process.argv[3]||'test-results/message-alert-ui');await mkdir(out,{recursive:true});
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
const errors=[],checks=[],resolutions=[],reads=[];
const bots=['writer','researcher'].map((id,index)=>({id,name:index?'Scout':'Quill',role:'Teammate',color:index?'#cf8d42':'#728eca',avatar:'',capabilities:[],status:'active',desired_state:'running',revision:1,applied_revision:1,readiness:{compatible:true},enabled_tools:[],enabled_skills:[],memory_mb:2048,cpus:1.5}));
const conversations=bots.map((bot,index)=>({id:index?'scout':'quill',title:bot.name,kind:'dm',bot_id:bot.id,bot_ids:[bot.id]}));
const state={bots,retired_bots:[],conversations,tasks:[],work_items:[],approvals:[],input_requests:[],artifacts:[],grants:[],proposals:[],operations:[],controls:[],workspace:{},provider_configured:true,protocol_fixture:false,event_cursor:0};
const TITLE='A message from Scout to Quill couldn’t be delivered';
const RESENT_TITLE='Your resent message from Scout to Quill couldn’t be delivered';
async function open(scenario){
  // scenario: {comesBack, refuse}. After a resolution the item reads as resolved, unless the resend came back.
  // control.elsewhere() is another tab resending it, after which it failed again (revision 2), unseen by this page.
  let resolved=false,back=false,version=1;
  const item=()=>({id:'item-msg',version,title:back?RESENT_TITLE:TITLE,summary:back?'Quill is archived. Resend it, or choose Done if you’ll handle it yourself.':'It failed 3 times and was dropped. If Scout was waiting for the answer, it was told. Resend it, or choose Done if you’ll handle it yourself.',
    source_status:'failed',active:!resolved||back,needs_owner:!resolved||back,unread:!resolved||back,updated_at:new Date().toISOString(),refs:{bot_id:'researcher',bot_name:'Scout',message_id:'msg-1',task_id:'task-1',conversation_id:'scout',delivery_revision:back?2:1}});
  const context=await browser.newContext({viewport:{width:1440,height:900},serviceWorkers:'block',timezoneId:'UTC'});
  await context.addInitScript(()=>{window.EventSource=class extends EventTarget{close(){}};});
  await context.route('**/*',async route=>{
    const req=route.request(),url=new URL(req.url());if(url.origin!==origin)return route.abort();
    if(!url.pathname.startsWith('/api/'))return route.continue();
    if(req.method()==='POST'&&url.pathname==='/api/v1/teammate-messages/msg-1/resolve'){
      const body=JSON.parse(req.postData()||'{}');resolutions.push(body);
      if(scenario.refuse)return route.fulfill({status:409,json:{detail:scenario.refuse}});
      if(body.expected_revision!==item().refs.delivery_revision)return route.fulfill({status:409,json:{detail:STALE}});
      resolved=true;back=!!(scenario.comesBack&&body.action==='resend');version+=1;  // A resend may be released again within a second.
      return route.fulfill({json:{id:'msg-1',action:body.action}});
    }
    if(req.method()==='POST'&&url.pathname==='/api/v1/attention/read'){reads.push(JSON.parse(req.postData()||'{}'));return route.fulfill({json:{status:'recorded'}});}
    if(req.method()!=='GET')return route.fulfill({status:403,json:{detail:'Fixture rejects writes'}});
    let result={};
    if(url.pathname==='/api/v1/state')result=state;
    else if(url.pathname==='/api/v1/attention/item-msg')result={...item(),work:null,task:null,conversation:null,thread:null,request:null};
    else if(url.pathname==='/api/v1/attention'){const value=item();result={items:value.active&&value.needs_owner?[value]:[],unread:value.unread?1:0,needs_owner:value.active&&value.needs_owner?1:0,next:null};}
    else if(url.pathname.endsWith('/timeline'))result={entries:[],cursor:0,has_more:false,has_earlier:false,artifacts:[],attachments:[]};
    await route.fulfill({json:result});
  });
  const page=await context.newPage();page.on('pageerror',error=>{errors.push(error.message);console.error(error.message);});
  await page.goto(origin+'/?attention=item-msg');
  await page.getByRole('heading',{name:scenario.startBack?RESENT_TITLE:TITLE}).waitFor();
  return {page,context,elsewhere:()=>{back=true;version+=1;}};
}
const STALE='This alert changed since you opened it. Check the latest failure, then choose again.';
try{
  const resend=page=>page.getByRole('button',{name:'Resend message',exact:true});
  const done=page=>page.getByRole('button',{name:'Done',exact:true});

  let {page,context}=await open({});
  await resend(page).waitFor();await done(page).waitFor();
  await page.screenshot({path:`${out}/open-alert-1440.png`});
  await Promise.all([page.waitForRequest(r=>r.url().endsWith('/api/v1/attention/read')),done(page).click()]);
  await page.getByText('This request is resolved or no longer actionable.').waitFor();
  assert.deepEqual(resolutions,[{action:'done',expected_revision:1}]);
  assert.equal(await resend(page).count(),0,'No actions remain once it is resolved');
  assert.equal(reads.at(-1).action,'read');
  checks.push('An open alert offers Resend and Done; Done closes it.');
  await context.close();

  ({page,context}=await open({}));
  await Promise.all([page.waitForRequest(r=>r.url().endsWith('/api/v1/attention/read')),resend(page).click()]);
  await page.getByText('This request is resolved or no longer actionable.').waitFor();
  assert.deepEqual(resolutions.at(-1),{action:'resend',expected_revision:1});
  checks.push('Resend sends the message back and closes the alert.');
  await context.close();

  ({page,context}=await open({comesBack:true}));
  await Promise.all([page.waitForRequest(r=>r.url().endsWith('/api/v1/attention/read')),resend(page).click()]);
  await page.getByRole('heading',{name:RESENT_TITLE}).waitFor();
  await page.getByText('Quill is archived. Resend it, or choose Done if you’ll handle it yourself.').waitFor();
  await resend(page).waitFor();await done(page).waitFor();
  await page.screenshot({path:`${out}/came-back-1440.png`});
  checks.push('A resend that can’t be delivered comes back with its reason, and both actions are offered again.');
  await context.close();

  const REASON='This message was already handled';
  ({page,context}=await open({refuse:REASON}));
  await resend(page).click();
  await page.getByRole('alert').filter({hasText:REASON}).waitFor();
  await done(page).waitFor();
  checks.push("A refused action shows the server's reason and leaves the alert open.");
  await context.close();

  let elsewhere;
  ({page,context,elsewhere}=await open({}));
  elsewhere();  // Another tab resent it and it failed again; this page still shows the first failure.
  await resend(page).click();
  await page.getByRole('alert').filter({hasText:STALE}).waitFor();
  await page.getByRole('heading',{name:RESENT_TITLE}).waitFor();  // Reloaded without leaving the page.
  await page.getByText('Quill is archived. Resend it, or choose Done if you’ll handle it yourself.').waitFor();
  assert.deepEqual(resolutions.at(-1),{action:'resend',expected_revision:1});
  await page.screenshot({path:`${out}/stale-tab-1440.png`});
  await Promise.all([page.waitForRequest(r=>r.url().endsWith('/api/v1/attention/read')),done(page).click()]);
  await page.getByText('This request is resolved or no longer actionable.').waitFor();
  assert.deepEqual(resolutions.at(-1),{action:'done',expected_revision:2});
  checks.push('An action from an older view is refused, the alert reloads to the latest failure, and the next action names it.');
  await context.close();
  assert.deepEqual(errors,[]);
}finally{
  await browser.close();server.close();
  await writeFile(`${out}/report.json`,JSON.stringify({checks,errors,resolutions,reads},null,2));
}
console.log(JSON.stringify({checks,errors,resolutions},null,2));
