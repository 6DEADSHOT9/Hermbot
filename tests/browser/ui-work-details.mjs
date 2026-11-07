import {chromium} from 'playwright';
import assert from 'node:assert/strict';
import {mkdir,writeFile} from 'node:fs/promises';

const pass=process.argv[2]||'before',output=`.taste-skill/work-details/${pass}`;
await mkdir(output,{recursive:true});
const browser=await chromium.launch({headless:true});
const results=[],errors=[];
try {
  for(const {width,height} of [{width:1440,height:900},{width:390,height:844},{width:320,height:568}]){
    const context=await browser.newContext({baseURL:process.env.HERMBOT_TEST_URL||'http://api:8000',viewport:{width,height},reducedMotion:'reduce'});
    const page=await context.newPage();page.on('pageerror',error=>errors.push(error.message));
    await page.goto('/');await page.getByLabel('Owner token').fill(process.env.OWNER_TOKEN);await page.getByRole('button',{name:'Open workspace'}).click();
    if(width<=950)await page.getByRole('button',{name:'Open navigation'}).click();
    await page.getByRole('navigation',{name:'Team conversations'}).getByRole('button',{name:/Scout/}).click();
    if(width<=950)await page.getByRole('button',{name:'Toggle work panel'}).click();
    const panel=page.locator('.context-panel');await panel.waitFor();
    await panel.locator('.task-card').first().getByRole('button',{name:'Inspect work'}).click();
    const inspect=page.getByRole('dialog');await inspect.waitFor();await inspect.screenshot({path:`${output}/inspect-${width}.png`});
    results.push({screen:'inspect',width,dialogOverflow:await inspect.evaluate(element=>element.scrollWidth-element.clientWidth)});
    await inspect.getByRole('button',{name:'Close',exact:true}).click();
    const removed=panel.locator('.work-history > summary').filter({hasText:'Removed work'});
    await removed.scrollIntoViewIfNeeded();await removed.click();await panel.locator('.removed-work').first().waitFor();
    await panel.screenshot({path:`${output}/removed-${width}.png`});
    await panel.locator('.removed-work').first().screenshot({path:`${output}/removed-row-${width}.png`});
    const removedMetrics=await panel.evaluate(element=>{const row=element.querySelector('.removed-work'),buttons=[...row.querySelectorAll('button')];return {panelWidth:element.clientWidth,panelScrollWidth:element.scrollWidth,rowWidth:row.clientWidth,mainWidth:row.querySelector('.removed-work-main').getBoundingClientRect().width,rowScrollWidth:row.scrollWidth,buttons:buttons.map(button=>({name:button.textContent,rect:button.getBoundingClientRect().toJSON()}))};});
    const actions=await panel.locator('.task-card').first().evaluate(card=>({cardWidth:card.clientWidth,buttons:[...card.querySelectorAll('.task-actions button')].map(button=>({name:button.textContent,width:button.getBoundingClientRect().width,y:button.getBoundingClientRect().y}))}));
    results.push({screen:'removed',width,...removedMetrics,actions});
    await removed.click();
    const finished=panel.locator('.work-history > summary').filter({hasText:'Finished work'});
    await finished.scrollIntoViewIfNeeded();await finished.click();await panel.locator('.work-history .task-card').first().waitFor();
    await panel.screenshot({path:`${output}/finished-${width}.png`});
    await panel.locator('.work-history .task-card').first().screenshot({path:`${output}/finished-card-${width}.png`});
    results.push({screen:'finished',width,panelOverflow:await panel.evaluate(element=>element.scrollWidth-element.clientWidth)});
    await page.getByRole('button',{name:/^Outputs/}).click();await panel.screenshot({path:`${output}/outputs-${width}.png`});
    results.push({screen:'outputs',width,panelOverflow:await panel.evaluate(element=>element.scrollWidth-element.clientWidth)});
    assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth-innerWidth),0);
    if(pass==='after'){
      assert.equal(removedMetrics.panelScrollWidth-removedMetrics.panelWidth,0);
      assert(removedMetrics.mainWidth>=removedMetrics.rowWidth*.9,'Removed title must use the row width');
      assert(removedMetrics.buttons.every(button=>button.rect.height>=36));
      if(width===1440)assert.equal(actions.buttons[0].y,actions.buttons[1].y,'Desktop work actions should share a row');
      assert(!await panel.locator('.artifact-card').allTextContents().then(labels=>labels.some(label=>label.includes('0.0 KB'))));
    }
    await context.close();
  }
  await writeFile(`${output}/metrics.json`,JSON.stringify({results,errors},null,2));
  assert.deepEqual(errors,[]);
  console.log(`Work detail audit ${pass}: ${results.length} screens, no page errors.`);
} finally {await browser.close();}
