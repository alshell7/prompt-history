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
  timestamp:'2026-09-21T10:00:00Z',ts:1790000000+i,project:i===1?'/second/app':'/first/app',
  project_name:'app',turn:1,kind:i===0?'slash-command':'prompt',words:20,chars:i===0?text.length:30,
  section:i===0?'Research':null,model:'test-model',references:i===0?[{label:'Goal objective',target:'/first/app/goal.md',kind:'file'}]:[],
}));
const data={prompts,sessions:prompts.map(p=>({id:p.session_id,title:p.session_title})),cloud_threads:[],
  stats:{prompts:3,sessions:3,projects:2,words:60,by_tool:{Codex:2,Claude:1},duplicates_removed:0,cloud_threads:0,scanned_roots:[]}};
const server=http.createServer((req,res)=>{
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
    fs.mkdirSync(path.join(__dirname,'../.qa'),{recursive:true});
    await page.screenshot({path:path.join(__dirname,'../.qa/desktop.png'),fullPage:true});
    await page.setViewportSize({width:390,height:844});
    await page.screenshot({path:path.join(__dirname,'../.qa/mobile.png'),fullPage:true});
    assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),'No horizontal page overflow');
    assert.deepEqual(errors,[]);
    assert.deepEqual(outbound,[]);
    console.log('Browser checks passed: desktop/mobile, safe references, original clipboard, search, distinct projects, icons, no outbound requests.');
  } finally {
    if(browser)await browser.close();
    await new Promise(resolve=>server.close(resolve));
  }
})().catch(error=>{console.error(error);process.exitCode=1;});
