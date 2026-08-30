import { readFileSync } from "node:fs";

const html = readFileSync("public/index.html", "utf8");
const script = html.match(/<script>([\s\S]*?)<\/script>/)?.[1];

if (!script) {
  throw new Error("public/index.html does not contain an inline script");
}

// Parsing without executing catches JavaScript syntax failures in CI.
new Function(script);
console.log("Vercel browser JavaScript syntax is valid.");

