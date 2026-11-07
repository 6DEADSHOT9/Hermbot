import {chromium} from 'playwright';
import assert from 'node:assert/strict';
import {writeFile} from 'node:fs/promises';

const browser=await chromium.launch({headless:true});
const page=await browser.newPage({viewport:{width:1440,height:1000}}),errors=[];
page.on('pageerror',e=>errors.push(e.message));
try{
  await page.goto('http://api:8000');
  await page.getByLabel('Owner token').fill(process.env.OWNER_TOKEN);
  await page.getByRole('button',{name:'Open workspace',exact:true}).click();
  await page.getByRole('navigation',{name:'Team conversations'}).getByRole('button',{name:/Scout/}).click();
  await page.getByRole('button',{name:'Open Scout memory and skills',exact:true}).click();
  const dialog=page.getByRole('dialog',{name:'Scout · Memory & skills',exact:true});
  await dialog.locator('summary').filter({hasText:'Tools & application access'}).click();
  await dialog.getByText('App connections keep enabled tools available.',{exact:false}).waitFor();
  for(const label of ['Run local commands','Edit local files','Use the browser']){
    assert.equal(await dialog.locator('.capability-row').filter({hasText:label}).locator('span').innerText(),'Available');
  }
  const viewports=[];
  for(const width of [1440,390]){
    await page.setViewportSize({width,height:900});
    const measure=await dialog.evaluate(e=>({scroll:e.scrollWidth,client:e.clientWidth}));
    assert.ok(measure.scroll<=measure.client+1);
    assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth+1),false);
    await page.screenshot({path:`/output/native-tools-${width}.png`,fullPage:true});
    viewports.push({width,horizontal:false});
  }
  await dialog.getByRole('button',{name:'Close',exact:true}).click();
  await page.setViewportSize({width:1440,height:1000});
  await page.getByLabel('Owner menu').click();await page.getByRole('button',{name:'Connections',exact:true}).click();
  const connections=page.getByRole('dialog',{name:'Connections',exact:true});
  await connections.getByRole('button',{name:'Add connection',exact:true}).click();
  assert.match(await connections.getByRole('group',{name:'Teammates with access',exact:true}).innerText(),/App connections keep enabled tools available/);
  assert.doesNotMatch(await connections.innerText(),/native terminal\/browser tools are blocked/);
  await connections.getByRole('button',{name:'Close',exact:true}).click();
  assert.deepEqual(errors,[]);
  await writeFile('/output/native-tools-ui.json',JSON.stringify({complete:true,connected_native_tools_available:true,
    connection_forms_explain_account_permissions:true,read_only_check:true,viewports,script_errors:errors},null,2));
  console.log('Connected native tools and connection permissions are clear in the desktop/mobile UI.');
}finally{await browser.close();}
