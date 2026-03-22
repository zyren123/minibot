import { useEffect, useMemo, useState, type ReactNode } from "react";

import {
  connectMcpServer,
  createProvider,
  createProviderModels,
  deleteProviderModels,
  disconnectMcpServer,
  fetchProviderModels,
  getBotConfig,
  listDashboard,
  listMcpServers,
  listSkills,
  listSubagentCandidates,
  updateBotConfig,
  updateMcpServer,
  updateModel,
  updateProvider,
} from "../lib/api";
import type {
  BotConfig,
  DashboardData,
  FetchedModel,
  MCPServerInfo,
  ProviderRecord,
  SkillInfo,
  SubagentCandidate,
  UpdateMCPServerRequest,
} from "../lib/types";
import { useI18n } from "../lib/i18n";
import PlatformsView from "./PlatformsView";
import SkillsView from "./SkillsView";

function classNames(...xs: Array<string | false | null | undefined>) {
  return xs.filter(Boolean).join(" ");
}

type DashboardTab = "bots" | "providers" | "platforms" | "skills" | "mcp";

type BotDraft = {
  name: string;
  enabled: boolean;
  baseUrl: string;
  fallbackModel: string;
  chatModelId: string;
  streamEnabled: boolean;
  apiKeyMasked: string;
  apiKeyDraft: string;
  toolPlugins: string[];
  soul: string;
  skillsDisabled: string[];
  mcpOverrides: Record<string, boolean>;
  subagentExposable: boolean;
  subagentName: string;
  subagentDescription: string;
  attachedSubagentBotIds: string[];
};

type ProviderDraft = {
  name: string;
  baseUrl: string;
  enabled: boolean;
  apiKeyMasked: string;
  apiKeyDraft: string;
};

type McpDraft = {
  name: string;
  transport: string;
  enabled_default: boolean;
  command: string;
  argsText: string;
  url: string;
  env_keys: string[];
  connected: boolean;
};

function SectionCard(props: { title: string; subtitle?: string; children: ReactNode; right?: ReactNode }) {
  return (
    <div className="rounded-3xl border border-zinc-800 bg-zinc-950/90 p-5 shadow-[0_20px_80px_rgba(0,0,0,0.18)]">
      <div className="mb-4 flex items-start justify-between gap-4">
        <div>
          <div className="text-sm font-semibold text-zinc-100">{props.title}</div>
          {props.subtitle ? <div className="mt-1 text-xs text-zinc-500">{props.subtitle}</div> : null}
        </div>
        {props.right}
      </div>
      {props.children}
    </div>
  );
}

function ToggleButton(props: { enabled: boolean; onClick: () => void; disabled?: boolean; labels?: [string, string] }) {
  const { t } = useI18n();
  const labels = props.labels ?? [t("common.off"), t("common.on")];
  return (
    <button
      type="button"
      className={classNames(
        "rounded-full px-3 py-1 text-xs font-semibold transition",
        props.enabled ? "bg-emerald-500/20 text-emerald-300" : "bg-zinc-800 text-zinc-300",
        props.disabled && "opacity-50",
      )}
      onClick={props.onClick}
      disabled={props.disabled}
    >
      {props.enabled ? labels[1] : labels[0]}
    </button>
  );
}

function ListEditor(props: {
  label: string;
  items: string[];
  setItems: (items: string[]) => void;
  placeholder: string;
}) {
  const [draft, setDraft] = useState("");
  const { t } = useI18n();

  return (
    <SectionCard title={props.label}>
      <div className="flex gap-2">
        <input
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          placeholder={props.placeholder}
          className="w-full rounded-2xl border border-zinc-800 bg-zinc-900 px-3 py-2 text-sm text-zinc-100 placeholder:text-zinc-500 focus:outline-none focus:ring-2 focus:ring-zinc-600"
        />
        <button
          type="button"
          className="rounded-2xl bg-zinc-100 px-4 py-2 text-sm font-semibold text-zinc-900 hover:bg-white disabled:opacity-50"
          onClick={() => {
            const value = draft.trim();
            if (!value) return;
            props.setItems([...props.items, value]);
            setDraft("");
          }}
          disabled={!draft.trim()}
        >
          {t("common.add")}
        </button>
      </div>
      <div className="mt-3 flex flex-col gap-2">
        {props.items.map((item, idx) => (
          <div key={`${item}-${idx}`} className="flex items-center justify-between rounded-2xl border border-zinc-800 bg-zinc-900 px-3 py-2">
            <div className="min-w-0 truncate font-mono text-xs text-zinc-200">{item}</div>
            <button
              type="button"
              className="rounded-xl px-2 py-1 text-xs text-zinc-300 hover:bg-zinc-800"
              onClick={() => props.setItems(props.items.filter((_, itemIdx) => itemIdx !== idx))}
            >
              {t("common.remove")}
            </button>
          </div>
        ))}
        {props.items.length === 0 ? <div className="text-xs text-zinc-500">{t("config.listEditor.empty")}</div> : null}
      </div>
    </SectionCard>
  );
}

function draftComparable(draft: BotDraft | null) {
  if (!draft) return "";
  return JSON.stringify({
    ...draft,
    apiKeyDraft: draft.apiKeyDraft.trim() ? "__set__" : "",
  });
}

function providerComparable(draft: ProviderDraft | null) {
  if (!draft) return "";
  return JSON.stringify({
    ...draft,
    apiKeyDraft: draft.apiKeyDraft.trim() ? "__set__" : "",
  });
}

function mcpDraftFromServer(server: MCPServerInfo): McpDraft {
  return {
    name: server.name,
    transport: server.transport,
    enabled_default: server.enabled_default,
    command: server.command ?? "",
    argsText: (server.args ?? []).join("\n"),
    url: server.url ?? "",
    env_keys: server.env_keys ?? [],
    connected: server.connected,
  };
}

function mcpComparable(draft: McpDraft | null) {
  if (!draft) return "";
  return JSON.stringify({
    name: draft.name,
    transport: draft.transport,
    enabled_default: draft.enabled_default,
    command: draft.command.trim(),
    argsText: draft.argsText
      .split(/\r?\n/)
      .map((item) => item.trim())
      .filter(Boolean),
    url: draft.url.trim(),
  });
}

function emptyBotDraft(cfg: BotConfig): BotDraft {
  return {
    name: cfg.name ?? "",
    enabled: cfg.enabled,
    baseUrl: cfg.base_url ?? "",
    fallbackModel: cfg.model ?? "",
    chatModelId: cfg.chat_model_id ?? "",
    streamEnabled: cfg.stream_enabled,
    apiKeyMasked: cfg.api_key_masked ?? "",
    apiKeyDraft: "",
    toolPlugins: cfg.tool_plugins ?? [],
    soul: cfg.soul ?? "",
    skillsDisabled: cfg.skills_disabled ?? [],
    mcpOverrides: cfg.mcp_overrides ?? {},
    subagentExposable: cfg.subagent_exposable,
    subagentName: cfg.subagent_name ?? "",
    subagentDescription: cfg.subagent_description ?? "",
    attachedSubagentBotIds: cfg.attached_subagent_bot_ids ?? [],
  };
}

function providerDraftFromRecord(provider: ProviderRecord): ProviderDraft {
  return {
    name: provider.name,
    baseUrl: provider.base_url,
    enabled: provider.enabled,
    apiKeyMasked: provider.api_key_masked ?? "",
    apiKeyDraft: "",
  };
}

