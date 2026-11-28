import type { ReactNode } from "react";
import { useI18n } from "../lib/i18n";

export function SettingsSection(props: { title: string; subtitle?: string; children: ReactNode; right?: ReactNode }) {
  return (
    <section className="settings-section">
      <div className="mb-4 flex items-start justify-between gap-4">
        <div>
          <h2>{props.title}</h2>
          {props.subtitle ? <div className="mt-1 text-xs text-zinc-500">{props.subtitle}</div> : null}
        </div>
        {props.right}
      </div>
      {props.children}
    </section>
  );
}

export function SettingsToggle(props: { enabled: boolean; onClick: () => void; disabled?: boolean; labels?: [string, string] }) {
  const { t } = useI18n();
  const labels = props.labels ?? [t("common.off"), t("common.on")];
  return (
    <button
      type="button"
      className="chat-button settings-toggle"
      data-enabled={props.enabled}
      onClick={props.onClick}
      aria-pressed={props.enabled}
      disabled={props.disabled}
    >
      {props.enabled ? labels[1] : labels[0]}
    </button>
  );
}
