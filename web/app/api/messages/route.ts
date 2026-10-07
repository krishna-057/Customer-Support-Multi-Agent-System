import { NextRequest } from "next/server";
import { readBoundedBody } from "../../../lib/server";

export const runtime = "nodejs";

export async function POST(request: NextRequest) {
  const authorization = request.headers.get("authorization");
  if (!authorization?.startsWith("Bearer ") || authorization.length > 2055) {
    return Response.json({ error: "Customer session required" }, { status: 401 });
  }
  if (!request.headers.get("content-type")?.startsWith("application/json")) {
    return Response.json({ error: "JSON body required" }, { status: 415 });
  }
  const body = await readBoundedBody(request, 1500);
  if (body === null) return Response.json({ error: "Message too long" }, { status: 413 });
  const api = process.env.AGENT_API_URL ?? "http://127.0.0.1:8000";
  let upstream: Response;
  try {
    upstream = await fetch(new URL("/v1/support/messages/stream", api), {
      method: "POST",
      headers: { authorization, "content-type": "application/json" },
      body,
      cache: "no-store",
      signal: request.signal,
    });
  } catch {
    return Response.json({ error: "Support service unavailable" }, { status: 502 });
  }
  if (!upstream.ok || !upstream.body) {
    return Response.json(
      { error: upstream.status === 401 ? "Customer session expired" : "Support request failed" },
      { status: upstream.status === 401 ? 401 : 502 },
    );
  }
  return new Response(upstream.body, {
    headers: {
      "content-type": "text/event-stream; charset=utf-8",
      "cache-control": "no-store",
      "x-accel-buffering": "no",
    },
  });
}
