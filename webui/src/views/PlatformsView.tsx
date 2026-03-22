import { useEffect, useMemo, useState, type ReactNode } from "react";

import { createPlatform, deletePlatform, updatePlatform } from "../lib/api";
import { useI18n } from "../lib/i18n";
import type { BotMeta, PlatformConnection } from "../lib/types";

function classNames(...xs: Array<string | false | null | undefined>) {
  return xs.filter(Boolean).join(" ");
}

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

function ToggleButton(props: { enabled: boolean; onClick: () => void; disabled?: boolean }) {
  const { t } = useI18n();
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
      {props.enabled ? t("common.on") : t("common.off")}
    </button>
  );
}

type PlatformDraft = {
  name: string;
  enabled: boolean;
  boundBotId: string;
  kind: PlatformConnection["kind"];
  mode: "websocket";
  scope: "private";
  appId: string;
  appSecretMasked: string;
  appSecretDraft: string;
};

const PLATFORM_OPTIONS: Array<{ value: PlatformConnection["kind"]; labelKey: string }> = [
  { value: "feishu", labelKey: "config.platform.kind.feishu" },
  { value: "telegram", labelKey: "config.platform.kind.telegram" },
  { value: "whatsapp", labelKey: "config.platform.kind.whatsapp" },
];

function platformRequiresCredentials(kind: PlatformConnection["kind"]): boolean {
  return kind === "feishu";
}

function draftFromPlatform(platform: PlatformConnection): PlatformDraft {
  return {
    name: platform.name,
    enabled: platform.enabled,
    boundBotId: platform.bound_bot_id,
    kind: platform.kind,
    mode: platform.mode,
    scope: platform.scope,
    appId: platform.app_id,
    appSecretMasked: platform.app_secret_masked ?? "",
    appSecretDraft: "",
  };
}

function draftComparable(draft: PlatformDraft | null): string {
  if (!draft) return "";
  return JSON.stringify({
    ...draft,
    appSecretDraft: draft.appSecretDraft.trim() ? "__set__" : "",
  });
}

