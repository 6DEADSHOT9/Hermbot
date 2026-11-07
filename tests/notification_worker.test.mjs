// Controlled worker tests do not grant permission or prove OS banner display.
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import test from 'node:test';
import vm from 'node:vm';

const source=readFileSync(new URL('../web/public/sw.js',import.meta.url),'utf8');
const id='11111111-2222-4333-8444-555555555555';
function worker() {
  const events={},notifications=[];
  const self={addEventListener:(kind,handler)=>events[kind]=handler,
    registration:{showNotification:async(title,options)=>notifications.push({title,options})},
    location:{origin:'http://localhost:8780'},clients:{claim:async()=>{}},skipWaiting:async()=>{}};
  vm.runInNewContext(source,{self,URL,fetch:()=>{throw new Error('Push must not send diagnostic requests');}});
  return {notifications,async push(data){let completed;events.push({data:{json:()=>data},waitUntil:p=>completed=p});await completed;}};
}

test('push displays generic text and keeps the opaque navigation target without diagnostics',async()=>{
  const value=worker();
  await value.push({target:id,tag:'stable',message:'PRIVATE_CANARY',secret:'PRIVATE_CANARY'});
  assert.equal(value.notifications.length,1);
  assert.equal(value.notifications[0].title,'Hermbot');
  assert.equal(value.notifications[0].options.data.target,id);
  assert.equal(value.notifications[0].options.tag,'stable');
  assert.equal(JSON.stringify(value.notifications).includes('PRIVATE_CANARY'),false);
});
test('null and primitive payloads still produce a generic notification',async()=>{
  for(const payload of [null,123,'PRIVATE_CANARY']){
    const value=worker();await value.push(payload);
    assert.equal(value.notifications.length,1);
    assert.equal(value.notifications[0].options.data.target,'inbox');
  }
});
