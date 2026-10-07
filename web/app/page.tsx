"use client";

import { useRef, useState } from "react";
import {
  ArrowUp,
  CircleAlert,
  Headset,
  KeyRound,
  MessageSquareText,
  Plus,
  Shield,
  ShieldCheck,
  X,
} from "lucide-react";
import { readEvents } from "../lib/sse";

type Evidence = { article_id: string; title: string; section: string; revision: string };
type Answer = {
  status: "answered" | "escalated";
  intent: string;
  answer: string;
  evidence: Evidence[];
  escalation_reason: string | null;
};
type Entry = { id: string; role: "customer" | "support"; text: string; answer?: Answer };

const progressLabels: Record<string, string> = {
  routing: "Classifying request",
  tool_started: "Checking support data",
  retrieval: "Reviewing support article",
  tool_finished: "Support check complete",
  escalated: "Escalating to a specialist",
};

function isAnswer(value: unknown): value is Answer {
  if (!value || typeof value !== "object") return false;
  const item = value as Partial<Answer>;
  return (
    (item.status === "answered" || item.status === "escalated") &&
    typeof item.answer === "string" &&
    Array.isArray(item.evidence)
  );
}

export default function Home() {
  const [session, setSession] = useState("");
  const [draft, setDraft] = useState("");
  const [orderId, setOrderId] = useState("");
  const [entries, setEntries] = useState<Entry[]>([]);
  const [progress, setProgress] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const abort = useRef<AbortController | null>(null);

  function clearThread() {
    abort.current?.abort();
    abort.current = null;
    setBusy(false);
    setEntries([]);
    setProgress("");
    setError("");
    setDraft("");
    setOrderId("");
  }

  async function send(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const message = draft.trim();
    if (!message || busy) return;
    if (!session.trim()) {
      setError("Enter a customer session before sending a message.");
      return;
    }
    const controller = new AbortController();
    abort.current = controller;
    setEntries((current) => [...current, { id: crypto.randomUUID(), role: "customer", text: message }]);
    setDraft("");
    setError("");
    setProgress("Connecting to support");
    setBusy(true);
    try {
      const response = await fetch("/api/messages", {
        method: "POST",
        headers: { "content-type": "application/json", authorization: `Bearer ${session.trim()}` },
        body: JSON.stringify({ message, ...(orderId.trim() ? { order_id: orderId.trim() } : {}) }),
        signal: controller.signal,
      });
      if (!response.ok || !response.body) {
        throw new Error(response.status === 401 ? "Customer session expired or invalid." : "Support is unavailable. Try again.");
      }
      let completed = false;
      for await (const item of readEvents(response.body)) {
        if (controller.signal.aborted) return;
        if (item.event === "error") throw new Error("Support is unavailable. Try again.");
        if (item.event === "completed") {
          if (!isAnswer(item.data)) throw new Error("Support returned an invalid response.");
          const answer: Answer = item.data;
          setEntries((current) => [
            ...current,
            { id: crypto.randomUUID(), role: "support", text: answer.answer, answer },
          ]);
          completed = true;
        } else if (progressLabels[item.event]) {
          setProgress(progressLabels[item.event]);
        }
      }
      if (!completed) throw new Error("Support response ended early. Try again.");
    } catch (cause) {
      if (!controller.signal.aborted) {
        setError(cause instanceof Error ? cause.message : "Support is unavailable. Try again.");
      }
    } finally {
      if (abort.current === controller) {
        abort.current = null;
        setBusy(false);
        setProgress("");
      }
    }
  }

  return (
    <div className="shell">
      <aside className="sidebar">
        <div className="brand"><span className="brand-mark"><Headset size={18} /></span><span>Support Console</span></div>
        <div className="sidebar-section">
          <span className="sidebar-label">Workspace</span>
          <div className="nav-current"><MessageSquareText size={17} /> Customer chat</div>
        </div>
        <div className="session-panel">
          <div className="session-heading"><KeyRound size={16} /> Customer session</div>
          <label htmlFor="session-token" className="sr-only">Signed customer session</label>
          <input
            id="session-token"
            type="password"
            autoComplete="off"
            spellCheck={false}
            placeholder="Signed session token"
            value={session}
            onChange={(event) => setSession(event.target.value)}
          />
          <div className="session-state"><ShieldCheck size={14} /> {session ? "Session in memory" : "Session required"}</div>
        </div>
      </aside>

      <main className="main">
        <header className="topbar">
          <div className="topbar-title"><span className="mobile-brand"><Headset size={17} /></span>Customer chat</div>
          <div className="top-actions"><a className="icon-button" href="/admin" aria-label="Admin review" title="Admin review"><Shield size={18} /></a><button className="icon-button" type="button" onClick={clearThread} aria-label="New conversation" title="New conversation"><Plus size={19} /></button></div>
        </header>

        <div className="conversation">
          {entries.length === 0 ? (
            <div className="empty-state">
              <div className="empty-icon"><MessageSquareText size={27} /></div>
              <h1>How can we help?</h1>
              <p>Ask about an account issue, technical problem, or order.</p>
            </div>
          ) : (
            <div className="messages" aria-live="polite">
              {entries.map((entry) => (
                <div className={`message-row ${entry.role}`} key={entry.id}>
                  <div className="message-meta">{entry.role === "customer" ? "You" : "Support"}</div>
                  <div className="message-body">{entry.text}</div>
                  {entry.answer?.status === "escalated" && (
                    <div className="escalation"><CircleAlert size={15} /> Specialist review requested</div>
                  )}
                  {entry.answer?.evidence?.length ? (
                    <div className="evidence">
                      <span>Sources</span>
                      {entry.answer.evidence.map((source) => (
                        <span className="source" key={source.article_id} title={`${source.section} · ${source.revision}`}>
                          {source.article_id} · {source.title}
                        </span>
                      ))}
                    </div>
                  ) : null}
                </div>
              ))}
            </div>
          )}
          {busy && <div className="progress" role="status"><span className="pulse" />{progress}</div>}
        </div>

        <div className="composer-area">
          {error && <div className="error" role="alert"><CircleAlert size={17} /><span>{error}</span><button type="button" onClick={() => setError("")} aria-label="Dismiss error" title="Dismiss error"><X size={16} /></button></div>}
          <form onSubmit={send} className="composer">
            <label htmlFor="order-id" className="order-label">Order ID <span>optional</span></label>
            <input id="order-id" className="order-input" placeholder="UUID for tracking" value={orderId} onChange={(event) => setOrderId(event.target.value)} disabled={busy} />
            <div className="composer-main">
              <label htmlFor="message" className="sr-only">Message</label>
              <textarea
                id="message"
                value={draft}
                maxLength={500}
                onChange={(event) => setDraft(event.target.value)}
                onKeyDown={(event) => {
                  if (event.key === "Enter" && !event.shiftKey) {
                    event.preventDefault();
                    event.currentTarget.form?.requestSubmit();
                  }
                }}
                placeholder="Write a message…"
                rows={2}
                disabled={busy}
              />
              <button className="send-button" type="submit" disabled={busy || !draft.trim()} aria-label="Send message" title="Send message"><ArrowUp size={19} /></button>
            </div>
          </form>
        </div>
      </main>
    </div>
  );
}
