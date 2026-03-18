import { useEffect, useMemo, useState } from "react";
import ChatView from "./views/ChatView";
import ConfigView from "./views/ConfigView";

type Tab = "chat" | "config";

export default function App() {
  const [tab, setTab] = useState<Tab>("chat");

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

  return (
    <div className="h-full bg-zinc-950 text-zinc-100">
      <header className="flex items-center justify-between border-b border-zinc-800 px-4 py-3">
        <div className="text-lg font-semibold tracking-tight">Minibot</div>
        <div className="flex items-center gap-2">
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
        {tab === "chat" ? <ChatView /> : <ConfigView />}
      </main>
    </div>
  );
}

