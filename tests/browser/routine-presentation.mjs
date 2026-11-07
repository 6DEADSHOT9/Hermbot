import {chromium} from 'playwright';
import assert from 'node:assert/strict';

const browser=await chromium.launch({headless:true});
try {
  const page=await browser.newPage({viewport:{width:390,height:844},baseURL:process.env.HERMBOT_TEST_URL||'http://api:8000'});
  const errors=[];page.on('pageerror',error=>errors.push(error.message));
  const definition={name:'',instructions:'Check the report',bot_id:'researcher',conversation_id:'',schedule:{kind:'interval',timezone:'UTC',interval_minutes:60},requirements:[],requested_effects:[],artifact_ids:[],attachment_ids:[],skill_refs:[],active_budget_seconds:3600,start_window_minutes:15,catch_up:'skip',max_runs_per_day:24,enabled:true};
  const rows=[
    {name:'Hourly review',state:'active',schedule:{kind:'interval',interval_minutes:60},text:'Every hour'},
    {name:'Minute check',state:'paused',schedule:{kind:'interval',interval_minutes:1},text:'Every minute'},
    {name:'Team check',state:'paused',schedule:{kind:'weekly',weekdays:[0,2],hour:9,minute:30},text:'Weekly · Mon, Wed at 09:30'},
    {name:'Month end report',state:'draft',schedule:{kind:'monthly',last_day:true,hour:8,minute:0},text:'Monthly · last day at 08:00'},
    {name:'One time handoff',state:'finished',schedule:{kind:'once',at:new Date(Date.now()+3600000).toISOString()},text:'your device time'},
  ].map((item,index)=>({id:`00000000-0000-4000-8000-${String(index+1).padStart(12,'0')}`,revision:1,state:item.state,definition:{...definition,name:item.name,schedule:{...definition.schedule,...item.schedule}},next_run_at:item.state==='active'?new Date(Date.now()+3600000).toISOString():null,error:null,latest_run:null,scheduler:{error:null,last_tick_at:new Date().toISOString()},text:item.text}));
  await page.goto('/');await page.getByLabel('Owner token').fill(process.env.OWNER_TOKEN);await page.getByRole('button',{name:'Open workspace'}).click();
  await page.route('**/api/v1/routines',route=>route.fulfill({json:{data:rows}}));
  await page.route('**/api/v1/routines/*/runs',route=>route.fulfill({json:{data:[]}}));
  await page.getByRole('button',{name:'Open navigation'}).click();await page.getByRole('button',{name:'Routines'}).click();
  const dialog=page.getByRole('dialog',{name:'Routines'});await dialog.locator('.routine-item').first().waitFor();
  for(const row of rows){const item=dialog.locator('.routine-item').filter({hasText:row.definition.name});assert.ok((await item.innerText()).includes(row.text));}
  assert.equal(await dialog.locator('.routine-item').count(),5);
  await dialog.locator('.routine-item').first().getByRole('button',{name:'History'}).click();
  await dialog.getByText('No runs recorded.').waitFor();
  await dialog.locator('.routine-item').first().getByRole('button',{name:'Edit'}).click();
  assert.equal(await dialog.getByLabel('Routine name').inputValue(),'Hourly review');
  await dialog.getByRole('button',{name:'Back to routines'}).click();
  assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth-innerWidth),0);
  assert.deepEqual(errors,[]);
  console.log('Routine labels, status, history and edit navigation passed at phone width without writes.');
} finally {await browser.close();}
