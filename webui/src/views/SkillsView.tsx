import { useEffect, useMemo, useState, type ReactNode } from "react";

import { createSkill, deleteSkill, getConfig } from "../lib/api";
import type { Config, SkillInfo } from "../lib/types";
import { useI18n } from "../lib/i18n";

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

type SkillFilter = "all" | "active" | "overridden";

function scopeTone(scope: string) {
  if (scope === "user") return "border-emerald-500/20 bg-emerald-500/10 text-emerald-200";
  if (scope === "project") return "border-amber-500/20 bg-amber-500/10 text-amber-200";
  if (scope === "builtin") return "border-sky-500/20 bg-sky-500/10 text-sky-200";
  if (scope === "common") return "border-fuchsia-500/20 bg-fuchsia-500/10 text-fuchsia-200";
  return "border-zinc-700 bg-zinc-900 text-zinc-300";
}

type Translate = ReturnType<typeof useI18n>["t"];

function deletePolicy(skill: SkillInfo, t: Translate) {
  if (skill.deletable) return t("config.skills.delete.policyUser");
  if (skill.source_type === "project") return t("config.skills.delete.policyProject");
  if (skill.builtin) return t("config.skills.delete.policyBuiltin");
  return t("config.skills.delete.policyReadonly");
}

export default function SkillsView(props: {
  skills: SkillInfo[];
  onSkillsChanged?: () => Promise<void> | void;
  onStatus?: (status: string | null) => void;
}) {
  const { t } = useI18n();
  const [config, setConfig] = useState<Config | null>(null);
  const [query, setQuery] = useState("");
  const [filter, setFilter] = useState<SkillFilter>("all");
  const [selectedPath, setSelectedPath] = useState<string | null>(null);
  const [createOpen, setCreateOpen] = useState(false);
  const [createName, setCreateName] = useState("");
  const [createDescription, setCreateDescription] = useState("");
  const [createScope, setCreateScope] = useState<"user" | "project">("user");
  const [creatingSkill, setCreatingSkill] = useState(false);
  const [deleteOpen, setDeleteOpen] = useState(false);
  const [deletingSkill, setDeletingSkill] = useState(false);

  useEffect(() => {
    getConfig()
      .then((next) => {
        setConfig(next);
        setCreateScope(next.default_skill_target === "project" ? "project" : "user");
      })
      .catch((err) => props.onStatus?.(String(err)));
  }, [props.onStatus]);

  const filteredSkills = useMemo(() => {
    const lowered = query.trim().toLowerCase();
    return props.skills.filter((item) => {
      if (filter === "active" && !item.is_active) return false;
      if (filter === "overridden" && item.is_active) return false;
      if (!lowered) return true;
      return `${item.name} ${item.description} ${item.source_type} ${item.resolved_path}`
        .toLowerCase()
        .includes(lowered);
    });
  }, [filter, props.skills, query]);

  useEffect(() => {
    if (filteredSkills.length === 0) {
      setSelectedPath(null);
      return;
    }
    if (!selectedPath || !filteredSkills.some((item) => item.resolved_path === selectedPath)) {
      setSelectedPath(filteredSkills[0].resolved_path);
    }
  }, [filteredSkills, selectedPath]);

  const selectedSkill = useMemo(
    () => filteredSkills.find((item) => item.resolved_path === selectedPath) ?? filteredSkills[0] ?? null,
    [filteredSkills, selectedPath],
  );

  const summary = useMemo(
    () => ({
      total: props.skills.length,
      active: props.skills.filter((item) => item.is_active).length,
      overridden: props.skills.filter((item) => !item.is_active).length,
      user: props.skills.filter((item) => item.source_type === "user").length,
    }),
    [props.skills],
  );

  async function handleCreateSkill() {
    if (!createName.trim()) return;
    setCreatingSkill(true);
    props.onStatus?.(null);
    try {
      const created = await createSkill({
        name: createName.trim(),
        description: createDescription.trim() || undefined,
        scope: createScope,
      });
      await props.onSkillsChanged?.();
      setSelectedPath(created.resolved_path);
      setCreateOpen(false);
      setCreateName("");
      setCreateDescription("");
      props.onStatus?.(t("config.status.skillCreated", { name: created.name }));
    } catch (err) {
      props.onStatus?.(String(err));
    } finally {
      setCreatingSkill(false);
    }
  }

  async function handleDeleteSkill() {
    if (!selectedSkill) return;
    setDeletingSkill(true);
    props.onStatus?.(null);
    try {
      const result = await deleteSkill(selectedSkill.scope, selectedSkill.folder_name);
      await props.onSkillsChanged?.();
      setDeleteOpen(false);
      setSelectedPath(null);
      props.onStatus?.(t("config.status.skillDeleted", { name: result.skill_name }));
    } catch (err) {
      props.onStatus?.(String(err));
    } finally {
      setDeletingSkill(false);
    }
  }

  return (
    <>
      <div className="grid gap-6 xl:grid-cols-[360px_minmax(0,1fr)]">
        <div className="flex min-w-0 flex-col gap-4">
          <SectionCard
            title={t("config.section.skillsManager.title")}
            subtitle={t("config.section.skillsManager.subtitle")}
            right={
              <button
                type="button"
                className="rounded-2xl bg-zinc-100 px-4 py-2 text-sm font-semibold text-zinc-900 hover:bg-white"
                onClick={() => setCreateOpen(true)}
              >
                {t("config.skills.create.button")}
              </button>
            }
          >
            <div className="grid gap-3">
              <input
                value={query}
                onChange={(e) => setQuery(e.target.value)}
                placeholder={t("config.skills.search")}
                className="rounded-2xl border border-zinc-800 bg-zinc-900 px-3 py-2 text-sm text-zinc-100 placeholder:text-zinc-500 focus:outline-none focus:ring-2 focus:ring-zinc-600"
              />
              <div className="flex flex-wrap gap-2">
                {(["all", "active", "overridden"] as SkillFilter[]).map((item) => (
                  <button
                    key={item}
                    type="button"
                    onClick={() => setFilter(item)}
                    className={classNames(
                      "rounded-full px-3 py-1.5 text-xs font-semibold transition",
                      filter === item ? "bg-zinc-100 text-zinc-900" : "bg-zinc-900 text-zinc-300 hover:bg-zinc-800",
                    )}
                  >
                    {t(`config.skills.filter.${item}`)}
                  </button>
                ))}
              </div>
            </div>

            <div className="mt-4 grid gap-3">
              {filteredSkills.map((item) => (
                <button
                  key={item.resolved_path}
                  type="button"
                  onClick={() => setSelectedPath(item.resolved_path)}
                  className={classNames(
                    "w-full rounded-3xl border px-4 py-3 text-left transition",
                    selectedSkill?.resolved_path === item.resolved_path
                      ? "border-zinc-600 bg-zinc-900"
                      : "border-zinc-800 bg-zinc-950 hover:border-zinc-700 hover:bg-zinc-900/60",
                  )}
                >
                  <div className="flex items-start justify-between gap-3">
                    <div className="min-w-0">
                      <div className="truncate text-sm font-semibold text-zinc-100">{item.name}</div>
                      <div className="mt-1 line-clamp-2 text-xs text-zinc-400">{item.description}</div>
                    </div>
                    <span className={classNames("rounded-full border px-2 py-0.5 text-[11px] font-medium", scopeTone(item.source_type))}>
                      {t(`config.skills.scope.${item.source_type}`)}
                    </span>
                  </div>
                  <div className="mt-3 flex flex-wrap items-center gap-2 text-[11px] text-zinc-500">
                    <span>{item.is_active ? t("config.skills.state.active") : t("config.skills.state.overridden")}</span>
                    {item.override_count > 0 ? <span>{t("config.skills.overrideCount", { count: item.override_count })}</span> : null}
                    {item.resources.length > 0 ? <span>{item.resources.join(" / ")}</span> : null}
                    <span
                      className={classNames(
                        "rounded-full border px-2 py-0.5 font-medium",
                        item.deletable
                          ? "border-red-500/20 bg-red-500/10 text-red-200"
                          : "border-zinc-700 bg-zinc-900 text-zinc-400",
                      )}
                    >
                      {item.deletable ? t("config.skills.capability.deletable") : t("config.skills.capability.readonly")}
                    </span>
                  </div>
                </button>
              ))}
              {filteredSkills.length === 0 ? (
                <div className="rounded-2xl border border-dashed border-zinc-800 px-4 py-6 text-sm text-zinc-500">
                  {t("config.skills.manager.empty")}
                </div>
              ) : null}
            </div>
          </SectionCard>
        </div>

        <div className="flex min-w-0 flex-col gap-4">
          <div className="grid gap-4 md:grid-cols-4">
            {[
              { label: t("config.summary.skills"), value: summary.total, tone: "from-slate-500/20 to-slate-500/5" },
              { label: t("config.skills.filter.active"), value: summary.active, tone: "from-emerald-500/20 to-emerald-500/5" },
              { label: t("config.skills.filter.overridden"), value: summary.overridden, tone: "from-amber-500/20 to-amber-500/5" },
              { label: t("config.skills.scope.user"), value: summary.user, tone: "from-sky-500/20 to-sky-500/5" },
            ].map((item) => (
              <div key={item.label} className={classNames("rounded-3xl border border-zinc-800 bg-gradient-to-br p-4", item.tone)}>
                <div className="text-xs uppercase tracking-[0.22em] text-zinc-500">{item.label}</div>
                <div className="mt-3 text-3xl font-semibold text-zinc-100">{item.value}</div>
              </div>
            ))}
          </div>

          <SectionCard
            title={selectedSkill?.name ?? t("config.skills.detail.emptyTitle")}
            subtitle={
              selectedSkill
                ? t("config.skills.detail.subtitle")
                : t("config.skills.detail.emptySubtitle")
            }
            right={
              selectedSkill ? (
                <div className="flex flex-wrap items-center justify-end gap-2">
                  <span
                    className={classNames(
                      "rounded-full border px-2.5 py-1 text-[11px] font-semibold",
                      selectedSkill.deletable
                        ? "border-red-500/20 bg-red-500/10 text-red-200"
                        : "border-zinc-700 bg-zinc-900 text-zinc-400",
                    )}
                  >
                    {selectedSkill.deletable
                      ? t("config.skills.capability.deletable")
                      : t("config.skills.capability.readonly")}
                  </span>
                  <button
                    type="button"
                    className={classNames(
                      "rounded-2xl px-4 py-2 text-sm font-semibold transition",
                      selectedSkill.deletable
                        ? "bg-red-600 text-white hover:bg-red-500"
                        : "cursor-not-allowed bg-zinc-900 text-zinc-500",
                    )}
                    onClick={() => setDeleteOpen(true)}
                    disabled={!selectedSkill.deletable}
                  >
                    {t("common.delete")}
                  </button>
                </div>
              ) : null
            }
          >
            {selectedSkill ? (
              <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_280px]">
                <div className="grid gap-4">
                  <div className="rounded-2xl border border-zinc-800 bg-zinc-900 px-4 py-4">
                    <div className="flex flex-wrap items-center gap-2">
                      <span className={classNames("rounded-full border px-2.5 py-1 text-xs font-semibold", scopeTone(selectedSkill.source_type))}>
                        {t(`config.skills.scope.${selectedSkill.source_type}`)}
                      </span>
                      <span className="rounded-full bg-zinc-800 px-2.5 py-1 text-xs text-zinc-300">
                        {selectedSkill.is_active ? t("config.skills.state.active") : t("config.skills.state.overridden")}
                      </span>
                    </div>
                    <div className="mt-3 text-sm text-zinc-300">{selectedSkill.description}</div>
                    <div className="mt-4 grid gap-3 text-sm text-zinc-400">
                      <div>
                        <div className="text-[11px] uppercase tracking-[0.22em] text-zinc-500">{t("config.skills.path")}</div>
                        <div className="mt-1 break-all font-mono text-xs text-zinc-300">{selectedSkill.resolved_path}</div>
                      </div>
                      <div>
                        <div className="text-[11px] uppercase tracking-[0.22em] text-zinc-500">{t("config.skills.resources")}</div>
                        <div className="mt-1 text-xs text-zinc-300">
                          {selectedSkill.resources.length > 0 ? selectedSkill.resources.join(", ") : t("common.none")}
                        </div>
                      </div>
                      {!selectedSkill.is_active && selectedSkill.overridden_by_path ? (
                        <div>
                          <div className="text-[11px] uppercase tracking-[0.22em] text-zinc-500">{t("config.skills.overriddenBy")}</div>
                          <div className="mt-1 break-all text-xs text-zinc-300">
                            {t("config.skills.overriddenBy.value", {
                              source: t(`config.skills.scope.${selectedSkill.overridden_by_source_type ?? "custom"}`),
                            })}
                          </div>
                          <div className="mt-1 break-all font-mono text-[11px] text-zinc-500">{selectedSkill.overridden_by_path}</div>
                        </div>
                      ) : null}
                    </div>
                  </div>

                  <div className="rounded-2xl border border-zinc-800 bg-zinc-900 px-4 py-4">
                    <div className="text-[11px] uppercase tracking-[0.22em] text-zinc-500">
                      {t("config.skills.delete.policyTitle")}
                    </div>
                    <div className="mt-2 text-sm leading-6 text-zinc-300">{deletePolicy(selectedSkill, t)}</div>
                  </div>

                  {!selectedSkill.deletable ? (
                    <div className="rounded-2xl border border-zinc-800 bg-zinc-900 px-4 py-3 text-sm text-zinc-400">
                      {selectedSkill.source_type === "project"
                        ? t("config.skills.detail.projectNote")
                        : selectedSkill.builtin
                          ? t("config.skills.detail.builtinNote")
                          : t("config.skills.detail.readonlyNote")}
                    </div>
                  ) : null}
                </div>

                <div className="grid gap-4">
                  <SectionCard title={t("config.skills.destinations.title")} subtitle={t("config.skills.destinations.subtitle")}>
                    <div className="space-y-3 text-sm text-zinc-400">
                      <div className="rounded-2xl border border-zinc-800 bg-zinc-900 px-3 py-3">
                        <div className="text-xs uppercase tracking-[0.22em] text-zinc-500">{t("config.skills.scope.user")}</div>
                        <div className="mt-2 break-all font-mono text-xs text-zinc-300">{config?.user_skills_dir ?? t("common.unknown")}</div>
                      </div>
                      <div className="rounded-2xl border border-zinc-800 bg-zinc-900 px-3 py-3">
                        <div className="text-xs uppercase tracking-[0.22em] text-zinc-500">{t("config.skills.scope.project")}</div>
                        <div className="mt-2 break-all font-mono text-xs text-zinc-300">{config?.project_skills_dir ?? t("common.unknown")}</div>
                      </div>
                      <div className="rounded-2xl border border-emerald-500/20 bg-emerald-500/10 px-3 py-3 text-xs text-emerald-200">
                        {t("config.skills.destinations.default", {
                          target: t(`config.skills.scope.${config?.default_skill_target ?? "user"}`),
                        })}
                      </div>
                    </div>
                  </SectionCard>
                </div>
              </div>
            ) : (
              <div className="rounded-2xl border border-dashed border-zinc-800 px-4 py-8 text-sm text-zinc-500">
                {t("config.skills.detail.empty")}
              </div>
            )}
          </SectionCard>
        </div>
      </div>

      {createOpen ? (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center bg-black/65 p-4"
          onMouseDown={(e) => {
            if (e.target === e.currentTarget) setCreateOpen(false);
          }}
        >
          <div className="w-full max-w-xl rounded-3xl border border-zinc-800 bg-zinc-950 p-5 shadow-2xl">
            <div className="text-lg font-semibold text-zinc-100">{t("config.skills.create.title")}</div>
            <div className="mt-1 text-sm text-zinc-400">{t("config.skills.create.subtitle")}</div>

            <div className="mt-5 grid gap-4">
              <div className="grid gap-2">
                <label className="text-sm font-semibold text-zinc-200">{t("config.skills.create.name")}</label>
                <input
                  value={createName}
                  onChange={(e) => setCreateName(e.target.value)}
                  placeholder="incident-triage"
                  className="rounded-2xl border border-zinc-800 bg-zinc-900 px-3 py-2 text-sm text-zinc-100 placeholder:text-zinc-500 focus:outline-none focus:ring-2 focus:ring-zinc-600"
                />
              </div>
              <div className="grid gap-2">
                <label className="text-sm font-semibold text-zinc-200">{t("config.skills.create.description")}</label>
                <textarea
                  value={createDescription}
                  onChange={(e) => setCreateDescription(e.target.value)}
                  placeholder={t("config.skills.create.descriptionPlaceholder")}
                  className="min-h-[96px] rounded-2xl border border-zinc-800 bg-zinc-900 px-3 py-2 text-sm text-zinc-100 placeholder:text-zinc-500 focus:outline-none focus:ring-2 focus:ring-zinc-600"
                />
              </div>
              <div className="grid gap-2">
                <label className="text-sm font-semibold text-zinc-200">{t("config.skills.create.scope")}</label>
                <select
                  value={createScope}
                  onChange={(e) => setCreateScope(e.target.value as "user" | "project")}
                  className="rounded-2xl border border-zinc-800 bg-zinc-900 px-3 py-2 text-sm text-zinc-100 focus:outline-none focus:ring-2 focus:ring-zinc-600"
                >
                  <option value="user">{t("config.skills.create.scopeUser")}</option>
                  <option value="project">{t("config.skills.create.scopeProject")}</option>
                </select>
              </div>
            </div>

            <div className="mt-6 flex justify-end gap-2">
              <button
                type="button"
                className="rounded-2xl bg-zinc-900 px-4 py-2 text-sm font-semibold text-zinc-200 hover:bg-zinc-800 disabled:opacity-50"
                onClick={() => setCreateOpen(false)}
                disabled={creatingSkill}
              >
                {t("common.cancel")}
              </button>
              <button
                type="button"
                className="rounded-2xl bg-zinc-100 px-4 py-2 text-sm font-semibold text-zinc-900 hover:bg-white disabled:opacity-50"
                onClick={() => void handleCreateSkill()}
                disabled={creatingSkill || !createName.trim()}
              >
                {creatingSkill ? t("config.skills.create.creating") : t("config.skills.create.confirm")}
              </button>
            </div>
          </div>
        </div>
      ) : null}

      {deleteOpen && selectedSkill ? (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center bg-black/65 p-4"
          onMouseDown={(e) => {
            if (e.target === e.currentTarget) setDeleteOpen(false);
          }}
        >
          <div className="w-full max-w-lg rounded-3xl border border-zinc-800 bg-zinc-950 p-5 shadow-2xl">
            <div className="text-lg font-semibold text-zinc-100">{t("config.skills.delete.title")}</div>
            <div className="mt-2 text-sm text-zinc-400">
              {t("config.skills.delete.description", {
                name: selectedSkill.name,
                path: selectedSkill.resolved_path,
              })}
            </div>
            <div className="mt-4 rounded-2xl border border-zinc-800 bg-zinc-900 px-3 py-3 font-mono text-xs text-zinc-300">
              {selectedSkill.resolved_path}
            </div>

            <div className="mt-6 flex justify-end gap-2">
              <button
                type="button"
                className="rounded-2xl bg-zinc-900 px-4 py-2 text-sm font-semibold text-zinc-200 hover:bg-zinc-800 disabled:opacity-50"
                onClick={() => setDeleteOpen(false)}
                disabled={deletingSkill}
              >
                {t("common.cancel")}
              </button>
              <button
                type="button"
                className="rounded-2xl bg-red-600 px-4 py-2 text-sm font-semibold text-white hover:bg-red-500 disabled:opacity-50"
                onClick={() => void handleDeleteSkill()}
                disabled={deletingSkill}
              >
                {deletingSkill ? t("config.skills.delete.deleting") : t("config.skills.delete.confirm")}
              </button>
            </div>
          </div>
        </div>
      ) : null}
    </>
  );
}
