import {chromium} from 'playwright';
import {parseEnv} from 'node:util';
import {readFile,writeFile} from 'node:fs/promises';
import assert from 'node:assert/strict';

// Owner-authorized reads only. Never print the credential or conversation contents.
const token=parseEnv(await readFile('.env','utf8')).OWNER_TOKEN;
assert(token);
const origin='http://localhost:8780',browser=await chromium.launch({channel:'chrome',headless:true});
const errors=[],writes=[];
try{
  const context=await browser.newContext({viewport:{width:1440,height:900},serviceWorkers:'block'});
  await context.route('**/*',route=>{
    const request=route.request(),url=new URL(request.url());
    if(url.origin!==origin)return route.abort();
    if(request.method()!=='GET'){writes.push(url.pathname);return route.abort();}
    return route.continue({headers:{...request.headers(),...(url.pathname.startsWith('/api/')?{authorization:`Bearer ${token}`}:{})}});
  });
  const page=await context.newPage();page.on('pageerror',error=>errors.push(error.message));
  await page.goto(origin);await page.getByRole('heading',{level:1}).waitFor();
  await page.getByRole('button',{name:/^Quill /}).click();
  await page.locator('[data-message-id="960"]').waitFor();
  const failures=await page.locator('.request-state.has-error').count();assert(failures>=3);
  for(const task of ['ea78ed51-c918-4841-bb5f-c3caa25bffbe','19b1c7fc-711a-4570-9f16-4a8d339065e3']){
    const ownerMessage=await page.locator(`[data-response-task-id="${task}"]`).evaluate(node=>Number(node.closest('[data-message-id]').dataset.messageId));
    assert(ownerMessage<960);
  }
  await page.getByRole('button',{name:/^Scout /}).click();
  await page.getByRole('log',{name:'Conversation history'}).waitFor();
  for(let count=0;count<5&&await page.getByRole('button',{name:'Load earlier messages',exact:true}).count();count++){
    await page.getByRole('button',{name:'Load earlier messages',exact:true}).click();
    await page.getByRole('button',{name:'Loading earlier messages…',exact:true}).waitFor({state:'hidden'});
  }
  assert.equal(await page.locator('[data-message-id="403"]').count(),1);
  const messages=await page.locator('[data-message-id]').count();assert.equal(messages,149);
  await page.setViewportSize({width:390,height:844});await page.waitForTimeout(300);
  assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
  assert.deepEqual(errors,[]);assert.deepEqual(writes,[]);
  const result={quill_failures_anchored:true,scout_root_messages:messages,message_403_present:true,mobile_overflow:false,errors,writes};
  await writeFile('test-results/timeline-live-browser.json',JSON.stringify(result,null,2));console.log(JSON.stringify(result));
}finally{await browser.close();}
