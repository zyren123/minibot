import { useEffect, useMemo, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

import type { Message, SessionMeta, StreamEvent } from "../lib/types";
import { cancelSession, createSession, listSessions, loadSession, streamChat } from "../lib/api";

function classNames(...xs: Array<string | false | null | undefined>) {
  return xs.filter(Boolean).join(" ");
}

export default function ChatView() {
  const [sessions, setSessions] = useState<SessionMeta[]>([]);
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [messages, setMessages] = useState<Message[]>([]);
  const [prompt, setPrompt] = useState("");
  const [streaming, setStreaming] = useState(false);
  const [status, setStatus] = useState<string | null>(null);

  const scrollRef = useRef<HTMLDivElement | null>(null);

  const sortedSessions = useMemo(
    () => [...sessions].sort((a, b) => b.modified_at.localeCompare(a.modified_at)),
    [sessions],
  );

  async function refreshSessions() {
    const list = await listSessions();
    setSessions(list);
    if (!sessionId && list.length > 0) setSessionId(list[0].session_id);
  }

  async function ensureSession() {
    if (sessionId) return sessionId;
    const created = await createSession();
    setSessionId(created.session_id);
    await refreshSessions();
    return created.session_id;
  }

  async function selectSession(id: string) {
    setSessionId(id);
    const sess = await loadSession(id);
    setMessages(sess.messages);
  }

  useEffect(() => {
    refreshSessions().catch((e) => setStatus(String(e)));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    if (!sessionId) return;
    selectSession(sessionId).catch((e) => setStatus(String(e)));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sessionId]);

  useEffect(() => {
    const el = scrollRef.current;
    if (!el) return;
    el.scrollTop = el.scrollHeight;
  }, [messages, streaming]);

  function applyStreamEvent(ev: StreamEvent) {
    if (ev.type === "assistant_delta") {
      setMessages((prev) => {
        const last = prev[prev.length - 1];
        if (!last || last.role !== "assistant") {
          return [...prev, { role: "assistant", content: ev.delta_text ?? "" }];
        }
        const updated: Message = { ...last, content: (last.content ?? "") + (ev.delta_text ?? "") };
        return [...prev.slice(0, -1), updated];
      });
      return;
    }

    if (ev.type === "tool_call") {
      const args = ev.tool_args ? JSON.stringify(ev.tool_args, null, 2) : "";
      setMessages((prev) => [
        ...prev,
        { role: "tool", content: `> ${ev.tool_name ?? "tool"}\n\n${args}`.trim() },
      ]);
      return;
    }

    if (ev.type === "tool_result") {
      setMessages((prev) => [
        ...prev,
        { role: "tool", content: `Result (${ev.tool_name ?? "tool"}):\n\n${ev.tool_output ?? ""}`.trim() },
      ]);
      return;
    }

    if (ev.type === "system") {
      if (typeof ev.message === "string") setStatus(ev.message);
      return;
    }
  }

  async function onSend() {
    if (!prompt.trim() || streaming) return;
    setStatus(null);
    setStreaming(true);

    const sid = await ensureSession();
    setMessages((prev) => [...prev, { role: "user", content: prompt.trim() }]);
    setPrompt("");

    try {
      for await (const ev of streamChat(sid, prompt.trim())) {
        applyStreamEvent(ev);
      }
      await refreshSessions();
      await selectSession(sid);
    } catch (e) {
      setStatus(String(e));
    } finally {
      setStreaming(false);
    }
  }

  async function onStop() {
    if (!sessionId) return;
    try {
      await cancelSession(sessionId);
    } catch (e) {
      setStatus(String(e));
    }
  }

  return (
    <div className="flex h-full">
      <aside className="w-72 shrink-0 border-r border-zinc-800 bg-zinc-950">
        <div className="flex items-center justify-between px-3 py-2">
          <div className="text-sm font-semibold text-zinc-200">Sessions</div>
          <div className="flex gap-2">
            <button
              className="rounded-md bg-zinc-900 px-2 py-1 text-xs text-zinc-200 hover:bg-zinc-800"
              onClick={() => createSession().then((r) => setSessionId(r.session_id)).then(refreshSessions)}
              disabled={streaming}
            >
              New
            </button>
            <button
              className="rounded-md bg-zinc-900 px-2 py-1 text-xs text-zinc-200 hover:bg-zinc-800"
              onClick={() => refreshSessions().catch((e) => setStatus(String(e)))}
              disabled={streaming}
            >
              Refresh
            </button>
          </div>
        </div>
        <div className="h-[calc(100%-40px)] overflow-auto px-2 pb-2">
          {sortedSessions.map((s) => (
            <button
              key={s.session_id}
              className={classNames(
                "w-full rounded-md px-3 py-2 text-left text-sm",
                sessionId === s.session_id ? "bg-zinc-900 text-zinc-100" : "text-zinc-300 hover:bg-zinc-900/60",
              )}
              onClick={() => setSessionId(s.session_id)}
              disabled={streaming}
            >
              <div className="flex items-center justify-between gap-2">
                <div className="font-mono text-xs">{s.session_id}</div>
                <div className="text-[10px] text-zinc-500">{s.message_count}</div>
              </div>
              <div className="mt-1 line-clamp-2 text-xs text-zinc-400">{s.preview || "(no preview)"}</div>
            </button>
          ))}
          {sortedSessions.length === 0 && (
            <div className="px-3 py-2 text-xs text-zinc-500">No sessions yet. Click “New”.</div>
          )}
        </div>
      </aside>

      <section className="flex min-w-0 flex-1 flex-col">
        <div className="flex items-center justify-between border-b border-zinc-800 px-4 py-2">
          <div className="text-xs text-zinc-400">
            Session: <span className="font-mono">{sessionId ?? "(none)"}</span>
          </div>
          <div className="flex items-center gap-2">
            {status && <div className="text-xs text-zinc-400">{status}</div>}
            {streaming ? (
              <button
                className="rounded-md bg-red-600 px-3 py-1.5 text-xs font-semibold text-white hover:bg-red-500"
                onClick={() => void onStop()}
              >
                Stop
              </button>
            ) : null}
          </div>
        </div>

        <div ref={scrollRef} className="flex-1 overflow-auto px-6 py-6">
          <div className="mx-auto flex max-w-3xl flex-col gap-4">
            {messages.map((m, idx) => (
              <div key={idx} className={classNames("flex", m.role === "user" ? "justify-end" : "justify-start")}>
                <div
                  className={classNames(
                    "max-w-[85%] rounded-2xl px-4 py-3 text-sm leading-relaxed",
                    m.role === "user"
                      ? "bg-zinc-100 text-zinc-900"
                      : m.role === "tool"
                        ? "bg-zinc-900 text-zinc-200 border border-zinc-800"
                        : "bg-zinc-900 text-zinc-100",
                  )}
                >
                  {m.role === "assistant" ? (
                    <ReactMarkdown remarkPlugins={[remarkGfm]}>{m.content}</ReactMarkdown>
                  ) : (
                    <pre className="whitespace-pre-wrap font-sans">{m.content}</pre>
                  )}
                </div>
              </div>
            ))}
            {messages.length === 0 && (
              <div className="text-sm text-zinc-500">Start chatting by sending a message.</div>
            )}
          </div>
        </div>

        <div className="border-t border-zinc-800 bg-zinc-950 px-4 py-4">
          <div className="mx-auto flex max-w-3xl items-end gap-3">
            <textarea
              value={prompt}
              onChange={(e) => setPrompt(e.target.value)}
              placeholder="Message Minibot…"
              className="min-h-[44px] w-full resize-none rounded-xl border border-zinc-800 bg-zinc-900 px-4 py-3 text-sm text-zinc-100 placeholder:text-zinc-500 focus:outline-none focus:ring-2 focus:ring-zinc-600"
              rows={1}
              disabled={streaming}
              onKeyDown={(e) => {
                if (e.key === "Enter" && !e.shiftKey) {
                  e.preventDefault();
                  void onSend();
                }
              }}
            />
            <button
              className="rounded-xl bg-zinc-100 px-4 py-3 text-sm font-semibold text-zinc-900 hover:bg-white disabled:opacity-50"
              onClick={() => void onSend()}
              disabled={streaming || !prompt.trim()}
            >
              Send
            </button>
          </div>
          <div className="mx-auto mt-2 max-w-3xl text-xs text-zinc-500">Enter to send • Shift+Enter for newline</div>
        </div>
      </section>
    </div>
  );
}

