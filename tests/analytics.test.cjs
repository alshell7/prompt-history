const {test} = require('node:test');
const assert = require('node:assert/strict');
const {analyze,textStats,dayKey} = require('../prompthistory/web/analytics.js');
const now = Date.parse('2026-09-22T12:00:00Z');
const opts = {now};
function prompt(minutes, extra={}){
  return {text:'Fix the parser. Add tests!',tool:'Codex',project:'/a/app',project_name:'app',
    session_id:'s1',session_title:'Parser',timestamp:new Date(Date.parse('2026-09-20T10:00:00Z')+minutes*60000).toISOString(),...extra};
}
test('empty analytics are finite and serializable',()=>{
  const a=analyze([],[],opts);
  for(const value of Object.values(a.totals)) assert.equal(Number.isFinite(value),true);
  assert.equal(a.totals.prompts,0); assert.equal(a.totals.activitySeconds,0);
  assert.equal(a.earliest,null);assert.equal(a.projects.length,0);
  assert.doesNotMatch(JSON.stringify(a),/NaN|Infinity/);
});
test('text counts include unicode code points and use whitespace word counts',()=>{
  assert.deepEqual(textStats({text:'Hello world!\nこんにちは。 次です！\nمرحبا 😀'}),{words:6,characters:32,sentences:4});
});
test('sentences ignore code and URLs while words preserve submitted text',()=>{
  const p={text:'Read [the docs](https://example.com/a.b).\n```js\nx.y();\nz();\n```\nThen use `x.y`!'};
  assert.equal(textStats(p).sentences,2);
  assert.equal(textStats(p).words,p.text.split(/\s+/).length);
});
test('typing estimates respond to speed and do not imply measured time',()=>{
  const p=prompt(0,{text:'one two three four'});
  assert.equal(analyze([p],[p],{now,wpm:40}).totals.typingSeconds,6);
  assert.equal(analyze([p],[p],{now,wpm:80}).totals.typingSeconds,3);
  assert.equal(analyze([p],[p],opts).totals.activitySeconds,0);
});
test('idle gaps excluded; boundary equals cutoff is included',()=>{
  const rows=[prompt(0),prompt(30),prompt(61),prompt(71)];
  const a=analyze(rows,rows,opts);
  assert.equal(a.totals.activitySeconds,40*60);
  assert.equal(a.totals.intervals,2);
  assert.equal(analyze(rows,rows,{now,idleMinutes:10}).totals.activitySeconds,600);
});
test('interleaved projects and tasks cannot double-count time',()=>{
  const rows=[prompt(0),prompt(5,{project:'/b/app',session_id:'s2'}),prompt(10),prompt(15,{tool:'Claude',session_id:'s3'})];
  const a=analyze(rows,rows,opts);
  assert.equal(a.totals.activitySeconds,900);
  assert.equal(a.projects.length,2);
  assert.equal(a.projects.find(p=>p.key==='/a/app').activitySeconds,600);
  assert.equal(a.projects.find(p=>p.key==='/b/app').activitySeconds,300);
  assert.equal(a.projects.reduce((n,p)=>n+p.activitySeconds,0),a.totals.activitySeconds);
  assert.equal(a.totals.projectSwitches,2);
});
test('filters do not bridge hidden prompts or extrapolate a last prompt',()=>{
  const rows=[prompt(0),prompt(5),prompt(10),prompt(15)];
  assert.equal(analyze(rows,[rows[0],rows[2],rows[3]],opts).totals.activitySeconds,300);
  assert.equal(analyze(rows,[rows[1]],opts).totals.activitySeconds,0);
});
test('simultaneous submissions in different projects are excluded from time',()=>{
  const rows=[prompt(0),prompt(0,{project:'/b'}),prompt(10)];
  const a=analyze(rows,rows,opts);
  assert.equal(a.totals.activitySeconds,0);assert.equal(a.totals.ambiguousIntervals,1);
});
test('simultaneous prompts in same project contribute only one interval',()=>{
  const rows=[prompt(0),prompt(0,{tool:'Claude'}),prompt(10)];
  const a=analyze(rows,rows,opts);
  assert.equal(a.totals.activitySeconds,600);
  assert.equal(a.tools.reduce((n,t)=>n+t.activitySeconds,0),0);
});
test('invalid and future dates count toward text but not timeline',()=>{
  const rows=[prompt(0,{timestamp:null}),prompt(0,{timestamp:'invalid'}),prompt(0,{timestamp:'2099-01-01T00:00:00Z'})];
  const a=analyze(rows,rows,opts);
  assert.equal(a.totals.prompts,3);assert.equal(a.totals.words,15);
  assert.equal(a.totals.undated,2);assert.equal(a.totals.future,1);
  assert.equal(a.daily.length,0);assert.equal(a.totals.activitySeconds,0);
});
test('local midnight splits activity accurately and conserves total',()=>{
  const start=new Date(2026,8,19,23,50),end=new Date(2026,8,20,0,10);
  const rows=[prompt(0,{timestamp:start.toISOString()}),prompt(0,{timestamp:end.toISOString()})];
  const a=analyze(rows,rows,opts);
  assert.deepEqual(a.daily.map(d=>d.activitySeconds),[600,600]);
  assert.equal(a.daily.reduce((n,d)=>n+d.activitySeconds,0),a.totals.activitySeconds);
});
test('streaks use local calendar dates and allow yesterday',()=>{
  const rows=[18,19,20].map(d=>prompt(0,{timestamp:new Date(2026,8,d,12).toISOString()}));
  let a=analyze(rows,rows,{now:new Date(2026,8,21,12).getTime()});
  assert.equal(a.totals.currentStreak,3);assert.equal(a.totals.longestStreak,3);
  a=analyze(rows,rows,{now:new Date(2026,8,22,12).getTime()});
  assert.equal(a.totals.currentStreak,0);
});
test('DST day lengths do not change daily streaks',()=>{
  const rows=[7,8,9].map(d=>prompt(0,{timestamp:new Date(2026,2,d,12).toISOString()}));
  assert.equal(analyze(rows,rows,{now:new Date(2026,2,9,18).getTime()}).totals.currentStreak,3);
});
test('session identifiers are namespaced by tool and project keys by full path',()=>{
  const rows=[prompt(0),prompt(1,{tool:'Claude',project:'/b/app'})];
  const a=analyze(rows,rows,opts);
  assert.equal(a.totals.sessions,2);assert.equal(a.totals.projects,2);
});
test('word distribution, percentiles and repeated submissions',()=>{
  const rows=[prompt(0,{text:'one'}),prompt(1,{text:'one'}),prompt(2,{text:'word '.repeat(100)}),prompt(3,{text:'word '.repeat(500)})];
  const t=analyze(rows,rows,opts).totals;
  assert.equal(t.words,602);assert.equal(t.medianWords,50.5);assert.equal(t.p90Words,500);
  assert.equal(t.repeatedSubmissions,1);assert.equal(t.longestPromptWords,500);
  assert.deepEqual(analyze(rows,rows,opts).lengths.map(b=>b.prompts),[2,0,1,1]);
});
test('malicious labels and missing projects are plain data, not object keys',()=>{
  const rows=[prompt(0,{project:'__proto__',project_name:'<img src=x>'}),prompt(1,{project:null})];
  const a=analyze(rows,rows,opts);
  assert.equal(a.totals.projects,2);assert.equal(a.projects[0].name,'<img src=x>');
});
test('input order does not affect results and analytics never mutates prompts',()=>{
  const rows=[prompt(0),prompt(10),prompt(20)];
  rows.forEach(Object.freeze);Object.freeze(rows);
  assert.deepEqual(analyze(rows,rows,opts),analyze([...rows].reverse(),[...rows].reverse(),opts));
});
test('aggregate export does not include original prompt text',()=>{
  const rows=[prompt(0,{text:'a private text sentinel'})];
  assert.doesNotMatch(JSON.stringify(analyze(rows,rows,opts)),/private text sentinel/);
});
test('invalid estimate parameters are rejected instead of producing misleading totals',()=>{
  for(const wpm of [0,-1,201,NaN,Infinity]) assert.throws(()=>analyze([],[],{wpm}),RangeError);
  for(const idleMinutes of [0,121,NaN]) assert.throws(()=>analyze([],[],{idleMinutes}),RangeError);
});
test('large histories preserve all totals',()=>{
  const rows=Array.from({length:20000},(_,i)=>prompt(i,{project:'/p/'+(i%50)}));
  const a=analyze(rows,rows,{now:Date.parse('2030-01-01T00:00:00Z')});
  assert.equal(a.totals.prompts,20000);assert.equal(a.totals.activitySeconds,19999*60);
  assert.equal(a.projects.reduce((n,p)=>n+p.prompts,0),20000);
});
