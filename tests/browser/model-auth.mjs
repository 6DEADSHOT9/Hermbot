import {chromium} from 'playwright';
import assert from 'node:assert/strict';
import {createServer} from 'node:http';
import {readFile,writeFile,mkdir} from 'node:fs/promises';
import {resolve,extname} from 'node:path';

// Public assets and synthetic API data only; no live account, bot or desktop connection.
const root=resolve(process.argv[2]||'test-results/subscription-web-build');
const out=resolve(process.argv[3]||'test-results/codex-auth-ui');
await mkdir(out,{recursive:true});
const server=createServer(async(req,res)=>{
  try{const path=resolve(root,'.'+new URL(req.url,'http://fixture').pathname.replace(/\/$/,'/index.html'));
    if(!path.startsWith(root+'/')){res.writeHead(403);return res.end();}
    res.writeHead(200,{'content-type':{'.html':'text/html','.js':'text/javascript','.css':'text/css','.woff2':'font/woff2'}[extname(path)]||'application/octet-stream'});res.end(await readFile(path));
  }catch{res.writeHead(404);res.end();}
});
await new Promise(r=>server.listen(0,'127.0.0.1',r));
const origin=`http://127.0.0.1:${server.address().port}`;
const browser=await chromium.launch({headless:true,channel:'chrome'}),errors=[],writes=[],checks=[];
try{
  const context=await browser.newContext({viewport:{width:1440,height:900},serviceWorkers:'block'});
  await context.routeWebSocket('**/*',socket=>socket.close());
  const page=await context.newPage();page.on('pageerror',error=>errors.push(error.message));
  const workspace={timezone:'UTC',max_bots:10,max_active:3,active_budget_seconds:3600,permitted_tools:['file','terminal'],default_provider_id:'api-fixture',decision_provider_id:null,decision_mode:'disabled',decision_model:'',decision_actions:false};
  const providers=[{id:'api-fixture',name:'Environment provider',base_url:'https://fixture.invalid/v1',models:['api-model'],default_model:'api-model',revision:1,auth_kind:'api_key'},
    {id:'oauth-fixture',name:'ChatGPT fixture',base_url:'https://api.openai.com/v1',models:[],discovered_models:[],verified_models:[],default_model:'',revision:1,auth_kind:'chatgpt_oauth',connection_status:'disconnected',account_label:'',plan_enabled:false}];
  const oauth=providers[1];
  const codex={...structuredClone(oauth),id:'codex-fixture',name:'Codex fixture',auth_kind:'codex_oauth',base_url:'https://chatgpt.com/backend-api/codex'};providers.push(codex);
  let polls=0,deviceLogin=false;
  const state={workspace,bots:[],retired_bots:[],conversations:[],tasks:[],work_items:[],approvals:[],artifacts:[],grants:[],proposals:[],operations:[],controls:[],input_requests:[],provider_configured:true,event_cursor:0};
  let pendingSignIn=null,cancelled=false;
  await context.route('**/*',async route=>{
    const req=route.request(),url=new URL(req.url());
    if(url.origin!==origin)return route.abort();
    if(!url.pathname.startsWith('/api/'))return route.continue();
    const path=url.pathname,method=req.method();let result={};
    if(method!=='GET'){
      writes.push({path,method});
      if(path.includes('/codex-fixture/')){
        if(path.endsWith('/signin')||path.endsWith('/device')){polls=0;deviceLogin=path.endsWith('/device');codex.connection_status='connecting';result={status:'connecting'};}
        else if(path.endsWith('/status')){if(++polls>=2){Object.assign(codex,{connection_status:'connected',plan_enabled:true,account_label:'owner@example.invalid',name:'Codex · owner@example.invalid',account_plan:'Pro'});}result={status:codex.connection_status,plan_enabled:codex.plan_enabled,label:codex.account_label,...(deviceLogin&&codex.connection_status==='connecting'?{device_code:'FAKE-CODE'}:{})};}
        else if(path.endsWith('/models')){Object.assign(codex,{models:['codex-model'],discovered_models:['codex-model'],default_model:'codex-model'});result={status:'connected',models:[{slug:'codex-model'}]};}
        else if(path.endsWith('/test')){codex.verified_models=['codex-model'];result={verified:true};}
        else if(path.endsWith('/cancel')||path.endsWith('/disconnect')){Object.assign(codex,{connection_status:'disconnected',plan_enabled:false});result={status:'disconnected',revocation_confirmed:false};}
        else return route.fulfill({status:403,json:{detail:'Fixture rejects unexpected Codex operation'}});
      }else if(path.endsWith('/chatgpt/signin')){
        if(!cancelled){pendingSignIn=route;return;}
        Object.assign(oauth,{connection_status:'connected',plan_enabled:true,account_label:'Fixture owner'});result={status:'connected',plan_enabled:true,label:'Fixture owner'};
      }else if(path.endsWith('/chatgpt/cancel')){
        cancelled=true;await pendingSignIn.fulfill({status:503,json:{detail:'Sign-in cancelled'}});pendingSignIn=null;result={status:'disconnected'};
      }else if(path.endsWith('/chatgpt/models')){
        Object.assign(oauth,{models:['fixture-model','fixture-second'],discovered_models:['fixture-model','fixture-second'],default_model:'fixture-model'});result={models:oauth.models.map(slug=>({slug}))};
      }else if(path.endsWith('/chatgpt/test')){oauth.verified_models=['fixture-model'];result={verified:true};}
      else if(path.endsWith('/chatgpt/disconnect')){Object.assign(oauth,{connection_status:'disconnected',plan_enabled:false});result={status:'disconnected',revocation_confirmed:true};}
      else return route.fulfill({status:403,json:{detail:'Fixture blocks this mutation'}});
    }else if(path==='/api/v1/state')result=state;
    else if(path==='/api/v1/settings')result={workspace,providers};
    else if(path==='/api/v1/attention')result={items:[],unread:0,needs_owner:0,next:null};
    else if(path==='/api/v1/events')return route.fulfill({contentType:'text/event-stream',body:': fixture\n\n'});
    else return route.fulfill({status:404,json:{detail:'No fixture for '+path}});
    return route.fulfill({json:structuredClone(result)});
  });
  await page.goto(origin);
  await page.getByLabel('Owner menu').click();
  await page.getByRole('button',{name:'Workspace settings',exact:true}).click();
  const dialog=page.getByRole('dialog',{name:'Workspace settings'});
  await dialog.getByRole('button',{name:'Providers',exact:true}).click();
  const connection=dialog.getByLabel('ChatGPT fixture connection');
  await connection.getByRole('button',{name:'Continue with ChatGPT',exact:true}).click();
  await connection.getByText('Waiting for OpenAI authorization',{exact:false}).waitFor();
  await connection.getByRole('button',{name:'Cancel sign-in'}).click();
  await connection.getByText('Sign-in cancelled.',{exact:true}).waitFor();
  assert.equal(await connection.getByRole('alert').count(),0);
  checks.push('Cancellation reports its result and ignores the stale sign-in response.');
  await connection.getByRole('button',{name:'Continue with ChatGPT',exact:true}).click();
  await connection.getByText('Account connected. Test a model before assigning it.',{exact:true}).waitFor();
  await connection.getByRole('button',{name:'Test model',exact:true}).click();
  await connection.getByText('Model verified. You can assign it to a teammate.',{exact:true}).waitFor();
  checks.push('Browser sign-in, model discovery and explicit model verification render correctly.');
  const codexCard=dialog.locator('.chatgpt-provider').filter({has:page.getByRole('heading',{name:/^Codex/})});
  await codexCard.getByRole('button',{name:'Sign in with Codex',exact:true}).click();
  await codexCard.getByText('Waiting for OpenAI authorization',{exact:false}).waitFor();
  await codexCard.getByRole('button',{name:'Test model',exact:true}).waitFor();
  await codexCard.getByRole('button',{name:'Test model',exact:true}).click();
  await codexCard.getByText('Model verified. You can assign it to a teammate.',{exact:true}).waitFor();
  await codexCard.getByRole('heading',{name:'Codex · owner@example.invalid',exact:true}).waitFor();
  assert.ok(await codexCard.getByText('owner@example.invalid · Pro · connected',{exact:true}).isVisible());
  await codexCard.getByText('Connection settings',{exact:true}).click();
  assert.equal(await codexCard.getByLabel('Connection name',{exact:true}).inputValue(),'Codex · owner@example.invalid');
  await codexCard.getByLabel('Connection name',{exact:true}).fill('My unsaved account name');
  codex.name='Codex · '+ 'long.account.'.repeat(5)+'@example.invalid';
  codex.account_label=codex.name.slice('Codex · '.length);
  // A catalog refresh also refreshes settings without replacing a dirty name field.
  await codexCard.getByRole('button',{name:'Refresh models',exact:true}).click();
  await codexCard.getByRole('heading',{name:codex.name,exact:true}).waitFor();
  assert.equal(await codexCard.getByLabel('Connection name',{exact:true}).inputValue(),'My unsaved account name');
  checks.push('Verified identity updates the default name and plan label; settings refresh preserves an unsaved custom name.');
  assert.ok(await dialog.getByText('Environment provider',{exact:true}).isVisible());
  checks.push('Codex background login polls to completion, discovers models and verifies explicitly alongside the API provider.');
  for(const width of [1440,768,390,320]){
    await page.setViewportSize({width,height:900});await page.waitForTimeout(150);
    assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false);
    assert.equal(await dialog.evaluate(e=>e.scrollWidth>e.clientWidth+1),false);
    await page.screenshot({path:`${out}/providers-${width}.png`});
  }
  await page.setViewportSize({width:1440,height:900});
  const html=await page.evaluate(()=>`<!doctype html><html data-theme="light"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">${[...document.querySelectorAll('link[rel="stylesheet"]')].map(e=>e.outerHTML).join('')}</head><body>${document.querySelector('[role="dialog"]').outerHTML}</body></html>`);
  await writeFile(resolve(root,'inspection.html'),html);
  await codexCard.getByRole('button',{name:'Use a device code',exact:true}).click();
  await codexCard.getByText('FAKE-CODE',{exact:true}).waitFor();
  assert.equal(await codexCard.getByRole('link',{name:'OpenAI device sign-in'}).getAttribute('href'),'https://auth.openai.com/codex/device');
  await codexCard.getByRole('button',{name:'Cancel sign-in',exact:true}).click();
  await codexCard.getByText('Sign-in cancelled.',{exact:true}).waitFor();
  assert.equal(await codexCard.getByText('FAKE-CODE',{exact:true}).count(),0);
  checks.push('Device-code sign-in shows the fixed OpenAI destination and clears the code on cancellation.');
  await connection.getByRole('button',{name:'Disconnect account'}).click();
  await connection.getByText('Disconnected.',{exact:true}).waitFor();
  assert.ok(await connection.getByRole('button',{name:'Remove connection'}).isVisible());
  checks.push('Disconnect status and removal action remain distinct; four widths have no horizontal overflow.');
  assert.deepEqual(errors,[]);
  await writeFile(`${out}/report.json`,JSON.stringify({checks,errors,requests:writes,live_accounts:false},null,2));
  console.log(JSON.stringify({checks,errors,live_accounts:false}));
}finally{await browser.close();server.close();}
