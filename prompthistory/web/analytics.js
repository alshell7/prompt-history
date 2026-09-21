/* Local, deterministic analytics. No requests, storage, or prompt mutation. */
(function(root){
  "use strict";
  const cache = new WeakMap();
  const segmenter = typeof Intl.Segmenter === "function" ? new Intl.Segmenter("en", {granularity:"sentence"}) : null;
  function textStats(prompt){
    if (cache.has(prompt)) return cache.get(prompt);
    const text = String(prompt.text || "");
    // Sentence counts describe prose, not fenced code, URLs, or link targets.
    const prose = text.replace(/```[^\n]*\n[\s\S]*?(?:```|$)|~~~[^\n]*\n[\s\S]*?(?:~~~|$)/g, " ")
      .replace(/`[^`\n]+`/g, " ").replace(/\[([^\]]+)\]\([^\n]*?\)/g, "$1")
      .replace(/https?:\/\/\S+/g, " ");
    let sentences = 0;
    for (const line of prose.split(/\n+/).filter(s => /[\p{L}\p{N}]/u.test(s))){
      const parts = segmenter ? Array.from(segmenter.segment(line), s => s.segment) : line.split(/[.!?。！？]+(?:\s|$)/u);
      sentences += parts.filter(s => /[\p{L}\p{N}]/u.test(s)).length;
    }
    const result = {words:text.trim() ? text.trim().split(/\s+/u).length : 0,
      characters:Array.from(text).length, sentences};
    cache.set(prompt, result);
    return result;
  }
  function dayKey(date){
    return `${date.getFullYear()}-${String(date.getMonth()+1).padStart(2,"0")}-${String(date.getDate()).padStart(2,"0")}`;
  }
  function dayNumber(key){ return Date.parse(key + "T00:00:00Z") / 86400000; }
  function stamp(p){
    if (!p.timestamp) return null;
    const value = Date.parse(p.timestamp);
    return Number.isFinite(value) ? value : null;
  }
  const projectKey = p => p.project || null;
  const sessionKey = p => JSON.stringify([p.tool || "Other", p.session_id]);
  function analyze(all, selected=all, options={}){
    const wpm = Number(options.wpm ?? 40), idleMinutes = Number(options.idleMinutes ?? 30);
    if (!Number.isFinite(wpm) || wpm < 10 || wpm > 200) throw new RangeError("Typing speed must be 10–200 words per minute.");
    if (!Number.isFinite(idleMinutes) || idleMinutes < 1 || idleMinutes > 120) throw new RangeError("Idle cutoff must be 1–120 minutes.");
    const now = options.now === undefined ? Date.now() : Number(options.now);
    if (!Number.isFinite(now)) throw new RangeError("Invalid current time.");
    const chosen = new Set(selected);
    const projects = new Map(), tools = new Map(), sessions = new Map(), days = new Map(), repeats = new Map();
    const hours = Array(24).fill(0), weekdays = Array(7).fill(0), lengths = [0,0,0,0];
    const sizes = [];
    let words=0, characters=0, sentences=0, undated=0, future=0, recovered=0, commands=0, references=0;
    const bucket = (map,key,label) => {
      if (!map.has(key)) map.set(key, {key,name:label,prompts:0,words:0,sentences:0,characters:0,activitySeconds:0,sessions:new Set(),days:new Set()});
      return map.get(key);
    };
    const dayBucket = key => {
      if (!days.has(key)) days.set(key,{date:key,prompts:0,words:0,activitySeconds:0});
      return days.get(key);
    };
    for (const p of selected){
      const stats = textStats(p), ts = stamp(p), dated = ts !== null && ts <= now;
      words += stats.words; characters += stats.characters; sentences += stats.sentences; sizes.push(stats.words);
      lengths[stats.words < 20 ? 0 : stats.words < 100 ? 1 : stats.words < 500 ? 2 : 3]++;
      repeats.set(p.text, (repeats.get(p.text)||0)+1);
      if (ts === null) undated++; else if (ts > now) future++;
      if (p.recovery_note) recovered++;
      if (p.kind === "slash-command") commands++;
      if (p.references?.length) references++;
      const day = dated ? dayKey(new Date(ts)) : null;
      for (const b of [bucket(projects,projectKey(p),p.project_name || "Unknown project"),
        bucket(tools,p.tool || "Other",p.tool || "Other"), bucket(sessions,sessionKey(p),p.session_title || p.session_id || "Unknown session")]){
        b.prompts++; b.words += stats.words; b.sentences += stats.sentences; b.characters += stats.characters;
        b.sessions.add(sessionKey(p)); if (day) b.days.add(day);
      }
      const session = sessions.get(sessionKey(p));
      session.tool = p.tool; session.project = p.project; session.sessionId = p.session_id;
      if (day){
        const d = new Date(ts), b = dayBucket(day);
        b.prompts++; b.words += stats.words;
        hours[d.getHours()]++; weekdays[(d.getDay()+6)%7]++;
      }
    }
    // One global timeline avoids double-counting concurrent tasks. Filtered-out
    // prompts remain boundaries: a search must never invent a continuous span.
    const timeline = all.map(p => ({p,ts:stamp(p)})).filter(x => x.ts !== null && x.ts <= now).sort((a,b)=>a.ts-b.ts);
    const groups = [];
    for (const item of timeline){
      if (groups.length && groups[groups.length-1].ts === item.ts) groups[groups.length-1].items.push(item.p);
      else groups.push({ts:item.ts,items:[item.p]});
    }
    let activitySeconds=0, intervals=0, ambiguousIntervals=0, switches=0;
    for (let i=0;i<groups.length-1;i++){
      const a=groups[i], b=groups[i+1], seconds=(b.ts-a.ts)/1000;
      if (seconds > idleMinutes*60 || !a.items.some(p=>chosen.has(p)) || !b.items.some(p=>chosen.has(p))) continue;
      const keys = new Set(a.items.map(projectKey));
      // Simultaneous submissions in different projects have no reliable owner.
      if (keys.size !== 1){ ambiguousIntervals++; continue; }
      const p = a.items.find(p=>chosen.has(p));
      activitySeconds += seconds; intervals++;
      projects.get(projectKey(p)).activitySeconds += seconds;
      // Attribute a tied interval to a tool/session only when unambiguous.
      if (new Set(a.items.map(p=>p.tool)).size === 1) tools.get(p.tool || "Other").activitySeconds += seconds;
      if (new Set(a.items.map(sessionKey)).size === 1) sessions.get(sessionKey(p)).activitySeconds += seconds;
      if (new Set(b.items.map(projectKey)).size === 1 && projectKey(b.items[0]) !== projectKey(p)) switches++;
      let cursor=a.ts;
      while(cursor < b.ts){
        const date=new Date(cursor), midnight=new Date(date.getFullYear(),date.getMonth(),date.getDate()+1).getTime();
        const end=Math.min(midnight,b.ts);
        dayBucket(dayKey(date)).activitySeconds += (end-cursor)/1000;
        cursor=end;
      }
    }
    const daily = [...days.values()].sort((a,b)=>a.date.localeCompare(b.date));
    const active = daily.filter(d=>d.prompts).map(d=>d.date);
    let longestStreak=0, run=0, previous=-Infinity;
    for(const date of active){ const day=dayNumber(date); run=day===previous+1 ? run+1 : 1; longestStreak=Math.max(longestStreak,run); previous=day; }
    const today=dayNumber(dayKey(new Date(now)));
    let currentStreak=0, cursor=active.length-1;
    if(cursor>=0 && today-dayNumber(active[cursor])<=1){
      currentStreak=1;
      while(cursor>0 && dayNumber(active[cursor])-dayNumber(active[cursor-1])===1){currentStreak++;cursor--;}
    }
    sizes.sort((a,b)=>a-b);
    const sortedBuckets = map => [...map.values()].map(b=>({...b,sessions:b.sessions.size,activeDays:b.days.size,days:undefined,typingSeconds:b.words/wpm*60}))
      .sort((a,b)=>b.activitySeconds-a.activitySeconds || b.words-a.words || String(a.key).localeCompare(String(b.key)));
    return {
      methodology:{wpm,idleMinutes,timeZone:Intl.DateTimeFormat().resolvedOptions().timeZone || "Local",
        activity:"Gaps between adjacent recorded prompts up to the idle cutoff, assigned to the preceding project. Both endpoints must match the filters. Overlapping tasks are counted once; ambiguous simultaneous projects are excluded. Includes waiting and reading, misses work outside these spans; not a work timer.",
        typing:"Text words divided by the chosen typing speed. Includes pasted, dictated, code, and recovered content; not measured keystrokes. Do not add this to activity time.",
        text:"Words are whitespace-separated tokens; characters are Unicode code points including whitespace. Sentences are approximate prose segments, excluding fenced/inline code and URLs, using English sentence rules. Fragments on separate lines count; other languages may differ."},
      totals:{prompts:selected.length,words,characters,sentences,projects:projects.size,sessions:sessions.size,
        activitySeconds,typingSeconds:words/wpm*60,activeDays:active.length,averageWords:selected.length ? words/selected.length : 0,
        medianWords:sizes.length ? (sizes[Math.floor((sizes.length-1)/2)]+sizes[Math.floor(sizes.length/2)])/2 : 0,
        p90Words:sizes.length ? sizes[Math.ceil(sizes.length*.9)-1] : 0,longestPromptWords:sizes[sizes.length-1]||0,
        promptsPerActiveDay:active.length ? (selected.length-undated-future)/active.length : 0,
        longestStreak,currentStreak,projectSwitches:switches,intervals,ambiguousIntervals,
        undated,future,recovered,commands,references,repeatedSubmissions:[...repeats.values()].reduce((n,c)=>n+Math.max(0,c-1),0)},
      projects:sortedBuckets(projects),tools:sortedBuckets(tools),sessions:sortedBuckets(sessions),daily,hours,weekdays,
      lengths: ["Under 20","20–99","100–499","500+"].map((name,i)=>({name,prompts:lengths[i]})),
      earliest:active[0]||null, latest:active[active.length-1]||null,
    };
  }
  const api={analyze,textStats,dayKey};
  if (typeof module === "object" && module.exports) module.exports=api;
  else root.PromptAnalytics=api;
})(typeof globalThis !== "undefined" ? globalThis : this);
