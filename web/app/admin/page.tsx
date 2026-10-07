"use client";

import { useState } from "react";
import { ArrowLeft, ArrowRight, Check, KeyRound, RefreshCw, Shield, X } from "lucide-react";

type ReviewAction = {
  request_id: string;
  customer_id: string;
  conversation_id: string;
  action: "refund" | "cancellation";
  target_id: string;
  amount_cents: number | null;
  reason: string;
  status: "pending" | "approved";
  created_at: string;
};
type Queue = { items: ReviewAction[]; offset: number; has_more: boolean };
type Decision = "approve" | "reject";

export default function AdminReview() {
  const [session, setSession] = useState("");
  const [queue, setQueue] = useState<Queue | null>(null);
  const [loading, setLoading] = useState(false);
  const [acting, setActing] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [confirm, setConfirm] = useState<{ item: ReviewAction; decision: Decision } | null>(null);

  async function load(offset = 0) {
    if (!session.trim()) {
      setError("Enter an administrator session to load the queue.");
      return;
    }
    setLoading(true);
    setError("");
    try {
      const response = await fetch(`/api/admin/actions?offset=${offset}`, {
        headers: { authorization: `Bearer ${session.trim()}` },
        cache: "no-store",
      });
      if (!response.ok) throw new Error(response.status === 401 ? "Administrator session expired or invalid." : "Review queue unavailable.");
      const result = (await response.json()) as Queue;
      if (!Array.isArray(result.items)) throw new Error("Review queue returned an invalid response.");
      setQueue(result);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Review queue unavailable.");
    } finally {
      setLoading(false);
    }
  }

  async function decide() {
    if (!confirm || acting) return;
    const { item, decision } = confirm;
    setActing(true);
    setError("");
    try {
      const response = await fetch("/api/admin/decision", {
        method: "POST",
        headers: { authorization: `Bearer ${session.trim()}`, "content-type": "application/json" },
        body: JSON.stringify({
          customer_id: item.customer_id,
          conversation_id: item.conversation_id,
          action: item.action,
          request_id: item.request_id,
          decision,
        }),
      });
      if (!response.ok) throw new Error(response.status === 401 ? "Administrator session expired or invalid." : response.status === 409 ? "This request has already changed. Refresh the queue." : "Decision failed. Refresh and retry.");
      const result = await response.json() as { status: string };
      if (result.status !== "executed" && result.status !== "rejected") throw new Error("Decision did not complete. Refresh the queue.");
      setNotice(result.status === "executed" ? "Approved and completed." : "Request rejected.");
      setConfirm(null);
      await load(queue?.offset ?? 0);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Decision failed.");
      setConfirm(null);
    } finally {
      setActing(false);
    }
  }

  return (
    <main className="admin-page">
      <header className="admin-header">
        <div className="admin-heading"><span className="brand-mark"><Shield size={18} /></span><div><h1>Review queue</h1><span>Billing actions</span></div></div>
        <a className="back-link" href="/"><ArrowLeft size={16} /> Customer chat</a>
      </header>
      <section className="admin-content">
        <div className="admin-controls">
          <div className="admin-session"><label htmlFor="admin-token"><KeyRound size={15} /> Administrator session</label><input id="admin-token" type="password" autoComplete="off" spellCheck={false} placeholder="Signed admin session token" value={session} disabled={loading || acting} onChange={(event) => { setSession(event.target.value); setQueue(null); setNotice(""); setError(""); }} /></div>
          <button className="refresh-button" type="button" onClick={() => load(0)} disabled={loading || acting}><RefreshCw size={16} /> {loading ? "Loading" : "Load queue"}</button>
        </div>
        {error && <div className="admin-error" role="alert">{error}</div>}
        {notice && <div className="admin-notice" role="status">{notice}</div>}
        <div className="queue-heading"><h2>Actions for review</h2>{queue && <button className="icon-button" type="button" aria-label="Refresh queue" title="Refresh queue" onClick={() => load(queue.offset)} disabled={loading}><RefreshCw size={17} /></button>}</div>
        {!queue ? <div className="queue-empty">Load the queue to review pending actions.</div> : queue.items.length === 0 ? <div className="queue-empty">No reviewable actions on this page.</div> : (
          <div className="queue-list">
            {queue.items.map((item) => (
              <article className="queue-row" key={item.request_id}>
                <div className="queue-main"><div className="queue-title"><span className="queue-action">{item.action === "refund" ? "Refund" : "Cancellation"}</span><span className={`queue-status ${item.status}`}>{item.status}</span></div><div className="queue-reason">{item.reason}</div><div className="queue-details"><span>Customer {item.customer_id}</span><span>Target {item.target_id}</span>{item.amount_cents !== null && <span>Amount {(item.amount_cents / 100).toFixed(2)}</span>}<span>{new Date(item.created_at).toLocaleString()}</span></div></div>
                <div className="queue-buttons"><button type="button" className="reject-button" onClick={() => setConfirm({ item, decision: "reject" })} disabled={acting}>Reject</button><button type="button" className="approve-button" onClick={() => setConfirm({ item, decision: "approve" })} disabled={acting}><Check size={15} /> Approve</button></div>
              </article>
            ))}
          </div>
        )}
        {queue && <div className="pagination"><button type="button" onClick={() => load(Math.max(0, queue.offset - 50))} disabled={loading || queue.offset === 0}><ArrowLeft size={15} /> Previous</button><span>Page {Math.floor(queue.offset / 50) + 1}</span><button type="button" onClick={() => load(queue.offset + 50)} disabled={loading || !queue.has_more}>Next <ArrowRight size={15} /></button></div>}
      </section>
      {confirm && <div className="dialog-backdrop" role="presentation"><div className="dialog" role="dialog" aria-modal="true" aria-labelledby="decision-title"><h2 id="decision-title">{confirm.decision === "approve" ? "Approve this action?" : "Reject this action?"}</h2><p>{confirm.decision === "approve" ? "The approved mock action will execute after the paused workflow resumes." : "The customer request will be declined."}</p><div className="dialog-detail">{confirm.item.action === "refund" ? "Refund" : "Cancellation"} · {confirm.item.request_id}</div><div className="dialog-buttons"><button type="button" onClick={() => setConfirm(null)} disabled={acting}><X size={15} /> Cancel</button><button type="button" className={confirm.decision === "approve" ? "approve-button" : "reject-button"} onClick={decide} disabled={acting}>{acting ? "Processing" : confirm.decision === "approve" ? "Confirm approval" : "Confirm rejection"}</button></div></div></div>}
    </main>
  );
}
