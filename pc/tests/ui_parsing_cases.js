// Cases for the pure functions of static/redesign.js that read what people and models write:
// token counts ("1M", "1 000 000") and links to files in answers. The functions are taken
// from the real file, not copied, so the test checks what runs in the window.
const fs = require("fs");
const path = require("path");

const src = fs.readFileSync(path.join(__dirname, "..", "static", "redesign.js"), "utf8");
function extract(name, until) {
  const a = src.indexOf(`function ${name}`);
  const b = src.indexOf(until, a + 1);
  if (a < 0 || b < 0) throw new Error(`${name} not found`);
  return src.slice(a, b);
}
eval(extract("parseTokens", "// Rounded for display"));
eval(extract("fmtTokensShort", "// Человекочитаемо"));
eval(extract("fileLinkTarget", "function openFileTarget"));

let failed = 0;
function check(what, got, want) {
  if (got !== want) { failed++; console.log(`FAIL ${what}: ${JSON.stringify(got)} != ${JSON.stringify(want)}`); }
}

const tokens = {
  "1M": 1e6, "1м": 1e6, "1М": 1e6, "1 млн": 1e6, "1,5M": 1.5e6, "1.5m": 1.5e6, "200K": 2e5, "200к": 2e5,
  "200 тыс": 2e5, "128000": 128000, "128 000": 128000, "1,000,000": 1e6, "1.000.000": 1e6,
  "1 000 000": 1e6, "1\u00a0000\u00a0000": 1e6, "1.000M": 1e6, "262144": 262144, "1M tokens": 1e6,
  "": 0, "abc": 0,
};
for (const [text, want] of Object.entries(tokens)) check(`parseTokens(${JSON.stringify(text)})`, parseTokens(text), want);
check("fmtTokensShort(412345)", fmtTokensShort(412345), "412K");
check("fmtTokensShort(1500000)", fmtTokensShort(1500000), "1.5M");

const links = [
  ["habr-0.1.0/article.md", "", "habr-0.1.0/article.md"],
  ["img/a.png", "habr-0.1.0", "habr-0.1.0/img/a.png"],
  ["../x.md", "docs/sub", "docs/x.md"],
  ["./y.md", "docs", "docs/y.md"],
  ["file:src/a.py", "docs", "src/a.py"],
  ["file:///D:/p/x.txt", "", "D:/p/x.txt"],
  ["D:\\Altair\\x.md", "", "D:/Altair/x.md"],
  ["/files/D:/w/a.md", "", "D:/w/a.md"],
  ["sandbox:/mnt/data/r.html", "", ""],
  ["javascript:alert(1)", "", ""],
  ["https://x.com", "", null],
  ["#top", "", null],
  ["mailto:a@b.c", "", null],
  ["my%20file.md", "", "my file.md"],
  ["a.md#sec", "", "a.md"],
];
for (const [href, base, want] of links) check(`fileLinkTarget(${JSON.stringify(href)}, ${JSON.stringify(base)})`, fileLinkTarget(href, base), want);

if (failed) { console.log(`${failed} FAILED`); process.exit(1); }
console.log("ALL CASES PASSED");
