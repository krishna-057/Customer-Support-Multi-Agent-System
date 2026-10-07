import assert from "node:assert/strict";
import test from "node:test";
import { readBoundedBody } from "../lib/server.ts";

test("reads a small proxy body", async () => {
  const request = new Request("http://localhost", { method: "POST", body: "hello" });
  assert.equal(await readBoundedBody(request, 10), "hello");
});

test("rejects an oversized proxy body", async () => {
  const request = new Request("http://localhost", { method: "POST", body: "x".repeat(11) });
  assert.equal(await readBoundedBody(request, 10), null);
});
