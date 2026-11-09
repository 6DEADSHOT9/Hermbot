import {chromium} from 'playwright';
import assert from 'node:assert/strict';
import {writeFile} from 'node:fs/promises';

const browser=await chromium.launch({headless:true});
const page=await browser.newPage({viewport:{width:1440,height:1000}}), errors=[];
const taskId='00000000-0000-4000-8000-000000000961';
const itemId='00000000-0000-4000-8000-000000000962';
let fixture, configuration, continued=0, saved;
page.on('pageerror',e=>errors.push(e.message));
page.setDefaultTimeout(15000);

try {
  await page.route('**/api/v1/state',async route=>{
    const response=await route.fetch(), state=await response.json();
    if(!response.ok())return route.fulfill({response});
    if(!fixture){
      const source=state.work_items.find(w=>w.bot_id==='writer'&&w.current_task);
      assert.ok(source,'A real work item supplies the fixture shape');
      // The source item may live in any conversation; the fixture belongs to Quill's direct conversation, where the check looks.
      const direct=state.conversations.find(c=>c.kind==='dm'&&c.bot_id==='writer');assert.ok(direct,'Quill has a direct conversation');
      const task={...source.current_task,id:taskId,root_id:taskId,conversation_id:direct.id,grant_id:null,status:'paused_budget',stop_kind:'budget',stop_reason:'Active work budget reached',work_item_id:itemId,error:null,input:'Execution budget UI fixture',output:'Report is ready',parent_id:null,depth:0,connector_state:null};
      const outcome={...source.outcomes[0],work_item_id:itemId,source_task_id:taskId,kind:'report',required:true,revision:1,reason:'',target:{},dependencies:[]};
      fixture={...source,id:itemId,conversation_id:direct.id,title:'Paused work layout check',status:'paused_budget',current_task:task,executions:[task],outputs:[],outcomes_version:1,progress:{fulfilled:1,required:2},outcomes:[{...outcome,id:'00000000-0000-4000-8000-000000000963',key:'report',title:'Report',status:'fulfilled',evidence_strength:'model_reported'},{...outcome,id:'00000000-0000-4000-8000-000000000964',key:'delivery',title:'Remaining delivery',status:'pending',evidence_strength:''}]};
    }
    await route.fulfill({response,json:{...state,tasks:[fixture.current_task,...state.tasks],work_items:[fixture,...state.work_items]}});
  });
  await page.route(`**/api/v1/tasks/${taskId}`,route=>route.fulfill({json:{...fixture.current_task,children:[],events:[],executions:[{number:1,status:'cancelled'}],execution_budget:{used_seconds:3600,limit_seconds:3600,remaining_seconds:0},assessments:[{id:'fixture-pending',recommendation:null,confidence:null,status:'evaluating',error:null},{id:'fixture-assessment',recommendation:'continue',confidence:.95,status:'observed',error:null}]}}));
  await page.route(`**/api/v1/tasks/${taskId}/continue`,async route=>{
    assert.equal(route.request().method(),'POST');
    assert.ok(route.request().headers()['idempotency-key']);
    continued++;
    fixture.current_task={...fixture.current_task,status:'queued',stop_kind:null,stop_reason:null};
    fixture.executions=[fixture.current_task];fixture.status='working';
    await route.fulfill({status:200,json:fixture.current_task});
  });
  await page.route('**/api/v1/settings',async route=>{
    if(route.request().method()==='PUT'){
      saved=route.request().postDataJSON();
      configuration.workspace={...configuration.workspace,...saved};
      await route.fulfill({status:200,json:configuration.workspace});
    }else{
      const response=await route.fetch();
      if(!response.ok())return route.fulfill({response});
      if(!configuration)configuration=await response.json();
      await route.fulfill({response,json:configuration});
    }
  });
  await page.goto('http://api:8000');
  await page.getByLabel('Owner token').fill(process.env.OWNER_TOKEN);
  await page.getByRole('button',{name:'Open workspace',exact:true}).click();
  await page.getByRole('navigation',{name:'Team conversations'}).waitFor();
  const before=await (await page.request.get('http://api:8000/api/v1/settings')).json();
  const stateBefore=await (await page.request.get('http://api:8000/api/v1/state')).json();
  await page.getByRole('navigation',{name:'Team conversations'}).getByRole('button',{name:/Quill/}).click();
  const panel=page.getByRole('complementary',{name:'Work and outputs'});
  const card=panel.locator(`[data-work-item-id="${itemId}"]`);
  await card.getByText('Budget reached',{exact:true}).waitFor();
  await page.locator('.header-presence').getByText('Ready',{exact:true}).waitFor();
  assert.equal(await card.getByRole('button',{name:'Retry task',exact:true}).count(),0);
  assert.equal(await card.getByRole('button',{name:'Redirect',exact:true}).count(),0);
  await card.getByRole('button',{name:'Inspect work',exact:true}).click();
  const inspector=page.getByRole('dialog',{name:'Inspect the work',exact:true});
  await inspector.getByText('Active work: 60 of 60 minutes',{exact:true}).waitFor();
  await inspector.getByText('Checking execution · evaluating',{exact:true}).waitFor();
  await inspector.getByText('continue · observed · 95% confidence',{exact:true}).waitFor();
  await inspector.getByRole('button',{name:'Close',exact:true}).click();
  await page.getByLabel('Owner menu').click();await page.getByRole('button',{name:'Settings',exact:true}).click();
  const settings=page.getByRole('dialog',{name:'Settings',exact:true});
  await settings.getByRole('tab',{name:'General'}).click();
  await settings.getByLabel('Active work budget',{exact:true}).fill('120');
  const provider=configuration.providers[0];
  await settings.getByLabel('Decision provider',{exact:true}).selectOption(provider.id);
  await settings.getByLabel('Decision model',{exact:true}).fill(provider.default_model);
  await settings.getByLabel('Decision API',{exact:true}).selectOption('chat');
  await settings.getByLabel('Allow assessments to send recovery guidance',{exact:true}).check();
  await settings.getByRole('button',{name:'Save workspace settings',exact:true}).click();
  await page.waitForFunction(()=>document.querySelector('button.primary')?.disabled===false);
  assert.equal(saved.active_budget_seconds,7200);
  assert.equal(saved.decision_mode,'chat');assert.equal(saved.decision_actions,true);
  await settings.getByRole('button',{name:'Close',exact:true}).click();
  const widths=[];
  for(const width of [1440,390,320]){
    await page.setViewportSize({width,height:900});
    if(width===390)await page.getByRole('button',{name:'Toggle work panel',exact:true}).click();
    await card.getByRole('button',{name:'Continue',exact:true}).scrollIntoViewIfNeeded();
    const geometry=await page.evaluate(()=>{const e=document.querySelector('.context-scroll');return {document:document.documentElement.scrollWidth,viewport:innerWidth,panel:e.clientWidth,panelScroll:e.scrollWidth};});
    assert.ok(geometry.document<=width+1&&geometry.panelScroll<=geometry.panel+1);
    assert.equal(await card.getByRole('button',{name:'Continue',exact:true}).count(),1);
    await page.screenshot({path:`/output/execution-budget-${width}.png`});widths.push(width);
  }
  await card.getByRole('button',{name:'Continue',exact:true}).click();
  await page.waitForFunction(()=>!document.querySelector('[data-work-item-id="00000000-0000-4000-8000-000000000962"] button.secondary'));
  assert.equal(continued,1);
  const after=await (await page.request.get('http://api:8000/api/v1/settings')).json();
  assert.deepEqual(after.workspace,before.workspace,'Only mocked workspace configuration is changed');
  const stateAfter=await (await page.request.get('http://api:8000/api/v1/state')).json();
  assert.deepEqual(stateAfter.tasks.map(t=>[t.id,t.status]),stateBefore.tasks.map(t=>[t.id,t.status]));
  assert.deepEqual(errors,[]);
  await writeFile('/output/execution-monitoring-ui.json',JSON.stringify({complete:true,viewports:widths,budget_inspection:true,assessment_inspection:true,configuration_form:true,continue_calls:continued,live_tasks_unchanged:true,live_configuration_unchanged:true,external_changes:false,script_errors:errors},null,2));
  console.log('Execution budget, assessment inspection, configuration and mocked Continue passed on desktop and mobile.');
} finally {
  await page.unrouteAll({behavior:'wait'});
  await page.request.delete('http://api:8000/api/v1/session');
  await browser.close();
}
