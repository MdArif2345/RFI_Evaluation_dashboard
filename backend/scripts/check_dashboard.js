const fs = require("fs");
const path = require("path").resolve(__dirname, "../../index.html");
const html = fs.readFileSync(path, "utf8");

const s = html.lastIndexOf("<script>");
const js = html.slice(s + 8, html.indexOf("</script>", s));

try {
  new Function(js);
  console.log("inline JS parses OK");
} catch (e) {
  console.log("JS ERROR:", e.message);
  process.exit(1);
}

const ids = [
  "insKpis", "chartThemes", "chartIntent", "chartInsCodes", "chartPosture",
  "chartEffort", "reuseTable", "cooccurTable", "themeTable",
  "insightsBody", "insightsMissing"
];
let bad = 0;
for (const id of ids) {
  const inHtml = html.includes(`id="${id}"`);
  const inJs = js.includes(`"${id}"`) || js.includes(`'${id}'`);
  if (!inHtml || !inJs) {
    console.log("MISMATCH", id, "html:", inHtml, "js:", inJs);
    bad++;
  }
}
console.log(bad === 0 ? "all element ids wired correctly" : `${bad} mismatches`);

const helpers = ["fmt(", "barOpts(", "formatCodeRich(", "formatCodeTag(",
  "formatCodeChartLabel(", "enrichCodeText(", "escapeHtml(", "getCodeEntry("];
console.log("helpers available:", helpers.filter((h) => js.includes(h)).length, "of", helpers.length);

console.log("sections:", (html.match(/id="sec-\d+"/g) || []).join(" "));
const toc = js.match(/\["1[01]",[^\]]+\]/g) || [];
console.log("TOC tail:", toc.join(" | "));

const canvases = (html.match(/<canvas id="[^"]+"/g) || []).map((c) => c.slice(12, -1));
const created = [...js.matchAll(/getElementById\("(chart[^"]+)"\)/g)].map((m) => m[1]);
const orphan = created.filter((c) => !canvases.includes(c));
console.log(orphan.length ? "ORPHAN chart targets: " + orphan : "every chart target has a canvas");
