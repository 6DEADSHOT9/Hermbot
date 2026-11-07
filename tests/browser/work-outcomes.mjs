import {chromium} from 'playwright';
import assert from 'node:assert/strict';
import {writeFile} from 'node:fs/promises';

const browser=await chromium.launch({headless:true}),page=await browser.newPage({viewport:{width:1440,height:1000}}),errors=[];
page.on('pageerror',e=>errors.push(e.message));
let fixture,answerBody,editedBody,mode='text';
const fakeTask='00000000-0000-4000-8000-000000000101',fakeItem='00000000-0000-4000-8000-000000000102',fakeInput='00000000-0000-4000-8000-000000000103';
const fakeApproval='00000000-0000-4000-8000-000000000104';
const connectorAction={connection_id:'00000000-0000-4000-8000-000000000105',connection_revision:3,
  action:'slack.post',arguments:{channel:'U123',text:'Prepared report'},outcome_id:'delivery',outcome_revision:1};
try {
  await page.route('**/api/v1/state',async route=>{
    const response=await route.fetch(),state=await response.json();
    if(!response.ok())return route.fulfill({response});
    const conversation=state.conversations.find(c=>c.kind==='dm'&&c.bot_id==='researcher');
    fixture={...state,tasks:[{id:fakeTask,root_id:fakeTask,bot_id:'researcher',conversation_id:conversation.id,parent_id:null,status:'waiting_input',message_intent:'assignment',work_item_id:fakeItem,depth:0,input:'Research and deliver',created_at:new Date().toISOString(),deadline:new Date(Date.now()+3600000).toISOString(),attempts:1}],approvals:[],input_requests:mode==='answered'?[]:[{id:fakeInput,task_id:fakeTask,work_item_id:fakeItem,prompt:'Which recipient should receive the report?',answer_type:mode==='choice'?'choice':'text',choices:mode==='choice'?['Person A','Person B']:[],question_hash:'a'.repeat(64),status:'pending',expires_at:new Date(Date.now()+3600000).toISOString()}]};
    fixture.work_items=[{id:fakeItem,conversation_id:conversation.id,bot_id:'researcher',title:'Research and delivery',objective:'Research and deliver',status:mode==='answered'?'needs_attention':'needs_answer',hidden_at:null,legacy:false,current_task:fixture.tasks[0],executions:fixture.tasks,outputs:[],outcomes_version:1,progress:{fulfilled:1,required:2},outcomes:[{id:'report',key:'report',title:'Research report',kind:'report',required:true,revision:1,status:'fulfilled',reason:'',evidence_strength:'model_reported',target:{}},{id:'delivery',key:'delivery',title:'Slack delivery',kind:'connector_effect',required:true,revision:1,status:'blocked',reason:'Awaiting recipient and exact approval',evidence_strength:'',target:{}}]}];
    if(mode==='connector'){
      fixture.input_requests=[];fixture.tasks[0].status='reviewing';fixture.work_items[0].status='needs_approval';
      fixture.work_items[0].outcomes[1].target={resource:'U123',display_name:'Person A'};
      fixture.approvals=[{id:fakeApproval,task_id:fakeTask,status:'pending',kind:'connector',
        payload_hash:'b'.repeat(64),expires_at:new Date(Date.now()+3600000).toISOString(),
        payload:{summary:'Send the prepared report to Person A',action:connectorAction}}];
    }
    await route.fulfill({response,json:fixture});
  });
  await page.route('**/api/v1/input-requests/*/answer',async route=>{
    answerBody=route.request().postDataJSON();mode='answered';
    await route.fulfill({json:{status:'answered'}});
  });
  // No fixture mutation is forwarded to the real backend.
  await page.route('**/api/v1/outcomes/*',route=>route.fulfill({status:409,json:{detail:'Fixture only'}}));
  await page.route(`**/api/v1/approvals/${fakeApproval}`,async route=>{
    if(route.request().method()!=='PUT')return route.fulfill({status:409,json:{detail:'Fixture only'}});
    editedBody=route.request().postDataJSON();mode='answered';
    await route.fulfill({json:{approval_id:fakeApproval}});
  });
  await page.goto('http://api:8000');
  await page.getByLabel('Owner token').fill(process.env.OWNER_TOKEN);
  await page.getByRole('button',{name:'Open workspace',exact:true}).click();
  await page.getByRole('navigation',{name:'Team conversations'}).getByRole('button',{name:/Scout/}).click();
  const question=page.locator('.input-card');
  await question.getByText('Your answer is needed').waitFor();
  assert.equal(await question.getByRole('button',{name:/Allow|Approve/}).count(),0);
  await question.getByLabel("Answer the bot's question").fill('Person A');
  await page.request.get('http://api:8000/health');
  await question.getByRole('button',{name:'Send answer',exact:true}).click();
  assert.equal(answerBody.answer,'Person A');assert.ok(!('decision' in answerBody));
  await page.reload();
  await page.getByRole('navigation',{name:'Team conversations'}).getByRole('button',{name:/Scout/}).click();
  assert.equal(await page.locator('.input-card').count(),0);
  await page.getByText('1 of 2 required results ready').click();
  await page.getByText('Awaiting recipient and exact approval').waitFor();
  const widths=[];
  for(const width of [1440,390]){
    await page.setViewportSize({width,height:900});
    if(width===390)await page.getByRole('button',{name:'Toggle work panel'}).click();
    assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth+1),false);
    await page.screenshot({path:`/output/work-outcomes-${width}.png`,fullPage:true});widths.push(width);
  }
  mode='choice';await page.setViewportSize({width:1440,height:1000});await page.reload();
  await page.getByRole('navigation',{name:'Team conversations'}).getByRole('button',{name:/Scout/}).click();
  await page.getByRole('button',{name:'Person B',exact:true}).click();assert.equal(answerBody.answer,'Person B');
  mode='connector';await page.reload();
  await page.getByRole('navigation',{name:'Team conversations'}).getByRole('button',{name:/Scout/}).click();
  const review=page.locator('.review-card');
  await review.getByText('Person A (U123)',{exact:true}).waitFor();
  await review.getByText('Prepared report',{exact:true}).waitFor();
  assert.equal(await review.locator('pre').count(),0);
  await review.getByRole('button',{name:'Edit draft',exact:true}).click();
  const editor=page.getByRole('dialog',{name:'Edit review draft',exact:true});
  await editor.getByLabel('Slack recipient or conversation',{exact:true}).fill('U999');
  await editor.getByLabel('Message',{exact:true}).fill('Updated report');
  await editor.getByRole('button',{name:'Replace review',exact:true}).click();
  await editor.waitFor({state:'hidden'});
  assert.deepEqual(editedBody.payload,{...connectorAction,arguments:{channel:'U999',text:'Updated report'}});
  assert.equal(editedBody.expected_payload_hash,'b'.repeat(64));
  assert.deepEqual(errors,[]);
  await writeFile('/output/work-outcomes-ui.json',JSON.stringify({complete:true,typed_answers:true,choice_answers:true,partial_results:true,readable_connector_preview:true,structured_draft_edit_preserves_binding:true,viewports:widths,external_changes:false,script_errors:errors},null,2));
  console.log('Questions, answers, partial results, structured reviews and responsive layouts passed.');
}finally{
  await page.unrouteAll({behavior:'wait'});
  await page.request.delete('http://api:8000/api/v1/session');
  await browser.close();
}
