// Optional browser integration: npm install --no-save playwright, then
// BROWSER_CHANNEL=chrome node tests/browser_smoke.cjs (PowerShell: $env:BROWSER_CHANNEL='chrome').
// Uses synthetic data only and never scans local user history.
const {chromium} = require('playwright');
const fs = require('node:fs');
const path = require('node:path');
const http = require('node:http');
const assert = require('node:assert/strict');

const html = fs.readFileSync(path.join(__dirname,'../prompthistory/web/index.html'));
const text = '/goal Build **the complete feature** with [README](README.md), [$skill](skill://design/main), and [Browser](plugin://browser/tab).\n\n' +
  'Preserve the original prompt, العربية, and 日本語.\n'.repeat(15) +
  '\n```xml\n<task-notification>This is my code example</task-notification>\n```\n<img src=x onerror=alert(1)> [unsafe](javascript:alert(1))';
const prompts = ['Codex','Codex','Claude'].map((tool,i) => ({
  id:'p'+i,text:i===0?text:'Review the parser in project '+i,preview:'Prompt '+i,
  tool,source:tool==='Codex'?'codex':'claude-code',source_label:tool,
  session_id:'s'+i,session_title:['Goal recovery','Second project','Claude notification fix'][i],
  timestamp:new Date(Date.parse('2026-09-21T10:00:00Z')+i*300000).toISOString(),ts:1790000000+i,project:i===1?'/second/app':'/first/app',
  project_name:'app',turn:1,kind:i===0?'slash-command':'prompt',words:20,chars:i===0?text.length:30,
  section:i===0?'Research':null,model:'test-model',references:i===0?[{label:'Goal objective',target:'/first/app/goal.md',kind:'file'}]:[],
}));
const data={prompts,sessions:prompts.map(p=>({id:p.session_id,title:p.session_title})),cloud_threads:[],
  stats:{prompts:3,sessions:3,projects:2,words:60,by_tool:{Codex:2,Claude:1},duplicates_removed:0,cloud_threads:0,scanned_roots:[]}};
const server=http.createServer((req,res)=>{
  if(req.url==='/analytics.js' || req.url==='/share-card.js'){
    res.setHeader('Content-Type','text/javascript');
    res.end(fs.readFileSync(path.join(__dirname,'../prompthistory/web',req.url.slice(1))));return;
  }
  res.setHeader('Content-Type',req.url==='/api/data'?'application/json':'text/html');
  res.end(req.url==='/api/data'?JSON.stringify(data):html);
});

