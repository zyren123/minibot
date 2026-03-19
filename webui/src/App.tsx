import { useEffect, useMemo, useRef, useState } from "react";
import ChatView from "./views/ChatView";
import ConfigView from "./views/ConfigView";
import type { BotMeta } from "./lib/types";
import { createBot, deleteBot, listBots } from "./lib/api";
import {
  I18nProvider,
  LANGUAGE_STORAGE_KEY,
  createTranslator,
  parseLanguage,
  type Language,
} from "./lib/i18n";

type Tab = "chat" | "config";
type ThemeMode = "dark" | "light" | "system";
type ResolvedTheme = "dark" | "light";

const BOT_STORAGE_KEY = "minibot_web_bot_id";
const THEME_STORAGE_KEY = "minibot_web_theme_mode";
const THEME_CYCLE: ThemeMode[] = ["light", "dark", "system"];
const LANGUAGE_OPTIONS: Language[] = ["zh-CN", "en"];

function parseThemeMode(value: string | null): ThemeMode {
  return value === "dark" || value === "light" || value === "system" ? value : "system";
}

function getSystemTheme(): ResolvedTheme {
  if (typeof window === "undefined" || !window.matchMedia) return "dark";
  return window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
}

function classNames(...values: Array<string | false | null | undefined>) {
  return values.filter(Boolean).join(" ");
}

function nextThemeMode(mode: ThemeMode): ThemeMode {
  const index = THEME_CYCLE.indexOf(mode);
  return THEME_CYCLE[(index + 1) % THEME_CYCLE.length];
}

function SunIcon(props: { className?: string }) {
  return (
    <svg
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.8"
      strokeLinecap="round"
      strokeLinejoin="round"
      className={props.className}
      aria-hidden="true"
    >
      <circle cx="12" cy="12" r="4" />
      <path d="M12 2.75V5.25" />
      <path d="M12 18.75V21.25" />
      <path d="M21.25 12H18.75" />
      <path d="M5.25 12H2.75" />
      <path d="M18.54 5.46L16.77 7.23" />
      <path d="M7.23 16.77L5.46 18.54" />
      <path d="M18.54 18.54L16.77 16.77" />
      <path d="M7.23 7.23L5.46 5.46" />
    </svg>
  );
}

function MoonIcon(props: { className?: string }) {
  return (
    <svg
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.8"
      strokeLinecap="round"
      strokeLinejoin="round"
      className={props.className}
      aria-hidden="true"
    >
      <path d="M20 14.31A8 8 0 0 1 9.69 4 6.25 6.25 0 1 0 20 14.31Z" />
    </svg>
  );
}

function LaptopIcon(props: { className?: string }) {
  return (
    <svg
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.8"
      strokeLinecap="round"
      strokeLinejoin="round"
      className={props.className}
      aria-hidden="true"
    >
      <rect x="4" y="5" width="16" height="11" rx="1.5" />
      <path d="M2.75 18.5H21.25" />
      <path d="M9.5 18.5H14.5" />
    </svg>
  );
}

function GlobeIcon(props: { className?: string }) {
  return (
    <svg
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.8"
      strokeLinecap="round"
      strokeLinejoin="round"
      className={props.className}
      aria-hidden="true"
    >
      <circle cx="12" cy="12" r="9" />
      <path d="M3 12H21" />
      <path d="M12 3C14.5 5.4 16 8.58 16 12C16 15.42 14.5 18.6 12 21" />
      <path d="M12 3C9.5 5.4 8 8.58 8 12C8 15.42 9.5 18.6 12 21" />
    </svg>
  );
}

function CheckIcon(props: { className?: string }) {
  return (
    <svg
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
      className={props.className}
      aria-hidden="true"
    >
      <path d="M6 12.5L10 16.5L18 8.5" />
    </svg>
  );
}

function ThemeModeGlyph(props: { mode: ThemeMode }) {
  if (props.mode === "light") {
    return <SunIcon className="control-button-icon" />;
  }
  if (props.mode === "dark") {
    return <MoonIcon className="control-button-icon" />;
  }
  return <LaptopIcon className="control-button-icon" />;
}

