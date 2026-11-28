// Test: smart-sort by a COMPUTED (formula) column.
//
//   node tests/test_sheet_sort.js
//
// Bug: clicking a column header that holds a formula ($/GB = price/capacity) did
// not sort — _sheetSortByCol compared the RAW cell (the formula text "=B2/A2")
// with _numify, which can't parse "=", so every formula cell fell into the text
// bucket and sorted lexicographically (i.e. original row order). And even once the
// key is fixed, the absolute refs don't follow a moved row, so the values
// re-scramble on recompute. The fix (pure _sheetSortGrid + _remapRefs): sort by
// the COMPUTED value, and remap single-cell refs through the row permutation so
// each moved formula follows its own data (ranges are left alone — an aggregate
// over the block is order-independent).
//
// Extracts the REAL helpers from static/dashboard.html; stubs _calcEval with plain
// arithmetic (mathjs isn't available to node, and after ref substitution the
// expression is pure numbers/operators).
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const html = fs.readFileSync(path.join(__dirname, '..', 'dyva', 'static', 'dashboard.html'), 'utf8');

function extractFn(src, name) {
  const start = src.indexOf('function ' + name + '(');
  if (start < 0) throw new Error('function not found: ' + name);
  let depth = 0;
  for (let j = src.indexOf('{', start); j < src.length; j++) {
    if (src[j] === '{') depth++;
    else if (src[j] === '}') { depth--; if (depth === 0) return src.slice(start, j + 1); }
  }
  throw new Error('unbalanced braces for ' + name);
}

const NEED = ['_colName', '_colIdx', '_refRC', '_sheetGrid', '_sheetJSON',
  '_sheetEval', '_numify', '_sheetCompute', '_sheetCmp', '_remapRefs', '_sheetSortGrid'];

const sandbox = {
  _SHEET_MAXROWS: 200, _SHEET_MAXCOLS: 52,
  _CUR: /[$€£¥₹₩฿¢]/g,
  _calcEval: function (s) { return eval(String(s)); },   // test-only arithmetic stand-in
};
vm.createContext(sandbox);

let failed = false;
function fail(msg) { console.error('FAIL: ' + msg); failed = true; }
function eq(a, b, msg) { if (JSON.stringify(a) !== JSON.stringify(b)) fail(msg + ' — got ' + JSON.stringify(a) + ' want ' + JSON.stringify(b)); }

try {
  const code = NEED.map(n => extractFn(html, n)).join('\n') + '\n' +
    NEED.map(n => 'this.' + n + '=' + n + ';').join('');
  vm.runInContext(code, sandbox);
} catch (e) {
  fail('could not load helpers: ' + e.message);
  console.log(failed ? 'TEST FAILED' : 'ok');
  process.exit(failed ? 1 : 0);
}

const S = sandbox;
const colVals = g => S._sheetCompute(g).values;

// ---- 1. formula column sorts desc AND stays correct after recompute ----
(function () {
  // header row, then 3 data rows. col 2 ($/GB) = price(col1) / capacity(col0).
  const grid = [
    ['GB', 'Price', '$/GB'],
    ['16', '299', '=B2/A2'],   // 18.6875
    ['8', '100', '=B3/A3'],    // 12.5
    ['4', '200', '=B4/A4'],    // 50
  ];
  const out = S._sheetSortGrid(grid, 2, 'desc');
  if (!out) { fail('desc: _sheetSortGrid returned null'); return; }
  const v = colVals(out);
  // the whole point: recomputing the sorted grid gives a DESCENDING $/GB column
  eq([v[1][2], v[2][2], v[3][2]], [50, 18.6875, 12.5], 'desc: $/GB not descending after recompute');
  // data integrity: each row's other cells moved with it (price 200 is now first)
  eq([out[1][0], out[1][1]], ['4', '200'], 'desc: row data did not move with the sort key');
  eq([out[2][1], out[3][1]], ['299', '100'], 'desc: remaining rows out of order');
  // header untouched
  eq(out[0], ['GB', 'Price', '$/GB'], 'desc: header row was disturbed');
})();

// ---- 2. asc is the reverse ----
(function () {
  const grid = [
    ['GB', 'Price', '$/GB'],
    ['16', '299', '=B2/A2'],
    ['8', '100', '=B3/A3'],
    ['4', '200', '=B4/A4'],
  ];
  const v = colVals(S._sheetSortGrid(grid, 2, 'asc'));
  eq([v[1][2], v[2][2], v[3][2]], [12.5, 18.6875, 50], 'asc: $/GB not ascending');
})();

// ---- 3. a plain literal column still sorts (regression) ----
(function () {
  const grid = [
    ['name', 'qty'],
    ['a', '7'],
    ['b', '2'],
    ['c', '40'],
  ];
  const out = S._sheetSortGrid(grid, 1, 'desc');
  const v = colVals(out);
  eq([v[1][1], v[2][1], v[3][1]], [40, 7, 2], 'literal: qty not descending');
  eq([out[1][0], out[2][0], out[3][0]], ['c', 'a', 'b'], 'literal: names did not follow');
})();

// ---- 4. a range below the block is left untouched; a single ref follows its row ----
(function () {
  const grid = [
    ['GB', 'Price', '$/GB'],
    ['16', '299', '=B2/A2'],      // row idx 1 -> moves to idx 2 under the desc sort
    ['8', '100', '=B3/A3'],
    ['4', '200', '=B4/A4'],
    ['', '', ''],                 // blank row ends the block
    ['total', '=sum(B2:B4)', '=B2'], // range spans the block (untouched); =B2 must follow row idx 1
  ];
  const out = S._sheetSortGrid(grid, 2, 'desc');
  eq(out[5][1], '=sum(B2:B4)', 'range endpoints must be left untouched (aggregate is order-independent)');
  eq(out[5][2], '=B3', 'single ref below the block did not follow its row through the permutation');
})();

console.log(failed ? 'TEST FAILED' : 'all sheet-sort tests passed');
process.exit(failed ? 1 : 0);
