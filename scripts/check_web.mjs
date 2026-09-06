import { existsSync, readFileSync } from "node:fs";

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
const styles = html.match(/<style>([\s\S]*?)<\/style>/)?.[1] ?? "";

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

// Cascade gates. The page switches views with the hidden attribute alone, and CSS is the
// half of that contract no other check reads -- which is how commit 80ca854 shipped a
// stylesheet with no [hidden] rule and painted all seven views down one page.

require(
  /\[hidden\]\s*\{[^}]*display\s*:\s*none\s*!important/.test(styles),
  "[hidden]{display:none!important} must be declared, or any author `display` (.work, "
    + ".band, .btn all set one) beats the UA rule and every view renders at once",
);

require(
  /id="trip-navigation"/.test(markup)
    && /\$\(['"]trip-navigation['"]\)\.hidden\s*=\s*authEnabled\s*&&\s*\(!signedIn\s*\|\|\s*!journeyNavigationEnabled\)/.test(script),
  "journey navigation must be hidden while Cognito is enabled and the user is signed out",
);

require(
  /\.auth-intro h1:focus\s*\{\s*outline\s*:\s*none\s*\}/.test(styles),
  "the non-interactive signed-out heading must not paint a control-style focus outline",
);

require(
  /function enterSignedOutState\(\)[\s\S]*?\$\(['"]mode-banner['"]\)\.hidden\s*=\s*true/.test(script)
    && /function bootLanding\(\)[\s\S]*?\$\(['"]mode-banner['"]\)\.hidden\s*=\s*false/.test(script),
  "the provenance banner must be shown with authenticated planning data, not unresolved on login",
);

require(
  /read\(['"]\/api\/v1\/consents\/journey-planning\/status['"]\)/.test(script)
    && /mutate\(\s*['"]\/api\/v1\/consents['"]/.test(script)
    && /data_categories:\s*pendingConsentStatus\.categories/.test(script)
    && /policy_version:\s*pendingConsentStatus\.policy_version/.test(script),
  "the client must discover and submit the server-owned consent contract before planning",
);

// A rule inside @media carries no extra specificity, so a base rule for the same selector
// placed after the block wins at every viewport. That is how the mobile top bar went dead.
const mediaRanges = [...styles.matchAll(/@media[^{]*\{[\s\S]*?\n\}/g)]
  .map((match) => [match.index, match.index + match[0].length]);
const inMedia = (index) => mediaRanges.some(([from, to]) => index >= from && index < to);

const displayRules = [];
for (const match of styles.matchAll(/([^{}@]+)\{([^{}]*)\}/g)) {
  if (!/(^|[;\s])display\s*:/.test(match[2])) continue;
  for (const selector of match[1].split(",").map((s) => s.trim()).filter(Boolean)) {
    displayRules.push({ selector, index: match.index, media: inMedia(match.index) });
  }
}
require(displayRules.length > 0, "no `display` rules were parsed; the stylesheet scan broke");
for (const rule of displayRules.filter((r) => r.media)) {
  const shadowed = displayRules.some(
    (other) => !other.media && other.selector === rule.selector && other.index > rule.index,
  );
  require(
    !shadowed,
    `\`${rule.selector}\` sets display inside @media but a base rule for it appears later; `
      + "media queries add no specificity, so the base rule wins at every viewport",
  );
}

// --ash is #adadad: 2.2:1 on white. It is a border and fill token, and the easiest rule
// in the system to undo by accident, because it reads as "a grey" at a glance. The lookbehind
// on the property name matters: `color` also appears inside `border-color`/`background-color`.
const ashText = [...styles.matchAll(/(?:^|[;{\s])color\s*:\s*([^;}]+)/g)]
  .map(([, value]) => value.trim())
  .filter((value) => /var\(--ash\)|#adadad/i.test(value));
require(
  ashText.length === 0,
  `--ash carries text in ${ashText.join(", ")}; at 2.2:1 it is a borders-and-fills token `
    + "only -- use --muted (4.9:1) or --disabled (4.5:1) for text",
);

// A renamed or unexported asset should fail here rather than render as a broken image.
for (const [, src] of markup.matchAll(/\ssrc="(\/[^"]+)"/g)) {
  require(
    existsSync(`public${src}`),
    `${src} is referenced but missing from public/, so it will 404 once deployed`,
  );
}

// The two front ends must tell one provenance story. Both derive their wording from
// adaptsg.presentation, so the badge text is compared directly rather than restated.
// Contract gates. The journey API is stateful: the server owns the itinerary, every
// mutation is keyed, and a transport fault is not a safety verdict. A syntax check cannot
// see any of that, which is how the client once drifted a whole contract behind the routes.

// Network access is split into three explicit helpers: config loading, OAuth token
// exchange/refresh, and the API request() helper. Every literal /api/... call must
// still flow through read() or mutate(), which flow through request(), so bearer and
// idempotency headers can never be forgotten at a call site.
require(
  (script.match(/fetch\(/g) ?? []).length === 3,
  "network access must stay split into exactly three helpers: config, token, and API request",
);

const apiLiterals = [...script.matchAll(/[`'"](\/api\/[^`'"]*)[`'"]/g)];
const helperCalls = [...script.matchAll(/\b(?:read|mutate)\(\s*(?:`[^`]*`|'[^']*'|"[^"]*")/g)];
require(
  apiLiterals.length > 0 && apiLiterals.length === helperCalls.length,
  "every literal /api/... call must flow through read() or mutate(), or a call site can skip its headers",
);

const requestFn = script.match(/async function request\([^)]*\)\s*\{[\s\S]*?\n  \}/)?.[0] ?? "";
require(
  requestFn.length > 0,
  "the API request() helper must exist",
);
require(
  /['"]Authorization['"]\]?\s*=/.test(requestFn)
    && !/['"]Authorization['"]\]?\s*=/.test(script.replace(requestFn, "")),
  "only the API request() helper may attach the Authorization header",
);
require(
  /['"]Idempotency-Key['"]\]?\s*=/.test(requestFn) && !/['"]Idempotency-Key['"]\]?\s*=/.test(script.replace(requestFn, "")),
  "only the API request() helper may set the Idempotency-Key header the API requires",
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
