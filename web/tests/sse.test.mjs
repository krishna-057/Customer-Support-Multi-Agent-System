import assert from "node:assert/strict";
import test from "node:test";
import { readEvents } from "../lib/sse.ts";

function chunks(parts) {
  return new ReadableStream({
    start(controller) {
      for (const part of parts) controller.enqueue(new TextEncoder().encode(part));
      controller.close();
    },
  });
}

test("parses SSE frames split across network chunks", async () => {
  const events = [];
  for await (const event of readEvents(chunks([
    'event: routing\ndata: {"int',
    'ent":"technical"}\r',
    '\n\r\nevent: completed\ndata: {"answer":"ok"}\n\n',
  ]))) events.push(event);
  assert.deepEqual(events, [
    { event: "routing", data: { intent: "technical" } },
    { event: "completed", data: { answer: "ok" } },
  ]);
});

test("rejects oversized unfinished events", async () => {
  await assert.rejects(async () => {
    for await (const event of readEvents(chunks(["x".repeat(65537)]))) void event;
  }, /too large/);
});
