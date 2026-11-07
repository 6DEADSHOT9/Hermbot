import {chromium} from 'playwright';
import assert from 'node:assert/strict';
import {writeFile} from 'node:fs/promises';

const browser=await chromium.launch({headless:true});
const page=await browser.newPage({viewport:{width:1440,height:1000}});
const errors=[];
page.on('pageerror',error=>errors.push(error.message));
const id='00000000-0000-4000-8000-000000000301',queuedId='00000000-0000-4000-8000-000000000302',workId='00000000-0000-4000-8000-000000000303';
const now=new Date().toISOString(),progress=new Date(Date.now()-125000).toISOString();
let task;
try{
  await page.route('**/api/v1/state',async route=>{
    const response=await route.fetch(),state=await response.json();
    if(!response.ok())return route.fulfill({response});
    const conversation=state.conversations.find(c=>c.kind==='dm'&&c.bot_id==='reviewer');
    task={id,root_id:id,parent_id:null,bot_id:'reviewer',conversation_id:conversation.id,status:'waiting_results',input:'Combine required contributions',attempts:1,depth:0,message_intent:'assignment',work_item_id:workId,
      activity:{started_at:progress,last_contact_at:now,execution_status:'completed',last_progress_at:progress,last_tool:'read_file',last_tool_event:'tool.completed',last_tool_at:progress}};
    const queued={...task,id:queuedId,parent_id:id,status:'queued',attempts:0,depth:1,input:'Coordination follow-up',activity:null};
    const work={id:workId,conversation_id:conversation.id,bot_id:'reviewer',title:'Parallel research',objective:'Read and compare the documents',status:'waiting_results',current_task:task,executions:[task,queued],outputs:[],outcomes:[{id:workId,key:'report',title:'Combined report',kind:'artifact',required:true,revision:1,status:'pending',reason:'',evidence_strength:'',target:{},contributions:[{task_id:queuedId,bot_id:'writer',required:true,expected_output:'Research contribution',status:'submitted'},{task_id:id,bot_id:'researcher',required:true,expected_output:'Research contribution',status:'pending'}]}],progress:{fulfilled:0,required:1},hidden_at:null,outcomes_version:1};
    return route.fulfill({response,json:{...state,tasks:[queued,task],work_items:[work],approvals:[],input_requests:[]}});
  });
  await page.route(`**/api/v1/tasks/${id}`,route=>route.fulfill({json:{...task,children:[],executions:[{number:1,status:'completed'}],events:[{id:1,kind:'tool.completed',data:{tool:'read_file'},created_at:progress}],assessments:[]}}));
  await page.goto('http://api:8000');
  await page.getByLabel('Owner token').fill(process.env.OWNER_TOKEN);
  await page.getByRole('button',{name:'Open workspace',exact:true}).click();
  const sage=page.getByRole('navigation',{name:'Team conversations'}).getByRole('button',{name:/Sage/});
  await sage.getByText('Waiting for required results · 1 queued',{exact:true}).waitFor();
  await sage.click();
  await page.getByText('0 of 1 required results ready',{exact:true}).click();
  await page.getByText('Quill: Submitted for review · required',{exact:true}).waitFor();
  await page.getByText('Scout: Pending · required',{exact:true}).waitFor();
  await page.getByRole('button',{name:'Inspect work',exact:true}).click();
  const panel=page.getByRole('dialog',{name:'Inspect the work'});
  await panel.getByText(/Last tool activity: read_file · completed/).waitFor();
  await panel.getByText('Last progress: 2 min ago',{exact:true}).waitFor();
  await panel.getByText(/Last runtime contact: \d+ sec ago/).waitFor();
  for(const width of [1440,390]){
    await page.setViewportSize({width,height:900});
    assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth+1),false);
    await page.screenshot({path:`/output/coordination-${width}.png`,fullPage:true});
  }
  assert.deepEqual(errors,[]);
  await writeFile('/output/coordination-ui.json',JSON.stringify({complete:true,required_wait_status:true,contribution_review_status:true,partial_result_kept_open:true,progress_separate_from_runtime_contact:true,event_times:true,viewports:[1440,390],external_changes:false,script_errors:errors},null,2));
  console.log('Required waits, contribution states and partial completion passed on desktop/mobile.');
}finally{
  await page.unrouteAll({behavior:'wait'});
  await page.request.delete('http://api:8000/api/v1/session');
  await browser.close();
}
