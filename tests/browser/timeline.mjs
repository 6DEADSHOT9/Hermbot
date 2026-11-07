import {chromium} from 'playwright';
import assert from 'node:assert/strict';
import {createServer} from 'node:http';
import {readFile,writeFile,mkdir} from 'node:fs/promises';
import {resolve,extname} from 'node:path';

// Synthetic history only. Every API request is intercepted; no live owner session.
const root=resolve(process.argv[2]||'test-results/timeline-web');
const out=resolve(process.argv[3]||'test-results/timeline-ui');await mkdir(out,{recursive:true});
const server=createServer(async(req,res)=>{try{
  const path=resolve(root,'.'+new URL(req.url,'http://fixture').pathname.replace(/\/$/,'/index.html'));
  if(!path.startsWith(root+'/')){res.writeHead(403);return res.end();}
  res.writeHead(200,{'content-type':{'.html':'text/html','.js':'text/javascript','.css':'text/css','.woff2':'font/woff2'}[extname(path)]||'application/octet-stream'});res.end(await readFile(path));
}catch{res.writeHead(404);res.end();}});
await new Promise(r=>server.listen(0,'127.0.0.1',r));
const origin=`http://127.0.0.1:${server.address().port}`;
const browser=await chromium.launch({headless:true,channel:'chrome'});
const errors=[],checks=[],writes=[];
try{
  const context=await browser.newContext({viewport:{width:1440,height:900},serviceWorkers:'block',timezoneId:'UTC'});
  await context.addInitScript(()=>{window.__streams=[];window.EventSource=class extends EventTarget{constructor(){super();window.__streams.push(this);}close(){}};});
  const page=await context.newPage();page.on('pageerror',error=>{errors.push(error.message);console.error(error.message);});
  const bots=['writer','researcher'].map((id,index)=>({id,name:index?'Scout':'Quill',role:'Teammate',color:index?'#cf8d42':'#728eca',avatar:'',capabilities:[],status:'active',desired_state:'running',revision:1,readiness:{compatible:true},enabled_tools:[],enabled_skills:[]}));
  const conversations=bots.map((bot,index)=>({id:index?'scout':'quill',title:bot.name,kind:'dm',bot_id:bot.id,bot_ids:[bot.id]}));
  const task=(id,status='completed',error=null)=>({id,root_id:id,parent_id:null,bot_id:'writer',conversation_id:'quill',input:'Greeting',depth:0,context:'chat',created_at:'2026-11-02T18:45:00Z',deadline:null,attempts:1,status,error,message_intent:'discussion'});
  const message=(id,content,role='assistant',author='researcher',date='2026-11-03T06:00:00Z')=>({id,source_id:`fixture:${id}`,task_id:null,content,role,author,created_at:date,attachment_ids:[],mentions:[],reply_to_message_id:null,reactions:[],reply_count:0});
  const entry=(position,m,tasks=[])=>({id:`message:${m.id}`,position,revision:position,occurred_at:m.created_at,thread_id:null,message:m,tasks});
  const histories={quill:[entry(1,message(1,'Earlier greeting','user','owner','2026-11-02T18:45:00Z'),[task('failed','failed','Old authentication failure')]),entry(2,message(2,'Please try again','user','owner'),[{...task('blocked','queued','The model could not interpret this message'),message_intent:'auto',orchestration_version:2,intake_status:'failed'}]),entry(3,message(3,'Latest greeting','user','owner'),[task('latest')]),entry(4,message(4,'Hi! What can I help with?','assistant','writer'))],scout:Array.from({length:149},(_,i)=>entry(i+1,message(i+100,`History ${i+1}`)))};
  const state={bots,retired_bots:[],conversations,tasks:[],work_items:[],approvals:[],input_requests:[],artifacts:[],grants:[],proposals:[],operations:[],controls:[],workspace:{},provider_configured:true,event_cursor:0};
  let holdEarlier=null,delayEarlier=false;
  await context.route('**/*',async route=>{
    const req=route.request(),url=new URL(req.url());if(url.origin!==origin)return route.abort();
    if(!url.pathname.startsWith('/api/'))return route.continue();
    if(req.method()!=='GET'){writes.push(url.pathname);return route.fulfill({status:403,json:{detail:'Fixture rejects writes'}});}
    let result={};
    if(url.pathname==='/api/v1/state')result=state;
    else if(url.pathname.includes('/teaching'))result=[];
    else if(url.pathname==='/api/v1/attention')result={unread:0,needs_owner:0,items:[]};
    else if(url.pathname.match(/\/conversations\/[^/]+\/timeline$/)){
      const cid=url.pathname.split('/')[4],all=structuredClone(histories[cid]);
      if(delayEarlier&&url.searchParams.has('before')){await new Promise(resolve=>holdEarlier=resolve);delayEarlier=false;}
      const after=url.searchParams.get('after'),before=url.searchParams.get('before');
      let rows=after!==null?all.filter(e=>e.revision>Number(after)).sort((a,b)=>a.revision-b.revision):all.filter(e=>!e.deleted&&(before===null||e.position<Number(before))).sort((a,b)=>b.position-a.position);
      const more=rows.length>100;rows=rows.slice(0,100);if(after===null)rows.reverse();
      result={entries:rows,cursor:after!==null&&more?rows.at(-1).revision:Math.max(0,...all.map(e=>e.revision)),has_more:after!==null&&more,has_earlier:after===null&&more,artifacts:[],attachments:[]};
    }else if(url.pathname.startsWith('/api/v1/tasks/'))result={...task(url.pathname.split('/').at(-1)),children:[],executions:[],events:[]};
    else if(url.pathname.endsWith('/conversations/quill'))result={...conversations[0],messages:histories.quill.map(e=>e.message).filter(Boolean),has_earlier:false,artifacts:[],attachments:[]};
    await route.fulfill({json:result});
  });
  const refresh=async()=>{await page.evaluate(()=>window.__streams.forEach(stream=>stream.dispatchEvent(new MessageEvent('update',{data:JSON.stringify({id:1,kind:'task.updated',conversation_id:'scout',data:{}})}))));};
  await page.goto(origin);await page.getByText('Hi! What can I help with?',{exact:true}).waitFor({timeout:10000}).catch(async error=>{await page.screenshot({path:`${out}/failure.png`});console.error((await page.locator('body').innerText()).slice(0,3000));throw error;});
  assert(await page.locator('[data-message-id="1"]').innerText().then(text=>text.includes('Old authentication failure')));
  assert(!await page.locator('[data-message-id="4"]').innerText().then(text=>text.includes('Old authentication failure')));
  assert.equal(await page.locator('.conversation-day').count(),2);checks.push('Old failures are attached to the original request; days are separated.');
  await page.locator('[data-response-task-id="failed"]').getByText('Inspect response',{exact:true}).click();
  await page.getByRole('heading',{name:'Inspect the work'}).waitFor();await page.getByRole('button',{name:'Close',exact:true}).click();
  checks.push('Historical task inspection works without the global task snapshot.');
  await page.getByRole('button',{name:'Scout Teammate'}).click();await page.locator('[data-message-id="248"]').waitFor();
  const scroll=page.locator('.conversation-scroll');await scroll.evaluate(node=>node.scrollTop=0);
  const anchor=page.locator('[data-entry-id="message:149"]');const before=(await anchor.boundingBox()).y;
  delayEarlier=true;await page.getByRole('button',{name:'Load earlier messages',exact:true}).click();
  await page.waitForFunction(()=>document.querySelector('button')!==null);
  histories.scout.push(entry(150,message(249,'New response while reading')));await refresh();await page.getByText('New response while reading',{exact:true}).waitFor();
  assert(await scroll.evaluate(node=>node.scrollTop<100));await page.getByRole('button',{name:'New messages ↓'}).waitFor();
  holdEarlier();await page.locator('[data-message-id="100"]').waitFor();
  assert.equal(await page.locator('[data-entry-id]').count(),150);
  assert(Math.abs((await anchor.boundingBox()).y-before)<3);checks.push('149 loaded messages plus a live result remain 150; prepending preserves the visible anchor.');
  await page.getByRole('button',{name:'New messages ↓'}).click();
  assert(await scroll.evaluate(node=>node.scrollHeight-node.scrollTop-node.clientHeight<100));
  for(let index=151;index<=275;index++)histories.scout.push(entry(index,message(index+99,`Burst ${index}`)));
  await refresh();await page.getByText('Burst 275',{exact:true}).waitFor();assert.equal(await page.locator('[data-entry-id]').count(),275);checks.push('Reconnect catchup drains more than one page.');
  const stream={id:'response:attempt-one',position:276,revision:276,occurred_at:'2026-11-03T06:01:00Z',thread_id:null,author:'researcher',status:'running',text:'Live response',tasks:[]};histories.scout.push(stream);await refresh();await page.getByText('Live response',{exact:true}).waitFor();
  await page.locator('[data-entry-id="response:attempt-one"]').evaluate(node=>node.dataset.stable='yes');
  Object.assign(stream,{revision:277,status:'completed',message:message(999,'Final response')});await refresh();await page.getByText('Final response',{exact:true}).waitFor();
  assert.equal(await page.locator('[data-entry-id="response:attempt-one"]').getAttribute('data-stable'),'yes');checks.push('Stream and final output reuse the same mounted entry.');
  const question={id:'question:fixture',position:278,revision:278,occurred_at:'2026-11-03T06:02:00Z',thread_id:null,author:'researcher',tasks:[],actionable:true,question:{id:'fixture',task_id:'latest',prompt:'Which format?',answer_type:'text',choices:[],status:'pending',question_hash:'fixture',expires_at:'2099-01-01T00:00:00Z'}};
  histories.scout.push(question);await refresh();const input=page.getByRole('textbox',{name:"Answer the bot's question"});await input.fill('Markdown');
  stream.revision=279;stream.message.content='Final response updated';await refresh();await page.getByText('Final response updated',{exact:true}).waitFor();assert(await input.evaluate(node=>node===document.activeElement));assert.equal(await input.inputValue(),'Markdown');
  question.question.status='answered';question.question.answer='Markdown';question.actionable=false;question.revision=280;await refresh();await page.getByText('Question answered',{exact:true}).waitFor();await page.getByText('Which format?',{exact:true}).waitFor();checks.push('Question input keeps focus on refresh; answered questions remain in history.');
  for(const width of [1440,768,390]){await page.setViewportSize({width,height:900});await page.waitForTimeout(300);await page.screenshot({path:`${out}/timeline-${width}.png`});assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));}
  await page.getByRole('button',{name:'Open navigation',exact:true}).click();await page.getByRole('button',{name:'Quill Teammate'}).click();await page.getByText('Hi! What can I help with?',{exact:true}).waitFor();
  assert.equal(await page.locator('[data-entry-id="response:attempt-one"]').count(),0);checks.push('Conversation switches keep their own cache; mobile has no horizontal overflow.');
  const other=await context.newPage();other.on('pageerror',error=>errors.push(error.message));await other.goto(origin);await other.getByText('Hi! What can I help with?',{exact:true}).waitFor();
  await other.getByRole('button',{name:'Scout Teammate'}).click();await other.getByText('Question answered',{exact:true}).waitFor();
  const olderButton=other.getByRole('button',{name:'Load earlier messages',exact:true});await other.locator('.conversation-scroll').evaluate(node=>node.scrollTop=0);
  delayEarlier=true;await olderButton.click();await other.getByRole('button',{name:'Loading earlier messages…',exact:true}).waitFor();
  histories.scout[100].revision=400;histories.scout[100].message.content='Updated unloaded history';
  histories.scout[101].revision=401;histories.scout[101].deleted=true;
  await other.evaluate(()=>window.__streams.forEach(stream=>stream.dispatchEvent(new MessageEvent('update',{data:JSON.stringify({id:2,kind:'task.updated',conversation_id:'scout',data:{}})}))));
  await other.waitForTimeout(600);holdEarlier();await other.getByText('Updated unloaded history',{exact:true}).waitFor();
  assert.equal(await other.locator('[data-message-id="201"]').count(),0);checks.push('A stale older page cannot undo a newer update or resurrect a tombstone.');
  await other.close();
  await page.setViewportSize({width:1440,height:900});await page.waitForTimeout(300);await page.locator('.conversation-scroll').evaluate(node=>node.scrollTop=0);
  await writeFile(`${root}/inspection.html`,(await page.content()).replace(/<script\b[^>]*>[\s\S]*?<\/script>/gi,''));
  assert.deepEqual(writes,[]);assert.deepEqual(errors,[]);
  await writeFile(`${out}/checks.json`,JSON.stringify({checks,errors,writes},null,2));console.log(JSON.stringify({checks,errors,writes},null,2));
}finally{await browser.close();await new Promise(resolve=>server.close(resolve));}
