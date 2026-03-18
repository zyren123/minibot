import { useEffect, useMemo, useState } from "react";
import type { MCPServerInfo, SkillInfo } from "../lib/types";
import { getBotConfig, listMcpServers, listSkills, updateBotConfig } from "../lib/api";

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

export default function ConfigView(props: { botId: string; onBotsChanged?: () => void }) {
  const { botId, onBotsChanged } = props;
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [status, setStatus] = useState<string | null>(null);

  const [name, setName] = useState<string>("");
  const [baseUrl, setBaseUrl] = useState<string>("");
  const [model, setModel] = useState<string>("");
  const [streamEnabled, setStreamEnabled] = useState<boolean>(true);
  const [apiKeyMasked, setApiKeyMasked] = useState<string>("");
  const [apiKeyDraft, setApiKeyDraft] = useState<string>("");

  const [toolPlugins, setToolPlugins] = useState<string[]>([]);
  const [soul, setSoul] = useState<string>("");

  const [skills, setSkills] = useState<SkillInfo[]>([]);
  const [skillsDisabled, setSkillsDisabled] = useState<string[]>([]);
  const [skillQuery, setSkillQuery] = useState<string>("");

  const [mcpServers, setMcpServers] = useState<MCPServerInfo[]>([]);
  const [mcpOverrides, setMcpOverrides] = useState<Record<string, boolean>>({});

  const filteredSkills = useMemo(() => {
    const q = skillQuery.trim().toLowerCase();
    if (!q) return skills;
    return skills.filter((s) => `${s.name} ${s.description}`.toLowerCase().includes(q));
  }, [skills, skillQuery]);

  const dirty = useMemo(
    () => true,
    [name, baseUrl, model, streamEnabled, apiKeyDraft, toolPlugins, soul, skillsDisabled, mcpOverrides],
  );

  async function refreshBotConfig(nextBotId: string = botId) {
    setLoading(true);
    setStatus(null);
    try {
      const cfg = await getBotConfig(nextBotId);
      setName(cfg.name ?? "");
      setBaseUrl(cfg.base_url ?? "");
      setModel(cfg.model ?? "");
      setStreamEnabled(Boolean(cfg.stream_enabled));
      setApiKeyMasked(cfg.api_key_masked ?? "");
      setApiKeyDraft("");
      setToolPlugins(cfg.tool_plugins ?? []);
      setSoul(cfg.soul ?? "");
      setSkillsDisabled(cfg.skills_disabled ?? []);
      setMcpOverrides(cfg.mcp_overrides ?? {});
    } catch (e) {
      setStatus(String(e));
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    listSkills().then(setSkills).catch((e) => setStatus(String(e)));
    listMcpServers().then(setMcpServers).catch((e) => setStatus(String(e)));
  }, []);

  useEffect(() => {
    refreshBotConfig(botId).catch(() => null);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [botId]);

  function toggleSkill(skillName: string) {
    setSkillsDisabled((prev) => {
      const set = new Set(prev);
      if (set.has(skillName)) set.delete(skillName);
      else set.add(skillName);
      return Array.from(set).sort();
    });
  }

  function mcpValue(name: string): "inherit" | "on" | "off" {
    if (Object.prototype.hasOwnProperty.call(mcpOverrides, name)) {
      return mcpOverrides[name] ? "on" : "off";
    }
    return "inherit";
  }

  function setMcpValue(name: string, value: "inherit" | "on" | "off") {
    setMcpOverrides((prev) => {
      const next = { ...prev };
      if (value === "inherit") delete next[name];
      else next[name] = value === "on";
      return next;
    });
  }

  async function onSave() {
    setSaving(true);
    setStatus(null);
    try {
      await updateBotConfig(botId, {
        name: name.trim() || null,
        base_url: baseUrl || null,
        model: model || null,
        stream_enabled: streamEnabled,
        tool_plugins: toolPlugins,
        skills_disabled: skillsDisabled,
        mcp_overrides: mcpOverrides,
        soul,
        api_key: apiKeyDraft.trim() ? apiKeyDraft.trim() : undefined,
      });
      setStatus("Saved.");
      await refreshBotConfig(botId);
      onBotsChanged?.();
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
            <div className="text-lg font-semibold">Bot Configuration</div>
            <div className="text-sm text-zinc-400">Applies to this bot on next agent creation.</div>
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
            <label className="text-sm font-semibold text-zinc-200">Bot name</label>
            <input
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="My Bot"
              className="rounded-lg border border-zinc-800 bg-zinc-900 px-3 py-2 text-sm text-zinc-100 placeholder:text-zinc-500 focus:outline-none focus:ring-2 focus:ring-zinc-600"
              disabled={loading}
            />
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

        <div className="grid gap-2 rounded-2xl border border-zinc-800 bg-zinc-950 p-4">
          <div className="text-sm font-semibold text-zinc-200">Soul (system prompt)</div>
          <textarea
            value={soul}
            onChange={(e) => setSoul(e.target.value)}
            placeholder="Describe this bot's persona, tone, constraints, and values..."
            className="min-h-[140px] w-full resize-y rounded-lg border border-zinc-800 bg-zinc-900 px-3 py-2 text-sm text-zinc-100 placeholder:text-zinc-500 focus:outline-none focus:ring-2 focus:ring-zinc-600"
            disabled={loading}
          />
          <div className="text-xs text-zinc-500">Saved to `soul.md` and injected into the system prompt.</div>
        </div>

        <div className="rounded-2xl border border-zinc-800 bg-zinc-950 p-4">
          <div className="mb-2 flex items-center justify-between gap-3">
            <div>
              <div className="text-sm font-semibold text-zinc-200">Skills</div>
              <div className="text-xs text-zinc-500">Enable/disable individual skills for this bot.</div>
            </div>
            <input
              value={skillQuery}
              onChange={(e) => setSkillQuery(e.target.value)}
              placeholder="Search skills…"
              className="w-56 rounded-lg border border-zinc-800 bg-zinc-900 px-3 py-2 text-sm text-zinc-100 placeholder:text-zinc-500 focus:outline-none focus:ring-2 focus:ring-zinc-600"
            />
          </div>

          <div className="mt-3 flex max-h-80 flex-col gap-2 overflow-auto pr-1">
            {filteredSkills.map((s) => {
              const enabled = !skillsDisabled.includes(s.name);
              return (
                <div
                  key={s.name}
                  className="flex items-start justify-between gap-3 rounded-xl border border-zinc-800 bg-zinc-900 px-3 py-2"
                >
                  <div className="min-w-0">
                    <div className="truncate font-mono text-xs text-zinc-200">{s.name}</div>
                    <div className="mt-1 line-clamp-2 text-xs text-zinc-400">{s.description}</div>
                  </div>
                  <button
                    className={classNames(
                      "shrink-0 rounded-full px-3 py-1 text-xs font-semibold",
                      enabled ? "bg-green-500/20 text-green-300" : "bg-zinc-800 text-zinc-300",
                    )}
                    onClick={() => toggleSkill(s.name)}
                    disabled={loading}
                  >
                    {enabled ? "On" : "Off"}
                  </button>
                </div>
              );
            })}
            {filteredSkills.length === 0 && <div className="text-xs text-zinc-500">No skills found.</div>}
          </div>
        </div>

        <div className="rounded-2xl border border-zinc-800 bg-zinc-950 p-4">
          <div className="mb-3">
            <div className="text-sm font-semibold text-zinc-200">MCP Servers</div>
            <div className="text-xs text-zinc-500">Override per bot. Inherit uses the global YAML default.</div>
          </div>
          <div className="flex flex-col gap-2">
            {mcpServers.map((s) => (
              <div
                key={s.name}
                className="flex items-center justify-between gap-3 rounded-xl border border-zinc-800 bg-zinc-900 px-3 py-2"
              >
                <div className="min-w-0">
                  <div className="flex items-center gap-2">
                    <div className="truncate font-mono text-xs text-zinc-200">{s.name}</div>
                    <div className="text-[10px] text-zinc-500">{s.transport}</div>
                    <div className="text-[10px] text-zinc-500">default: {s.enabled_default ? "on" : "off"}</div>
                  </div>
                  <div className="mt-1 truncate text-xs text-zinc-400">
                    {s.transport === "sse" ? s.url || "(no url)" : s.command ? `${s.command} ${(s.args || []).join(" ")}` : "(no command)"}
                  </div>
                </div>
                <select
                  value={mcpValue(s.name)}
                  onChange={(e) => setMcpValue(s.name, e.target.value as "inherit" | "on" | "off")}
                  className="shrink-0 rounded-lg border border-zinc-800 bg-zinc-950 px-2 py-1.5 text-xs text-zinc-200 focus:outline-none focus:ring-2 focus:ring-zinc-600"
                  disabled={loading}
                >
                  <option value="inherit">Inherit</option>
                  <option value="on">Enabled</option>
                  <option value="off">Disabled</option>
                </select>
              </div>
            ))}
            {mcpServers.length === 0 && <div className="text-xs text-zinc-500">No MCP servers configured.</div>}
          </div>
        </div>

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