export default function App() {
  const [tab, setTab] = useState<Tab>("chat");
  const [bots, setBots] = useState<BotMeta[]>([]);
  const [botId, setBotId] = useState<string>("default");
  const [themeMode, setThemeMode] = useState<ThemeMode>(() =>
    typeof window === "undefined" ? "system" : parseThemeMode(window.localStorage.getItem(THEME_STORAGE_KEY)),
  );
  const [language, setLanguage] = useState<Language>(() =>
    typeof window === "undefined" ? "zh-CN" : parseLanguage(window.localStorage.getItem(LANGUAGE_STORAGE_KEY)),
  );
  const [systemTheme, setSystemTheme] = useState<ResolvedTheme>(() => getSystemTheme());
  const [botStatus, setBotStatus] = useState<string | null>(null);
  const [createOpen, setCreateOpen] = useState(false);
  const [createName, setCreateName] = useState("");
  const [deleteOpen, setDeleteOpen] = useState(false);
  const [languageMenuOpen, setLanguageMenuOpen] = useState(false);
  const [botBusy, setBotBusy] = useState(false);

  const languageMenuRef = useRef<HTMLDivElement | null>(null);

  const t = useMemo(() => createTranslator(language), [language]);
  const i18nValue = useMemo(
    () => ({
      language,
      setLanguage,
      t,
    }),
    [language, setLanguage, t],
  );
  const tabs = useMemo(
    () => [
      { id: "chat" as const, label: t("app.tab.chat") },
      { id: "config" as const, label: t("app.tab.config") },
    ],
    [t],
  );
  const resolvedTheme = themeMode === "system" ? systemTheme : themeMode;
  const nextTheme = nextThemeMode(themeMode);
  const themeLabel = t(`theme.mode.${themeMode}`);
  const nextThemeLabel = t(`theme.mode.${nextTheme}`);
  const currentLanguageLabel = language === "zh-CN" ? t("language.option.zh") : t("language.option.en");

  useEffect(() => {
    document.title = tab === "chat" ? t("app.title.chat") : t("app.title.config");
  }, [tab, t]);

  async function refreshBots() {
    const list = await listBots();
    setBots(list);

    const stored = localStorage.getItem(BOT_STORAGE_KEY) || "";
    const candidate = stored || botId;
    const exists = list.some((b) => b.bot_id === candidate);
    setBotId(exists ? candidate : "default");
  }

  useEffect(() => {
    refreshBots().catch((e) => setBotStatus(String(e)));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    localStorage.setItem(BOT_STORAGE_KEY, botId);
  }, [botId]);

  useEffect(() => {
    if (!window.matchMedia) return;
    const mediaQuery = window.matchMedia("(prefers-color-scheme: dark)");
    const onChange = (event: MediaQueryListEvent) => setSystemTheme(event.matches ? "dark" : "light");
    setSystemTheme(mediaQuery.matches ? "dark" : "light");
    mediaQuery.addEventListener("change", onChange);
    return () => mediaQuery.removeEventListener("change", onChange);
  }, []);

  useEffect(() => {
    localStorage.setItem(THEME_STORAGE_KEY, themeMode);
  }, [themeMode]);

  useEffect(() => {
    localStorage.setItem(LANGUAGE_STORAGE_KEY, language);
  }, [language]);

  useEffect(() => {
    document.documentElement.dataset.themeMode = themeMode;
    document.documentElement.dataset.theme = resolvedTheme;
    document.documentElement.style.colorScheme = resolvedTheme;
  }, [resolvedTheme, themeMode]);

  useEffect(() => {
    document.documentElement.lang = language;
  }, [language]);

  useEffect(() => {
    if (!languageMenuOpen) return;
    function onPointerDown(event: MouseEvent) {
      if (!languageMenuRef.current?.contains(event.target as Node)) {
        setLanguageMenuOpen(false);
      }
    }
    document.addEventListener("mousedown", onPointerDown);
    return () => document.removeEventListener("mousedown", onPointerDown);
  }, [languageMenuOpen]);

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
      setLanguageMenuOpen(false);
    }
    if (!createOpen && !deleteOpen && !languageMenuOpen) return;
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [createOpen, deleteOpen, languageMenuOpen]);

  const currentBot = bots.find((b) => b.bot_id === botId);

  return (
    <I18nProvider value={i18nValue}>
      <div className="app-shell flex h-full flex-col bg-zinc-950 text-zinc-100">
        <header className="flex flex-wrap items-center justify-between gap-3 border-b border-zinc-800 px-4 py-3">
          <div className="flex flex-wrap items-center gap-3">
            <div className="text-lg font-semibold tracking-tight">{t("app.brand")}</div>
            <div className="flex flex-wrap items-center gap-2">
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
                {t("app.bot.new")}
              </button>
              {botId !== "default" ? (
                <button
                  onClick={() => void onDeleteBot()}
                  className="rounded-md bg-red-600 px-2 py-1.5 text-sm text-white hover:bg-red-500"
                >
                  {t("app.bot.delete")}
                </button>
              ) : null}
            </div>
          </div>
          <div className="flex flex-wrap items-center justify-end gap-2">
            {botStatus && <div className="text-xs text-zinc-400">{botStatus}</div>}
            <div ref={languageMenuRef} className="relative">
              <button
                type="button"
                className="control-icon-button language-mode-button"
                aria-label={t("language.button.aria", { current: currentLanguageLabel })}
                aria-haspopup="menu"
                aria-expanded={languageMenuOpen}
                title={t("language.button.title")}
                onClick={() => setLanguageMenuOpen((prev) => !prev)}
              >
                <GlobeIcon className="control-button-icon" />
              </button>
              {languageMenuOpen ? (
                <div className="control-menu-popover" role="menu" aria-label={t("language.menu.label")}>
                  <div className="control-menu-label">{t("language.menu.label")}</div>
                  <div className="control-menu-list">
                    {LANGUAGE_OPTIONS.map((option) => {
                      const active = option === language;
                      const label = option === "zh-CN" ? t("language.option.zh") : t("language.option.en");
                      const description =
                        option === "zh-CN"
                          ? t("language.option.zh.description")
                          : t("language.option.en.description");
                      return (
                        <button
                          key={option}
                          type="button"
                          role="menuitemradio"
                          aria-checked={active}
                          className={classNames("control-menu-item", active && "control-menu-item-active")}
                          onClick={() => {
                            setLanguage(option);
                            setLanguageMenuOpen(false);
                          }}
                        >
                          <span className="control-menu-item-copy">
                            <span className="control-menu-item-title">{label}</span>
                            <span className="control-menu-item-description">{description}</span>
                          </span>
                          <span className="control-menu-item-check">
                            {active ? <CheckIcon className="control-menu-check-icon" /> : null}
                          </span>
                        </button>
                      );
                    })}
                  </div>
                </div>
              ) : null}
            </div>
            <button
              type="button"
              className="control-icon-button theme-mode-button"
              data-mode={themeMode}
              aria-label={t("theme.button.aria", { current: themeLabel, next: nextThemeLabel })}
              title={t("theme.button.aria", { current: themeLabel, next: nextThemeLabel })}
              onClick={() => setThemeMode(nextTheme)}
            >
              <ThemeModeGlyph mode={themeMode} />
            </button>
            {tabs.map((item) => (
              <button
                key={item.id}
                onClick={() => setTab(item.id)}
                className={classNames(
                  "rounded-md px-3 py-1.5 text-sm font-medium",
                  tab === item.id
                    ? "bg-zinc-100 text-zinc-900"
                    : "bg-zinc-900 text-zinc-200 hover:bg-zinc-800",
                )}
              >
                {item.label}
              </button>
            ))}
          </div>
        </header>

        <main className="min-h-0 flex-1">
          {tab === "chat" ? (
            <ChatView botId={botId} botName={currentBot?.name || botId} />
          ) : (
            <ConfigView botId={botId} onBotsChanged={refreshBots} onSelectBot={setBotId} />
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
              <div className="text-lg font-semibold">{t("app.bot.create.title")}</div>
              <div className="mt-1 text-sm text-zinc-400">{t("app.bot.create.description")}</div>
              <div className="mt-4 grid gap-2">
                <label className="text-sm font-semibold text-zinc-200">{t("app.bot.create.label")}</label>
                <input
                  value={createName}
                  onChange={(e) => setCreateName(e.target.value)}
                  placeholder={t("app.bot.create.placeholder")}
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
                  {t("common.cancel")}
                </button>
                <button
                  className="rounded-xl bg-zinc-100 px-4 py-2 text-sm font-semibold text-zinc-900 hover:bg-white disabled:opacity-50"
                  onClick={() => void confirmCreateBot()}
                  disabled={botBusy}
                >
                  {t("app.bot.create.confirm")}
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
              <div className="text-lg font-semibold">{t("app.bot.delete.title")}</div>
              <div className="mt-1 text-sm text-zinc-400">
                {t("app.bot.delete.description", { name: currentBot?.name || botId })}
              </div>
              <div className="mt-5 flex justify-end gap-2">
                <button
                  className="rounded-xl bg-zinc-900 px-4 py-2 text-sm font-semibold text-zinc-200 hover:bg-zinc-800 disabled:opacity-50"
                  onClick={() => setDeleteOpen(false)}
                  disabled={botBusy}
                >
                  {t("common.cancel")}
                </button>
                <button
                  className="rounded-xl bg-red-600 px-4 py-2 text-sm font-semibold text-white hover:bg-red-500 disabled:opacity-50"
                  onClick={() => void confirmDeleteBot()}
                  disabled={botBusy}
                >
                  {t("common.delete")}
                </button>
              </div>
            </div>
          </div>
        ) : null}
      </div>
    </I18nProvider>
  );
}
