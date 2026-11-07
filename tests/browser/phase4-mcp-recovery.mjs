// Recovery of the real public documentation response loss; never replay the original search.
import {chromium} from 'playwright';
import assert from 'node:assert/strict';
import {writeFile} from 'node:fs/promises';
const taskId='d1090118-252b-46c9-bd5a-053bff317e3c',opId='a8df463a-07dc-4324-a231-6c7fc1e303a2';
const browser=await chromium.launch({headless:true});
const context=await browser.newContext({baseURL:'http://api:8000',viewport:{width:1440,height:1000}});
const page=await context.newPage(),errors=[];page.on('pageerror',e=>errors.push(e.message));page.setDefaultTimeout(15000);
await page.goto('/');await page.getByLabel('Owner token').fill(process.env.OWNER_TOKEN);await page.getByRole('button',{name:'Open workspace'}).click();await page.getByRole('navigation',{name:'Team conversations'}).waitFor();
async function get(path){const r=await context.request.get(path);assert.ok(r.ok(),`HTTP ${r.status()} ${path}: ${await r.text()}`);return r.json();}
const before=await get(`/api/v1/tasks/${taskId}`);assert.equal(before.status,'cancelled');assert.equal(before.connector_state,'uncertain');
const state=await get('/api/v1/state'),group=state.conversations.find(c=>c.id===before.conversation_id);assert.ok(group);
await page.getByRole('navigation',{name:'Group conversations'}).getByRole('button',{name:new RegExp(group.title)}).click();
await page.getByRole('button',{name:'Review application outcome',exact:true}).click();
const dialog=page.getByRole('dialog');await dialog.getByRole('heading',{name:'Connections',exact:true}).waitFor();
const card=dialog.locator('.grant-card').filter({hasText:opId});await card.getByRole('button',{name:'Reconcile outcome'}).waitFor();
const retry=await context.request.post(`/api/v1/tasks/${taskId}/retry`,{headers:{'Idempotency-Key':crypto.randomUUID()},data:{}});assert.equal(retry.status(),409);
await card.getByRole('button',{name:'Reconcile outcome'}).click();await card.getByLabel('Recorded outcome').selectOption('unresolved');
const evidence='Public unauthenticated Cloudflare documentation search returned no usable receipt because its SSE stream stayed open. The task is stopped. No account mutation was possible; actual search outcome remains unknown. Close tracking without replay or a success/failure claim.';
await card.getByLabel('Reconciliation evidence').fill(evidence);await card.getByRole('button',{name:'Record outcome',exact:true}).click();
await card.getByText('mcp.search_cloudflare_documentation · unresolved',{exact:true}).waitFor();
const after=await get(`/api/v1/tasks/${taskId}`),op=(await get('/api/v1/connections')).operations.find(o=>o.id===opId);
assert.equal(after.status,'cancelled');assert.equal(after.connector_state,'unresolved');assert.equal(after.executions.length,before.executions.length);
assert.equal(op.status,'unresolved');assert.equal(op.result.reconciled,false);assert.equal(op.result.owner_evidence,evidence);assert.ok(op.error);
const again=await context.request.post(`/api/v1/tasks/${taskId}/retry`,{headers:{'Idempotency-Key':crypto.randomUUID()},data:{}});assert.equal(again.status(),409);
await card.getByText('Exact action and receipt',{exact:true}).click();await page.screenshot({path:'/output/phase4-mcp-recovery.png',fullPage:true});assert.deepEqual(errors,[]);
await writeFile('/output/phase4-mcp-response-loss.json',JSON.stringify({actual_public_mcp:true,task_id:taskId,operation_id:opId,observed_outcome:'uncertain',closed_outcome:'unresolved',original_provider_outcome_known:false,original_error:op.error,owner_evidence:evidence,original_search_replayed:false,stopped_task_resumed:false,retry_before_and_after_closure:409,native_attempt_count_unchanged:true,script_errors:errors},null,2));
await browser.close();console.log('Real lost-response recovery UI and no-replay retry guards passed. Outcome remains unknown.');
