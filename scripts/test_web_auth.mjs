// Executable tests for the Cognito PKCE layer inline in public/index.html. The page
// has no build step and no test framework, so this loads the real inline script into
// a minimal mocked browser (fetch, location, history, crypto, sessionStorage, and a
// controllable clock) via node:vm and drives it exactly as a real session would.
//
// scripts/check_web.mjs proves the script is syntactically valid and dependency-free;
// this proves the PKCE state machine actually behaves: config fallback and fail-closed
// validation, sign-in/sign-up redirects, callback verification, token exchange and
// refresh, the terminal 401 transition, and logout - none of which a syntax check can see.

import { readFileSync } from "node:fs";
import { webcrypto } from "node:crypto";
import { createContext, runInContext } from "node:vm";

const VIEW_IDS = [
  "view-landing",
  "view-generating",
  "view-draft",
  "view-active",
  "view-approval",
  "view-rejected",
  "view-signedout",
];

function loadPatchedScript() {
  const html = readFileSync("public/index.html", "utf8");
  const script = html.match(/<script>([\s\S]*?)<\/script>/)?.[1];
  if (!script) {
    throw new Error("public/index.html does not contain an inline script");
  }
  const bootCalls = (script.match(/\bboot\(\);/g) ?? []).length;
  if (bootCalls !== 1) {
    throw new Error(`expected exactly one boot(); call to make observable, found ${bootCalls}`);
  }
  // The only change from the real page: capture boot()'s promise so the harness can
  // await it. Production behaviour is unchanged - the browser never reads this global.
  return script.replace(/\bboot\(\);/, "globalThis.__booted = boot();");
}

function jsonResponse(status, body) {
  return {
    ok: status >= 200 && status < 300,
    status,
    json: async () => body,
  };
}

const HEALTH_OK = jsonResponse(200, { status: "ok", mode: "demo", storage: "memory_demo" });

function makeConfig({ withAuth = true, authOverrides = {}, topOverrides = {} } = {}) {
  return {
    schemaVersion: 1,
    apiBaseUrl: "/api",
    auth: withAuth
      ? {
          authorizationEndpoint: "https://auth.example.com/oauth2/authorize",
          signInEndpoint: "https://auth.example.com/login",
          signUpEndpoint: "https://auth.example.com/signup",
          tokenEndpoint: "https://auth.example.com/oauth2/token",
          logoutEndpoint: "https://auth.example.com/logout",
          clientId: "client-123",
          redirectUri: "https://demo.example/",
          logoutUri: "https://demo.example/",
          responseType: "code",
          pkceRequired: true,
          scopes: ["openid", "email", "adaptsg/journeys.read"],
          selfSignUpEnabled: true,
          ...authOverrides,
        }
      : null,
    ...topOverrides,
  };
}

