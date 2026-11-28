import { useEffect, useMemo, useRef, useState } from "react";
import ChatView from "./views/ChatView";
import { IconButtonProvider } from "./components/IconButton";
import ConfigView from "./views/ConfigView";
import MemoryWorkspaceView from "./views/MemoryWorkspaceView";
import type { BotMeta } from "./lib/types";
import { createBot, deleteBot, listBots } from "./lib/api";
import {
  I18nProvider,
  LANGUAGE_STORAGE_KEY,
  createTranslator,
  parseLanguage,
  type Language,
} from "./lib/i18n";

type Tab = "chat" | "memory" | "config";
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
  const [memorySourceSlot, setMemorySourceSlot] = useState<HTMLDivElement | null>(null);

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
      { id: "memory" as const, label: t("app.tab.memory") },
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
    document.title =
      tab === "chat" ? t("app.title.chat") : tab === "memory" ? t("app.title.memory") : t("app.title.config");
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
      <div className="app-shell flex h-full flex-col">
        <header className="workspace-header">
          <div className="workspace-brand">
            <span className="workspace-brand-mark" aria-hidden="true">
              <span className="workspace-brand-monogram" />
              <span className="workspace-brand-dot" />
            </span>
            <span className="workspace-brand-name">{t("app.brand")}</span>
          </div>
          <nav className="workspace-nav">
            {tabs.map((item) => (
              <button
                key={item.id}
                type="button"
                onClick={() => setTab(item.id)}
                className="workspace-tab"
                aria-current={tab === item.id ? "page" : undefined}
              >
                {item.label}
              </button>
            ))}
          </nav>
          <div className="workspace-controls">
            <div className="workspace-bots">
              <div className="workspace-bot-picker">
                <select
                  value={botId}
                  onChange={(e) => setBotId(e.target.value)}
                  aria-label={t("app.bot.select")}
                >
                  {bots.map((b) => (
                    <option key={b.bot_id} value={b.bot_id}>
                      {b.name}
                    </option>
                  ))}
                </select>
                <svg viewBox="0 0 16 16" fill="none" stroke="currentColor" aria-hidden="true">
                  <path d="m4.5 6.5 3.5 3.5 3.5-3.5" />
                </svg>
              </div>
              <button
                type="button"
                onClick={() => void onCreateBot()}
                className="control-icon-button workspace-bot-action"
                aria-label={t("app.bot.new")}
                title={t("app.bot.new")}
              >
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" aria-hidden="true">
                  <path d="M12 5v14M5 12h14" />
                </svg>
              </button>
              {botId !== "default" ? (
                <button
                  type="button"
                  onClick={() => void onDeleteBot()}
                  className="control-icon-button workspace-bot-action workspace-action-danger"
                  aria-label={t("app.bot.delete")}
                  title={t("app.bot.delete")}
                >
                  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" aria-hidden="true">
                    <path d="M4 7h16m-10 4v6m4-6v6M6 7l1 13h10l1-13M9 7V4h6v3" />
                  </svg>
                </button>
              ) : null}
            </div>
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
          </div>
          {tab === "memory" && <div className="memory-source-slot" ref={setMemorySourceSlot} />}
          {botStatus && <p className="workspace-status" role="status">{botStatus}</p>}
        </header>

        <IconButtonProvider delayDuration={400}>
          <main className="min-h-0 flex-1">
            {/* Keep ChatView mounted so in-flight streaming state survives tab switches. */}
            <div className="h-full" hidden={tab !== "chat"} aria-hidden={tab !== "chat"}>
              <ChatView botId={botId} botName={currentBot?.name || botId} />
            </div>
            {tab === "memory" ? (
              <div className="h-full">
                <MemoryWorkspaceView key={botId} botId={botId} sourceSlot={memorySourceSlot} />
              </div>
            ) : null}
            {tab === "config" ? (
              <div className="h-full">
                <ConfigView botId={botId} onBotsChanged={refreshBots} onSelectBot={setBotId} />
              </div>
            ) : null}
          </main>
        </IconButtonProvider>

        {createOpen ? (
          <div
            className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4"
            onMouseDown={(e) => {
              if (e.target === e.currentTarget) setCreateOpen(false);
            }}
          >
            <div className="workspace-dialog" role="dialog" aria-modal="true" aria-labelledby="workspace-create-title">
              <div id="workspace-create-title" className="workspace-dialog-title">{t("app.bot.create.title")}</div>
              <div className="mt-1 text-sm text-zinc-400">{t("app.bot.create.description")}</div>
              <div className="mt-4 grid gap-2">
                <label htmlFor="workspace-new-bot-name" className="text-sm font-semibold text-zinc-200">{t("app.bot.create.label")}</label>
                <input
                  id="workspace-new-bot-name"
                  value={createName}
                  onChange={(e) => setCreateName(e.target.value)}
                  placeholder={t("app.bot.create.placeholder")}
                  className="workspace-dialog-input"
                  onKeyDown={(e) => {
                    if (e.key === "Enter") void confirmCreateBot();
                  }}
                  autoFocus
                  disabled={botBusy}
                />
              </div>
              <div className="mt-5 flex justify-end gap-2">
                <button
                  className="chat-button"
                  onClick={() => setCreateOpen(false)}
                  disabled={botBusy}
                >
                  {t("common.cancel")}
                </button>
                <button
                  className="chat-button chat-button-primary"
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
            <div className="workspace-dialog" role="dialog" aria-modal="true" aria-labelledby="workspace-delete-title">
              <div id="workspace-delete-title" className="workspace-dialog-title">{t("app.bot.delete.title")}</div>
              <div className="mt-1 text-sm text-zinc-400">
                {t("app.bot.delete.description", { name: currentBot?.name || botId })}
              </div>
              <div className="mt-5 flex justify-end gap-2">
                <button
                  className="chat-button"
                  onClick={() => setDeleteOpen(false)}
                  disabled={botBusy}
                >
                  {t("common.cancel")}
                </button>
                <button
                  className="chat-button chat-button-stop"
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
