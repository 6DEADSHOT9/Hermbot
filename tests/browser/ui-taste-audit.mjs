import {chromium} from 'playwright';
import assert from 'node:assert/strict';
import {mkdir, writeFile} from 'node:fs/promises';

const pass = process.argv[2] || 'before';
const output = `.taste-skill/${pass}`;
await mkdir(output, {recursive:true});
const browser = await chromium.launch({headless:true});
const context = await browser.newContext({baseURL:process.env.HERMBOT_TEST_URL || 'http://localhost:8780', viewport:{width:1440,height:900}, reducedMotion:'reduce'});
const page = await context.newPage();
const errors = [];
page.on('pageerror', error => errors.push(error.message));
const results = [];
try {
  await page.goto('/');
  await page.getByLabel('Owner token').fill(process.env.OWNER_TOKEN);
  await page.getByRole('button',{name:'Open workspace'}).click();
  await page.getByRole('navigation',{name:'Team conversations'}).waitFor();
  await page.getByRole('navigation',{name:'Team conversations'}).getByRole('button',{name:/Scout/}).click();
  await page.locator('.message').first().waitFor();
  await page.evaluate(()=>document.fonts.ready);
  if(pass==='after')assert.equal(await page.evaluate(()=>document.fonts.check('14px "DM Sans"')),true,'Local UI font did not load');
  for (const {width,height} of [{width:1440,height:900},{width:768,height:900},{width:390,height:844},{width:320,height:568},{width:844,height:390}]) {
    await page.setViewportSize({width,height});
    await page.screenshot({path:`${output}/workspace-${width}.png`});
    const workspace = await page.evaluate(() => ({
      overflow:document.documentElement.scrollWidth-innerWidth,
      transcriptSize:getComputedStyle(document.querySelector('.prose')).fontSize,
      sidebarOpen:document.querySelector('.sidebar')?.classList.contains('open'),
      headerActions:[...document.querySelectorAll('.chat-header button')].filter(e=>e.getBoundingClientRect().width>0).map(e=>({name:e.getAttribute('aria-label'),width:Math.round(e.getBoundingClientRect().width)})),
    }));
    if (pass === 'after') {assert.equal(workspace.overflow,0);assert.equal(workspace.transcriptSize,'14px');if(width<=950)assert.equal(workspace.sidebarOpen,false);}
    results.push({screen:'workspace',width,height,...workspace});
    const folded = await page.getByRole('button',{name:'Open navigation'}).isVisible();
    if (folded) await page.getByRole('button',{name:'Open navigation'}).click();
    if (pass === 'after') {
      const groups = page.getByRole('navigation',{name:'Group conversations'}).getByRole('button');
      await groups.last().scrollIntoViewIfNeeded();
      const nav = await page.evaluate(() => {const box=document.querySelector('.sidebar-scroll').getBoundingClientRect(),last=document.querySelector('#group-conversations button:last-child').getBoundingClientRect();return {top:last.top-box.top,bottom:box.bottom-last.bottom};});
      assert.ok(nav.top>=-1&&nav.bottom>=-1,`Last group clipped at ${width}×${height}: ${JSON.stringify(nav)}`);
    }
    await page.screenshot({path:`${output}/navigation-${width}.png`});
    if (folded) await page.keyboard.press('Escape');
    if (await page.getByLabel('Conversation tools').isVisible()) {await page.getByLabel('Conversation tools').click();await page.getByRole('button',{name:'Memory and skills'}).click();}
    else await page.getByRole('button',{name:'Open Scout memory and skills'}).click();
    await page.screenshot({path:`${output}/knowledge-${width}.png`});
    const knowledge=await page.evaluate(() => ({overflow:document.querySelector('.modal').scrollWidth-document.querySelector('.modal').clientWidth}));
    if(pass==='after')assert.equal(knowledge.overflow,0);
    results.push({screen:'knowledge',width,...knowledge});
    await page.getByRole('dialog').getByRole('button',{name:'Close',exact:true}).click();
    if (folded) await page.getByRole('button',{name:'Open navigation'}).click();
    await page.getByRole('button',{name:'Routines'}).click();
    await page.getByText('Loading routines…').waitFor({state:'hidden'});
    await page.screenshot({path:`${output}/routines-${width}.png`});
    const routines=await page.evaluate(() => ({overflow:document.querySelector('.modal').scrollWidth-document.querySelector('.modal').clientWidth,sidebarOpen:document.querySelector('.sidebar')?.classList.contains('open')}));
    if(pass==='after'){assert.equal(routines.overflow,0);if(folded)assert.equal(routines.sidebarOpen,false);}
    results.push({screen:'routines',width,...routines});
    await page.getByRole('dialog').getByRole('button',{name:'Close',exact:true}).click();
    if (folded) await page.keyboard.press('Escape');
  }
  if(pass==='after'){
    for(const width of [1440,390]){
      await page.setViewportSize({width,height:844});
      const folded=width<=950;
      const nav=async()=>{if(folded&&!await page.locator('.sidebar').getAttribute('class').then(value=>value.includes('open')))await page.getByRole('button',{name:'Open navigation'}).click();};
      const dialog=async(label,file)=>{await page.getByRole('dialog').waitFor();await page.screenshot({path:`${output}/${file}-${width}.png`});const size=await page.evaluate(()=>({scroll:document.querySelector('.modal').scrollWidth,width:document.querySelector('.modal').clientWidth}));assert.ok(size.scroll<=size.width+1,`${label} dialog overflows at ${width}px`);const sidebarOpen=folded&&await page.locator('.sidebar').getAttribute('class').then(value=>value.includes('open'));if(folded)assert.equal(sidebarOpen,false,`${label} leaves navigation open`);results.push({screen:label,width,dialogOverflow:size.scroll-size.width,sidebarOpen:!!sidebarOpen});await page.getByRole('dialog').getByRole('button',{name:'Close',exact:true}).click();};
      await nav();await page.getByLabel('Owner menu').click();await page.getByRole('button',{name:'Connections',exact:true}).waitFor();await page.screenshot({path:`${output}/owner-menu-${width}.png`});
      const menu=await page.evaluate(()=>{const box=document.querySelector('.owner-popover').getBoundingClientRect();return {top:box.top,bottom:box.bottom,left:box.left,right:box.right};});
      assert.ok(menu.top>=0&&menu.bottom<=844&&menu.left>=0&&menu.right<=width,`Owner menu clipped at ${width}px: ${JSON.stringify(menu)}`);
      results.push({screen:'Owner menu',width,menu});await page.locator('.brand-row').click();assert.equal(await page.locator('.owner-menu').getAttribute('open'),null);
      await nav();await page.getByRole('button',{name:'Create teammate'}).click();await page.getByRole('heading',{name:'Create teammate'}).waitFor();await dialog('Create teammate','create-teammate');
      await nav();await page.getByRole('button',{name:'New group conversation'}).click();await page.getByRole('heading',{name:'Bring your team together'}).waitFor();await dialog('Create group','create-group');
      await nav();await page.getByLabel('Owner menu').click();await page.getByRole('button',{name:'Settings',exact:true}).click();await page.getByRole('heading',{name:'Settings',exact:true}).waitFor();await dialog('Settings','workspace-settings');
      await nav();await page.getByLabel('Owner menu').click();await page.getByRole('button',{name:'Connections',exact:true}).click();await page.getByRole('heading',{name:'Connections'}).waitFor();await dialog('Connections','connections');
      await nav();await page.getByLabel('Owner menu').click();await page.getByRole('button',{name:'File library',exact:true}).click();await page.getByRole('heading',{name:'File library'}).waitFor();await dialog('File library','file-library');
    }
    await page.setViewportSize({width:390,height:844});
    await page.getByRole('button',{name:'Open navigation'}).click();
    await page.getByLabel('Owner menu').click();
    await page.getByRole('button',{name:'Switch to dark theme'}).click();
    await page.keyboard.press('Escape');
    await page.screenshot({path:`${output}/dark-390.png`});
    assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth-innerWidth),0);
    await page.getByRole('button',{name:'Open navigation'}).click();await page.getByLabel('Owner menu').click();
    await page.screenshot({path:`${output}/dark-owner-menu-390.png`});await page.keyboard.press('Escape');
    await page.getByRole('button',{name:'Open navigation'}).click();await page.getByRole('button',{name:'Routines'}).click();
    await page.getByText('Loading routines…').waitFor({state:'hidden'});await page.screenshot({path:`${output}/dark-routines-390.png`});
    assert.equal(await page.evaluate(()=>document.querySelector('.modal').scrollWidth-document.querySelector('.modal').clientWidth),0);
    await page.getByRole('dialog').getByRole('button',{name:'Close',exact:true}).click();
  }
  await writeFile(`${output}/metrics.json`,JSON.stringify({results,errors},null,2));
  console.log(JSON.stringify({results,errors}));
} finally { await browser.close(); }
