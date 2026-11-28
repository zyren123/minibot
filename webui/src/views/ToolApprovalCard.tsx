import { useI18n } from "../lib/i18n";
import type { PendingToolApproval } from "./chatSessionState";

export function ToolApprovalCard({ approval, onDecision }: {
  approval: PendingToolApproval;
  onDecision: (approved: boolean) => void;
}) {
  const { t } = useI18n();
  const submitting = approval.state === "submitting";
  return (
    <section aria-label={t("chat.approval.title")} className="tool-approval">
      <header className="tool-approval-header">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" aria-hidden="true">
          <path d="m12 3 7 3v5c0 4.5-2.5 7.5-7 10-4.5-2.5-7-5.5-7-10V6l7-3Z" />
          <path d="M12 8v4m0 3h.01" />
        </svg>
        <div>
          <h3><bdi>{approval.tool_name}</bdi></h3>
          <p>{t("chat.approval.actor")} <bdi>{approval.approval_actor}</bdi></p>
        </div>
      </header>
      <pre
        dir="ltr"
        tabIndex={0}
        aria-label={t("chat.tool.arguments")}
        className="tool-approval-preview"
      >{approval.approval_preview}</pre>
      {approval.state === "error" ? (
        <p role="alert" className="tool-approval-feedback tool-approval-error">{t("chat.approval.error")}</p>
      ) : null}
      {submitting ? (
        <p role="status" className="tool-approval-feedback">{t("chat.approval.submitting")}</p>
      ) : null}
      <div className="tool-approval-actions" aria-busy={submitting}>
        <button type="button" className="chat-button" disabled={submitting} onClick={() => onDecision(false)}>
          {t("chat.approval.deny")}
        </button>
        <button type="button" className="chat-button chat-button-primary" disabled={submitting} onClick={() => onDecision(true)}>
          {t("chat.approval.approve")}
        </button>
      </div>
    </section>
  );
}