function base64UrlEncode(bytes) {
  let binary = "";
  for (const byte of bytes) binary += String.fromCharCode(byte);
  return Buffer.from(binary, "binary").toString("base64").replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

class FakeElement {
  constructor(id, { className = "", hidden = false } = {}) {
    this.id = id;
    this.className = className;
    this.hidden = hidden;
    this.value = "";
    this.textContent = "";
    this.innerHTML = "";
    this.onclick = null;
    this.focusCount = 0;
  }

  focus() {
    this.focusCount += 1;
  }
}

function makeDocument() {
  const elements = new Map();
  for (const id of VIEW_IDS) {
    elements.set(id, new FakeElement(id, { className: "view", hidden: true }));
  }
  return {
    getElementById(id) {
      if (!elements.has(id)) elements.set(id, new FakeElement(id));
      return elements.get(id);
    },
    querySelectorAll(selector) {
      if (selector !== ".view") return [];
      return [...elements.values()].filter((element) => element.className === "view");
    },
  };
}

function makeLocation(initialUrl) {
  const parsed = new URL(initialUrl);
  return { href: parsed.href, search: parsed.search, pathname: parsed.pathname, hash: parsed.hash };
}

function makeHistory(location) {
  const calls = [];
  return {
    calls,
    replaceState(state, title, url) {
      calls.push({ state, title, url });
      const next = new URL(url, location.href);
      location.href = next.href;
      location.search = next.search;
      location.pathname = next.pathname;
      location.hash = next.hash;
    },
  };
}

function makeSessionStorage() {
  const store = new Map();
  return {
    getItem: (key) => (store.has(key) ? store.get(key) : null),
    setItem: (key, value) => store.set(key, String(value)),
    removeItem: (key) => store.delete(key),
  };
}

function makeClockDate(initialMs) {
  let currentMs = initialMs;
  class ClockDate extends Date {
    constructor(...args) {
      if (args.length === 0) super(currentMs);
      else super(...args);
    }

    static now() {
      return currentMs;
    }
  }
  return ClockDate;
}

async function runScenario({ initialUrl = "https://demo.example/", fetchResponses = [], seed } = {}) {
  const script = loadPatchedScript();
  const fetchLog = [];
  const queue = [...fetchResponses];
  async function fetchMock(url, init) {
    fetchLog.push({ url, init });
    if (queue.length === 0) throw new Error(`unexpected fetch call to ${url}`);
    const entry = queue.shift();
    return typeof entry === "function" ? entry(url, init) : entry;
  }

  const location = makeLocation(initialUrl);
  const history = makeHistory(location);
  const sessionStorage = makeSessionStorage();
  const document = makeDocument();

  const sandbox = {
    document,
    location,
    history,
    sessionStorage,
    fetch: fetchMock,
    crypto: webcrypto,
    URL,
    URLSearchParams,
    TextEncoder,
    btoa,
    atob,
    Date: makeClockDate(1_700_000_000_000),
    console,
  };
  if (seed) seed(sandbox);

  const context = createContext(sandbox);
  runInContext(script, context);
  await context.__booted;

  return { context, fetchLog, historyCalls: history.calls, sessionStorage, location };
}

function assert(condition, message) {
  if (!condition) throw new Error(message);
}

const results = [];
async function test(name, fn) {
  try {
    await fn();
    results.push({ name, ok: true });
  } catch (error) {
    results.push({ name, ok: false, error });
  }
}

// --- Configuration: local fallback vs. fail-closed network/JSON/schema errors -----

await test("a 404 for /runtime-config.json falls back to the local no-auth demo config", async () => {
  const { context, fetchLog } = await runScenario({
    fetchResponses: [jsonResponse(404, {}), HEALTH_OK],
  });
  assert(context.cfg !== null && context.cfg.auth === null, "auth must be null on the local fallback");
  assert(context.cfg.apiBaseUrl === "/api", "apiBaseUrl must fall back to /api");
  assert(context.document.getElementById("view-landing").hidden === false, "the landing view must show");
  assert(context.document.getElementById("view-signedout").hidden === true, "sign-in must be skipped entirely");
  assert(context.document.getElementById("trip-navigation").hidden === false, "local demo navigation must remain available");
  assert(context.document.getElementById("mode-banner").hidden === false, "the planning view must show provenance");
  assert(fetchLog.length === 2, "the fallback must still be followed by the ordinary health check");
});

await test("a config network failure fails closed rather than silently disabling login", async () => {
  const { context, fetchLog } = await runScenario({
    fetchResponses: [() => { throw new Error("network unreachable"); }],
  });
  assert(context.cfg === null, "cfg must stay unset after a configuration error");
  assert(context.document.getElementById("view-landing").hidden === true, "no view may activate after a config error");
  assert(context.document.getElementById("view-signedout").hidden === true);
  assert(context.document.getElementById("alert").hidden === false, "the configuration error must be surfaced");
  assert(fetchLog.length === 1, "a failed config fetch must not be followed by any other request");
});

await test("a non-404 HTTP failure for /runtime-config.json fails closed", async () => {
  const { context } = await runScenario({ fetchResponses: [jsonResponse(500, {})] });
  assert(context.cfg === null);
  assert(context.document.getElementById("view-signedout").hidden === true);
});

await test("invalid JSON in /runtime-config.json fails closed", async () => {
  const malformed = { ok: true, status: 200, json: async () => { throw new SyntaxError("bad json"); } };
  const { context } = await runScenario({ fetchResponses: [malformed] });
  assert(context.cfg === null);
});

await test("a schema violation in /runtime-config.json fails closed", async () => {
  const badConfig = makeConfig({ authOverrides: { tokenEndpoint: undefined } });
  const { context } = await runScenario({ fetchResponses: [jsonResponse(200, badConfig)] });
  assert(context.cfg === null);
});

// --- Sign-in / sign-up redirects ---------------------------------------------------

await test("sign-in and sign-up redirect with correct params and distinct PKCE material", async () => {
  const config = makeConfig();
  const { context, sessionStorage } = await runScenario({ fetchResponses: [jsonResponse(200, config)] });
  assert(context.document.getElementById("view-signedout").hidden === false, "no session must land signed-out");
  assert(context.document.getElementById("trip-navigation").hidden === true, "signed-out users must not see journey navigation");
  assert(context.document.getElementById("sign-out").hidden === true, "signed-out users must not see sign out");
  assert(context.document.getElementById("mode-banner").hidden === true, "signed-out users must not see unresolved provenance");

  await context.beginSignIn("signin");
  const firstVerifier = sessionStorage.getItem("adaptsg.pkce.verifier");
  const firstState = sessionStorage.getItem("adaptsg.pkce.state");
  const firstParams = new URL(context.location.href).searchParams;
  assert(context.location.href.startsWith(`${config.auth.signInEndpoint}?`), "sign-in must redirect to signInEndpoint");
  assert(firstParams.get("client_id") === config.auth.clientId);
  assert(firstParams.get("response_type") === "code");
  assert(firstParams.get("redirect_uri") === config.auth.redirectUri);
  assert(firstParams.get("scope") === config.auth.scopes.join(" "));
  assert(firstParams.get("code_challenge_method") === "S256");
  assert(firstParams.get("state") === firstState);
  const expectedChallenge = base64UrlEncode(
    new Uint8Array(await webcrypto.subtle.digest("SHA-256", new TextEncoder().encode(firstVerifier))),
  );
  assert(firstParams.get("code_challenge") === expectedChallenge, "code_challenge must be S256 of the verifier");

  await context.beginSignIn("signup");
  const secondVerifier = sessionStorage.getItem("adaptsg.pkce.verifier");
  const secondState = sessionStorage.getItem("adaptsg.pkce.state");
  assert(context.location.href.startsWith(`${config.auth.signUpEndpoint}?`), "sign-up must redirect to signUpEndpoint");
  assert(secondVerifier !== firstVerifier, "each attempt must use a fresh verifier");
  assert(secondState !== firstState, "each attempt must use a fresh state");
});

// --- Callback: missing/mismatched state and OAuth errors, before the code is used -

await test("a missing state on callback is rejected before the code is used", async () => {
  const config = makeConfig();
  const { context, fetchLog } = await runScenario({
    initialUrl: "https://demo.example/?code=AUTHCODE&state=unknown-state",
    fetchResponses: [jsonResponse(200, config)],
  });
  assert(context.accessToken === null, "a rejected callback must not hold a token");
  assert(context.document.getElementById("view-signedout").hidden === false);
  assert(fetchLog.length === 1, "no token request may be made when state cannot be verified");
});

await test("a state that does not match this session's is rejected before the code is used", async () => {
  const config = makeConfig();
  const { context, fetchLog } = await runScenario({
    initialUrl: "https://demo.example/?code=AUTHCODE&state=returned-state",
    fetchResponses: [jsonResponse(200, config)],
    seed: (sandbox) => {
      sandbox.sessionStorage.setItem("adaptsg.pkce.state", "stored-state");
      sandbox.sessionStorage.setItem("adaptsg.pkce.verifier", "stored-verifier");
    },
  });
  assert(context.accessToken === null);
  assert(fetchLog.length === 1);
});

await test("an OAuth error callback is rejected with no token request, even with a valid state", async () => {
  const config = makeConfig();
  const { context, fetchLog } = await runScenario({
    initialUrl: "https://demo.example/?error=access_denied&error_description=User+cancelled&state=matching-state",
    fetchResponses: [jsonResponse(200, config)],
    seed: (sandbox) => {
      sandbox.sessionStorage.setItem("adaptsg.pkce.state", "matching-state");
      sandbox.sessionStorage.setItem("adaptsg.pkce.verifier", "stored-verifier");
    },
  });
  assert(context.accessToken === null);
  assert(fetchLog.length === 1, "an OAuth error callback must never reach the token endpoint");
});

// --- Callback: successful exchange -------------------------------------------------

await test("a successful callback exchanges the code for the access token, never the ID token, and cleans the URL", async () => {
  const config = makeConfig();
  const { context, fetchLog, historyCalls, sessionStorage } = await runScenario({
    initialUrl: "https://demo.example/?keep=1&code=AUTHCODE&state=matching-state#frag",
    fetchResponses: [
      jsonResponse(200, config),
      jsonResponse(200, {
        access_token: "ACCESS1",
        id_token: "ID1",
        refresh_token: "REFRESH1",
        expires_in: 3600,
        token_type: "Bearer",
      }),
      HEALTH_OK,
    ],
    seed: (sandbox) => {
      sandbox.sessionStorage.setItem("adaptsg.pkce.state", "matching-state");
      sandbox.sessionStorage.setItem("adaptsg.pkce.verifier", "the-verifier");
    },
  });
  assert(context.accessToken === "ACCESS1", "the access token must be held");
  assert(context.accessToken !== "ID1", "the ID token must never be used as the session token");
  assert(context.heldRefreshToken === "REFRESH1");
  const tokenCall = fetchLog[1];
  assert(tokenCall.url === config.auth.tokenEndpoint);
  const body = new URLSearchParams(tokenCall.init.body);
  assert(body.get("grant_type") === "authorization_code");
  assert(body.get("code") === "AUTHCODE");
  assert(body.get("code_verifier") === "the-verifier");
  assert(body.get("client_secret") === null, "the exchange must never send a client secret");
  assert(sessionStorage.getItem("adaptsg.pkce.state") === null, "PKCE state must be cleared after use");
  assert(sessionStorage.getItem("adaptsg.pkce.verifier") === null, "PKCE verifier must be cleared after use");
  assert(historyCalls.length === 1);
  assert(context.location.search === "?keep=1", "unrelated query parameters must survive the cleanup");
  assert(context.location.hash === "#frag", "the fragment must survive the cleanup");
  assert(context.document.getElementById("view-landing").hidden === false);
});

// --- Token expiry, refresh, and the terminal 401 transition ------------------------

await test("an expired access token is refreshed once before an authenticated request", async () => {
  const config = makeConfig();
  const { context, fetchLog } = await runScenario({
    fetchResponses: [
      jsonResponse(200, config),
      jsonResponse(200, { access_token: "NEW", refresh_token: "REFRESH-NEW", expires_in: 3600 }),
      jsonResponse(200, { journey_id: "J1", version: 1, status: "draft", warnings: [] }),
    ],
  });
  context.accessToken = "OLD";
  context.heldRefreshToken = "REFRESH-OLD";
  context.accessTokenExpiresAt = 0;

  const result = await context.mutate("/api/journeys", { prompt: "x", journey_date: "2026-01-01" }, "key-1");

  assert(fetchLog.length === 3);
  const refreshCall = fetchLog[1];
  assert(refreshCall.url === config.auth.tokenEndpoint);
  const refreshBody = new URLSearchParams(refreshCall.init.body);
  assert(refreshBody.get("grant_type") === "refresh_token");
  assert(refreshBody.get("refresh_token") === "REFRESH-OLD");
  const apiCall = fetchLog[2];
  assert(apiCall.init.headers.Authorization === "Bearer NEW");
  assert(apiCall.init.headers["Idempotency-Key"] === "key-1");
  assert(context.accessToken === "NEW");
  assert(context.heldRefreshToken === "REFRESH-NEW");
  assert(result.journey_id === "J1");
});

await test("a refresh response without a new refresh token retains the old one", async () => {
  const config = makeConfig();
  const { context } = await runScenario({
    fetchResponses: [jsonResponse(200, config), jsonResponse(200, { access_token: "NEW2", expires_in: 3600 })],
  });
  context.accessToken = "OLD2";
  context.heldRefreshToken = "REFRESH-KEEP";
  context.accessTokenExpiresAt = 0;

  await context.ensureFreshAccessToken();

  assert(context.accessToken === "NEW2");
  assert(context.heldRefreshToken === "REFRESH-KEEP", "a refresh token must be retained when not rotated");
});

await test("a reactive 401 triggers exactly one refresh-and-retry", async () => {
  const config = makeConfig();
  const { context, fetchLog } = await runScenario({
    fetchResponses: [
      jsonResponse(200, config),
      jsonResponse(401, { detail: "expired" }),
      jsonResponse(200, { access_token: "NEW3", refresh_token: "REFRESH3", expires_in: 3600 }),
      jsonResponse(200, { journey_id: "J2", version: 1, status: "draft", warnings: [] }),
    ],
  });
  context.accessToken = "STILL-VALID-PER-CLOCK";
  context.heldRefreshToken = "REFRESH-2";
  context.accessTokenExpiresAt = Number.MAX_SAFE_INTEGER;

  const result = await context.mutate("/api/journeys", { prompt: "x", journey_date: "2026-01-01" }, "key-2");

  assert(fetchLog.length === 4);
  assert(fetchLog[2].url === config.auth.tokenEndpoint, "the 401 must trigger a refresh");
  assert(fetchLog[3].init.headers.Authorization === "Bearer NEW3");
  assert(result.journey_id === "J2");
  assert(context.accessToken === "NEW3");
});

await test("a failed refresh after a reactive 401 clears the session with a terminal transition", async () => {
  const config = makeConfig();
  const { context, fetchLog } = await runScenario({
    fetchResponses: [
      jsonResponse(200, config),
      jsonResponse(401, { detail: "expired" }),
      jsonResponse(400, { error: "invalid_grant" }),
    ],
  });
  context.accessToken = "STILL-VALID-PER-CLOCK";
  context.heldRefreshToken = "REFRESH-3";
  context.accessTokenExpiresAt = Number.MAX_SAFE_INTEGER;

  let caught = null;
  try {
    await context.mutate("/api/journeys", { prompt: "x", journey_date: "2026-01-01" }, "key-3");
  } catch (error) {
    caught = error;
  }

  assert(caught?.code === "authentication_expired", "a failed refresh must surface the terminal auth-expired code");
  assert(context.accessToken === null);
  assert(context.heldRefreshToken === null);
  assert(fetchLog.length === 3, "no second API retry may happen once refresh has failed");
});

await test("a 401 with no refresh token held is immediately terminal", async () => {
  const config = makeConfig();
  const { context, fetchLog } = await runScenario({
    fetchResponses: [jsonResponse(200, config), jsonResponse(401, { detail: "expired" })],
  });
  context.accessToken = "SOME-TOKEN";
  context.heldRefreshToken = null;
  context.accessTokenExpiresAt = Number.MAX_SAFE_INTEGER;

  let caught = null;
  try {
    await context.mutate("/api/journeys", { prompt: "x", journey_date: "2026-01-01" }, "key-4");
  } catch (error) {
    caught = error;
  }

  assert(caught?.code === "authentication_expired");
  assert(fetchLog.length === 2, "no refresh attempt may be made without a refresh token");
});

// --- Authenticated requests and logout ---------------------------------------------

await test("an authenticated request attaches the bearer token, and logout clears the session", async () => {
  const config = makeConfig();
  const { context, fetchLog } = await runScenario({
    fetchResponses: [jsonResponse(200, config), HEALTH_OK],
  });
  context.accessToken = "SESSION-TOKEN";
  context.heldRefreshToken = "SESSION-REFRESH";
  context.accessTokenExpiresAt = Number.MAX_SAFE_INTEGER;

  await context.request("/api/health", "GET", null, null);
  assert(fetchLog.at(-1).init.headers.Authorization === "Bearer SESSION-TOKEN");

  await context.signOut();
  assert(context.accessToken === null);
  assert(context.heldRefreshToken === null);
  assert(context.location.href.startsWith(`${config.auth.logoutEndpoint}?`));
  const logoutParams = new URL(context.location.href).searchParams;
  assert(logoutParams.get("client_id") === config.auth.clientId);
  assert(logoutParams.get("logout_uri") === config.auth.logoutUri);
  assert(typeof context.localStorage === "undefined", "the client must never reference localStorage");
});

// --- Self-signup visibility ---------------------------------------------------------

await test("selfSignUpEnabled=false hides the create-account action", async () => {
  const config = makeConfig({ authOverrides: { selfSignUpEnabled: false } });
  const { context } = await runScenario({ fetchResponses: [jsonResponse(200, config)] });
  assert(context.document.getElementById("signup").hidden === true);
});

await test("selfSignUpEnabled=true shows the create-account action", async () => {
  const config = makeConfig({ authOverrides: { selfSignUpEnabled: true } });
  const { context } = await runScenario({ fetchResponses: [jsonResponse(200, config)] });
  assert(context.document.getElementById("signup").hidden === false);
});

// --- Idempotency and version discipline survive under auth -------------------------

await test("a mutating call retains its Idempotency-Key header and expected_version field", async () => {
  const config = makeConfig({ withAuth: false });
  const { context, fetchLog } = await runScenario({
    fetchResponses: [
      jsonResponse(200, config),
      HEALTH_OK,
      jsonResponse(200, { journey_id: "J3", version: 3, status: "active" }),
    ],
  });

  await context.mutate(
    "/api/journeys/J3/decision",
    { target_id: "T1", decision: "approve", expected_version: 2 },
    "idem-xyz",
  );

  const call = fetchLog.at(-1);
  assert(call.init.method === "POST");
  assert(call.init.headers["Idempotency-Key"] === "idem-xyz");
  const body = JSON.parse(call.init.body);
  assert(body.expected_version === 2);
});

for (const result of results) {
  console.log(`${result.ok ? "PASS" : "FAIL"} ${result.name}${result.ok ? "" : `: ${result.error.message}`}`);
}

const failed = results.filter((result) => !result.ok);
if (failed.length > 0) {
  throw new Error(`${failed.length} of ${results.length} browser authentication tests failed`);
}
console.log(`All ${results.length} browser authentication tests passed.`);
