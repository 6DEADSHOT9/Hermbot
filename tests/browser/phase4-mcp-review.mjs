// Real public-documentation reads through native bot proposals and the connector review UI.
import {chromium} from 'playwright';
import assert from 'node:assert/strict';
import {writeFile} from 'node:fs/promises';
const browser=await chromium.launch({headless:true});
const context=await browser.newContext({baseURL:'http://api:8000',viewport:{width:1440,height:1000}});
const page=await context.newPage(),errors=[];
page.on('pageerror',e=>errors.push(e.message));page.setDefaultTimeout(15000);
await page.goto('/');await page.getByLabel('Owner token').fill(process.env.OWNER_TOKEN);await page.getByRole('button',{name:'Open workspace'}).click();await page.getByRole('navigation',{name:'Team conversations'}).waitFor();
async function request(path,method='GET',body){const r=await context.request.fetch(path,{method,headers:{'Idempotency-Key':crypto.randomUUID()},...(body===undefined?{}:{data:body})});assert.ok(r.ok(),`HTTP ${r.status()} ${path}: ${await r.text()}`);return r.json();}
async function until(test,timeout=240000){const end=Date.now()+timeout;while(Date.now()<end){const value=await test();if(value)return value;await page.waitForTimeout(700);}throw Error('Live MCP review timed out');}
const state=await request('/api/v1/state');assert.equal(state.protocol_fixture,false);
const bot=state.bots.find(b=>b.id==='researcher');assert.ok(bot?.readiness.compatible);
const policy={provider:'mcp',name:'Phase 4 real MCP review proof',endpoint:'https://docs.mcp.cloudflare.com/mcp',bot_ids:[],actions:[],resources:{}};
let connection=await request('/api/v1/connections','POST',policy),task;
const proof={real_native_model:true,actual_public_documentation_mcp:true,google_slack_github_acceptance:false,external_account_changes:0,connection_id:connection.id,checks:[],script_errors:errors};
try{
  connection=await request(`/api/v1/connections/${connection.id}/token`,'POST',{token:'',expected_revision:connection.revision});
  connection=await request(`/api/v1/connections/${connection.id}/discover`,'POST');
  assert.ok(connection.capabilities.some(t=>t.name==='search_cloudflare_documentation'));
  connection=await request(`/api/v1/connections/${connection.id}`,'PUT',{...policy,bot_ids:[bot.id],actions:['mcp.search_cloudflare_documentation'],expected_revision:connection.revision});
  async function propose(label,query){
    const title=`Phase 4 MCP ${label} ${Date.now()}`;
    const group=await request('/api/v1/groups','POST',{title,bot_ids:[bot.id,'writer']});
    task=await request('/api/v1/tasks','POST',{bot_id:bot.id,conversation_id:group.id,prompt:`This is a public documentation MCP review acceptance test. Call hermbot_connector exactly once with connection_id ${connection.id}, action mcp.search_cloudflare_documentation, arguments {"query":${JSON.stringify(query)}}, summary ${JSON.stringify('Review public documentation search '+label)}. End your turn immediately after creating the review. On denial, do not repeat it; finish with PHASE4_MCP_DENIED. If resumed with a definitive successful connector receipt, summarize the actual public documentation result in a local artifact using hermbot_publish and finish with PHASE4_MCP_RECEIPT. Do not call the connector again or use terminal/browser, delegate, change settings or request another review.`});
    console.log(`Real MCP ${label} task ${task.id} submitted.`);
    const approval=await until(async()=>{const s=await request('/api/v1/state');const a=s.approvals.find(a=>a.task_id===task.id&&a.status==='pending');const t=s.tasks.find(t=>t.id===task.id);if(t&&['completed','failed','uncertain','cancelled'].includes(t.status)&&!a)throw Error(`Task ${t.status}: ${t.error}`);return a;});
    assert.equal(approval.kind,'connector');
    await page.getByRole('navigation',{name:'Group conversations'}).getByRole('button',{name:new RegExp(title)}).click();
    await page.locator('.review-card').filter({hasText:approval.id.slice(0,8)}).waitFor();
    return approval;
  }
  let review=await propose('denial','Cloudflare Workers public documentation review denial test');
  await page.locator('.review-card').filter({hasText:review.id.slice(0,8)}).getByRole('button',{name:'Deny',exact:true}).click();
  const deniedTask=await until(async()=>{const t=await request(`/api/v1/tasks/${task.id}`);if(['failed','uncertain'].includes(t.status))throw Error(`Denied continuation ${t.status}`);return t.status==='completed'&&t;});
  let activity=await request('/api/v1/connections');
  const denied=activity.operations.find(o=>o.task_id===task.id);
  assert.equal(denied.status,'denied');assert.equal(denied.result,null);assert.equal(denied.lease_until,null);assert.ok(deniedTask.output.includes('PHASE4_MCP_DENIED'));
  proof.denied_task_id=task.id;proof.denied_operation_id=denied.id;proof.checks.push('Native proposal denied in UI; outbox was never dispatched; native denial continuation completed.');
  review=await propose('edited approval','Cloudflare Workers initial documentation query');
  const editedQuery='Cloudflare Workers bindings reference';
  await page.locator('.review-card').filter({hasText:review.id.slice(0,8)}).getByRole('button',{name:'Edit draft',exact:true}).click();
  await page.getByLabel('query',{exact:true}).fill(editedQuery);
  await page.getByRole('button',{name:'Replace review',exact:true}).click();await page.getByRole('dialog').waitFor({state:'hidden'});
  proof.approved_task_id=task.id;proof.original_approval_id=review.id;
  const replacement=await until(async()=>{const s=await request('/api/v1/state');return s.approvals.find(a=>a.task_id===task.id&&a.status==='pending'&&a.id!==review.id);});
  proof.replacement_approval_id=replacement.id;
  const stale=await context.request.post(`/api/v1/approvals/${review.id}`,{data:{decision:'once',expected_payload_hash:review.payload_hash}});assert.equal(stale.status(),409);
  await page.locator('.review-card').filter({hasText:replacement.id.slice(0,8)}).getByRole('button',{name:'Allow once',exact:true}).click();
  const completed=await until(async()=>{const t=await request(`/api/v1/tasks/${task.id}`);if(t.connector_state==='uncertain'||['failed','uncertain','cancelled'].includes(t.status))throw Error(`Approved continuation ${t.status}: ${t.error}`);return t.status==='completed'&&t;});
  activity=await request('/api/v1/connections');
  const ops=activity.operations.filter(o=>o.task_id===task.id);
  assert.equal(ops.find(o=>o.approval_id===review.id).status,'superseded');
  const success=ops.find(o=>o.approval_id===replacement.id);
  assert.equal(success.status,'succeeded');assert.equal(success.arguments.query,editedQuery);assert.ok(success.result.content?.length);
  assert.ok(completed.output.includes('PHASE4_MCP_RECEIPT'));
  assert.equal(ops.filter(o=>o.status==='succeeded').length,1);
  proof.approved_task_id=task.id;proof.approved_operation_id=success.id;proof.exact_query=editedQuery;proof.stale_approval_rejected=true;proof.actual_provider_receipt=true;proof.definitive_receipt_continuation=true;
  proof.checks.push('Edited connector review invalidated the prior approval; exact new public search ran once and returned an actual MCP receipt.');
  await page.getByLabel('Owner menu').click();await page.getByRole('button',{name:'Connections',exact:true}).click();await page.getByRole('dialog').getByRole('button',{name:'Activity',exact:true}).click();
  await page.locator('.grant-card').filter({hasText:success.id}).getByText('Exact action and receipt',{exact:true}).click();
  await page.screenshot({path:'/output/phase4-mcp-review.png',fullPage:true});await page.getByRole('button',{name:'Close',exact:true}).click();
  assert.deepEqual(errors,[]);
}finally{
  if(task){const status=await request(`/api/v1/tasks/${task.id}`);if(!['completed','failed','cancelled','interrupted'].includes(status.status)){await request(`/api/v1/tasks/${task.id}/stop`,'POST');await until(async()=>{const t=await request(`/api/v1/tasks/${task.id}`);return ['completed','failed','cancelled','interrupted'].includes(t.status)&&!t.executions.some(a=>['reserved','dispatching','running','stopping','uncertain'].includes(a.status));});}}
  connection=(await request('/api/v1/connections')).connections.find(c=>c.id===connection.id);
  await request(`/api/v1/connections/${connection.id}`,'PUT',{...policy,bot_ids:[],actions:connection.actions,expected_revision:connection.revision});
  await request(`/api/v1/connections/${connection.id}/revoke`,'POST');
  proof.temporary_connection_detached_and_revoked=true;
  await writeFile('/output/phase4-mcp-review.json',JSON.stringify(proof,null,2));await browser.close();
}
console.log('Real native MCP connector denial, review editing, approved public search, receipt and continuation passed.');
