"use client";

import { useState } from "react";
import { ArrowLeft, ArrowRight, KeyRound, RefreshCw, Shield } from "lucide-react";

type Ticket = {
  ticket_id: string;
  customer_id: string;
  conversation_id: string;
  summary: string;
  priority: "normal" | "high";
  status: "open";
  created_at: string;
};
type Queue = { items: Ticket[]; offset: number; has_more: boolean };

export default function Escalations() {
  const [session, setSession] = useState("");
  const [queue, setQueue] = useState<Queue | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  async function load(offset = 0) {
    if (!session.trim()) {
      setError("Enter an administrator session to load escalations.");
      return;
    }
    setLoading(true);
    setError("");
    try {
      const response = await fetch(`/api/admin/escalations?offset=${offset}`, {
        headers: { authorization: `Bearer ${session.trim()}` },
        cache: "no-store",
      });
      if (!response.ok) throw new Error(response.status === 401 ? "Administrator session expired or invalid." : "Escalation queue unavailable.");
      const result = (await response.json()) as Queue;
      if (!Array.isArray(result.items)) throw new Error("Escalation queue returned an invalid response.");
      setQueue(result);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Escalation queue unavailable.");
    } finally {
      setLoading(false);
    }
  }

  return (
    <main className="admin-page">
      <header className="admin-header">
        <div className="admin-heading"><span className="brand-mark"><Shield size={18} /></span><div><h1>Escalations</h1><span>Open support tickets</span></div></div>
        <div className="admin-links"><a className="back-link" href="/admin"><ArrowLeft size={16} /> Billing review</a><a className="back-link" href="/">Customer chat</a></div>
      </header>
      <section className="admin-content">
        <div className="admin-controls">
          <div className="admin-session"><label htmlFor="admin-token"><KeyRound size={15} /> Administrator session</label><input id="admin-token" type="password" autoComplete="off" spellCheck={false} placeholder="Signed admin session token" value={session} disabled={loading} onChange={(event) => { setSession(event.target.value); setQueue(null); setError(""); }} /></div>
          <button className="refresh-button" type="button" onClick={() => load(0)} disabled={loading}><RefreshCw size={16} /> {loading ? "Loading" : "Load tickets"}</button>
        </div>
        {error && <div className="admin-error" role="alert">{error}</div>}
        <div className="queue-heading"><h2>Open tickets</h2>{queue && <button className="icon-button" type="button" aria-label="Refresh tickets" title="Refresh tickets" onClick={() => load(queue.offset)} disabled={loading}><RefreshCw size={17} /></button>}</div>
        {!queue ? <div className="queue-empty">Load tickets to review escalations.</div> : queue.items.length === 0 ? <div className="queue-empty">No open tickets on this page.</div> : (
          <div className="queue-list">{queue.items.map((item) => (
            <article className="queue-row" key={item.ticket_id}>
              <div className="queue-main"><div className="queue-title"><span className="queue-action">{item.summary.replaceAll("_", " ")}</span><span className={`queue-status ${item.priority}`}>{item.priority} priority</span></div><div className="queue-details"><span>Ticket {item.ticket_id}</span><span>Customer {item.customer_id}</span><span>Conversation {item.conversation_id}</span><span>{new Date(item.created_at).toLocaleString()}</span></div></div>
            </article>
          ))}</div>
        )}
        {queue && <div className="pagination"><button type="button" onClick={() => load(Math.max(0, queue.offset - 50))} disabled={loading || queue.offset === 0}><ArrowLeft size={15} /> Previous</button><span>Page {Math.floor(queue.offset / 50) + 1}</span><button type="button" onClick={() => load(queue.offset + 50)} disabled={loading || !queue.has_more}>Next <ArrowRight size={15} /></button></div>}
      </section>
    </main>
  );
}
