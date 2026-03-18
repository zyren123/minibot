import { useEffect, useMemo, useState } from "react";
import ChatView from "./views/ChatView";
import ConfigView from "./views/ConfigView";
import type { BotMeta } from "./lib/types";
import { createBot, deleteBot, listBots } from "./lib/api";

type Tab = "chat" | "config";

export default function App() {
  const [tab, setTab] = useState<Tab>("chat");
  const [bots, setBots] = useState<BotMeta[]>([]);
  const [botId, setBotId] = useState<string>("default");
  const [botStatus, setBotStatus] = useState<string | null>(null);
  const [createOpen, setCreateOpen] = useState(false);
  const [createName, setCreateName] = useState("");
  const [deleteOpen, setDeleteOpen] = useState(false);
  const [botBusy, setBotBusy] = useState(false);

  const tabs = useMemo(
    () => [
      { id: "chat" as const, label: "Chat" },
      { id: "config" as const, label: "Config" },
    ],
    [],
  );

  useEffect(() => {
    document.title = tab === "chat" ? "Minibot • Chat" : "Minibot • Config";
  }, [tab]);

  async function refreshBots() {
    const list = await listBots();
    setBots(list);

    const stored = localStorage.getItem("minibot_web_bot_id") || "";
    const candidate = stored || botId;
    const exists = list.some((b) => b.bot_id === candidate);
    setBotId(exists ? candidate : "default");
  }

  useEffect(() => {
    refreshBots().catch((e) => setBotStatus(String(e)));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    localStorage.setItem("minibot_web_bot_id", botId);
  }, [botId]);

  async function onCreateBot() {
    try {
      setBotStatus(null);
      setCreateName("");
      setCreateOpen(true);
    } catch (e) {
      setBotStatus(String(e));
    }
  }

  async function onDeleteBot() {
    if (botId === "default") return;
    try {
      setBotStatus(null);
      setDeleteOpen(true);
    } catch (e) {
      setBotStatus(String(e));
    }
  }

  async function confirmCreateBot() {
    if (botBusy) return;
    setBotBusy(true);
    try {
      setBotStatus(null);
      const created = await createBot(createName);
      setCreateOpen(false);
      await refreshBots();
      setBotId(created.bot_id);
    } catch (e) {
      setBotStatus(String(e));
    } finally {
      setBotBusy(false);
    }
  }

  async function confirmDeleteBot() {
    if (botId === "default" || botBusy) return;
    setBotBusy(true);
    try {
      setBotStatus(null);
      await deleteBot(botId);
      setDeleteOpen(false);
      setBotId("default");
      await refreshBots();
    } catch (e) {
      setBotStatus(String(e));
    } finally {
      setBotBusy(false);
    }
  }

  useEffect(() => {
    function onKeyDown(e: KeyboardEvent) {
      if (e.key !== "Escape") return;
      setCreateOpen(false);
      setDeleteOpen(false);
    }
    if (!createOpen && !deleteOpen) return;
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [createOpen, deleteOpen]);

  const currentBot = bots.find((b) => b.bot_id === botId);

  return (
    <div className="h-full bg-zinc-950 text-zinc-100">
      <header className="flex items-center justify-between border-b border-zinc-800 px-4 py-3">
        <div className="flex items-center gap-3">
          <div className="text-lg font-semibold tracking-tight">Minibot</div>
          <div className="flex items-center gap-2">
            <select
              value={botId}
              onChange={(e) => setBotId(e.target.value)}
              className="rounded-md border border-zinc-800 bg-zinc-900 px-2 py-1.5 text-sm text-zinc-100 focus:outline-none focus:ring-2 focus:ring-zinc-600"
            >
              {bots.map((b) => (
                <option key={b.bot_id} value={b.bot_id}>
                  {b.name}
                </option>
              ))}
            </select>
            <button
              onClick={() => void onCreateBot()}
              className="rounded-md bg-zinc-900 px-2 py-1.5 text-sm text-zinc-200 hover:bg-zinc-800"
            >
              New Bot
            </button>
            {botId !== "default" ? (
              <button
                onClick={() => void onDeleteBot()}
                className="rounded-md bg-red-600 px-2 py-1.5 text-sm text-white hover:bg-red-500"
              >
                Delete
              </button>
            ) : null}
          </div>
        </div>
        <div className="flex items-center gap-2">
          {botStatus && <div className="text-xs text-zinc-400">{botStatus}</div>}
          {tabs.map((t) => (
            <button
              key={t.id}
              onClick={() => setTab(t.id)}
              className={[
                "rounded-md px-3 py-1.5 text-sm font-medium",
                tab === t.id
                  ? "bg-zinc-100 text-zinc-900"
                  : "bg-zinc-900 text-zinc-200 hover:bg-zinc-800",
              ].join(" ")}
            >
              {t.label}
            </button>
          ))}
        </div>
      </header>

      <main className="h-[calc(100%-56px)]">
        {tab === "chat" ? (
          <ChatView botId={botId} botName={currentBot?.name || botId} />
        ) : (
          <ConfigView botId={botId} onBotsChanged={refreshBots} />
        )}
      </main>

      {createOpen ? (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4"
          onMouseDown={(e) => {
            if (e.target === e.currentTarget) setCreateOpen(false);
          }}
        >
          <div className="w-full max-w-md rounded-2xl border border-zinc-800 bg-zinc-950 p-4 shadow-xl">
            <div className="text-lg font-semibold">Create bot</div>
            <div className="mt-1 text-sm text-zinc-400">Give your bot a friendly name (optional).</div>
            <div className="mt-4 grid gap-2">
              <label className="text-sm font-semibold text-zinc-200">Bot name</label>
              <input
                value={createName}
                onChange={(e) => setCreateName(e.target.value)}
                placeholder="e.g. Mini QA"
                className="rounded-lg border border-zinc-800 bg-zinc-900 px-3 py-2 text-sm text-zinc-100 placeholder:text-zinc-500 focus:outline-none focus:ring-2 focus:ring-zinc-600"
                onKeyDown={(e) => {
                  if (e.key === "Enter") void confirmCreateBot();
                }}
                autoFocus
                disabled={botBusy}
              />
            </div>
            <div className="mt-5 flex justify-end gap-2">
              <button
                className="rounded-xl bg-zinc-900 px-4 py-2 text-sm font-semibold text-zinc-200 hover:bg-zinc-800 disabled:opacity-50"
                onClick={() => setCreateOpen(false)}
                disabled={botBusy}
              >
                Cancel
              </button>
              <button
                className="rounded-xl bg-zinc-100 px-4 py-2 text-sm font-semibold text-zinc-900 hover:bg-white disabled:opacity-50"
                onClick={() => void confirmCreateBot()}
                disabled={botBusy}
              >
                Create
              </button>
            </div>
          </div>
        </div>
      ) : null}

      {deleteOpen ? (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4"
          onMouseDown={(e) => {
            if (e.target === e.currentTarget) setDeleteOpen(false);
          }}
        >
          <div className="w-full max-w-md rounded-2xl border border-zinc-800 bg-zinc-950 p-4 shadow-xl">
            <div className="text-lg font-semibold">Delete bot</div>
            <div className="mt-1 text-sm text-zinc-400">
              Delete <span className="font-mono text-zinc-200">{currentBot?.name || botId}</span> and all its sessions?
              This cannot be undone.
            </div>
            <div className="mt-5 flex justify-end gap-2">
              <button
                className="rounded-xl bg-zinc-900 px-4 py-2 text-sm font-semibold text-zinc-200 hover:bg-zinc-800 disabled:opacity-50"
                onClick={() => setDeleteOpen(false)}
                disabled={botBusy}
              >
                Cancel
              </button>
              <button
                className="rounded-xl bg-red-600 px-4 py-2 text-sm font-semibold text-white hover:bg-red-500 disabled:opacity-50"
                onClick={() => void confirmDeleteBot()}
                disabled={botBusy}
              >
                Delete
              </button>
            </div>
          </div>
        </div>
      ) : null}
    </div>
  );
}
