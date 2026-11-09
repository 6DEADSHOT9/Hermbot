import {chromium} from 'playwright';
import assert from 'node:assert/strict';
import {writeFile} from 'node:fs/promises';

const browser=await chromium.launch({headless:true}),page=await browser.newPage({viewport:{width:1440,height:1000}});
const errors=[];page.on('pageerror',error=>errors.push(error.message));
const taskId='00000000-0000-4000-8000-000000000201',workId='00000000-0000-4000-8000-000000000202';
let mode='work',retried=false,savedPolicy,interpreted=null,scoutConversation=null;
try{
  await page.route('**/api/v1/state',async route=>{
    const response=await route.fetch(),state=await response.json();
    if(!response.ok())return route.fulfill({response});
    const conversation=state.conversations.find(c=>c.kind==='dm'&&c.bot_id==='researcher');
    const task={id:taskId,root_id:taskId,parent_id:null,bot_id:'researcher',conversation_id:conversation.id,
      input:'Analyze data and publish the report',status:'running',attempts:1,depth:0,message_intent:'assignment',work_item_id:workId};
    const item={id:workId,conversation_id:conversation.id,bot_id:'researcher',title:'Data analysis',objective:'Include uncertainty estimates',
      status:'working',revision:2,current_task:task,executions:[task],outputs:[],hidden_at:null,outcomes_version:1,
      outcomes:[{id:'current',key:'current',title:'Report with uncertainty estimates',kind:'artifact',required:true,revision:1,status:'pending',target:{}}],
      progress:{fulfilled:0,required:1},changes:[{revision:2,kind:'update',status:'applied',change:{objective:'Include uncertainty estimates'},created_at:new Date().toISOString()}],
      directions:[{id:'direction',task_id:taskId,revision:2,status:'pending'}]};
    scoutConversation=conversation.id;
    if(mode==='interpretation'){
      Object.assign(task,{status:'queued',attempts:0,message_intent:'auto',work_item_id:null,orchestration_version:2,intake_status:retried?'pending':'failed',error:'The original instruction is preserved.'});
      interpreted=task;
    }
    await route.fulfill({response,json:{...state,tasks:[task],work_items:mode==='work'?[item]:[],approvals:[],input_requests:[]}});
  });
  // The conversation is drawn from its timeline, so the interpretation task is also delivered there, under its message.
  await page.route('**/api/v1/conversations/*/timeline*',async route=>{
    const response=await route.fetch();
    if(mode!=='interpretation'||!interpreted||!response.ok()||!route.request().url().includes(`/conversations/${scoutConversation}/timeline`))return route.fulfill({response});
    // A fixed, newest position keeps the entry in place across catch-up refreshes, which carry only new entries.
    const body=await response.json(),created=new Date().toISOString(),position=1e9;
    const message={id:990201,source_id:'fixture:interpretation',task_id:taskId,content:interpreted.input,role:'user',author:'owner',created_at:created,attachment_ids:[],mentions:[],reply_to_message_id:null,reactions:[],reply_count:0};
    await route.fulfill({response,json:{...body,entries:[...body.entries,{id:'message:990201',position,revision:Date.now(),occurred_at:created,thread_id:null,message,tasks:[{...interpreted}]}]}});
  });
  await page.route(`**/api/v1/messages/${taskId}/retry-interpretation`,async route=>{retried=true;await route.fulfill({json:{status:'pending'}});});
  await page.route('**/api/v1/settings',async route=>{
    if(route.request().method()==='PUT'){savedPolicy=route.request().postDataJSON().external_action_mode;return route.fulfill({json:{}});}
    return route.continue();
  });
  await page.goto('http://api:8000');
  await page.getByLabel('Owner token').fill(process.env.OWNER_TOKEN);
  await page.getByRole('button',{name:'Open workspace',exact:true}).click();
  await page.getByRole('navigation',{name:'Team conversations'}).getByRole('button',{name:/Scout/}).click();
  await page.getByText('Request history · revision 2').click();
  await page.getByText('Revision 2: Include uncertainty estimates').waitFor();
  await page.getByText('Your updated instructions are recorded and awaiting delivery to the affected teammates.').waitFor();
  for(const width of [1440,390]){
    await page.setViewportSize({width,height:900});
    if(width===390)await page.getByRole('button',{name:'Toggle work panel'}).click();
    assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth+1),false);
    await page.screenshot({path:`/output/work-controller-${width}.png`,fullPage:true});
  }
  await page.setViewportSize({width:1440,height:1000});
  mode='interpretation';await page.reload();
  await page.getByRole('navigation',{name:'Team conversations'}).getByRole('button',{name:/Scout/}).click();
  await page.getByRole('button',{name:'Retry interpretation',exact:true}).click();
  assert.equal(retried,true);
  await page.getByRole('main').getByText('Understanding your message').first().waitFor();
  await page.getByLabel('Owner menu').click();await page.getByRole('button',{name:'Settings',exact:true}).click();
  const dialog=page.getByRole('dialog',{name:'Settings',exact:true});
  await dialog.getByRole('tab',{name:'General'}).click();
  await dialog.getByLabel('Connected app actions',{exact:true}).selectOption('review');
  await dialog.getByRole('button',{name:'Save workspace settings',exact:true}).click();
  assert.equal(savedPolicy,'review');
  assert.deepEqual(errors,[]);
  await writeFile('/output/work-controller-ui.json',JSON.stringify({complete:true,request_history:true,pending_directions:true,interpretation_recovery:true,action_policy_setting:true,viewports:[1440,390],external_changes:false,script_errors:errors},null,2));
  console.log('Request history, direction status, interpretation recovery and policy controls passed on desktop and mobile.');
}finally{
  await page.unrouteAll({behavior:'wait'});
  await page.request.delete('http://api:8000/api/v1/session');
  await browser.close();
}
