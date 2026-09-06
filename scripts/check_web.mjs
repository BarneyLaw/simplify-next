import { readFileSync } from "node:fs";

const html = readFileSync("public/index.html", "utf8");
const script = html.match(/<script>([\s\S]*?)<\/script>/)?.[1];

if (!script) {
  throw new Error("public/index.html does not contain an inline script");
}

new Function(script);

// Static accessibility gates for the browser client. The page has no build step and
// CI installs no npm packages, so these are deliberately dependency-free checks of
// the properties Role 3 must not regress.
const failures = [];
let checked = 0;
const require = (condition, message) => {
  checked += 1;
  if (!condition) {
    failures.push(message);
  }
};

const markup = html.replace(/<script>[\s\S]*?<\/script>/, "");

require(
  /<a[^>]+class="skip"[^>]*href="#/.test(markup),
  "a skip link must let keyboard users reach the planning form",
);

require(
  /role="alert"/.test(markup),
  "errors need an assertive live region; role=\"status\" is announced politely",
);

require(
  !/classList\.(add|remove|toggle)\(\s*['"]hidden['"]/.test(script),
  "toggle the hidden attribute rather than a .hidden class so state reaches assistive tech",
);

// Controls injected by script are unreachable announcements unless focus moves to
// them, so every programmatic focus target must actually be focused by name.
const focusTargets = [...html.matchAll(/id="([^"]+)"[^>]*tabindex="-1"/g)].map(([, id]) => id);
require(
  focusTargets.length > 0,
  "injected controls need a tabindex=\"-1\" focus target, or keyboard users are stranded",
);
for (const id of focusTargets) {
  require(
    new RegExp(`\\$\\(['"]${id}['"]\\)\\.focus\\(\\)`).test(script),
    `focus target #${id} is never focused, so the injected region is never announced`,
  );
}

for (const [, attributes] of markup.matchAll(/<table[^>]*>([\s\S]*?)<\/table>/g)) {
  require(
    /<caption>/.test(attributes),
    "every table needs a caption describing its contents",
  );
  const headers = [...attributes.matchAll(/<th\b([^>]*)>/g)];
  require(headers.length > 0, "the itinerary table needs header cells");
  require(
    headers.every(([, attribute]) => /scope="(col|row)"/.test(attribute)),
    "every <th> needs a scope so its column is announced with each cell",
  );
}

for (const [element] of markup.matchAll(/<[^>]*aria-live[^>]*>/g)) {
  const id = element.match(/id="([^"]+)"/)?.[1];
  if (!id) continue;
  const region = markup.match(new RegExp(`<[^>]*id="${id}"[\\s\\S]*?</\\w+>`))?.[0] ?? "";
  require(
    !/<table/.test(region),
    `live region #${id} contains a table; a full re-render would be read aloud`,
  );
}

for (const [, attributes] of markup.matchAll(/<(?:input|textarea|select)\b([^>]*)>/g)) {
  const id = attributes.match(/id="([^"]+)"/)?.[1];
  require(
    id !== undefined && new RegExp(`<label[^>]+for="${id}"`).test(markup),
    `form control ${id ?? "(no id)"} needs an associated <label for>`,
  );
}

for (const [element, inner] of markup.matchAll(/<button\b([^>]*)>([\s\S]*?)<\/button>/g)) {
  const name = inner.replace(/<[^>]*>/g, "").trim() || element.match(/aria-label="([^"]+)"/)?.[1];
  require(Boolean(name), "every button needs a visible label or an aria-label");
}

// The two front ends must tell one provenance story. Both derive their wording from
// adaptsg.presentation, so the badge text is compared directly rather than restated.
// Contract gates. The journey API is stateful: the server owns the itinerary, every
// mutation is keyed, and a transport fault is not a safety verdict. A syntax check cannot
// see any of that, which is how the client once drifted a whole contract behind the routes.

require(
  (script.match(/fetch\(/g) ?? []).length === 1,
  "every request must go through the single fetch helper, or a call site can skip its headers",
);

require(
  (script.match(/['"]POST['"]/g) ?? []).length === 1,
  "POSTs must be issued only by the mutate helper",
);

require(
  /['"]Idempotency-Key['"]\]?\s*=/.test(script),
  "the request helper must set the Idempotency-Key header the API requires",
);

require(
  /function mutate\([^)]*idempotencyKey[^)]*\)\s*\{\s*\n\s*if \(!idempotencyKey\) throw/.test(script),
  "mutate must refuse to send a state-changing request without an idempotency key",
);

require(
  /crypto\.randomUUID\(\)/.test(script),
  "idempotency keys must be unique per action so a retry replays instead of reapplying",
);

require(
  !/\bitinerary\s*:/.test(script),
  "the client must never send an itinerary; it holds a journey id and a version",
);

require(
  /expected_version/.test(script),
  "mutations must carry expected_version so a stale plan is rejected rather than overwritten",
);

require(
  /payload\.code/.test(script),
  "the typed error code must reach the client; detail alone cannot distinguish domain states",
);

const SAFETY_COPY = "did not weaken";
const safetyUses = (script.match(new RegExp(SAFETY_COPY, "g")) ?? []).length;
require(
  safetyUses === 1,
  `the stop-and-ask copy ("${SAFETY_COPY}") must appear once, for one error code only`,
);
if (safetyUses === 1) {
  const preceding = script.slice(0, script.indexOf(SAFETY_COPY));
  const owner = [...preceding.matchAll(/(\w+):\s*\(/g)].at(-1)?.[1];
  require(
    owner === "no_feasible_itinerary",
    `only no_feasible_itinerary may claim nothing was relaxed; "${SAFETY_COPY}" sits under ${owner}`,
  );
}

const presentation = readFileSync("src/adaptsg/presentation.py", "utf8");
for (const [, badge] of presentation.matchAll(/return "((?:DEMO|LIVE) DATA[^"]*)"/g)) {
  require(
    html.includes(badge),
    `browser client is missing the provenance badge used by the Streamlit demo: "${badge}"`,
  );
}

if (failures.length > 0) {
  throw new Error(`Accessibility gate failed:\n  - ${failures.join("\n  - ")}`);
}

console.log(
  `Vercel browser JavaScript syntax is valid and ${checked} accessibility checks passed.`,
);
