// Test: the write_file LIVE preview must NOT open/key a document on a model name
// that is still streaming (a partial JSON string). It must wait for the name's
// closing quote, so a name streamed as "open-re" -> "open-research.html" produces
// ONE doc titled with the full name, not a stray "open-re" duplicate.
//
//   node dyva/test_live_writefile_name.js
//
// Extracts the REAL _extractJsonStringField + _maybeLiveWriteFile from dashboard.html
// (stubbing syncWriteFileLive to capture the names it's asked to open with).
const fs = require('fs');
const path = require('path');

const html = fs.readFileSync(path.join(__dirname, 'static', 'dashboard.html'), 'utf8');

// Pull one top-level `function NAME(...){ ... }` out of the source by brace-matching.
function extractFn(src, name) {
  const start = src.indexOf('function ' + name + '(');
  if (start < 0) throw new Error('function not found: ' + name);
  let i = src.indexOf('{', start), depth = 0;
  for (let j = i; j < src.length; j++) {
    if (src[j] === '{') depth++;
    else if (src[j] === '}') { depth--; if (depth === 0) return src.slice(start, j + 1); }
  }
  throw new Error('unbalanced braces for ' + name);
}

const opened = [];                       // names syncWriteFileLive is called with
const sandbox = {
  syncWriteFileLive: function (gen, spec) { opened.push(spec.name); },
};
// define the two real functions into the sandbox
const code = extractFn(html, '_extractJsonStringField') + '\n' +
             extractFn(html, '_maybeLiveWriteFile') + '\n' +
             'this._extractJsonStringField=_extractJsonStringField;' +
             'this._maybeLiveWriteFile=_maybeLiveWriteFile;';
const vm = require('vm');
vm.createContext(sandbox);
vm.runInContext(code, sandbox);

function tc(argStr) { return [{ function: { name: 'write_file', arguments: argStr } }]; }
const gen = { chatId: 1 };

// Simulate the argument JSON growing one chunk at a time, exactly as a stream does.
const chunks = [
  '{"name":"open-re',                                  // name still streaming — must NOT open
  '{"name":"open-research.html',                       // name value complete but quote not closed yet
  '{"name":"open-research.html"',                      // closing quote arrived — NOW it may open
  '{"name":"open-research.html","type":"code","content":"<ht',   // content streaming
  '{"name":"open-research.html","type":"code","content":"<html></html>"}',
];
chunks.forEach(c => sandbox._maybeLiveWriteFile(gen, tc(c)));

// Assertions
let failed = false;
const partials = opened.filter(n => n !== 'open-research.html');
if (partials.length) {
  console.error('FAIL: live preview opened on a partial/streaming name:', JSON.stringify(partials));
  failed = true;
}
if (!opened.includes('open-research.html')) {
  console.error('FAIL: live preview never opened on the full name "open-research.html"');
  failed = true;
}

// sanity: the pure extractor reports closed correctly
const a = sandbox._extractJsonStringField('{"name":"open-re', 'name');
const b = sandbox._extractJsonStringField('{"name":"open-research.html"', 'name');
if (!(a && a.closed === false && a.value === 'open-re')) { console.error('FAIL: extractor closed-state (partial)'); failed = true; }
if (!(b && b.closed === true && b.value === 'open-research.html')) { console.error('FAIL: extractor closed-state (complete)'); failed = true; }

if (failed) { console.error('opened names were:', JSON.stringify(opened)); process.exit(1); }
console.log('PASS: live write_file preview waits for the complete name; opened =', JSON.stringify(opened));
