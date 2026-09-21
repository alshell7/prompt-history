// Run with node --test tests/web.test.cjs. No npm dependencies required.
const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const page = fs.readFileSync(require('node:path').join(__dirname, '../prompthistory/web/index.html'), 'utf8');
const script = page.split('<script>').at(-1).split('</script>')[0];
new vm.Script(script); // Validate the entire shipped script, including icon strings.
const helpers = script.slice(script.indexOf('function mark('), script.indexOf('/* Filters the server'));
const context = vm.createContext({});
vm.runInContext(`let query = ''; const words = () => query.toLowerCase().split(/\\s+/).filter(Boolean);
const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
${helpers}`, context);
const render = (text, project='/work') => context.renderPrompt(text, project);

test('renders file, skill, tool and app citations inline', () => {
  for (const target of ['README.md', 'https://example.com/docs', 'skill://package/main', 'plugin://browser/tab', 'app://drive', 'codex://threads/123']) {
    assert.match(render(`Use [Reference](${target}) here.`), /<a href="[^"]+"[^>]*>Reference<\/a> here\./);
  }
});
test('handles Windows paths, spaces, parentheses and escaped labels', () => {
  assert.match(render('[My file](<C:/My Project/goal (1).md>)'), /file:\/\/\/C:\/My%20Project\/goal%20\(1\).md/);
  assert.match(render('[file\\_name](src/a(b).py)'), />file_name<\/a>/);
});
test('unresolved paths retain labels and targets without fake web links', () => {
  const html = render('[file](unknown.md)', null);
  assert.match(html, /title="unknown.md"/);
  assert.doesNotMatch(html, /<a /);
});
for (const target of ['javascript:alert(1)', 'data:text/html,bad', 'vbscript:evil', '//evil.example', '\\\\evil.example\\share', 'java\tscript:alert(1)']) {
  test(`blocks active or network-share target ${JSON.stringify(target)}`, () => {
    const html = context.referenceHtml('Unsafe', target, '/work');
    assert.doesNotMatch(html, /<a /);
  });
}
test('HTML and attribute injection are escaped', () => {
  const html = render('<img src=x onerror=alert(1)> [<svg>](https://example.com/"onclick="evil)');
  assert.doesNotMatch(html, /<img|<svg| onclick=/);
  assert.match(html, /&lt;img/);
});
test('search highlighting only touches text, not URLs or entities', () => {
  vm.runInContext("query = 'https amp file';", context);
  const html = render('[file](https://example.com/?q=amp) &amp;');
  assert.match(html, /href="https:\/\/example.com\/\?q=amp"/);
  assert.match(html, /<mark>file<\/mark>/);
  assert.doesNotMatch(html, /href="[^"\n]*<mark>/);
  vm.runInContext("query = '';", context);
});
test('fenced and inline code remain literal', () => {
  const html = render('`[not a link](https://example.com)`\n```html\n<script>bad</script>\n```');
  assert.doesNotMatch(html, /<a |<script>/);
  assert.match(html, /<pre><code>&lt;script&gt;/);
});
test('Unicode, emphasis and very long content render without loss', () => {
  const input = '**مرحبا 日本語**\n' + 'long '.repeat(10000);
  const html = render(input);
  assert.match(html, /<strong>مرحبا 日本語<\/strong>/);
  assert.ok(html.endsWith('long '.repeat(10000)));
});
test('provider icons are embedded and no CDN dependency is introduced', () => {
  assert.match(page, /codex:'<svg class="provider-icon"/);
  assert.match(page, /claude:'<svg class="provider-icon"/);
  assert.doesNotMatch(page, /<(?:script|img)[^>]+src="https?:/);
});
