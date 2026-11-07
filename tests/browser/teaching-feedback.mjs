import {chromium} from 'playwright';
import assert from 'node:assert/strict';
import {createServer} from 'node:http';
import {readFile,writeFile,mkdir} from 'node:fs/promises';
import {resolve,extname} from 'node:path';

// This fixture serves public build assets only; all API calls and WebSockets are intercepted.
const root=resolve(process.argv[2]||'web/dist'),out=resolve('test-results/teaching-feedback-ui');
await mkdir(out,{recursive:true});
const server=createServer(async(req,res)=>{
  try{
    const file=resolve(root,'.'+new URL(req.url,'http://fixture').pathname.replace(/\/$/,'/index.html'));
    if(!file.startsWith(root+'/')){res.writeHead(403);return res.end();}
    const type={'.html':'text/html','.js':'text/javascript','.css':'text/css','.woff2':'font/woff2','.svg':'image/svg+xml'}[extname(file)]||'application/octet-stream';
    res.writeHead(200,{'content-type':type});res.end(await readFile(file));
  }catch{res.writeHead(404);res.end();}
});
await new Promise(r=>server.listen(0,'127.0.0.1',r));
const origin=`http://127.0.0.1:${server.address().port}`;
const browser=await chromium.launch({headless:true,channel:'chrome'});
const errors=[],writes=[],checks=[];
try{
  const context=await browser.newContext({serviceWorkers:'block',viewport:{width:1440,height:900}});
  const page=await context.newPage();page.on('pageerror',e=>errors.push(e.message));
  let mine=false,dismissFail=false;
  const bot={id:'researcher',name:'Scout',role:'Research and evidence',color:'#cf8d42',avatar:'',capabilities:['research'],status:'active',desired_state:'running',revision:1,applied_revision:1,enabled_tools:['browser','skills'],enabled_skills:[],readiness:{compatible:true}};
  const conversation={id:'fixture-dm',kind:'dm',title:'Scout',bot_id:bot.id,bot_ids:[bot.id]};
  const state={bots:[bot],retired_bots:[],conversations:[conversation],tasks:[],work_items:[],approvals:[],artifacts:[],grants:[],proposals:[],operations:[],controls:[],input_requests:[],workspace:{timezone:'UTC',permitted_tools:['browser','skills'],max_bots:10},provider_configured:true,event_cursor:0};
  const content='---\nname: hermbot-fixture-notes\ndescription: "Prepare a local note"\n---\nUse the textbox and independently compare the result.';
  const detail={id:'fixture-recording',revision:5,state:'ready',reason:'finish',last_seq:123,retained_bytes:8192,skill_name:'hermbot-fixture-notes',evidence_hash:'e'.repeat(64),draft_hash:'a'.repeat(64),job_state:'completed',error:null,pinned:true,created_at:'2026-11-02T10:00:00Z',info:{goal:'Prepare temporary notes',expected_result:'Verified displayed text'},evidence:{text:'Reviewed local note procedure.',frames:[]},draft:content,learning:{summary:'The textbox and verification instructions are incorporated. Existing-content behaviour needs your answer.',changes:[{text:'Use the single note textbox; a separate title is unnecessary.',source_ids:['answer:fixture']}],questions:[{id:'fixture-question',prompt:'Should existing notes be appended to, replaced, or left pending your decision?',source_ids:['test:fixture']}]},tests:[{id:'fixture-test',task_id:'fixture-task',conversation_id:conversation.id,status:'completed',current:true,consulted_at:'2026-11-02T10:01:00Z',owner_passed:false,checks:'Check the note and preserve existing content.',report:'',output:'The expected text appeared, but earlier text remained. The clean-note check failed.'}],publications:[]};
  await page.addInitScript(()=>{sessionStorage.setItem('teaching-selected:researcher','fixture-recording');sessionStorage.setItem('teaching-closed:researcher','fixture-recording');});
  await page.addInitScript(()=>{
    // noVNC validates immediate prototype properties; use a closed synthetic channel, never a live desktop.
    class FixtureSocket {
      static CONNECTING=0;static OPEN=1;static CLOSING=2;static CLOSED=3;
      constructor(){this.binaryType='arraybuffer';this.onerror=null;this.onmessage=null;this.onopen=null;this.onclose=null;this.protocol='';this.readyState=0;queueMicrotask(()=>this.close());}
      send(){}
      close(){this.readyState=3;this.onclose?.({code:1000,reason:'Synthetic desktop fixture',wasClean:true});}
    }
    window.WebSocket=FixtureSocket;
  });
  await page.route('**/*',async route=>{
    const request=route.request(),url=new URL(request.url());
    if(url.origin!==origin)return route.abort();
    if(!url.pathname.startsWith('/api/'))return route.continue();
    const path=url.pathname,method=request.method();
    let data={};
    if(method!=='GET'){
      writes.push({path,method});
      if(path.endsWith('/teaching-dismiss')){
        if(dismissFail)return route.fulfill({status:503,json:{detail:'Synthetic pause unavailable'}});
        if(detail.state==='recording')detail.state='paused';
        data={capture:detail};
      }else if(path.endsWith('/answer')){
        const answer=request.postDataJSON();assert.equal(answer.scope,'workflow');
        detail.learning={summary:'Your answer is incorporated. Test the revised skill.',changes:[{text:'Ask before replacing existing notes.',source_ids:['teaching-answer:fixture-question']}],questions:[],suggested_inputs:'Write a fresh local note.',suggested_checks:'Read back exact text; ask before overwriting existing notes.'};
        detail.draft=content+'\nAsk before replacing existing notes.';detail.draft_hash='b'.repeat(64);detail.revision++;
        detail.tests[0].current=false;data={queued:true};
      }else return route.fulfill({status:403,json:{detail:'Fixture blocks this mutation'}});
    }else if(path==='/api/v1/state')data=state;
    else if(path==='/api/v1/attention')data={items:[],unread:0,needs_owner:0,next:null};
    else if(path==='/api/v1/events')return route.fulfill({contentType:'text/event-stream',body:': fixture\n\n'});
    else if(path==='/api/v1/conversations/fixture-dm')data={...conversation,messages:[],artifacts:[],attachments:[],has_earlier:false};
    else if(path.endsWith('/teaching'))data=[detail];
    else if(path.endsWith('/teaching/fixture-recording'))data=detail;
    else if(path.endsWith('/timeline'))data={events:[],steps:[]};
    else if(path.endsWith('/revisions'))data=[];
    else if(path.endsWith('/computer'))data={desktop:{installed:true,running:true,geometry:'1440x900',blocker:null,memory_available_mb:1800,browser:'Chromium'},lease:{holder:mine?'human':'agent',epoch:1,reason:''},mine,input_ready:true,admission_paused:mine};
    else if(path.endsWith('/computer/thumbnail'))data={image:null};
    else return route.fulfill({status:404,json:{detail:'No fixture for '+path}});
    return route.fulfill({json:structuredClone(data)});
  });
  await page.goto(origin);
  await page.getByRole('button',{name:'Open recording',exact:true}).click();
  const dialog=page.getByRole('dialog',{name:'Scout computer'});
  await dialog.getByText('Clarify this workflow',{exact:true}).waitFor();
  assert.ok(await dialog.getByRole('button',{name:'Run candidate test'}).isDisabled());
  assert.match(await dialog.innerText(),/Execution finished/);
  assert.match(await dialog.innerText(),/Checks not owner-verified/);
  for(const width of [1440,768,390,320]){
    await page.setViewportSize({width,height:900});
    await page.waitForTimeout(150);
    assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false);
    assert.equal(await dialog.evaluate(e=>e.scrollWidth>e.clientWidth+1),false);
    await page.screenshot({path:`${out}/questions-${width}.png`});
  }
  const html=await page.evaluate(()=>`<!doctype html><html data-theme="light"><head><meta name="viewport" content="width=device-width,initial-scale=1">${[...document.querySelectorAll('link[rel="stylesheet"]')].map(e=>e.outerHTML).join('')}</head><body>${document.querySelector('[role="dialog"]').outerHTML}</body></html>`);
  await writeFile(root+'/inspection.html',html);
  checks.push('Focused question and truthful execution/check states render at 1440, 768, 390 and 320 px without horizontal overflow.');
  await page.setViewportSize({width:1440,height:900});
  await dialog.getByLabel('Your answer',{exact:true}).fill('Ask before replacing existing notes.');
  await dialog.getByRole('button',{name:'Answer and update skill'}).click();
  await dialog.getByText('Your answer is incorporated. Test the revised skill.').waitFor();
  assert.equal(await dialog.getByLabel('Fresh test inputs').inputValue(),'Write a fresh local note.');
  assert.equal(await dialog.getByRole('button',{name:'Run candidate test'}).isEnabled(),true);
  assert.match(await dialog.innerText(),/Earlier candidate/);
  checks.push('Answer prepares a revision and suggested tests; earlier execution does not become a passing current test.');
  await dialog.getByText('Edit skill text (advanced)',{exact:true}).click();
  const editor=dialog.getByLabel('SKILL.md',{exact:true});
  await editor.fill(content+'\nUNSAVED LOCAL CHANGE');
  detail.draft=content+'\nA newer saved revision.';detail.draft_hash='c'.repeat(64);detail.revision++;
  await dialog.getByText(/Your local edits are preserved/).waitFor();
  assert.match(await editor.inputValue(),/UNSAVED LOCAL CHANGE/);
  await editor.press('Escape');
  await dialog.getByRole('button',{name:'Close without saving teaching edits'}).waitFor();
  assert.equal(await dialog.isVisible(),true);
  await dialog.getByRole('button',{name:'Close without saving teaching edits'}).click();
  await dialog.waitFor({state:'hidden'});
  checks.push('Polling preserves concurrent local edits; Esc offers an explicit discard before closing.');
  mine=true;detail.state='recording';
  await page.getByRole('button',{name:'Open recording',exact:true}).click();
  await dialog.getByText('Recording this computer · pause before showing private information.').waitFor();
  dismissFail=true;
  await dialog.getByRole('button',{name:'Close',exact:true}).press('Escape');
  await dialog.getByText(/Could not confirm recording pause/).waitFor();
  assert.equal(await dialog.isVisible(),true);
  dismissFail=false;
  await dialog.getByRole('button',{name:'Close',exact:true}).press('Escape');
  await dialog.waitFor({state:'hidden'});
  const strip=page.getByRole('complementary',{name:'Scout recording status'});
  await strip.getByText('Scout · Recording paused',{exact:true}).waitFor();
  assert.match(await strip.innerText(),/123 observations saved so far/);
  await strip.getByRole('button',{name:'Resume or finish'}).click();
  await dialog.getByRole('button',{name:'Resume on safe screen'}).waitFor();
  assert.equal(await dialog.getByLabel('Recordings').inputValue(),'fixture-recording');
  checks.push('Failed pause leaves the pane open; acknowledged pause leaves a persistent saved-prefix strip and restores the recording.');
  await dialog.getByRole('button',{name:'Expand live view'}).click();
  await page.waitForTimeout(300);
  assert.equal(await page.locator('.computer-canvas').count(),1,JSON.stringify(errors));
  await dialog.locator('.computer-canvas').evaluate(element=>{const target=document.createElement('div');target.tabIndex=0;element.append(target);target.focus();});
  const before=writes.length;
  await page.keyboard.press('Escape');
  assert.equal(await dialog.isVisible(),true);assert.equal(writes.length,before);
  checks.push('Esc focused inside the synthetic remote canvas stays in the computer instead of dismissing the pane.');
  assert.deepEqual(errors,[]);
  assert.ok(writes.every(w=>w.path.endsWith('/answer')||w.path.endsWith('/teaching-dismiss')));
  await writeFile(`${out}/report.json`,JSON.stringify({fixture_only:true,live_api_access:false,live_desktop_input:false,checks,errors,writes},null,2));
  console.log(JSON.stringify({checks,errors}));
}finally{await browser.close();server.closeAllConnections();await new Promise(r=>server.close(r));}
