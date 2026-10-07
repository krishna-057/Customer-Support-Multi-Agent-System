import { NextRequest } from "next/server";
import { readBoundedBody } from "../../../../lib/server";

export const runtime = "nodejs";

const uuid = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

export async function POST(request: NextRequest) {
  const authorization = request.headers.get("authorization");
  if (!authorization?.startsWith("Bearer ") || authorization.length > 2055) {
    return Response.json({ error: "Administrator session required" }, { status: 401 });
  }
  if (!request.headers.get("content-type")?.startsWith("application/json")) {
    return Response.json({ error: "JSON body required" }, { status: 415 });
  }
  const raw = await readBoundedBody(request, 1000);
  if (!raw) return Response.json({ error: "Invalid decision" }, { status: 400 });
  let body: Record<string, unknown>;
  try {
    body = JSON.parse(raw);
  } catch {
    return Response.json({ error: "Invalid decision" }, { status: 400 });
  }
  if (!body || typeof body !== "object" || Array.isArray(body)) {
    return Response.json({ error: "Invalid decision" }, { status: 400 });
  }
  const { customer_id, conversation_id, action, request_id, decision } = body;
  if (
    typeof customer_id !== "string" || !uuid.test(customer_id) ||
    typeof conversation_id !== "string" || !uuid.test(conversation_id) ||
    typeof request_id !== "string" || !uuid.test(request_id) ||
    (action !== "refund" && action !== "cancellation") ||
    (decision !== "approve" && decision !== "reject")
  ) return Response.json({ error: "Invalid decision" }, { status: 400 });
  const path = `/v1/admin/customers/${customer_id}/conversations/${conversation_id}/actions/${action}/${request_id}/decision`;
  try {
    const upstream = await fetch(new URL(path, process.env.AGENT_API_URL ?? "http://127.0.0.1:8000"), {
      method: "POST",
      headers: { authorization, "content-type": "application/json" },
      body: JSON.stringify({ decision }),
      cache: "no-store",
      signal: request.signal,
    });
    if (!upstream.ok) {
      const error = upstream.status === 401 ? "Administrator session expired" :
        upstream.status === 409 ? "Request already decided or unavailable" : "Decision failed";
      return Response.json({ error }, { status: upstream.status === 401 ? 401 : upstream.status === 409 ? 409 : 502 });
    }
    return new Response(upstream.body, {
      headers: { "content-type": "application/json", "cache-control": "no-store" },
    });
  } catch {
    return Response.json({ error: "Decision failed" }, { status: 502 });
  }
}
