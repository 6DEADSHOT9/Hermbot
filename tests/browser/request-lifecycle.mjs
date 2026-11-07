import {chromium} from 'playwright';
import assert from 'node:assert/strict';
import {writeFile} from 'node:fs/promises';

const browser=await chromium.launch({headless:true});
const page=await browser.newPage({baseURL:'http://api:8000',viewport:{width:1440,height:900}});
const errors=[];
page.on('pageerror',e=>errors.push(e.message));
try {
  const response=await page.request.get('/api/v1/state',{headers:{Authorization:'Bearer '+process.env.OWNER_TOKEN}});
  assert.ok(response.ok());
  const baseline=await response.json();
  const chat=baseline.conversations.find(c=>c.title==='Project chat');
  assert.ok(chat,'Reported Project chat must still exist');
  assert.ok(!baseline.approvals.some(a=>a.id==='1d1793e1-383e-42fc-b7e4-6d9615ce35ba'),'Obsolete reported review is still actionable');
  await page.goto('/');
  await page.getByLabel('Owner token').fill(process.env.OWNER_TOKEN);
  await page.getByRole('button',{name:'Open workspace'}).click();
  await page.locator('.group-row').filter({has:page.getByText('Project chat',{exact:true})}).click();
  await page.getByRole('heading',{name:'Project chat',exact:true}).waitFor();
  assert.equal(await page.locator('.review-card').count(),0);

  const fixture=structuredClone(baseline),template=baseline.tasks.find(t=>t.conversation_id===chat.id);
  assert.ok(template);
  const taskIds=[crypto.randomUUID(),crypto.randomUUID(),crypto.randomUUID()];
  fixture.tasks.push(...taskIds.map((id,index)=>({...template,id,root_id:id,work_item_id:null,status:index===2?'completed':index===0?'waiting_input':'waiting_approval',deadline:null})));
  const future=new Date(Date.now()+3600000).toISOString(),past=new Date(Date.now()-3600000).toISOString();
  const questionIds=[crypto.randomUUID(),crypto.randomUUID(),crypto.randomUUID()];
  fixture.input_requests=questionIds.map((id,index)=>({id,task_id:taskIds[index===1?2:0],work_item_id:null,prompt:['Lifecycle fixture: current question','Lifecycle fixture: completed question','Lifecycle fixture: expired question'][index],answer_type:'text',choices:[],question_hash:'a'.repeat(64),status:'pending',expires_at:index===2?past:future}));
  fixture.approvals=[0,1,2].map(index=>({id:crypto.randomUUID(),task_id:taskIds[index===1?2:1],kind:'runtime',status:'pending',payload:{description:['Lifecycle fixture: current review','Lifecycle fixture: completed review','Lifecycle fixture: expired review'][index]},payload_hash:'b'.repeat(64),expires_at:index===2?past:future}));
  await page.route('**/api/v1/state',route=>route.fulfill({json:fixture}));
  await page.getByText('Lifecycle fixture: current question',{exact:true}).waitFor();
  await page.getByText('Lifecycle fixture: current review',{exact:true}).waitFor();
  for(const text of ['completed question','expired question','completed review','expired review']) {
    assert.equal(await page.getByText('Lifecycle fixture: '+text,{exact:true}).count(),0);
  }
  assert.equal(await page.locator('.review-card').count(),2);
  fixture.tasks=fixture.tasks.map(t=>taskIds.includes(t.id)?{...t,status:'completed'}:t);
  await page.locator(`[data-input-request-id="${questionIds[0]}"]`).waitFor({state:'hidden'});
  await page.waitForFunction(()=>document.querySelectorAll('.review-card').length===0);
  for(const width of [1440,390]) {
    await page.setViewportSize({width,height:900});
    await page.screenshot({path:`/output/request-lifecycle-${width}.png`,fullPage:true});
  }
  assert.deepEqual(errors,[]);
  await writeFile('/output/request-lifecycle-browser.json',JSON.stringify({checks:['Reported obsolete Project chat review absent from live API/UI','Active question/review still render','Expired prompts hidden despite stale pending state','Completed tasks hide their pending prompts on refresh','No answer, approval or model work submitted','Desktop and phone captures','No browser errors'],errors},null,2));
  console.log('Request lifecycle browser checks passed.');
} finally {await browser.close();}