(async()=>{
  await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
  let browser;
  try{
    browser=await chromium.launch({headless:true,channel:process.env.BROWSER_CHANNEL||'chrome'});
    const context=await browser.newContext({viewport:{width:1440,height:1050},permissions:['clipboard-read','clipboard-write']});
    const page=await context.newPage();
    const errors=[],outbound=[];
    page.on('pageerror',e=>errors.push(e.message));
    page.on('request',r=>{if(!r.url().startsWith('http://127.0.0.1:'))outbound.push(r.url());});
    await page.goto(`http://127.0.0.1:${server.address().port}`);
    await page.waitForSelector('.entry');
    assert.equal(await page.locator('.entry').count(),3);
    assert.equal(await page.locator('.badge .provider-icon').count(),3);
    assert.equal(await page.locator('.entry img').count(),0);
    assert.equal(await page.locator('a[href^="javascript:"]').count(),0);
    assert.match(await page.locator('[data-id="p0"]').innerText(),/Section: Research/);
    await page.locator('[data-open="p0"]').click();
    assert.equal(await page.locator('[data-id="p0"] .body.open').count(),1);
    assert.equal(await page.locator('[data-id="p0"] .body a').count(),3);
    await page.locator('[data-copy="p0"]').click();
    assert.equal((await page.evaluate(()=>navigator.clipboard.readText())).replace(/\r\n/g,'\n'),text);
    await page.locator('#q').fill('README');
    await page.waitForFunction(()=>document.querySelectorAll('.entry').length===1);
    assert.equal(await page.locator('.body a mark').innerText(),'README');
    await page.locator('#q').fill('');
    await page.waitForFunction(()=>document.querySelectorAll('.entry').length===3);
    await page.locator('[data-pick="tool"][data-tool="Codex"]').click();
    assert.equal(await page.locator('[data-pick="project"][data-tool="Codex"]').count(),2);
    await page.locator('[data-pick="project"][data-project="/second/app"]').click();
    assert.equal(await page.locator('.entry').count(),1);
    assert.equal(await page.locator('.entry').getAttribute('data-id'),'p1');
    await page.locator('[data-pick="all"]').click();
    await page.locator('#analyticsView').click();
    assert.equal(await page.locator('#analytics').isVisible(),true);
    assert.equal(await page.locator('#list').isVisible(),false);
    assert.equal(await page.locator('[data-analytics-project]').count(),2);
    assert.equal(await page.locator('.analytics-summary dd').nth(4).innerText(),'10m');
    const initialTyping=await page.locator('.analytics-summary dd').nth(5).innerText();
    await page.locator('#analyticsWpm').fill('80');
    await page.locator('#analyticsWpm').blur();
    assert.notEqual(await page.locator('.analytics-summary dd').nth(5).innerText(),initialTyping);
    await page.locator('#analyticsSince').fill('2099-01-01');
    assert.match(await page.locator('#analyticsReport').innerText(),/No activity/);
    await page.locator('#analyticsAllTime').click();
    await page.locator('[data-analytics-project="/second/app"]').click();
    assert.match(await page.locator('#count').innerText(),/1 of 3/);
    await page.locator('[data-pick="all"]').click();
    await page.locator('#analyticsUntil').fill('2020-01-01');
    await page.locator('#analyticsSince').fill('2026-01-01');
    assert.match(await page.locator('#analyticsError').innerText(),/start date/);
    await page.locator('#analyticsAllTime').click();
    const downloadPromise=page.waitForEvent('download');
    await page.locator('#analyticsExport').click();
    const download=await downloadPromise;
    const exported=JSON.parse(fs.readFileSync(await download.path(),'utf8'));
    assert.equal(exported.totals.prompts,3);
    assert.equal(exported.methodology.wpm,80);
    assert.ok(!JSON.stringify(exported).includes('Preserve the original prompt'));
    await page.locator('#q').fill('no-such-prompt');
    await page.waitForFunction(()=>document.querySelector('#analyticsReport').textContent.includes('No activity'));
    await page.locator('#q').fill('');
    await page.waitForFunction(()=>document.querySelectorAll('.analytics-summary dd').length===8);
    await page.locator('#analyticsWpm').fill('0');
    await page.locator('#analyticsWpm').blur();
    assert.equal(await page.locator('#analyticsExport').isDisabled(),true);
    await page.locator('#analyticsWpm').fill('80');
    await page.locator('#analyticsWpm').blur();
    await page.locator('#promptsView').click();
    assert.equal(await page.locator('.entry').count(),3);
    // A realistic history for the visual pass; content remains synthetic.
    const rich=[];
    for(let day=0;day<90;day++) for(let turn=0;turn<day%7+2;turn++){
      const ts=Date.parse('2026-06-01T09:00:00Z')+day*86400000+turn*600000;
      const project=['/work/parser','/work/site','/work/research'][day%3];
      rich.push({...prompts[2],id:`d${day}p${turn}`,text:'Design the parser and add coverage for malformed input. '.repeat(turn+1),
        timestamp:new Date(ts).toISOString(),ts:ts/1000,project,project_name:project.split('/').pop(),
        tool:day%2?'Codex':'Claude',session_id:`d${day}`,session_title:`Iteration ${day+1}`,words:10*(turn+1)});
    }
    data.prompts=rich;data.stats={...data.stats,prompts:rich.length,projects:3,sessions:90,by_tool:{Codex:200,Claude:200}};
    await page.locator('#rescan').click();
    await page.locator('#analyticsView').click();
    await page.waitForSelector('.activity-column');
    await page.locator('#analyticsShare').click();
    await page.waitForFunction(()=>!document.querySelector('#shareDownload').disabled);
    assert.equal(await page.locator('#shareDialog').isVisible(),true);
    assert.equal(await page.locator('#shareNames').isChecked(),false);
    const card=await page.locator('#sharePreview').getAttribute('src');
    const png=Buffer.from(card.split(',')[1],'base64');
    assert.equal(png.readUInt32BE(16),1200);assert.equal(png.readUInt32BE(20),1000);
    fs.mkdirSync(path.join(__dirname,'../.qa'),{recursive:true});
    fs.writeFileSync(path.join(__dirname,'../.qa/share-card-light.png'),png);
    const imageDownloadPromise=page.waitForEvent('download');
    await page.locator('#shareDownload').click();
    assert.equal((await imageDownloadPromise).suggestedFilename(),'prompt-history-activity.png');
    await page.locator('#shareCopy').click();
    await page.waitForFunction(()=>document.querySelector('#shareStatus').textContent.includes('Image copied'));
    assert.match(await page.locator('#shareStatus').innerText(),/Image copied/);
    assert.ok(await page.evaluate(async()=>(await navigator.clipboard.read()).some(item=>item.types.includes('image/png'))));
    await page.locator('#shareNames').check();
    await page.locator('#shareTheme').selectOption('dark');
    await page.waitForFunction(()=>!document.querySelector('#shareDownload').disabled);
    fs.writeFileSync(path.join(__dirname,'../.qa/share-card-dark.png'),Buffer.from((await page.locator('#sharePreview').getAttribute('src')).split(',')[1],'base64'));
    await page.keyboard.press('Escape');
    assert.equal(await page.locator('#shareDialog').isVisible(),false);
    assert.equal(await page.locator('#analyticsShare').evaluate(el=>el===document.activeElement),true);
    await page.locator('#analyticsShare').click();
    assert.equal(await page.locator('#shareNames').isChecked(),false);
    await page.locator('#shareClose').click();
    await page.evaluate(()=>document.querySelector('#toast').classList.remove('up'));
    fs.mkdirSync(path.join(__dirname,'../.qa'),{recursive:true});
    await page.screenshot({path:path.join(__dirname,'../.qa/desktop.png'),fullPage:true});
    await page.setViewportSize({width:390,height:844});
    await page.waitForFunction(()=>!document.querySelector('#browsePanel').open);
    assert.equal(await page.locator('#browsePanel').getAttribute('open'),null);
    await page.screenshot({path:path.join(__dirname,'../.qa/mobile.png'),fullPage:true});
    assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),'No horizontal page overflow');
    assert.ok(await page.locator('.analytics-table-wrap').evaluateAll(els=>els.every(el=>el.scrollWidth<=el.clientWidth+1)),'Tables fit mobile without horizontal scrolling');
    await page.emulateMedia({colorScheme:'dark'});
    await page.addStyleTag({content:'*{transition:none !important}'});
    await page.screenshot({path:path.join(__dirname,'../.qa/analytics-dark-mobile.png'),fullPage:true});
    assert.deepEqual(errors,[]);
    assert.deepEqual(outbound,[]);
    console.log('Browser checks passed: desktop/mobile, safe references, original clipboard, search, distinct projects, icons, no outbound requests.');
  } finally {
    if(browser)await browser.close();
    await new Promise(resolve=>server.close(resolve));
  }
})().catch(error=>{console.error(error);process.exitCode=1;});
