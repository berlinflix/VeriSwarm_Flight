import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

async function render() {
  const workerUrl = new URL("../dist/server/index.js", import.meta.url);
  workerUrl.searchParams.set("test", `${process.pid}-${Date.now()}`);
  const { default: worker } = await import(workerUrl.href);

  return worker.fetch(
    new Request("http://localhost/", {
      headers: { accept: "text/html" },
    }),
    {
      ASSETS: {
        fetch: async () => new Response("Not found", { status: 404 }),
      },
    },
    {
      waitUntil() {},
      passThroughOnException() {},
    },
  );
}

test("server-renders the VeriSwarm SIH project story", async () => {
  const response = await render();
  assert.equal(response.status, 200);
  assert.match(response.headers.get("content-type") ?? "", /^text\/html\b/i);

  const html = await response.text();
  assert.match(
    html,
    /<title>VeriSwarm Rescue \| Search wider\. Verify before acting\.<\/title>/i,
  );
  assert.match(html, /SIH 26177/);
  assert.match(html, /Search wider\./);
  assert.match(html, /Operation Varuna/);
  assert.match(html, /Five coordinated roles/);
  assert.match(html, /Fail-closed safety/);
  assert.match(html, /WORKING NOW/);
  assert.doesNotMatch(html, /Your site is taking shape|Building your site/);
});

test("keeps project claims and presentation controls in source", async () => {
  const [page, layout, css, packageJson] = await Promise.all([
    readFile(new URL("../app/page.tsx", import.meta.url), "utf8"),
    readFile(new URL("../app/layout.tsx", import.meta.url), "utf8"),
    readFile(new URL("../app/globals.css", import.meta.url), "utf8"),
    readFile(new URL("../package.json", import.meta.url), "utf8"),
  ]);

  assert.match(page, /"use client"/);
  assert.match(page, /pointermove/);
  assert.match(page, /window\.scrollTo\(\{ top: 0, behavior: "smooth" \}\)/);
  assert.match(page, /className="button primary return-button"/);
  assert.match(page, /role="tablist"/);
  assert.match(page, /PERSON CANDIDATE/);
  assert.match(page, /Illustrative interface state/);
  assert.match(page, /NEXT INTEGRATION/);
  assert.match(layout, /VeriSwarm Rescue/);
  assert.match(css, /prefers-reduced-motion:\s*reduce/);
  assert.match(css, /@media \(max-width:\s*760px\)/);
  assert.doesNotMatch(packageJson, /react-loading-skeleton/);
});