export default function PlatformsView(props: {
  platforms: PlatformConnection[];
  bots: BotMeta[];
  onPlatformsChanged: () => Promise<void> | void;
  onStatus?: (status: string | null) => void;
}) {
  const { platforms, bots, onPlatformsChanged, onStatus } = props;
  const { t } = useI18n();

  const [selectedPlatformId, setSelectedPlatformId] = useState<string | null>(null);
  const [draft, setDraft] = useState<PlatformDraft | null>(null);
  const [baseline, setBaseline] = useState("");
  const [saving, setSaving] = useState(false);
  const [deleting, setDeleting] = useState(false);

  const [modalOpen, setModalOpen] = useState(false);
  const [creating, setCreating] = useState(false);
  const [nameDraft, setNameDraft] = useState("");
  const [kindDraft, setKindDraft] = useState<PlatformConnection["kind"]>("feishu");
  const [boundBotDraft, setBoundBotDraft] = useState<string>("default");
  const [appIdDraft, setAppIdDraft] = useState("");
  const [appSecretDraft, setAppSecretDraft] = useState("");

  const selectedPlatform = useMemo(
    () => platforms.find((item) => item.platform_id === selectedPlatformId) ?? null,
    [platforms, selectedPlatformId],
  );
  const dirty = draftComparable(draft) !== baseline;
  const createNeedsCredentials = platformRequiresCredentials(kindDraft);

  function platformKindLabel(kind: PlatformConnection["kind"]): string {
    return t(`config.platform.kind.${kind}`);
  }

  useEffect(() => {
    if (selectedPlatformId && platforms.some((item) => item.platform_id === selectedPlatformId)) {
      return;
    }
    setSelectedPlatformId(platforms[0]?.platform_id ?? null);
  }, [platforms, selectedPlatformId]);

  useEffect(() => {
    if (!selectedPlatform) {
      setDraft(null);
      setBaseline("");
      return;
    }
    const next = draftFromPlatform(selectedPlatform);
    setDraft(next);
    setBaseline(draftComparable(next));
  }, [selectedPlatform]);

  function setDraftField<K extends keyof PlatformDraft>(key: K, value: PlatformDraft[K]) {
    setDraft((prev) => (prev ? { ...prev, [key]: value } : prev));
  }

  async function handleSave() {
    if (!selectedPlatform || !draft) return;
    setSaving(true);
    onStatus?.(null);
    try {
      await updatePlatform(selectedPlatform.platform_id, {
        name: draft.name.trim(),
        enabled: draft.enabled,
        bound_bot_id: draft.boundBotId,
        app_id: draft.appId.trim(),
        app_secret: draft.appSecretDraft.trim() ? draft.appSecretDraft.trim() : undefined,
      });
      await onPlatformsChanged();
      onStatus?.(t("config.status.platformUpdated"));
    } catch (err) {
      onStatus?.(String(err));
    } finally {
      setSaving(false);
    }
  }

  async function handleDelete() {
    if (!selectedPlatform) return;
    if (!window.confirm(t("config.platform.delete.confirmation", { name: selectedPlatform.name }))) return;
    setDeleting(true);
    onStatus?.(null);
    try {
      await deletePlatform(selectedPlatform.platform_id);
      await onPlatformsChanged();
      onStatus?.(t("config.status.platformDeleted"));
    } catch (err) {
      onStatus?.(String(err));
    } finally {
      setDeleting(false);
    }
  }

  async function handleCreate() {
    if (!nameDraft.trim()) return;
    if (createNeedsCredentials && (!appIdDraft.trim() || !appSecretDraft.trim())) return;
    setCreating(true);
    onStatus?.(null);
    try {
      const created = await createPlatform({
        name: nameDraft.trim(),
        kind: kindDraft,
        enabled: true,
        bound_bot_id: boundBotDraft,
        app_id: appIdDraft.trim() || undefined,
        app_secret: appSecretDraft.trim() || undefined,
      });
      setSelectedPlatformId(created.platform_id);
      setModalOpen(false);
      setNameDraft("");
      setKindDraft("feishu");
      setBoundBotDraft("default");
      setAppIdDraft("");
      setAppSecretDraft("");
      await onPlatformsChanged();
      onStatus?.(t("config.status.platformAdded"));
    } catch (err) {
      onStatus?.(String(err));
    } finally {
      setCreating(false);
    }
  }

  return (
    <div className="grid gap-6 xl:grid-cols-[320px_minmax(0,1fr)]">
      <div className="flex flex-col gap-4">
        <SectionCard
          title={t("config.section.platforms.title")}
          subtitle={t("config.section.platforms.subtitle")}
          right={
            <button
              type="button"
              className="rounded-2xl bg-zinc-100 px-4 py-2 text-sm font-semibold text-zinc-900 hover:bg-white"
              onClick={() => setModalOpen(true)}
            >
              {t("config.platform.add")}
            </button>
          }
        >
          <div className="space-y-3">
            {platforms.map((platform) => (
              <button
                key={platform.platform_id}
                type="button"
                className={classNames(
                  "w-full rounded-3xl border px-4 py-3 text-left transition",
                  platform.platform_id === selectedPlatformId
                    ? "border-zinc-600 bg-zinc-900"
                    : "border-zinc-800 bg-zinc-950 hover:border-zinc-700 hover:bg-zinc-900/60",
                )}
                onClick={() => setSelectedPlatformId(platform.platform_id)}
              >
                <div className="flex items-start justify-between gap-3">
                  <div className="min-w-0">
                    <div className="truncate text-sm font-semibold text-zinc-100">{platform.name}</div>
                    <div className="mt-1 flex flex-wrap gap-2 text-[11px] text-zinc-500">
                      <span>{platformKindLabel(platform.kind)}</span>
                      <span>{platform.bound_bot_name}</span>
                    </div>
                    <div className="mt-1 truncate text-xs text-zinc-500">
                      {platform.connected ? t("config.platform.connected") : t("config.platform.disconnected")}
                    </div>
                  </div>
                  <ToggleButton enabled={platform.enabled} onClick={() => void 0} disabled />
                </div>
              </button>
            ))}
            {platforms.length === 0 ? (
              <div className="rounded-2xl border border-dashed border-zinc-800 px-4 py-6 text-sm text-zinc-500">
                {t("config.platforms.empty")}
              </div>
            ) : null}
          </div>
        </SectionCard>
      </div>

      <div className="flex min-w-0 flex-col gap-4">
        {selectedPlatform && draft ? (
          <>
            <SectionCard
              title={selectedPlatform.name}
              subtitle={t("config.section.platformDetail.subtitle")}
              right={
                <div className="flex items-center gap-2">
                  <button
                    type="button"
                    className="rounded-2xl bg-red-500/15 px-4 py-2 text-sm font-semibold text-red-100 hover:bg-red-500/25 disabled:opacity-50"
                    onClick={() => void handleDelete()}
                    disabled={deleting}
                  >
                    {deleting ? t("config.platform.deleting") : t("config.platform.delete")}
                  </button>
                  <button
                    type="button"
                    className="rounded-2xl bg-zinc-100 px-4 py-2 text-sm font-semibold text-zinc-900 hover:bg-white disabled:opacity-50"
                    onClick={() => void handleSave()}
                    disabled={!dirty || saving}
                  >
                    {saving ? t("config.platform.saving") : t("config.platform.save")}
                  </button>
                </div>
              }
            >
              <div className="grid gap-4 md:grid-cols-2">
                <div className="grid gap-2">
                  <label className="text-sm font-semibold text-zinc-200">{t("config.platform.name")}</label>
                  <input
                    value={draft.name}
                    onChange={(e) => setDraftField("name", e.target.value)}
                    className="rounded-2xl border border-zinc-800 bg-zinc-900 px-3 py-2 text-sm text-zinc-100 placeholder:text-zinc-500 focus:outline-none focus:ring-2 focus:ring-zinc-600"
                  />
                </div>
                <div className="flex items-center justify-between rounded-2xl border border-zinc-800 bg-zinc-900 px-4 py-3">
                  <div>
                    <div className="text-sm font-semibold text-zinc-200">{t("config.platform.enabled")}</div>
                    <div className="text-xs text-zinc-500">{t("config.platform.enabled.subtitle")}</div>
                  </div>
                  <ToggleButton enabled={draft.enabled} onClick={() => setDraftField("enabled", !draft.enabled)} />
                </div>
                <div className="grid gap-2">
                  <label className="text-sm font-semibold text-zinc-200">{t("config.platform.boundBot")}</label>
                  <select
                    value={draft.boundBotId}
                    onChange={(e) => setDraftField("boundBotId", e.target.value)}
                    className="rounded-2xl border border-zinc-800 bg-zinc-900 px-3 py-2 text-sm text-zinc-100 focus:outline-none focus:ring-2 focus:ring-zinc-600"
                  >
                    {bots.map((bot) => (
                      <option key={bot.bot_id} value={bot.bot_id}>
                        {bot.name}
                      </option>
                    ))}
                  </select>
                </div>
                {platformRequiresCredentials(draft.kind) ? (
                  <div className="grid gap-2">
                    <label className="text-sm font-semibold text-zinc-200">{t("config.platform.appId")}</label>
                    <input
                      value={draft.appId}
                      onChange={(e) => setDraftField("appId", e.target.value)}
                      className="rounded-2xl border border-zinc-800 bg-zinc-900 px-3 py-2 text-sm text-zinc-100 placeholder:text-zinc-500 focus:outline-none focus:ring-2 focus:ring-zinc-600"
                    />
                  </div>
                ) : (
                  <div className="rounded-2xl border border-amber-500/20 bg-amber-500/10 px-4 py-3 text-sm text-amber-100 md:col-span-2">
                    {t("config.platform.pendingRuntime")}
                  </div>
                )}
                <div className="grid gap-2">
                  <label className="text-sm font-semibold text-zinc-200">{t("config.platform.kind")}</label>
                  <div className="rounded-2xl border border-zinc-800 bg-zinc-900 px-3 py-2 text-sm text-zinc-300">
                    {platformKindLabel(draft.kind)}
                  </div>
                </div>
                <div className="grid gap-2">
                  <label className="text-sm font-semibold text-zinc-200">{t("config.platform.mode")}</label>
                  <div className="rounded-2xl border border-zinc-800 bg-zinc-900 px-3 py-2 text-sm text-zinc-300">{draft.mode}</div>
                </div>
                <div className="grid gap-2">
                  <label className="text-sm font-semibold text-zinc-200">{t("config.platform.scope")}</label>
                  <div className="rounded-2xl border border-zinc-800 bg-zinc-900 px-3 py-2 text-sm text-zinc-300">{draft.scope}</div>
                </div>
                {platformRequiresCredentials(draft.kind) ? (
                  <div className="grid gap-2 md:col-span-2">
                    <label className="text-sm font-semibold text-zinc-200">{t("config.field.apiKey")}</label>
                    <div className="text-xs text-zinc-500">
                      {t("config.field.currentValue", { value: draft.appSecretMasked || t("common.notSet") })}
                    </div>
                    <input
                      value={draft.appSecretDraft}
                      onChange={(e) => setDraftField("appSecretDraft", e.target.value)}
                      placeholder={t("config.platform.newAppSecret")}
                      className="rounded-2xl border border-zinc-800 bg-zinc-900 px-3 py-2 text-sm text-zinc-100 placeholder:text-zinc-500 focus:outline-none focus:ring-2 focus:ring-zinc-600"
                    />
                  </div>
                ) : null}
              </div>
            </SectionCard>

            <SectionCard title={t("config.platform.status")}>
              <div className="grid gap-4 md:grid-cols-3">
                <div className="rounded-2xl border border-zinc-800 bg-zinc-900 px-4 py-3">
                  <div className="text-xs text-zinc-500">{t("config.platform.connectedLabel")}</div>
                  <div className="mt-2 text-sm font-semibold text-zinc-100">
                    {selectedPlatform.connected ? t("config.platform.connected") : t("config.platform.disconnected")}
                  </div>
                </div>
                <div className="rounded-2xl border border-zinc-800 bg-zinc-900 px-4 py-3">
                  <div className="text-xs text-zinc-500">{t("config.platform.lastEvent")}</div>
                  <div className="mt-2 break-all text-sm text-zinc-100">
                    {selectedPlatform.last_event_at || t("common.notSet")}
                  </div>
                </div>
                <div className="rounded-2xl border border-zinc-800 bg-zinc-900 px-4 py-3">
                  <div className="text-xs text-zinc-500">{t("config.platform.lastError")}</div>
                  <div className="mt-2 break-all text-sm text-zinc-100">
                    {selectedPlatform.last_error || t("common.notSet")}
                  </div>
                </div>
              </div>
            </SectionCard>
          </>
        ) : (
          <SectionCard
            title={t("config.platformDetail.title")}
            subtitle={t("config.platformDetail.emptySubtitle")}
          >
            <div className="text-sm text-zinc-500">{t("config.platformDetail.empty")}</div>
          </SectionCard>
        )}
      </div>

      {modalOpen ? (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center bg-black/65 p-4"
          onMouseDown={(e) => {
            if (e.target === e.currentTarget) setModalOpen(false);
          }}
        >
          <div className="w-full max-w-lg rounded-3xl border border-zinc-800 bg-zinc-950 p-5 shadow-2xl">
            <div className="text-lg font-semibold text-zinc-100">{t("config.platformModal.title")}</div>
            <div className="mt-1 text-sm text-zinc-400">{t("config.platformModal.subtitle")}</div>

            <div className="mt-5 grid gap-4">
              <div className="grid gap-2">
                <label className="text-sm font-semibold text-zinc-200">{t("config.platform.name")}</label>
                <input
                  value={nameDraft}
                  onChange={(e) => setNameDraft(e.target.value)}
                  placeholder={t("config.platformModal.name.placeholder")}
                  className="rounded-2xl border border-zinc-800 bg-zinc-900 px-3 py-2 text-sm text-zinc-100 placeholder:text-zinc-500 focus:outline-none focus:ring-2 focus:ring-zinc-600"
                />
              </div>
              <div className="grid gap-2">
                <label className="text-sm font-semibold text-zinc-200">{t("config.platform.kind")}</label>
                <select
                  value={kindDraft}
                  onChange={(e) => {
                    const nextKind = e.target.value as PlatformConnection["kind"];
                    setKindDraft(nextKind);
                    if (!platformRequiresCredentials(nextKind)) {
                      setAppIdDraft("");
                      setAppSecretDraft("");
                    }
                  }}
                  className="rounded-2xl border border-zinc-800 bg-zinc-900 px-3 py-2 text-sm text-zinc-100 focus:outline-none focus:ring-2 focus:ring-zinc-600"
                >
                  {PLATFORM_OPTIONS.map((option) => (
                    <option key={option.value} value={option.value}>
                      {t(option.labelKey)}
                    </option>
                  ))}
                </select>
                <div className="text-xs text-zinc-500">{t("config.platformModal.kindHelp")}</div>
              </div>
              <div className="grid gap-2">
                <label className="text-sm font-semibold text-zinc-200">{t("config.platform.boundBot")}</label>
                <select
                  value={boundBotDraft}
                  onChange={(e) => setBoundBotDraft(e.target.value)}
                  className="rounded-2xl border border-zinc-800 bg-zinc-900 px-3 py-2 text-sm text-zinc-100 focus:outline-none focus:ring-2 focus:ring-zinc-600"
                >
                  {bots.map((bot) => (
                    <option key={bot.bot_id} value={bot.bot_id}>
                      {bot.name}
                    </option>
                  ))}
                </select>
              </div>
              {createNeedsCredentials ? (
                <>
                  <div className="grid gap-2">
                    <label className="text-sm font-semibold text-zinc-200">{t("config.platform.appId")}</label>
                    <input
                      value={appIdDraft}
                      onChange={(e) => setAppIdDraft(e.target.value)}
                      placeholder={t("config.platformModal.appId.placeholder")}
                      className="rounded-2xl border border-zinc-800 bg-zinc-900 px-3 py-2 text-sm text-zinc-100 placeholder:text-zinc-500 focus:outline-none focus:ring-2 focus:ring-zinc-600"
                    />
                  </div>
                  <div className="grid gap-2">
                    <label className="text-sm font-semibold text-zinc-200">{t("config.field.apiKey")}</label>
                    <input
                      value={appSecretDraft}
                      onChange={(e) => setAppSecretDraft(e.target.value)}
                      placeholder={t("config.platformModal.appSecret.placeholder")}
                      className="rounded-2xl border border-zinc-800 bg-zinc-900 px-3 py-2 text-sm text-zinc-100 placeholder:text-zinc-500 focus:outline-none focus:ring-2 focus:ring-zinc-600"
                    />
                  </div>
                </>
              ) : (
                <div className="rounded-2xl border border-amber-500/20 bg-amber-500/10 px-4 py-3 text-sm text-amber-100">
                  {t("config.platform.pendingRuntime")}
                </div>
              )}
            </div>

            <div className="mt-6 flex justify-end gap-2">
              <button
                type="button"
                className="rounded-2xl bg-zinc-900 px-4 py-2 text-sm font-semibold text-zinc-200 hover:bg-zinc-800 disabled:opacity-50"
                onClick={() => setModalOpen(false)}
                disabled={creating}
              >
                {t("common.cancel")}
              </button>
              <button
                type="button"
                className="rounded-2xl bg-zinc-100 px-4 py-2 text-sm font-semibold text-zinc-900 hover:bg-white disabled:opacity-50"
                onClick={() => void handleCreate()}
                disabled={
                  creating ||
                  !nameDraft.trim() ||
                  (createNeedsCredentials && (!appIdDraft.trim() || !appSecretDraft.trim()))
                }
              >
                {creating ? t("config.platformModal.creating") : t("config.platformModal.confirm")}
              </button>
            </div>
          </div>
        </div>
      ) : null}
    </div>
  );
}
