import { useEffect, useMemo, useState } from "react";
import type { Config } from "../lib/types";
import { getConfig, updateConfig } from "../lib/api";

function classNames(...xs: Array<string | false | null | undefined>) {
  return xs.filter(Boolean).join(" ");
}

function ListEditor(props: {
  label: string;
  items: string[];
  setItems: (items: string[]) => void;
  placeholder: string;
}) {
  const [draft, setDraft] = useState("");
  const { label, items, setItems, placeholder } = props;

  return (
    <div className="rounded-xl border border-zinc-800 bg-zinc-950 p-4">
      <div className="mb-3 text-sm font-semibold text-zinc-200">{label}</div>
      <div className="flex gap-2">
        <input
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          placeholder={placeholder}
          className="w-full rounded-lg border border-zinc-800 bg-zinc-900 px-3 py-2 text-sm text-zinc-100 placeholder:text-zinc-500 focus:outline-none focus:ring-2 focus:ring-zinc-600"
        />
        <button
          className="shrink-0 rounded-lg bg-zinc-100 px-3 py-2 text-sm font-semibold text-zinc-900 hover:bg-white disabled:opacity-50"
          onClick={() => {
            const v = draft.trim();
            if (!v) return;
            setItems([...items, v]);
            setDraft("");
          }}
          disabled={!draft.trim()}
        >
          Add
        </button>
      </div>
      <div className="mt-3 flex flex-col gap-2">
        {items.map((it, idx) => (
          <div key={idx} className="flex items-center justify-between rounded-lg border border-zinc-800 bg-zinc-900 px-3 py-2">
            <div className="min-w-0 truncate font-mono text-xs text-zinc-200">{it}</div>
            <button
              className="rounded-md px-2 py-1 text-xs text-zinc-300 hover:bg-zinc-800"
              onClick={() => setItems(items.filter((_, i) => i !== idx))}
            >
              Remove
            </button>
          </div>
        ))}
        {items.length === 0 && <div className="text-xs text-zinc-500">No items.</div>}
      </div>
    </div>
  );
}

export default function ConfigView() {
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [status, setStatus] = useState<string | null>(null);

  const [baseUrl, setBaseUrl] = useState<string>("");
  const [model, setModel] = useState<string>("");
  const [streamEnabled, setStreamEnabled] = useState<boolean>(true);
  const [skillsDirs, setSkillsDirs] = useState<string[]>([]);
  const [toolPlugins, setToolPlugins] = useState<string[]>([]);
  const [apiKeyMasked, setApiKeyMasked] = useState<string>("");
  const [apiKeyDraft, setApiKeyDraft] = useState<string>("");

  const dirty = useMemo(() => true, [baseUrl, model, streamEnabled, skillsDirs, toolPlugins, apiKeyDraft]);

  async function refresh() {
    setLoading(true);
    setStatus(null);
    try {
      const cfg = await getConfig();
      applyConfig(cfg);
    } catch (e) {
      setStatus(String(e));
    } finally {
      setLoading(false);
    }
  }

  function applyConfig(cfg: Config) {
    setBaseUrl(cfg.base_url ?? "");
    setModel(cfg.model ?? "");
    setStreamEnabled(Boolean(cfg.stream_enabled));
    setSkillsDirs(cfg.skills_dirs ?? []);
    setToolPlugins(cfg.tool_plugins ?? []);
    setApiKeyMasked(cfg.api_key_masked ?? "");
    setApiKeyDraft("");
  }

  useEffect(() => {
    refresh().catch(() => null);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  async function onSave() {
    setSaving(true);
    setStatus(null);
    try {
      await updateConfig({
        base_url: baseUrl || null,
        model: model || null,
        stream_enabled: streamEnabled,
        skills_dirs: skillsDirs,
        tool_plugins: toolPlugins,
        api_key: apiKeyDraft.trim() ? apiKeyDraft.trim() : undefined,
      });
      setStatus("Saved.");
      await refresh();
    } catch (e) {
      setStatus(String(e));
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="h-full overflow-auto p-6">
      <div className="mx-auto flex max-w-3xl flex-col gap-6">
        <div className="flex items-center justify-between">
          <div>
            <div className="text-lg font-semibold">Configuration</div>
            <div className="text-sm text-zinc-400">Server-wide defaults (applied on next agent creation).</div>
          </div>
          <div className="flex items-center gap-3">
            {status && <div className="text-xs text-zinc-400">{status}</div>}
            <button
              className={classNames(
                "rounded-xl px-4 py-2 text-sm font-semibold",
                "bg-zinc-100 text-zinc-900 hover:bg-white disabled:opacity-50",
              )}
              onClick={() => void onSave()}
              disabled={saving || loading || !dirty}
            >
              {saving ? "Saving…" : "Save"}
            </button>
          </div>
        </div>

        <div className="grid gap-4 rounded-2xl border border-zinc-800 bg-zinc-950 p-4">
          <div className="grid gap-2">
            <label className="text-sm font-semibold text-zinc-200">Base URL</label>
            <input
              value={baseUrl}
              onChange={(e) => setBaseUrl(e.target.value)}
              placeholder="https://api.openai.com/v1"
              className="rounded-lg border border-zinc-800 bg-zinc-900 px-3 py-2 text-sm text-zinc-100 placeholder:text-zinc-500 focus:outline-none focus:ring-2 focus:ring-zinc-600"
              disabled={loading}
            />
          </div>
          <div className="grid gap-2">
            <label className="text-sm font-semibold text-zinc-200">Model</label>
            <input
              value={model}
              onChange={(e) => setModel(e.target.value)}
              placeholder="gpt-4.1-mini"
              className="rounded-lg border border-zinc-800 bg-zinc-900 px-3 py-2 text-sm text-zinc-100 placeholder:text-zinc-500 focus:outline-none focus:ring-2 focus:ring-zinc-600"
              disabled={loading}
            />
          </div>
          <div className="grid gap-2">
            <label className="text-sm font-semibold text-zinc-200">API Key</label>
            <div className="text-xs text-zinc-500">Current: {apiKeyMasked || "(not set)"}</div>
            <input
              value={apiKeyDraft}
              onChange={(e) => setApiKeyDraft(e.target.value)}
              placeholder="Paste a new API key (won't be echoed back)"
              className="rounded-lg border border-zinc-800 bg-zinc-900 px-3 py-2 text-sm text-zinc-100 placeholder:text-zinc-500 focus:outline-none focus:ring-2 focus:ring-zinc-600"
              disabled={loading}
            />
          </div>
          <div className="flex items-center justify-between rounded-xl border border-zinc-800 bg-zinc-900 px-4 py-3">
            <div>
              <div className="text-sm font-semibold text-zinc-200">Streaming</div>
              <div className="text-xs text-zinc-500">Enable streaming deltas for chat responses.</div>
            </div>
            <button
              className={classNames(
                "rounded-full px-3 py-1 text-xs font-semibold",
                streamEnabled ? "bg-green-500/20 text-green-300" : "bg-zinc-800 text-zinc-300",
              )}
              onClick={() => setStreamEnabled((v) => !v)}
              disabled={loading}
            >
              {streamEnabled ? "On" : "Off"}
            </button>
          </div>
        </div>

        <ListEditor
          label="Skills directories"
          items={skillsDirs}
          setItems={setSkillsDirs}
          placeholder="/path/to/skills"
        />
        <ListEditor
          label="Tool plugin paths"
          items={toolPlugins}
          setItems={setToolPlugins}
          placeholder="/path/to/tools.py"
        />

        {loading && <div className="text-sm text-zinc-500">Loading…</div>}
      </div>
    </div>
  );
}

