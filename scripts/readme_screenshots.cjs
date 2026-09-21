// Reproducible documentation images. Every prompt and project below is fictional.
// Requires Playwright and Chrome for development only. Never reads user history.
// Run: node scripts/readme_screenshots.cjs
const {chromium}=require('playwright');
const fs=require('node:fs');
const path=require('node:path');
const http=require('node:http');
const assert=require('node:assert/strict');
const root=path.resolve(__dirname,'..');
const output=path.join(root,'docs','images');
const projects=[
  {name:'atlas-api',title:'Reliable background jobs',texts:[
    'Add cursor-based pagination to the activity endpoint. Keep the response shape consistent with [API.md](docs/API.md), and cover empty pages and invalid cursors.',
    'The webhook handler should be safe to retry. Store the event ID before processing, return the previous result for duplicates, and add a test for two deliveries arriving together.',
    'Review the migration for the new audit log. We need a rollback path and a clear explanation of which indexes matter for the activity feed.',
    'Make the background worker recover after a restart. Pick up unfinished jobs, preserve the retry count, and stop retrying after five attempts.',
    'Add request IDs to the logs so we can follow a job from the API to the worker. Keep credentials and request bodies out of log output.',
    'Use [the release checklist](docs/release.md) to prepare the next version. Summarize the API changes and include a small example of the new pagination response.'
  ]},
  {name:'field-notes',title:'A better writing flow',texts:[
    'Build a distraction-free editor for field notes. Keep keyboard shortcuts discoverable, preserve drafts locally, and show when the latest change was saved.',
    'Improve the empty state for a new notebook. Explain what a notebook contains and offer one clear action to create the first note.',
    'Add a Markdown export that preserves headings, lists, and links. Use the notebook title for the filename, and handle characters that are invalid on Windows.',
    'Make note search work across titles and body text. Highlight matching phrases and let me open a result with the keyboard.',
    'Review [the editor spec](docs/editor.md) and simplify the toolbar. Keep formatting actions close to the selection and show a useful empty state.',
    'Add an undo action after deleting a note. Keep the message visible long enough to use, and restore the note to its original notebook.'
  ]},
  {name:'ember-web',title:'Accessible checkout',texts:[
    'Make checkout work with a keyboard from start to finish. Keep focus visible, announce validation errors, and return focus to the right field after an error.',
    'Add a responsive order summary. On a phone, show the total first and let the customer expand the item list. Keep every price readable at 200% zoom.',
    'Simplify the shipping form. Use clear labels, keep entered values when validation fails, and test addresses with accented characters.',
    'Follow [the component guidelines](docs/components.md) for the payment form. Make loading, failure, and success states easy to distinguish.',
    'Test the checkout at narrow widths and with long product names. The final price and submit button should remain easy to find.',
    'Add a confirmation page with a readable order summary. Show the delivery address, expected arrival, and a clear link back to the store.'
  ]}
];
const prompts=[];
for(let day=0;day<45;day++){
  const project=projects[(day+1)%3],tool=project.name==='field-notes'?'Claude':'Codex';
  for(let turn=0;turn<2+(day*7)%5;turn++){
    const text=project.texts[(day+turn)%project.texts.length];
    const ts=Date.parse('2026-07-09T09:00:00Z')+day*86400000+turn*(7+day%3*5)*60000;
    prompts.push({id:`demo-${day}-${turn}`,text,preview:text,tool,source:tool==='Claude'?'claude-code':'codex',source_label:tool,
      session_id:`demo-session-${day}`,session_title:project.title,timestamp:new Date(ts).toISOString(),ts:ts/1000,
      project:`/demo/${project.name}`,project_name:project.name,turn:turn+1,kind:'prompt',words:text.split(/\s+/).length,
      chars:text.length,git_branch:'main',model:null,references:[],section:null});
  }
}
prompts.sort((a,b)=>b.ts-a.ts);
const sessions=[...new Map(prompts.map(p=>[p.session_id,{id:p.session_id,title:p.session_title}])).values()];
const by_tool={};for(const p of prompts) by_tool[p.tool]=(by_tool[p.tool]||0)+1;
const data={prompts,sessions,cloud_threads:[],stats:{prompts:prompts.length,sessions:sessions.length,projects:3,
  words:prompts.reduce((n,p)=>n+p.words,0),by_tool,duplicates_removed:0,cloud_threads:0,scanned_roots:[]}};
const server=http.createServer((req,res)=>{
  if(req.url==='/api/data'){res.setHeader('Content-Type','application/json');res.end(JSON.stringify(data));return;}
  const assets={'/':'index.html','/analytics.js':'analytics.js','/share-card.js':'share-card.js'};
  const file=assets[req.url];
  if(!file){res.writeHead(404);res.end();return;}
  res.setHeader('Content-Type',file.endsWith('.js')?'text/javascript':'text/html');
  res.end(fs.readFileSync(path.join(root,'prompthistory','web',file)));
});
(async()=>{
  await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
  let browser;
  try{
    browser=await chromium.launch({headless:true,channel:process.env.BROWSER_CHANNEL||'chrome'});
    const context=await browser.newContext({viewport:{width:1440,height:980},deviceScaleFactor:1,colorScheme:'light',timezoneId:'UTC',locale:'en-US',reducedMotion:'reduce'});
    const page=await context.newPage(),errors=[],external=[];
    page.on('pageerror',e=>errors.push(e.message));
    await page.route('**/*',route=>{
      if(!route.request().url().startsWith(`http://127.0.0.1:${server.address().port}/`)){
        external.push(route.request().url());return route.abort();
      }
      return route.continue();
    });
    await page.goto(`http://127.0.0.1:${server.address().port}/`);
    await page.waitForSelector('.entry');
    await page.locator('[data-twist="t:Codex"]').click();
    await page.locator('[data-twist="t:Claude"]').click();
    fs.mkdirSync(output,{recursive:true});
    await page.screenshot({path:path.join(output,'prompt-browser.png')});
    await page.locator('#analyticsView').click();
    await page.waitForSelector('.analytics-summary');
    await page.setViewportSize({width:1440,height:1040});
    await page.screenshot({path:path.join(output,'local-analytics.png')});
    await page.locator('#analyticsShare').click();
    await page.waitForFunction(()=>!document.querySelector('#shareDownload').disabled);
    await page.locator('#shareNames').check();
    await page.waitForFunction(()=>!document.querySelector('#shareDownload').disabled);
    const image=await page.locator('#sharePreview').getAttribute('src');
    assert.ok(image.startsWith('data:image/png;base64,'));
    fs.writeFileSync(path.join(output,'activity-card.png'),Buffer.from(image.split(',')[1],'base64'));
    assert.deepEqual(errors,[]);assert.deepEqual(external,[]);
    console.log(`Wrote three documentation images from ${prompts.length} synthetic prompts to docs/images.`);
  }finally{
    if(browser) await browser.close();
    await new Promise(resolve=>server.close(resolve));
  }
})().catch(error=>{console.error(error);process.exitCode=1;});
