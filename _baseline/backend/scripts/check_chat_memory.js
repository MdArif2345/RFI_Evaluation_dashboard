// Extracts the transcript-persistence block from index.html and runs it against
// stubbed browser globals, so the shipped code is what gets tested.
const fs = require("fs");
const path = require("path");

const html = fs.readFileSync(path.resolve(__dirname, "../../index.html"), "utf8");
const START = "// ----- Transcript persistence";
const END = "// Generation is the long phase";
const block = html.slice(html.indexOf(START), html.indexOf(END));
if (!block || block.length < 500) throw new Error("could not extract persistence block");

const problems = [];
const check = (cond, label) => { if (!cond) problems.push(label); };

function makeEnv({ failWrites = 0 } = {}) {
  let store = {};
  let failures = failWrites;
  const localStorage = {
    getItem: (k) => (k in store ? store[k] : null),
    setItem: (k, v) => {
      if (failures > 0) { failures--; throw new Error("QuotaExceededError"); }
      store[k] = v;
    },
    removeItem: (k) => { delete store[k]; }
  };
  const rendered = [];
  const chatMessages = {
    _children: [],
    set innerHTML(_v) { this._children = []; rendered.length = 0; },
    appendChild(el) { this._children.push(el); }
  };
  const document = { createElement: () => ({ appendChild() {} }) };
  const chatInput = { focus() {} };
  const appendChat = (role, text, sources) => rendered.push({ role, text, sources });

  const factory = new Function(
    "localStorage", "chatMessages", "chatInput", "appendChat", "document",
    block + `
    return { recordTurn, loadTranscript, restoreTranscript, historyPayload,
             clearTranscript, saveTranscript, trimForStorage,
             get TRANSCRIPT() { return TRANSCRIPT; },
             set TRANSCRIPT(v) { TRANSCRIPT = v; } };`
  );
  const api = factory(localStorage, chatMessages, chatInput, appendChat, document);
  return { api, store: () => store, rendered, chatMessages };
}

// --- round trip through storage, then replay on a simulated reload ---
{
  const env = makeEnv();
  env.api.recordTurn("user", "How many for S.4.1?");
  env.api.recordTurn("bot", "75 questions.", [{ id: "qna-1", question: "q", answer_preview: "p", similarity: 1 }]);
  check(env.api.loadTranscript().length === 2, "two turns persisted");

  env.rendered.length = 0;
  env.api.TRANSCRIPT = [];
  env.api.restoreTranscript();
  check(env.rendered.length === 2, "restore replays both turns, got " + env.rendered.length);
  check(env.rendered[1].sources.length === 1, "restored answer keeps its sources");
}

// --- history payload maps bot -> assistant ---
{
  const a = makeEnv().api;
  a.recordTurn("user", "q1");
  a.recordTurn("bot", "a1", []);
  const h = a.historyPayload();
  check(JSON.stringify(h) === JSON.stringify([
    { role: "user", content: "q1" }, { role: "assistant", content: "a1" }
  ]), "historyPayload maps roles for the wire format, got " + JSON.stringify(h));
}

// --- caps ---
{
  const a = makeEnv().api;
  for (let i = 0; i < 60; i++) a.recordTurn("user", "turn" + i);
  const stored = a.loadTranscript();
  check(stored.length === 40, "stored turns capped at 40, got " + stored.length);
  check(stored[0].text === "turn20", "cap keeps the newest, got " + stored[0].text);

  const many = [{ role: "bot", text: "t", sources: Array.from({ length: 30 }, (_, i) => ({
    id: "s" + i, question: "q", answer_preview: "x".repeat(2000), similarity: 1 })) }];
  const trimmed = a.trimForStorage(many);
  check(trimmed[0].sources.length === 10, "sources capped at 10, got " + trimmed[0].sources.length);
  check(trimmed[0].sources[0].answer_preview.length === 400, "preview truncated to 400");
}

// --- quota rejection sheds oldest instead of losing everything ---
{
  const env = makeEnv({ failWrites: 1 });
  env.api.TRANSCRIPT = Array.from({ length: 10 }, (_, i) => ({ role: "user", text: "t" + i, sources: [] }));
  env.api.saveTranscript();
  const kept = JSON.parse(env.store()["cmc-chat-v1"]);
  check(kept.length === 5, "halves on quota error, got " + kept.length);
  check(kept[kept.length - 1].text === "t9", "keeps the newest after shedding");
}

// --- clear wipes memory, storage, and DOM ---
{
  const env = makeEnv();
  env.api.recordTurn("user", "hi");
  env.api.clearTranscript();
  check(env.api.TRANSCRIPT.length === 0, "clear empties the array");
  check(env.store()["cmc-chat-v1"] === undefined, "clear removes the storage key");
  check(env.chatMessages._children.length === 1, "clear leaves only the intro bubble");
}

// --- corrupt / hostile storage must not throw ---
{
  const env = makeEnv();
  env.store()["cmc-chat-v1"] = "{not json";
  check(env.api.loadTranscript().length === 0, "malformed JSON ignored");
  env.store()["cmc-chat-v1"] = JSON.stringify({ nope: 1 });
  check(env.api.loadTranscript().length === 0, "non-array ignored");
  env.store()["cmc-chat-v1"] = JSON.stringify([{ role: "system", text: "injected" }, { role: "user", text: "ok" }]);
  const loaded = env.api.loadTranscript();
  check(loaded.length === 1 && loaded[0].text === "ok", "unknown roles filtered out");
}

if (problems.length) {
  console.log("FAIL:\n - " + problems.join("\n - "));
  process.exit(1);
}
console.log("transcript persistence: all checks passed");
