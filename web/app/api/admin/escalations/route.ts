import { NextRequest } from "next/server";

export const runtime = "nodejs";

export async function GET(request: NextRequest) {
  const authorization = request.headers.get("authorization");
  if (!authorization?.startsWith("Bearer ") || authorization.length > 2055) {
    return Response.json({ error: "Administrator session required" }, { status: 401 });
  }
  const rawOffset = request.nextUrl.searchParams.get("offset") ?? "0";
  if (!/^\d{1,4}$/.test(rawOffset) || Number(rawOffset) > 1000) {
    return Response.json({ error: "Invalid page" }, { status: 400 });
  }
  try {
    const upstream = await fetch(
      new URL(`/v1/admin/tickets?offset=${rawOffset}`, process.env.AGENT_API_URL ?? "http://127.0.0.1:8000"),
      { headers: { authorization }, cache: "no-store", signal: request.signal },
    );
    if (!upstream.ok) {
      return Response.json(
        { error: upstream.status === 401 ? "Administrator session expired" : "Escalation queue unavailable" },
        { status: upstream.status === 401 ? 401 : 502 },
      );
    }
    return new Response(upstream.body, {
      headers: { "content-type": "application/json", "cache-control": "no-store" },
    });
  } catch {
    return Response.json({ error: "Escalation queue unavailable" }, { status: 502 });
  }
}