export default function ConfigView(props: {
  botId: string;
  onBotsChanged?: () => void;
  onSelectBot?: (botId: string) => void;
}) {
  const { botId, onBotsChanged, onSelectBot } = props;
  const { t } = useI18n();

  const [tab, setTab] = useState<DashboardTab>("bots");
  const [dashboard, setDashboard] = useState<DashboardData | null>(null);
  const [skills, setSkills] = useState<SkillInfo[]>([]);
  const [mcpServers, setMcpServers] = useState<MCPServerInfo[]>([]);
  const [candidates, setCandidates] = useState<SubagentCandidate[]>([]);

  const [loading, setLoading] = useState(true);
  const [savingBot, setSavingBot] = useState(false);
  const [status, setStatus] = useState<string | null>(null);

  const [botDraft, setBotDraft] = useState<BotDraft | null>(null);
  const [botBaseline, setBotBaseline] = useState("");
  const [skillQuery, setSkillQuery] = useState("");

  const [selectedProviderId, setSelectedProviderId] = useState<string | null>(null);
  const [providerDraft, setProviderDraft] = useState<ProviderDraft | null>(null);
  const [providerBaseline, setProviderBaseline] = useState("");
  const [savingProvider, setSavingProvider] = useState(false);
  const [selectedMcpServerName, setSelectedMcpServerName] = useState<string | null>(null);
  const [mcpDraft, setMcpDraft] = useState<McpDraft | null>(null);
  const [mcpBaseline, setMcpBaseline] = useState("");
  const [savingMcp, setSavingMcp] = useState(false);
  const [togglingMcpConnection, setTogglingMcpConnection] = useState<string | null>(null);

  const [manualModelName, setManualModelName] = useState("");
  const [fetchingModels, setFetchingModels] = useState(false);
  const [fetchedModels, setFetchedModels] = useState<FetchedModel[]>([]);
  const [fetchedModelQuery, setFetchedModelQuery] = useState("");
  const [selectedFetchedModels, setSelectedFetchedModels] = useState<string[]>([]);
  const [selectedProviderModelIds, setSelectedProviderModelIds] = useState<string[]>([]);
  const [deleteModelsOpen, setDeleteModelsOpen] = useState(false);
  const [pendingDeleteModelIds, setPendingDeleteModelIds] = useState<string[]>([]);
  const [deletingModels, setDeletingModels] = useState(false);

  const [providerModalOpen, setProviderModalOpen] = useState(false);
  const [providerNameDraft, setProviderNameDraft] = useState("");
  const [providerBaseUrlDraft, setProviderBaseUrlDraft] = useState("");
  const [providerApiKeyDraft, setProviderApiKeyDraft] = useState("");
  const [creatingProvider, setCreatingProvider] = useState(false);

  const filteredSkills = useMemo(() => {
    const q = skillQuery.trim().toLowerCase();
    const activeOnly = skills.filter((item) => item.is_active);
    if (!q) return activeOnly;
    return activeOnly.filter((item) => `${item.name} ${item.description}`.toLowerCase().includes(q));
  }, [skillQuery, skills]);

  const currentBotMeta = useMemo(
    () => dashboard?.bots.find((item) => item.bot_id === botId) ?? null,
    [botId, dashboard],
  );

  const selectedProvider = useMemo(
    () => dashboard?.providers.find((item) => item.provider_id === selectedProviderId) ?? null,
    [dashboard, selectedProviderId],
  );

  const selectedMcpServer = useMemo(
    () => mcpServers.find((item) => item.name === selectedMcpServerName) ?? null,
    [mcpServers, selectedMcpServerName],
  );

  const providerModels = useMemo(
    () => (dashboard?.models ?? []).filter((item) => item.provider_id === selectedProviderId),
    [dashboard, selectedProviderId],
  );

  const filteredFetchedModels = useMemo(() => {
    const query = fetchedModelQuery.trim().toLowerCase();
    if (!query) return fetchedModels;
    return fetchedModels.filter((item) => item.model_name.toLowerCase().includes(query));
  }, [fetchedModelQuery, fetchedModels]);

  const selectableFilteredFetchedNames = useMemo(
    () => filteredFetchedModels.filter((item) => !item.already_added).map((item) => item.model_name),
    [filteredFetchedModels],
  );

  const attachedSubagents = useMemo(() => {
    if (!botDraft || !dashboard) return [];
    const botMap = new Map(dashboard.bots.map((item) => [item.bot_id, item]));
    const candidateMap = new Map(candidates.map((item) => [item.bot_id, item]));
    return botDraft.attachedSubagentBotIds.map((attachedId) => {
      const meta = botMap.get(attachedId);
      const candidate = candidateMap.get(attachedId);
        return {
        bot_id: attachedId,
        name: meta?.name ?? candidate?.name ?? attachedId,
        label: candidate?.subagent_name ?? meta?.subagent_name ?? meta?.name ?? attachedId,
        description: candidate?.subagent_description ?? meta?.subagent_description ?? t("config.subagents.unavailableDescription"),
        available: Boolean(meta?.enabled && meta?.subagent_exposable),
      };
    });
  }, [botDraft, candidates, dashboard, t]);

  const availableCandidates = useMemo(() => {
    if (!botDraft) return candidates;
    const attached = new Set(botDraft.attachedSubagentBotIds);
    return candidates.filter((item) => !attached.has(item.bot_id));
  }, [botDraft, candidates]);

  const activeModels = dashboard?.available_models ?? [];
  const botDirty = draftComparable(botDraft) !== botBaseline;
  const providerDirty = providerComparable(providerDraft) !== providerBaseline;
  const mcpDirty = mcpComparable(mcpDraft) !== mcpBaseline;
  const allVisibleFetchedSelected =
    selectableFilteredFetchedNames.length > 0 &&
    selectableFilteredFetchedNames.every((item) => selectedFetchedModels.includes(item));
  const allProviderModelsSelected = providerModels.length > 0 && selectedProviderModelIds.length === providerModels.length;
  const pendingDeleteModels = providerModels.filter((item) => pendingDeleteModelIds.includes(item.model_id));

  async function refreshDashboard(preferredProviderId?: string | null) {
    const next = await listDashboard();
    setDashboard(next);
    const candidateId = preferredProviderId ?? selectedProviderId;
    if (candidateId && next.providers.some((item) => item.provider_id === candidateId)) {
      setSelectedProviderId(candidateId);
      return;
    }
    setSelectedProviderId(next.providers[0]?.provider_id ?? null);
  }

  async function refreshBot(nextBotId: string = botId) {
    const cfg = await getBotConfig(nextBotId);
    const draft = emptyBotDraft(cfg);
    setBotDraft(draft);
    setBotBaseline(draftComparable(draft));
    setCandidates(await listSubagentCandidates(nextBotId));
  }

  async function refreshSkills() {
    setSkills(await listSkills());
  }

  async function refreshMcpServers(preferredName?: string | null) {
    const next = await listMcpServers();
    setMcpServers(next);
    const candidateName = preferredName ?? selectedMcpServerName;
    if (candidateName && next.some((item) => item.name === candidateName)) {
      setSelectedMcpServerName(candidateName);
      return;
    }
    setSelectedMcpServerName(next[0]?.name ?? null);
  }

  async function handleSkillsChanged() {
    await refreshSkills();
    await refreshBot(botId);
  }

  useEffect(() => {
    Promise.all([refreshSkills(), refreshMcpServers(null)])
      .catch((err) => setStatus(String(err)));
  }, []);

  useEffect(() => {
    setLoading(true);
    Promise.all([refreshDashboard(null), refreshBot(botId)])
      .catch((err) => setStatus(String(err)))
      .finally(() => setLoading(false));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [botId]);

  useEffect(() => {
    if (!selectedProvider) {
      setProviderDraft(null);
      setProviderBaseline("");
      setFetchedModels([]);
      setFetchedModelQuery("");
      setSelectedFetchedModels([]);
      setSelectedProviderModelIds([]);
      setDeleteModelsOpen(false);
      setPendingDeleteModelIds([]);
      return;
    }
    const draft = providerDraftFromRecord(selectedProvider);
    setProviderDraft(draft);
    setProviderBaseline(providerComparable(draft));
    setFetchedModels([]);
    setFetchedModelQuery("");
    setSelectedFetchedModels([]);
    setSelectedProviderModelIds([]);
    setDeleteModelsOpen(false);
    setPendingDeleteModelIds([]);
  }, [selectedProvider]);

  useEffect(() => {
    const validModelIds = new Set(providerModels.map((item) => item.model_id));
    setSelectedProviderModelIds((prev) => prev.filter((item) => validModelIds.has(item)));
    setPendingDeleteModelIds((prev) => prev.filter((item) => validModelIds.has(item)));
  }, [providerModels]);

  useEffect(() => {
    if (!selectedMcpServerName) {
      setMcpDraft(null);
      setMcpBaseline("");
      return;
    }
    const selected = mcpServers.find((item) => item.name === selectedMcpServerName) ?? null;
    if (!selected) {
      setMcpDraft(null);
      setMcpBaseline("");
      return;
    }
    const draft = mcpDraftFromServer(selected);
    setMcpDraft(draft);
    setMcpBaseline(mcpComparable(draft));
  }, [mcpServers, selectedMcpServerName]);

  useEffect(() => {
    function onKeyDown(e: KeyboardEvent) {
      if (e.key !== "Escape") return;
      setDeleteModelsOpen(false);
      setPendingDeleteModelIds([]);
    }
    if (!deleteModelsOpen) return;
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [deleteModelsOpen]);

  function setBotField<K extends keyof BotDraft>(key: K, value: BotDraft[K]) {
    setBotDraft((prev) => (prev ? { ...prev, [key]: value } : prev));
  }

  function setProviderField<K extends keyof ProviderDraft>(key: K, value: ProviderDraft[K]) {
    setProviderDraft((prev) => (prev ? { ...prev, [key]: value } : prev));
  }

  function setMcpField<K extends keyof McpDraft>(key: K, value: McpDraft[K]) {
    setMcpDraft((prev) => (prev ? { ...prev, [key]: value } : prev));
  }

  function toggleSkill(skillName: string) {
    setBotDraft((prev) => {
      if (!prev) return prev;
      const next = new Set(prev.skillsDisabled);
      if (next.has(skillName)) next.delete(skillName);
      else next.add(skillName);
      return { ...prev, skillsDisabled: Array.from(next).sort() };
    });
  }

  function mcpValue(name: string): "inherit" | "on" | "off" {
    if (!botDraft) return "inherit";
    if (Object.prototype.hasOwnProperty.call(botDraft.mcpOverrides, name)) {
      return botDraft.mcpOverrides[name] ? "on" : "off";
    }
    return "inherit";
  }

  function setMcpValue(name: string, value: "inherit" | "on" | "off") {
    setBotDraft((prev) => {
      if (!prev) return prev;
      const next = { ...prev.mcpOverrides };
      if (value === "inherit") delete next[name];
      else next[name] = value === "on";
      return { ...prev, mcpOverrides: next };
    });
  }

  async function onSaveBot() {
    if (!botDraft) return;
    setSavingBot(true);
    setStatus(null);
    try {
      await updateBotConfig(botId, {
        name: botDraft.name.trim() || null,
        enabled: botDraft.enabled,
        base_url: botDraft.baseUrl.trim() || null,
        model: botDraft.fallbackModel.trim() || null,
        chat_model_id: botDraft.chatModelId || null,
        stream_enabled: botDraft.streamEnabled,
        tool_plugins: botDraft.toolPlugins,
        skills_disabled: botDraft.skillsDisabled,
        mcp_overrides: botDraft.mcpOverrides,
        soul: botDraft.soul,
        subagent_exposable: botDraft.subagentExposable,
        subagent_name: botDraft.subagentName.trim() || null,
        subagent_description: botDraft.subagentDescription.trim() || null,
        attached_subagent_bot_ids: botDraft.attachedSubagentBotIds,
        api_key: botDraft.apiKeyDraft.trim() ? botDraft.apiKeyDraft.trim() : undefined,
      });
      await refreshDashboard(selectedProviderId);
      await refreshBot(botId);
      onBotsChanged?.();
      setStatus(t("config.status.botSaved"));
    } catch (err) {
      setStatus(String(err));
    } finally {
      setSavingBot(false);
    }
  }

  async function onToggleBotEnabled(targetBotId: string, enabled: boolean) {
    try {
      setStatus(null);
      await updateBotConfig(targetBotId, { enabled });
      await refreshDashboard(selectedProviderId);
      if (targetBotId === botId) {
        await refreshBot(botId);
        onBotsChanged?.();
      }
    } catch (err) {
      setStatus(String(err));
    }
  }

  async function onSaveProvider() {
    if (!selectedProvider || !providerDraft) return;
    setSavingProvider(true);
    setStatus(null);
    try {
      await updateProvider(selectedProvider.provider_id, {
        name: providerDraft.name.trim(),
        base_url: providerDraft.baseUrl.trim(),
        enabled: providerDraft.enabled,
        api_key: providerDraft.apiKeyDraft.trim() ? providerDraft.apiKeyDraft.trim() : undefined,
      });
      await refreshDashboard(selectedProvider.provider_id);
      setStatus(t("config.status.providerUpdated"));
    } catch (err) {
      setStatus(String(err));
    } finally {
      setSavingProvider(false);
    }
  }

  async function onSaveMcpServer() {
    if (!mcpDraft) return;
    setSavingMcp(true);
    setStatus(null);
    try {
      const payload: UpdateMCPServerRequest = {
        enabled_default: mcpDraft.enabled_default,
      };
      if (mcpDraft.transport === "stdio") {
        payload.command = mcpDraft.command.trim() || null;
        payload.args = mcpDraft.argsText
          .split(/\r?\n/)
          .map((item) => item.trim())
          .filter(Boolean);
      } else if (mcpDraft.transport === "sse") {
        payload.url = mcpDraft.url.trim() || null;
      }
      await updateMcpServer(mcpDraft.name, payload);
      await refreshMcpServers(mcpDraft.name);
      await refreshBot(botId);
      setStatus(t("config.status.mcpSaved"));
    } catch (err) {
      setStatus(String(err));
    } finally {
      setSavingMcp(false);
    }
  }

  async function onToggleMcpConnection(server: MCPServerInfo) {
    setTogglingMcpConnection(server.name);
    setStatus(null);
    try {
      if (server.connected) {
        await disconnectMcpServer(server.name);
        setStatus(t("config.status.mcpDisconnected", { name: server.name }));
      } else {
        await connectMcpServer(server.name);
        setStatus(t("config.status.mcpConnected", { name: server.name }));
      }
      await refreshMcpServers(server.name);
      await refreshBot(botId);
    } catch (err) {
      setStatus(String(err));
    } finally {
      setTogglingMcpConnection(null);
    }
  }

  async function onCreateProvider() {
    if (!providerNameDraft.trim() || !providerBaseUrlDraft.trim()) return;
    setCreatingProvider(true);
    setStatus(null);
    try {
      const created = await createProvider({
        name: providerNameDraft.trim(),
        base_url: providerBaseUrlDraft.trim(),
        api_key: providerApiKeyDraft.trim() || undefined,
        enabled: true,
      });
      setProviderModalOpen(false);
      setProviderNameDraft("");
      setProviderBaseUrlDraft("");
      setProviderApiKeyDraft("");
      await refreshDashboard(created.provider_id);
      setTab("providers");
      setStatus(t("config.status.providerAdded"));
    } catch (err) {
      setStatus(String(err));
    } finally {
      setCreatingProvider(false);
    }
  }

  async function onFetchModels() {
    if (!selectedProvider) return;
    setFetchingModels(true);
    setStatus(null);
    try {
      const models = await fetchProviderModels(selectedProvider.provider_id);
      setFetchedModels(models);
      setFetchedModelQuery("");
      setSelectedFetchedModels([]);
    } catch (err) {
      setStatus(String(err));
    } finally {
      setFetchingModels(false);
    }
  }

  async function onImportModels(modelNames: string[], addedVia: string) {
    if (!selectedProvider || modelNames.length === 0) return;
    try {
      setStatus(null);
      await createProviderModels(selectedProvider.provider_id, { model_names: modelNames, added_via: addedVia });
      await refreshDashboard(selectedProvider.provider_id);
      setFetchedModels((prev) =>
        prev.map((item) => (modelNames.includes(item.model_name) ? { ...item, already_added: true } : item)),
      );
      setSelectedFetchedModels((prev) => prev.filter((item) => !modelNames.includes(item)));
      setManualModelName("");
      setStatus(t("config.status.importedModels", { count: modelNames.length }));
    } catch (err) {
      setStatus(String(err));
    }
  }

  async function onToggleModel(modelId: string, enabled: boolean) {
    try {
      setStatus(null);
      await updateModel(modelId, { enabled });
      await refreshDashboard(selectedProviderId);
      if (botDraft?.chatModelId === modelId) {
        await refreshBot(botId);
      }
    } catch (err) {
      setStatus(String(err));
    }
  }

  function toggleFetchedModelSelection(modelName: string) {
    setSelectedFetchedModels((prev) =>
      prev.includes(modelName) ? prev.filter((item) => item !== modelName) : [...prev, modelName],
    );
  }

  function toggleFetchedSelectionForVisible() {
    if (selectableFilteredFetchedNames.length === 0) return;
    if (allVisibleFetchedSelected) {
      setSelectedFetchedModels((prev) => prev.filter((item) => !selectableFilteredFetchedNames.includes(item)));
      return;
    }
    setSelectedFetchedModels((prev) => Array.from(new Set([...prev, ...selectableFilteredFetchedNames])));
  }

  function toggleProviderModelSelection(modelId: string) {
    setSelectedProviderModelIds((prev) =>
      prev.includes(modelId) ? prev.filter((item) => item !== modelId) : [...prev, modelId],
    );
  }

  function toggleAllProviderModels() {
    setSelectedProviderModelIds(allProviderModelsSelected ? [] : providerModels.map((item) => item.model_id));
  }

  function requestDeleteModels(modelIds: string[]) {
    const nextIds = providerModels
      .filter((item) => modelIds.includes(item.model_id))
      .map((item) => item.model_id);
    if (nextIds.length === 0) return;
    setPendingDeleteModelIds(nextIds);
    setDeleteModelsOpen(true);
  }

  async function confirmDeleteModels() {
    if (!selectedProvider || pendingDeleteModelIds.length === 0) return;
    setDeletingModels(true);
    try {
      setStatus(null);
      const deletedNameMap = new Map(
        providerModels
          .filter((item) => pendingDeleteModelIds.includes(item.model_id))
          .map((item) => [item.model_id, item.model_name]),
      );
      const result = await deleteProviderModels(selectedProvider.provider_id, { model_ids: pendingDeleteModelIds });
      const deletedIds = result.deleted_model_ids;
      const deletedNames = new Set(deletedIds.map((item) => deletedNameMap.get(item)).filter(Boolean));
      await refreshDashboard(selectedProvider.provider_id);
      if (botDraft?.chatModelId && deletedIds.includes(botDraft.chatModelId)) {
        await refreshBot(botId);
      }
      setFetchedModels((prev) =>
        prev.map((item) => (deletedNames.has(item.model_name) ? { ...item, already_added: false } : item)),
      );
      setSelectedProviderModelIds((prev) => prev.filter((item) => !deletedIds.includes(item)));
      setDeleteModelsOpen(false);
      setPendingDeleteModelIds([]);
      setStatus(t("config.status.deletedModels", { count: result.deleted_count }));
    } catch (err) {
      setStatus(String(err));
    } finally {
      setDeletingModels(false);
    }
  }

  const summaryCards = [
    {
      label: t("config.summary.providers"),
      value: dashboard?.providers.length ?? 0,
      accent: "from-sky-500/25 to-sky-500/5",
    },
    { label: t("config.summary.models"), value: dashboard?.models.length ?? 0, accent: "from-emerald-500/25 to-emerald-500/5" },
    { label: t("config.summary.bots"), value: dashboard?.bots.length ?? 0, accent: "from-amber-500/25 to-amber-500/5" },
    {
      label: t("config.summary.platforms"),
      value: dashboard?.platforms.length ?? 0,
      accent: "from-violet-500/25 to-violet-500/5",
    },
    {
      label: t("config.summary.skills"),
      value: skills.filter((item) => item.is_active).length,
      accent: "from-rose-500/25 to-rose-500/5",
    },
    {
      label: t("config.summary.mcp"),
      value: mcpServers.filter((item) => item.enabled_default).length,
      accent: "from-cyan-500/25 to-cyan-500/5",
    },
  ];

  return (
    <div className="app-dashboard-shell h-full overflow-auto px-5 py-6">
      <div className="mx-auto flex max-w-7xl flex-col gap-6">
        <div className="grid gap-4 md:grid-cols-6">
          {summaryCards.map((item) => (
            <div
              key={item.label}
              className={classNames(
                "rounded-3xl border border-zinc-800 bg-gradient-to-br p-4",
                item.accent,
              )}
            >
              <div className="text-xs uppercase tracking-[0.22em] text-zinc-500">{item.label}</div>
              <div className="mt-3 text-3xl font-semibold text-zinc-100">{item.value}</div>
            </div>
          ))}
        </div>

        <div className="flex flex-wrap items-center justify-between gap-3">
          <div>
            <div className="text-2xl font-semibold tracking-tight text-zinc-100">{t("config.header.title")}</div>
            <div className="mt-1 text-sm text-zinc-400">{t("config.header.subtitle")}</div>
          </div>
          <div className="flex items-center gap-2">
            {status ? <div className="text-xs text-zinc-400">{status}</div> : null}
            <div className="rounded-2xl border border-zinc-800 bg-zinc-950/90 p-1">
              {(["bots", "providers", "platforms", "skills", "mcp"] as DashboardTab[]).map((item) => (
                <button
                  key={item}
                  type="button"
                  onClick={() => setTab(item)}
                  className={classNames(
                    "rounded-xl px-3 py-2 text-sm font-medium capitalize transition",
                    tab === item ? "bg-zinc-100 text-zinc-900" : "text-zinc-300 hover:bg-zinc-900",
                  )}
                >
                  {item === "bots"
                    ? t("config.tab.bots")
                    : item === "providers"
                      ? t("config.tab.providers")
                      : item === "platforms"
                        ? t("config.tab.platforms")
                      : item === "skills"
                        ? t("config.tab.skills")
                        : t("config.tab.mcp")}
                </button>
              ))}
            </div>
          </div>
        </div>

        {loading ? <div className="text-sm text-zinc-500">{t("config.loading")}</div> : null}

        {tab === "bots" ? (
          <div className="grid gap-6 xl:grid-cols-[320px_minmax(0,1fr)]">
            <div className="flex flex-col gap-4">
              <SectionCard
                title={t("config.section.allBots.title")}
                subtitle={t("config.section.allBots.subtitle")}
              >
                <div className="space-y-3">
                  {(dashboard?.bots ?? []).map((bot) => (
                    <div
                      key={bot.bot_id}
                      className={classNames(
                        "rounded-3xl border px-4 py-3 transition",
                        bot.bot_id === botId
                          ? "border-zinc-600 bg-zinc-900"
                          : "border-zinc-800 bg-zinc-950 hover:border-zinc-700 hover:bg-zinc-900/60",
                      )}
                    >
                      <div className="flex items-start justify-between gap-3">
                        <button
                          type="button"
                          className="min-w-0 flex-1 text-left"
                          onClick={() => onSelectBot?.(bot.bot_id)}
                        >
                          <div className="truncate text-sm font-semibold text-zinc-100">{bot.name}</div>
                          <div className="mt-1 flex flex-wrap gap-2 text-[11px] text-zinc-500">
                            <span className="rounded-full bg-zinc-800 px-2 py-0.5 font-mono">{bot.bot_id}</span>
                            {bot.is_default ? <span>{t("config.bot.tag.default")}</span> : null}
                            {bot.subagent_exposable ? <span>{t("config.bot.tag.subagentReady")}</span> : null}
                          </div>
                        </button>
                        <ToggleButton
                          enabled={bot.enabled}
                          onClick={() => void onToggleBotEnabled(bot.bot_id, !bot.enabled)}
                        />
                      </div>
                      {!bot.chat_ready ? (
                        <div className="mt-3 rounded-2xl border border-amber-500/20 bg-amber-500/10 px-3 py-2 text-xs text-amber-200">
                          {bot.chat_disabled_reason ?? t("config.bot.chatUnavailable")}
                        </div>
                      ) : null}
                    </div>
                  ))}
                </div>
              </SectionCard>
            </div>

            <div className="flex min-w-0 flex-col gap-4">
              <SectionCard
                title={t("config.section.botDetail.title", { name: currentBotMeta?.name ?? "" })}
                subtitle={t("config.section.botDetail.subtitle")}
                right={
                  <button
                    type="button"
                    className="rounded-2xl bg-zinc-100 px-4 py-2 text-sm font-semibold text-zinc-900 hover:bg-white disabled:opacity-50"
                    onClick={() => void onSaveBot()}
                    disabled={!botDraft || savingBot || !botDirty}
                  >
                    {savingBot ? t("config.bot.saving") : t("config.bot.save")}
                  </button>
                }
              >
                {botDraft ? (
                  <div className="grid gap-4 md:grid-cols-2">
                    <div className="grid gap-2">
                      <label className="text-sm font-semibold text-zinc-200">{t("config.bot.name")}</label>
                      <input
                        value={botDraft.name}
                        onChange={(e) => setBotField("name", e.target.value)}
                        placeholder={t("config.bot.name.placeholder")}
                        className="rounded-2xl border border-zinc-800 bg-zinc-900 px-3 py-2 text-sm text-zinc-100 placeholder:text-zinc-500 focus:outline-none focus:ring-2 focus:ring-zinc-600"
                      />
                    </div>
                    <div className="flex items-center justify-between rounded-2xl border border-zinc-800 bg-zinc-900 px-4 py-3">
                      <div>
                        <div className="text-sm font-semibold text-zinc-200">{t("config.bot.enabled")}</div>
                        <div className="text-xs text-zinc-500">{t("config.bot.enabled.subtitle")}</div>
                      </div>
                      <ToggleButton enabled={botDraft.enabled} onClick={() => setBotField("enabled", !botDraft.enabled)} />
                    </div>
                    <div className="grid gap-2">
                      <label className="text-sm font-semibold text-zinc-200">{t("config.bot.chatModel")}</label>
                      <select
                        value={botDraft.chatModelId}
                        onChange={(e) => setBotField("chatModelId", e.target.value)}
                        className="rounded-2xl border border-zinc-800 bg-zinc-900 px-3 py-2 text-sm text-zinc-100 focus:outline-none focus:ring-2 focus:ring-zinc-600"
                      >
                        <option value="">{t("config.bot.chatModel.fallback")}</option>
                        {activeModels.map((item) => (
                          <option key={item.model_id} value={item.model_id}>
                            {item.provider_name} / {item.label}
                          </option>
                        ))}
                      </select>
                      {!currentBotMeta?.chat_ready ? (
                        <div className="text-xs text-amber-300">{currentBotMeta?.chat_disabled_reason}</div>
                      ) : null}
                    </div>
                    <div className="flex items-center justify-between rounded-2xl border border-zinc-800 bg-zinc-900 px-4 py-3">
                      <div>
                        <div className="text-sm font-semibold text-zinc-200">{t("config.bot.streaming")}</div>
                        <div className="text-xs text-zinc-500">{t("config.bot.streaming.subtitle")}</div>
                      </div>
                      <ToggleButton
                        enabled={botDraft.streamEnabled}
                        onClick={() => setBotField("streamEnabled", !botDraft.streamEnabled)}
                      />
                    </div>
                  </div>
                ) : null}
              </SectionCard>

              {botDraft ? (
                <>
                  <SectionCard
                    title={t("config.section.subagentIdentity.title")}
                    subtitle={t("config.section.subagentIdentity.subtitle")}
                  >
                    <div className="grid gap-4 md:grid-cols-2">
                      <div className="flex items-center justify-between rounded-2xl border border-zinc-800 bg-zinc-900 px-4 py-3">
                        <div>
                          <div className="text-sm font-semibold text-zinc-200">{t("config.bot.allowSubagent")}</div>
                          <div className="text-xs text-zinc-500">{t("config.bot.allowSubagent.subtitle")}</div>
                        </div>
                        <ToggleButton
                          enabled={botDraft.subagentExposable}
                          onClick={() => setBotField("subagentExposable", !botDraft.subagentExposable)}
                        />
                      </div>
                      <div className="grid gap-2">
                        <label className="text-sm font-semibold text-zinc-200">
                          {t("config.bot.subagentDisplayName")}
                        </label>
                        <input
                          value={botDraft.subagentName}
                          onChange={(e) => setBotField("subagentName", e.target.value)}
                          placeholder={botDraft.name || currentBotMeta?.name || botId}
                          className="rounded-2xl border border-zinc-800 bg-zinc-900 px-3 py-2 text-sm text-zinc-100 placeholder:text-zinc-500 focus:outline-none focus:ring-2 focus:ring-zinc-600"
                        />
                      </div>
                    </div>
                    <div className="mt-4 grid gap-2">
                      <label className="text-sm font-semibold text-zinc-200">{t("config.bot.subagentDescription")}</label>
                      <textarea
                        value={botDraft.subagentDescription}
                        onChange={(e) => setBotField("subagentDescription", e.target.value)}
                        placeholder={t("config.bot.subagentDescription.placeholder")}
                        className="min-h-[84px] rounded-2xl border border-zinc-800 bg-zinc-900 px-3 py-2 text-sm text-zinc-100 placeholder:text-zinc-500 focus:outline-none focus:ring-2 focus:ring-zinc-600"
                      />
                    </div>
                  </SectionCard>

                  <SectionCard
                    title={t("config.section.attachedSubagents.title")}
                    subtitle={t("config.section.attachedSubagents.subtitle")}
                  >
                    <div className="grid gap-5 lg:grid-cols-2">
                      <div>
                        <div className="mb-2 text-xs font-semibold uppercase tracking-[0.22em] text-zinc-500">
                          {t("config.subagents.attached")}
                        </div>
                        <div className="space-y-3">
                          {attachedSubagents.map((item) => (
                            <div key={item.bot_id} className="rounded-2xl border border-zinc-800 bg-zinc-900 px-4 py-3">
                              <div className="flex items-start justify-between gap-3">
                                <div className="min-w-0">
                                  <div className="truncate text-sm font-semibold text-zinc-100">{item.label}</div>
                                  <div className="mt-1 text-xs text-zinc-400">{item.description}</div>
                                  {!item.available ? (
                                    <div className="mt-2 text-[11px] text-amber-300">{t("config.subagents.unavailable")}</div>
                                  ) : null}
                                </div>
                                <button
                                  type="button"
                                  className="rounded-xl bg-zinc-800 px-3 py-1.5 text-xs text-zinc-200 hover:bg-zinc-700"
                                  onClick={() =>
                                    setBotField(
                                      "attachedSubagentBotIds",
                                      botDraft.attachedSubagentBotIds.filter((attachedId) => attachedId !== item.bot_id),
                                    )
                                  }
                                >
                                  {t("common.remove")}
                                </button>
                              </div>
                            </div>
                          ))}
                          {attachedSubagents.length === 0 ? (
                            <div className="rounded-2xl border border-dashed border-zinc-800 px-4 py-5 text-sm text-zinc-500">
                              {t("config.subagents.emptyAttached")}
                            </div>
                          ) : null}
                        </div>
                      </div>

                      <div>
                        <div className="mb-2 text-xs font-semibold uppercase tracking-[0.22em] text-zinc-500">
                          {t("config.subagents.available")}
                        </div>
                        <div className="space-y-3">
                          {availableCandidates.map((item) => (
                            <div key={item.bot_id} className="rounded-2xl border border-zinc-800 bg-zinc-900 px-4 py-3">
                              <div className="flex items-start justify-between gap-3">
                                <div className="min-w-0">
                                  <div className="truncate text-sm font-semibold text-zinc-100">{item.subagent_name}</div>
                                  <div className="mt-1 text-xs text-zinc-400">{item.subagent_description}</div>
                                  <div className="mt-2 text-[11px] text-zinc-500">
                                    {t("config.subagents.sourceBot", { name: item.name })}
                                  </div>
                                </div>
                                <button
                                  type="button"
                                  className="rounded-xl bg-zinc-100 px-3 py-1.5 text-xs font-semibold text-zinc-900 hover:bg-white"
                                  onClick={() =>
                                    setBotField("attachedSubagentBotIds", [
                                      ...botDraft.attachedSubagentBotIds,
                                      item.bot_id,
                                    ])
                                  }
                                >
                                  {t("common.add")}
                                </button>
                              </div>
                            </div>
                          ))}
                          {availableCandidates.length === 0 ? (
                            <div className="rounded-2xl border border-dashed border-zinc-800 px-4 py-5 text-sm text-zinc-500">
                              {t("config.subagents.emptyAvailable")}
                            </div>
                          ) : null}
                        </div>
                      </div>
                    </div>
                  </SectionCard>

                  <SectionCard title={t("config.section.soul.title")} subtitle={t("config.section.soul.subtitle")}>
                    <textarea
                      value={botDraft.soul}
                      onChange={(e) => setBotField("soul", e.target.value)}
                      placeholder={t("config.section.soul.placeholder")}
                      className="min-h-[160px] w-full rounded-2xl border border-zinc-800 bg-zinc-900 px-3 py-2 text-sm text-zinc-100 placeholder:text-zinc-500 focus:outline-none focus:ring-2 focus:ring-zinc-600"
                    />
                  </SectionCard>

                  <div className="grid gap-4 xl:grid-cols-2">
                    <SectionCard
                      title={t("config.section.skills.title")}
                      subtitle={t("config.section.skills.subtitle")}
                      right={
                        <div className="flex items-center gap-2">
                          <input
                            value={skillQuery}
                            onChange={(e) => setSkillQuery(e.target.value)}
                            placeholder={t("config.skills.search")}
                            className="w-52 rounded-2xl border border-zinc-800 bg-zinc-900 px-3 py-2 text-sm text-zinc-100 placeholder:text-zinc-500 focus:outline-none focus:ring-2 focus:ring-zinc-600"
                          />
                          <button
                            type="button"
                            className="rounded-2xl bg-zinc-900 px-3 py-2 text-xs font-semibold text-zinc-200 hover:bg-zinc-800"
                            onClick={() => setTab("skills")}
                          >
                            {t("config.skills.manage")}
                          </button>
                        </div>
                      }
                    >
                      <div className="max-h-80 space-y-2 overflow-auto pr-1">
                        {filteredSkills.map((item) => {
                          const enabled = !botDraft.skillsDisabled.includes(item.name);
                          return (
                            <div
                              key={item.name}
                              className="flex items-start justify-between gap-3 rounded-2xl border border-zinc-800 bg-zinc-900 px-3 py-2"
                            >
                              <div className="min-w-0">
                                <div className="truncate font-mono text-xs text-zinc-200">{item.name}</div>
                                <div className="mt-1 text-xs text-zinc-400">{item.description}</div>
                              </div>
                              <ToggleButton enabled={enabled} onClick={() => toggleSkill(item.name)} />
                            </div>
                          );
                        })}
                        {filteredSkills.length === 0 ? <div className="text-xs text-zinc-500">{t("config.skills.empty")}</div> : null}
                      </div>
                    </SectionCard>

                    <SectionCard
                      title={t("config.section.botMcp.title")}
                      subtitle={t("config.section.botMcp.subtitle", {
                        count: Object.keys(botDraft.mcpOverrides).length,
                      })}
                      right={
                        <button
                          type="button"
                          className="rounded-2xl bg-zinc-900 px-3 py-2 text-xs font-semibold text-zinc-200 hover:bg-zinc-800"
                          onClick={() => setTab("mcp")}
                        >
                          {t("config.mcp.manage")}
                        </button>
                      }
                    >
                      <div className="space-y-2">
                        {mcpServers.map((server) => (
                          <div
                            key={server.name}
                            className="flex items-center justify-between gap-3 rounded-2xl border border-zinc-800 bg-zinc-900 px-3 py-2"
                          >
                            <div className="min-w-0">
                              <div className="flex items-center gap-2">
                                <div className="truncate font-mono text-xs text-zinc-200">{server.name}</div>
                                <div className="text-[10px] text-zinc-500">{server.transport}</div>
                              </div>
                              <div className="mt-1 text-xs text-zinc-400">
                                {t("config.mcp.botSummary", {
                                  enabled: server.enabled_default ? t("common.enabled") : t("common.disabled"),
                                  connected: server.connected ? t("config.mcp.connected") : t("config.mcp.disconnected"),
                                })}
                              </div>
                            </div>
                            <select
                              value={mcpValue(server.name)}
                              onChange={(e) => setMcpValue(server.name, e.target.value as "inherit" | "on" | "off")}
                              className="rounded-2xl border border-zinc-800 bg-zinc-950 px-3 py-2 text-xs text-zinc-200 focus:outline-none focus:ring-2 focus:ring-zinc-600"
                            >
                              <option value="inherit">{t("config.mcp.inherit")}</option>
                              <option value="on">{t("common.enabled")}</option>
                              <option value="off">{t("common.disabled")}</option>
                            </select>
                          </div>
                        ))}
                      </div>
                    </SectionCard>
                  </div>

                  <ListEditor
                    label={t("config.section.toolPlugins.title")}
                    items={botDraft.toolPlugins}
                    setItems={(items) => setBotField("toolPlugins", items)}
                    placeholder="/path/to/tools.py"
                  />

                  <SectionCard
                    title={t("config.section.advanced.title")}
                    subtitle={t("config.section.advanced.subtitle")}
                  >
                    <div className="grid gap-4 md:grid-cols-2">
                      <div className="grid gap-2">
                        <label className="text-sm font-semibold text-zinc-200">{t("config.field.baseUrl")}</label>
                        <input
                          value={botDraft.baseUrl}
                          onChange={(e) => setBotField("baseUrl", e.target.value)}
                          placeholder="https://api.openai.com/v1"
                          className="rounded-2xl border border-zinc-800 bg-zinc-900 px-3 py-2 text-sm text-zinc-100 placeholder:text-zinc-500 focus:outline-none focus:ring-2 focus:ring-zinc-600"
                        />
                      </div>
                      <div className="grid gap-2">
                        <label className="text-sm font-semibold text-zinc-200">{t("config.field.fallbackModel")}</label>
                        <input
                          value={botDraft.fallbackModel}
                          onChange={(e) => setBotField("fallbackModel", e.target.value)}
                          placeholder="gpt-4.1-mini"
                          className="rounded-2xl border border-zinc-800 bg-zinc-900 px-3 py-2 text-sm text-zinc-100 placeholder:text-zinc-500 focus:outline-none focus:ring-2 focus:ring-zinc-600"
                        />
                      </div>
                      <div className="grid gap-2 md:col-span-2">
                        <label className="text-sm font-semibold text-zinc-200">{t("config.field.apiKey")}</label>
                        <div className="text-xs text-zinc-500">
                          {t("config.field.currentValue", { value: botDraft.apiKeyMasked || t("common.notSet") })}
                        </div>
                        <input
                          value={botDraft.apiKeyDraft}
                          onChange={(e) => setBotField("apiKeyDraft", e.target.value)}
                          placeholder={t("config.field.newApiKey")}
                          className="rounded-2xl border border-zinc-800 bg-zinc-900 px-3 py-2 text-sm text-zinc-100 placeholder:text-zinc-500 focus:outline-none focus:ring-2 focus:ring-zinc-600"
                        />
                      </div>
                    </div>
                  </SectionCard>
                </>
              ) : null}
            </div>
          </div>
        ) : tab === "providers" ? (
          <div className="grid gap-6 xl:grid-cols-[320px_minmax(0,1fr)]">
            <div className="flex flex-col gap-4">
              <SectionCard
                title={t("config.section.providers.title")}
                subtitle={t("config.section.providers.subtitle")}
                right={
                  <button
                    type="button"
                    className="rounded-2xl bg-zinc-100 px-4 py-2 text-sm font-semibold text-zinc-900 hover:bg-white"
                    onClick={() => setProviderModalOpen(true)}
                  >
                    {t("config.provider.add")}
                  </button>
                }
              >
                <div className="space-y-3">
                  {(dashboard?.providers ?? []).map((provider) => (
                    <button
                      key={provider.provider_id}
                      type="button"
                      className={classNames(
                        "w-full rounded-3xl border px-4 py-3 text-left transition",
                        provider.provider_id === selectedProviderId
                          ? "border-zinc-600 bg-zinc-900"
                          : "border-zinc-800 bg-zinc-950 hover:border-zinc-700 hover:bg-zinc-900/60",
                      )}
                      onClick={() => setSelectedProviderId(provider.provider_id)}
                    >
                      <div className="flex items-start justify-between gap-3">
                        <div className="min-w-0">
                          <div className="truncate text-sm font-semibold text-zinc-100">{provider.name}</div>
                          <div className="mt-1 truncate text-xs text-zinc-500">{provider.base_url}</div>
                        </div>
                        <ToggleButton enabled={provider.enabled} onClick={() => void 0} disabled />
                      </div>
                      <div className="mt-3 flex items-center gap-2 text-[11px] text-zinc-500">
                        <span>{provider.kind}</span>
                        <span>•</span>
                        <span>{provider.api_key_masked || t("common.notSet")}</span>
                      </div>
                    </button>
                  ))}
                  {(dashboard?.providers ?? []).length === 0 ? (
                    <div className="rounded-2xl border border-dashed border-zinc-800 px-4 py-6 text-sm text-zinc-500">
                      {t("config.providers.empty")}
                    </div>
                  ) : null}
                </div>
              </SectionCard>
            </div>

            <div className="flex min-w-0 flex-col gap-4">
              {selectedProvider && providerDraft ? (
                <>
                  <SectionCard
                    title={selectedProvider.name}
                    subtitle={t("config.section.providerDetail.subtitle")}
                    right={
                      <button
                        type="button"
                        className="rounded-2xl bg-zinc-100 px-4 py-2 text-sm font-semibold text-zinc-900 hover:bg-white disabled:opacity-50"
                        onClick={() => void onSaveProvider()}
                        disabled={!providerDirty || savingProvider}
                      >
                        {savingProvider ? t("config.provider.saving") : t("config.provider.save")}
                      </button>
                    }
                  >
                    <div className="grid gap-4 md:grid-cols-2">
                      <div className="grid gap-2">
                        <label className="text-sm font-semibold text-zinc-200">{t("config.provider.name")}</label>
                        <input
                          value={providerDraft.name}
                          onChange={(e) => setProviderField("name", e.target.value)}
                          className="rounded-2xl border border-zinc-800 bg-zinc-900 px-3 py-2 text-sm text-zinc-100 focus:outline-none focus:ring-2 focus:ring-zinc-600"
                        />
                      </div>
                      <div className="flex items-center justify-between rounded-2xl border border-zinc-800 bg-zinc-900 px-4 py-3">
                        <div>
                          <div className="text-sm font-semibold text-zinc-200">{t("config.provider.enabled")}</div>
                          <div className="text-xs text-zinc-500">{t("config.provider.enabled.subtitle")}</div>
                        </div>
                        <ToggleButton
                          enabled={providerDraft.enabled}
                          onClick={() => setProviderField("enabled", !providerDraft.enabled)}
                        />
                      </div>
                      <div className="grid gap-2 md:col-span-2">
                        <label className="text-sm font-semibold text-zinc-200">{t("config.field.baseUrl")}</label>
                        <input
                          value={providerDraft.baseUrl}
                          onChange={(e) => setProviderField("baseUrl", e.target.value)}
                          placeholder="https://api.openai.com/v1"
                          className="rounded-2xl border border-zinc-800 bg-zinc-900 px-3 py-2 text-sm text-zinc-100 focus:outline-none focus:ring-2 focus:ring-zinc-600"
                        />
                      </div>
                      <div className="grid gap-2 md:col-span-2">
                        <label className="text-sm font-semibold text-zinc-200">{t("config.field.apiKey")}</label>
                        <div className="text-xs text-zinc-500">
                          {t("config.field.currentValue", { value: providerDraft.apiKeyMasked || t("common.notSet") })}
                        </div>
                        <input
                          value={providerDraft.apiKeyDraft}
                          onChange={(e) => setProviderField("apiKeyDraft", e.target.value)}
                          placeholder={t("config.provider.newApiKey")}
                          className="rounded-2xl border border-zinc-800 bg-zinc-900 px-3 py-2 text-sm text-zinc-100 placeholder:text-zinc-500 focus:outline-none focus:ring-2 focus:ring-zinc-600"
                        />
                      </div>
                    </div>
                  </SectionCard>

                  <SectionCard
                    title={t("config.section.models.title")}
                    subtitle={t("config.section.models.subtitle")}
                    right={
                      <div className="flex items-center gap-2">
                        <input
                          value={manualModelName}
                          onChange={(e) => setManualModelName(e.target.value)}
                          placeholder="claude-3.7-sonnet"
                          className="w-56 rounded-2xl border border-zinc-800 bg-zinc-900 px-3 py-2 text-sm text-zinc-100 placeholder:text-zinc-500 focus:outline-none focus:ring-2 focus:ring-zinc-600"
                        />
                        <button
                          type="button"
                          className="rounded-2xl bg-zinc-100 px-4 py-2 text-sm font-semibold text-zinc-900 hover:bg-white disabled:opacity-50"
                          onClick={() => void onImportModels([manualModelName.trim()], "manual")}
                          disabled={!manualModelName.trim()}
                        >
                          {t("config.models.add")}
                        </button>
                      </div>
                    }
                  >
                    <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
                      <div className="text-xs text-zinc-500">
                        {t("config.models.selection", {
                          selected: selectedProviderModelIds.length,
                          total: providerModels.length,
                        })}
                      </div>
                      <div className="flex flex-wrap items-center gap-2">
                        <button
                          type="button"
                          className="rounded-2xl bg-zinc-900 px-3 py-2 text-xs font-semibold text-zinc-200 hover:bg-zinc-800 disabled:opacity-50"
                          onClick={toggleAllProviderModels}
                          disabled={providerModels.length === 0}
                        >
                          {allProviderModelsSelected ? t("common.clearSelection") : t("common.selectAll")}
                        </button>
                        <button
                          type="button"
                          className="rounded-2xl bg-red-600 px-3 py-2 text-xs font-semibold text-white hover:bg-red-500 disabled:opacity-50"
                          onClick={() => requestDeleteModels(selectedProviderModelIds)}
                          disabled={selectedProviderModelIds.length === 0}
                        >
                          {t("config.models.deleteSelected")}
                        </button>
                      </div>
                    </div>
                    <div className="space-y-2">
                      {providerModels.map((item) => (
                        <div
                          key={item.model_id}
                          className="flex items-center justify-between gap-3 rounded-2xl border border-zinc-800 bg-zinc-900 px-3 py-2"
                        >
                          <div className="flex min-w-0 items-center gap-3">
                            <input
                              type="checkbox"
                              className="h-4 w-4 rounded border-zinc-700 bg-zinc-950 text-zinc-100"
                              checked={selectedProviderModelIds.includes(item.model_id)}
                              onChange={() => toggleProviderModelSelection(item.model_id)}
                            />
                            <div className="min-w-0">
                              <div className="truncate text-sm font-semibold text-zinc-100">{item.label}</div>
                              <div className="mt-1 font-mono text-xs text-zinc-500">{item.model_name}</div>
                            </div>
                          </div>
                          <div className="flex items-center gap-2">
                            <ToggleButton
                              enabled={item.enabled}
                              onClick={() => void onToggleModel(item.model_id, !item.enabled)}
                            />
                            <button
                              type="button"
                              className="rounded-xl bg-red-500/10 px-3 py-2 text-xs font-semibold text-red-200 hover:bg-red-500/20"
                              onClick={() => requestDeleteModels([item.model_id])}
                            >
                              {t("common.delete")}
                            </button>
                          </div>
                        </div>
                      ))}
                      {providerModels.length === 0 ? <div className="text-sm text-zinc-500">{t("config.models.empty")}</div> : null}
                    </div>
                  </SectionCard>

                  <SectionCard
                    title={t("config.section.fetch.title")}
                    subtitle={t("config.section.fetch.subtitle")}
                    right={
                      <button
                        type="button"
                        className="rounded-2xl bg-zinc-100 px-4 py-2 text-sm font-semibold text-zinc-900 hover:bg-white disabled:opacity-50"
                        onClick={() => void onFetchModels()}
                        disabled={fetchingModels}
                      >
                        {fetchingModels ? t("config.fetch.loading") : t("config.fetch.action")}
                      </button>
                    }
                  >
                    {fetchedModels.length > 0 ? (
                      <>
                        <div className="mb-4 grid gap-3 md:grid-cols-[minmax(0,1fr)_auto] md:items-center">
                          <input
                            value={fetchedModelQuery}
                            onChange={(e) => setFetchedModelQuery(e.target.value)}
                            placeholder={t("config.fetch.search")}
                            className="rounded-2xl border border-zinc-800 bg-zinc-900 px-3 py-2 text-sm text-zinc-100 placeholder:text-zinc-500 focus:outline-none focus:ring-2 focus:ring-zinc-600"
                          />
                          <div className="text-xs text-zinc-500">
                            {t("config.fetch.selection", {
                              selected: selectedFetchedModels.length,
                              available: fetchedModels.filter((item) => !item.already_added).length,
                            })}
                          </div>
                        </div>
                        <div className="mb-3 flex flex-wrap items-center gap-2">
                          <button
                            type="button"
                            className="rounded-2xl bg-zinc-900 px-4 py-2 text-sm font-semibold text-zinc-200 hover:bg-zinc-800 disabled:opacity-50"
                            onClick={toggleFetchedSelectionForVisible}
                            disabled={selectableFilteredFetchedNames.length === 0}
                          >
                            {allVisibleFetchedSelected ? t("config.fetch.clearVisible") : t("common.selectAll")}
                          </button>
                          <button
                            type="button"
                            className="rounded-2xl bg-zinc-100 px-4 py-2 text-sm font-semibold text-zinc-900 hover:bg-white disabled:opacity-50"
                            onClick={() => void onImportModels(selectedFetchedModels, "fetched")}
                            disabled={selectedFetchedModels.length === 0}
                          >
                            {t("config.fetch.addSelected")}
                          </button>
                          <button
                            type="button"
                            className="rounded-2xl bg-zinc-900 px-4 py-2 text-sm font-semibold text-zinc-200 hover:bg-zinc-800 disabled:opacity-50"
                            onClick={() =>
                              void onImportModels(
                                fetchedModels.filter((item) => !item.already_added).map((item) => item.model_name),
                                "fetched",
                              )
                            }
                            disabled={!fetchedModels.some((item) => !item.already_added)}
                          >
                            {t("config.fetch.importAll")}
                          </button>
                        </div>
                        <div className="max-h-[420px] space-y-2 overflow-auto pr-1">
                          {filteredFetchedModels.map((item) => {
                            const checked = selectedFetchedModels.includes(item.model_name);
                            return (
                              <label
                                key={item.model_name}
                                className="flex cursor-pointer items-center justify-between gap-3 rounded-2xl border border-zinc-800 bg-zinc-900 px-3 py-2"
                              >
                                <div className="min-w-0">
                                  <div className="truncate text-sm font-semibold text-zinc-100">{item.model_name}</div>
                                  <div className="mt-1 text-xs text-zinc-500">
                                    {item.already_added ? t("config.fetch.alreadyAdded") : t("config.fetch.availableToImport")}
                                  </div>
                                </div>
                                <input
                                  type="checkbox"
                                  className="h-4 w-4 rounded border-zinc-700 bg-zinc-950 text-zinc-100"
                                  checked={checked}
                                  disabled={item.already_added}
                                  onChange={() => toggleFetchedModelSelection(item.model_name)}
                                />
                              </label>
                            );
                          })}
                          {filteredFetchedModels.length === 0 ? (
                            <div className="rounded-2xl border border-dashed border-zinc-800 px-4 py-6 text-sm text-zinc-500">
                              {t("config.fetch.emptySearch")}
                            </div>
                          ) : null}
                        </div>
                      </>
                    ) : (
                      <div className="text-sm text-zinc-500">{t("config.fetch.empty")}</div>
                    )}
                  </SectionCard>
                </>
              ) : (
                <SectionCard
                  title={t("config.providerDetail.title")}
                  subtitle={t("config.providerDetail.emptySubtitle")}
                >
                  <div className="text-sm text-zinc-500">{t("config.providerDetail.empty")}</div>
                </SectionCard>
              )}
            </div>
          </div>
        ) : tab === "platforms" ? (
          <PlatformsView
            platforms={dashboard?.platforms ?? []}
            bots={dashboard?.bots ?? []}
            onPlatformsChanged={() => refreshDashboard(selectedProviderId)}
            onStatus={setStatus}
          />
        ) : tab === "skills" ? (
          <SkillsView
            skills={skills}
            onSkillsChanged={handleSkillsChanged}
            onStatus={setStatus}
          />
        ) : (
          <div className="grid gap-6 xl:grid-cols-[320px_minmax(0,1fr)]">
            <div className="flex flex-col gap-4">
              <SectionCard
                title={t("config.section.mcpRegistry.title")}
                subtitle={t("config.section.mcpRegistry.subtitle")}
              >
                <div className="space-y-3">
                  {mcpServers.map((server) => (
                    <button
                      key={server.name}
                      type="button"
                      className={classNames(
                        "w-full rounded-3xl border px-4 py-3 text-left transition",
                        server.name === selectedMcpServerName
                          ? "border-zinc-600 bg-zinc-900"
                          : "border-zinc-800 bg-zinc-950 hover:border-zinc-700 hover:bg-zinc-900/60",
                      )}
                      onClick={() => setSelectedMcpServerName(server.name)}
                    >
                      <div className="flex items-start justify-between gap-3">
                        <div className="min-w-0">
                          <div className="truncate text-sm font-semibold text-zinc-100">{server.name}</div>
                          <div className="mt-1 flex flex-wrap gap-2 text-[11px] text-zinc-500">
                            <span>{server.transport}</span>
                            <span>{server.connected ? t("config.mcp.connected") : t("config.mcp.disconnected")}</span>
                          </div>
                        </div>
                        <button
                          type="button"
                          className={classNames(
                            "rounded-2xl px-3 py-1.5 text-xs font-semibold transition disabled:opacity-50",
                            server.connected
                              ? "bg-red-500/15 text-red-200 hover:bg-red-500/25"
                              : "bg-emerald-500/15 text-emerald-200 hover:bg-emerald-500/25",
                          )}
                          onClick={(e) => {
                            e.stopPropagation();
                            void onToggleMcpConnection(server);
                          }}
                          disabled={togglingMcpConnection === server.name}
                        >
                          {togglingMcpConnection === server.name
                            ? t("common.loading")
                            : server.connected
                              ? t("config.mcp.disconnect")
                              : t("config.mcp.connect")}
                        </button>
                      </div>
                    </button>
                  ))}
                  {mcpServers.length === 0 ? (
                    <div className="rounded-2xl border border-dashed border-zinc-800 px-4 py-6 text-sm text-zinc-500">
                      {t("config.mcp.empty")}
                    </div>
                  ) : null}
                </div>
              </SectionCard>
            </div>

            <div className="flex min-w-0 flex-col gap-4">
              {selectedMcpServer && mcpDraft ? (
                <>
                  <SectionCard
                    title={selectedMcpServer.name}
                    subtitle={t("config.section.mcpDetail.subtitle")}
                    right={
                      <div className="flex items-center gap-2">
                        <button
                          type="button"
                          className={classNames(
                            "rounded-2xl px-4 py-2 text-sm font-semibold transition disabled:opacity-50",
                            selectedMcpServer.connected
                              ? "bg-red-500/15 text-red-100 hover:bg-red-500/25"
                              : "bg-emerald-500/15 text-emerald-100 hover:bg-emerald-500/25",
                          )}
                          onClick={() => void onToggleMcpConnection(selectedMcpServer)}
                          disabled={togglingMcpConnection === selectedMcpServer.name}
                        >
                          {togglingMcpConnection === selectedMcpServer.name
                            ? t("common.loading")
                            : selectedMcpServer.connected
                              ? t("config.mcp.disconnect")
                              : t("config.mcp.connect")}
                        </button>
                        <button
                          type="button"
                          className="rounded-2xl bg-zinc-100 px-4 py-2 text-sm font-semibold text-zinc-900 hover:bg-white disabled:opacity-50"
                          onClick={() => void onSaveMcpServer()}
                          disabled={!mcpDirty || savingMcp}
                        >
                          {savingMcp ? t("config.mcp.saving") : t("config.mcp.save")}
                        </button>
                      </div>
                    }
                  >
                    <div className="grid gap-4 md:grid-cols-2">
                      <div className="rounded-2xl border border-zinc-800 bg-zinc-900 px-4 py-3">
                        <div className="text-sm font-semibold text-zinc-200">{t("config.mcp.status")}</div>
                        <div className="mt-2 text-xs text-zinc-500">
                          {mcpDraft.connected ? t("config.mcp.connected") : t("config.mcp.disconnected")}
                        </div>
                      </div>
                      <div className="flex items-center justify-between rounded-2xl border border-zinc-800 bg-zinc-900 px-4 py-3">
                        <div>
                          <div className="text-sm font-semibold text-zinc-200">{t("config.mcp.defaultEnabled")}</div>
                          <div className="text-xs text-zinc-500">{t("config.mcp.defaultEnabled.subtitle")}</div>
                        </div>
                        <ToggleButton
                          enabled={mcpDraft.enabled_default}
                          onClick={() => setMcpField("enabled_default", !mcpDraft.enabled_default)}
                        />
                      </div>
                      {mcpDraft.transport === "stdio" ? (
                        <>
                          <div className="grid gap-2 md:col-span-2">
                            <label className="text-sm font-semibold text-zinc-200">{t("config.mcp.command")}</label>
                            <input
                              value={mcpDraft.command}
                              onChange={(e) => setMcpField("command", e.target.value)}
                              placeholder={t("config.mcp.command.placeholder")}
                              className="rounded-2xl border border-zinc-800 bg-zinc-900 px-3 py-2 text-sm text-zinc-100 placeholder:text-zinc-500 focus:outline-none focus:ring-2 focus:ring-zinc-600"
                            />
                          </div>
                          <div className="grid gap-2 md:col-span-2">
                            <label className="text-sm font-semibold text-zinc-200">{t("config.mcp.args")}</label>
                            <textarea
                              value={mcpDraft.argsText}
                              onChange={(e) => setMcpField("argsText", e.target.value)}
                              placeholder={t("config.mcp.args.placeholder")}
                              className="min-h-[120px] rounded-2xl border border-zinc-800 bg-zinc-900 px-3 py-2 font-mono text-sm text-zinc-100 placeholder:text-zinc-500 focus:outline-none focus:ring-2 focus:ring-zinc-600"
                            />
                          </div>
                        </>
                      ) : (
                        <div className="grid gap-2 md:col-span-2">
                          <label className="text-sm font-semibold text-zinc-200">{t("config.mcp.url")}</label>
                          <input
                            value={mcpDraft.url}
                            onChange={(e) => setMcpField("url", e.target.value)}
                            placeholder="https://example.invalid/mcp"
                            className="rounded-2xl border border-zinc-800 bg-zinc-900 px-3 py-2 text-sm text-zinc-100 placeholder:text-zinc-500 focus:outline-none focus:ring-2 focus:ring-zinc-600"
                          />
                        </div>
                      )}
                    </div>
                  </SectionCard>

                  <SectionCard
                    title={t("config.mcp.envKeys")}
                    subtitle={t("config.mcp.envKeys.subtitle")}
                  >
                    {mcpDraft.env_keys.length > 0 ? (
                      <div className="flex flex-wrap gap-2">
                        {mcpDraft.env_keys.map((key) => (
                          <span
                            key={key}
                            className="rounded-full border border-zinc-800 bg-zinc-900 px-3 py-1 font-mono text-xs text-zinc-300"
                          >
                            {key}
                          </span>
                        ))}
                      </div>
                    ) : (
                      <div className="text-sm text-zinc-500">{t("config.mcp.envKeys.empty")}</div>
                    )}
                  </SectionCard>
                </>
              ) : (
                <SectionCard
                  title={t("config.section.mcpDetail.title")}
                  subtitle={t("config.section.mcpDetail.emptySubtitle")}
                >
                  <div className="text-sm text-zinc-500">{t("config.section.mcpDetail.empty")}</div>
                </SectionCard>
              )}
            </div>
          </div>
        )}
      </div>

      {deleteModelsOpen ? (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center bg-black/65 p-4"
          onMouseDown={(e) => {
            if (e.target === e.currentTarget) {
              setDeleteModelsOpen(false);
              setPendingDeleteModelIds([]);
            }
          }}
        >
          <div className="w-full max-w-lg rounded-3xl border border-zinc-800 bg-zinc-950 p-5 shadow-2xl">
            <div className="text-lg font-semibold text-zinc-100">
              {t("config.deleteModels.title", { count: pendingDeleteModelIds.length })}
            </div>
            <div className="mt-2 text-sm text-zinc-400">
              {pendingDeleteModelIds.length === 1 ? (
                t("config.deleteModels.single", {
                  model: pendingDeleteModels[0]?.model_name ?? t("common.unknown"),
                  provider: selectedProvider?.name ?? t("config.providerDetail.current"),
                })
              ) : (
                t("config.deleteModels.multi", {
                  count: pendingDeleteModelIds.length,
                  provider: selectedProvider?.name ?? t("config.providerDetail.current"),
                })
              )}
            </div>

            {pendingDeleteModels.length > 1 ? (
              <div className="mt-4 max-h-48 overflow-auto rounded-2xl border border-zinc-800 bg-zinc-900 px-3 py-3">
                <div className="space-y-2 text-sm text-zinc-300">
                  {pendingDeleteModels.map((item) => (
                    <div key={item.model_id} className="flex items-center justify-between gap-3">
                      <span className="truncate">{item.label}</span>
                      <span className="font-mono text-xs text-zinc-500">{item.model_name}</span>
                    </div>
                  ))}
                </div>
              </div>
            ) : null}

            <div className="mt-6 flex justify-end gap-2">
              <button
                type="button"
                className="rounded-2xl bg-zinc-900 px-4 py-2 text-sm font-semibold text-zinc-200 hover:bg-zinc-800"
                onClick={() => {
                  setDeleteModelsOpen(false);
                  setPendingDeleteModelIds([]);
                }}
                disabled={deletingModels}
              >
                {t("common.cancel")}
              </button>
              <button
                type="button"
                className="rounded-2xl bg-red-600 px-4 py-2 text-sm font-semibold text-white hover:bg-red-500 disabled:opacity-50"
                onClick={() => void confirmDeleteModels()}
                disabled={deletingModels || pendingDeleteModelIds.length === 0}
              >
                {deletingModels ? t("config.deleteModels.deleting") : t("config.deleteModels.confirm")}
              </button>
            </div>
          </div>
        </div>
      ) : null}

      {providerModalOpen ? (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center bg-black/65 p-4"
          onMouseDown={(e) => {
            if (e.target === e.currentTarget) setProviderModalOpen(false);
          }}
        >
          <div className="w-full max-w-lg rounded-3xl border border-zinc-800 bg-zinc-950 p-5 shadow-2xl">
            <div className="text-lg font-semibold text-zinc-100">{t("config.providerModal.title")}</div>
            <div className="mt-1 text-sm text-zinc-400">{t("config.providerModal.subtitle")}</div>

            <div className="mt-5 grid gap-4">
              <div className="grid gap-2">
                <label className="text-sm font-semibold text-zinc-200">{t("config.provider.name")}</label>
                <input
                  value={providerNameDraft}
                  onChange={(e) => setProviderNameDraft(e.target.value)}
                  placeholder={t("config.providerModal.name.placeholder")}
                  className="rounded-2xl border border-zinc-800 bg-zinc-900 px-3 py-2 text-sm text-zinc-100 placeholder:text-zinc-500 focus:outline-none focus:ring-2 focus:ring-zinc-600"
                />
              </div>
              <div className="grid gap-2">
                <label className="text-sm font-semibold text-zinc-200">{t("config.field.baseUrl")}</label>
                <input
                  value={providerBaseUrlDraft}
                  onChange={(e) => setProviderBaseUrlDraft(e.target.value)}
                  placeholder="https://api.openai.com/v1"
                  className="rounded-2xl border border-zinc-800 bg-zinc-900 px-3 py-2 text-sm text-zinc-100 placeholder:text-zinc-500 focus:outline-none focus:ring-2 focus:ring-zinc-600"
                />
              </div>
              <div className="grid gap-2">
                <label className="text-sm font-semibold text-zinc-200">{t("config.field.apiKey")}</label>
                <input
                  value={providerApiKeyDraft}
                  onChange={(e) => setProviderApiKeyDraft(e.target.value)}
                  placeholder={t("config.providerModal.apiKey.placeholder")}
                  className="rounded-2xl border border-zinc-800 bg-zinc-900 px-3 py-2 text-sm text-zinc-100 placeholder:text-zinc-500 focus:outline-none focus:ring-2 focus:ring-zinc-600"
                />
              </div>
            </div>

            <div className="mt-6 flex justify-end gap-2">
              <button
                type="button"
                className="rounded-2xl bg-zinc-900 px-4 py-2 text-sm font-semibold text-zinc-200 hover:bg-zinc-800 disabled:opacity-50"
                onClick={() => setProviderModalOpen(false)}
                disabled={creatingProvider}
              >
                {t("common.cancel")}
              </button>
              <button
                type="button"
                className="rounded-2xl bg-zinc-100 px-4 py-2 text-sm font-semibold text-zinc-900 hover:bg-white disabled:opacity-50"
                onClick={() => void onCreateProvider()}
                disabled={creatingProvider || !providerNameDraft.trim() || !providerBaseUrlDraft.trim()}
              >
                {creatingProvider ? t("config.providerModal.creating") : t("config.providerModal.confirm")}
              </button>
            </div>
          </div>
        </div>
      ) : null}
    </div>
  );
}
