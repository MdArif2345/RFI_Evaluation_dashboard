const fs = require("fs");
const nodePath = require("path");
const I = JSON.parse(fs.readFileSync(
  nodePath.resolve(__dirname, "../../qna_insights.json"), "utf8"));

const problems = [];
const need = (cond, label) => { if (!cond) problems.push(label); };

need(Number.isInteger(I.total_pairs), "total_pairs");
need(Array.isArray(I.themes) && I.themes.length > 0, "themes[]");
for (const t of I.themes) {
  need(typeof t.name === "string" && t.name.length > 0, `theme.name (${t.id})`);
  need(Number.isInteger(t.size), `theme.size (${t.id})`);
  need(typeof t.cohesion === "number", `theme.cohesion (${t.id})`);
  need(Array.isArray(t.terms) && t.terms.length >= 3, `theme.terms (${t.id})`);
  need(Array.isArray(t.examples) && t.examples.length > 0, `theme.examples (${t.id})`);
  need(Number.isInteger(t.median_answer_chars), `theme.median (${t.id})`);
  need(Number.isInteger(t.p90_answer_chars), `theme.p90 (${t.id})`);
  need(Array.isArray(t.top_codes), `theme.top_codes (${t.id})`);
}
need(I.intents.every((x) => x.intent && Number.isInteger(x.count)), "intents[]");
need(I.postures.every((x) => x.posture && Number.isInteger(x.count)), "postures[]");
need(typeof I.codes.coverage_pct === "number", "codes.coverage_pct");
need(Number.isInteger(I.codes.distinct_codes), "codes.distinct_codes");
need(I.codes.top.every((x) => x.code && Number.isInteger(x.count)), "codes.top[]");
need(I.codes.co_occurrence.every((x) => x.a && x.b && Number.isInteger(x.count)), "codes.co_occurrence[]");
need(typeof I.reuse.duplicate_rate_pct === "number", "reuse.duplicate_rate_pct");
need(Number.isInteger(I.reuse.duplicated_pairs), "reuse.duplicated_pairs");
need(I.reuse.top_groups.every((g) => Number.isInteger(g.count) && g.question), "reuse.top_groups[]");

const themeSum = I.themes.reduce((a, t) => a + t.size, 0);
const intentSum = I.intents.reduce((a, x) => a + x.count, 0);
const postureSum = I.postures.reduce((a, x) => a + x.count, 0);
need(themeSum === I.total_pairs, `theme sizes sum ${themeSum} != ${I.total_pairs}`);
need(intentSum === I.total_pairs, `intent sum ${intentSum} != ${I.total_pairs}`);
need(postureSum === I.total_pairs, `posture sum ${postureSum} != ${I.total_pairs}`);

if (problems.length) {
  console.log("FIELD PROBLEMS:\n  " + problems.join("\n  "));
  process.exit(1);
}
console.log("every field the UI reads is present and well-typed");
console.log(`themes=${I.themes.length} sum=${themeSum} intents=${intentSum} postures=${postureSum}`);
console.log("theme names:", I.themes.map((t) => t.name).join(" | "));
