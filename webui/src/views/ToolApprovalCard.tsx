import { useI18n } from "../lib/i18n";
import type { ToolApprovalRequestEvent } from "../lib/types";

export type PendingToolApproval = ToolApprovalRequestEvent & {
  botId: string;
  state: "pending" | "submitting" | "error";
};

export function ToolApprovalCard({ approval, onDecision }: {
  approval: PendingToolApproval;
  onDecision: (approved: boolean) => void;
}) {
  const { t } = useI18n();
  const submitting = approval.state === "submitting";
  return (
    <section
      aria-label={t("chat.approval.title")}
      aria-busy={submitting}
      className="min-w-0 space-y-3 rounded-[26px] border border-amber-200/15 bg-zinc-900 p-4 shadow-[0_14px_42px_rgba(0,0,0,0.24)] sm:px-5"
    >
      <div className="space-y-1">
        <h3 className="text-sm font-semibold text-amber-100">{t("chat.approval.title")}</h3>
        <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1 text-sm">
          <code dir="ltr" className="break-all text-zinc-100">{approval.tool_name}</code>
          <span className="break-all text-zinc-400">{t("chat.approval.actor")} <bdi>{approval.approval_actor}</bdi></span>
        </div>
      </div>
      <pre
        dir="ltr"
        tabIndex={0}
        aria-label={t("chat.tool.arguments")}
        className="max-h-40 overflow-auto whitespace-pre-wrap break-all rounded-2xl bg-zinc-950 p-3 text-xs leading-relaxed text-zinc-200 focus-visible:outline focus-visible:outline-2 focus-visible:outline-amber-200"
      >{approval.approval_preview}</pre>
      <div className="flex flex-wrap items-center justify-end gap-2">
        {approval.state === "error" ? (
          <p role="alert" className="mr-auto text-xs text-red-300">{t("chat.approval.error")}</p>
        ) : null}
        {submitting ? (
          <p role="status" className="mr-auto text-xs text-zinc-400">{t("chat.approval.submitting")}</p>
        ) : null}
        <button
          type="button"
          disabled={submitting}
          className="min-h-10 rounded-2xl border border-zinc-700 bg-zinc-950 px-4 text-sm font-semibold text-zinc-200 hover:bg-zinc-800 focus-visible:outline focus-visible:outline-2 focus-visible:outline-amber-200 disabled:opacity-50"
          onClick={() => onDecision(false)}
        >{t("chat.approval.deny")}</button>
        <button
          type="button"
          disabled={submitting}
          className="min-h-10 rounded-2xl bg-amber-200 px-4 text-sm font-semibold text-zinc-950 hover:bg-amber-100 focus-visible:outline focus-visible:outline-2 focus-visible:outline-amber-200 disabled:opacity-50"
          onClick={() => onDecision(true)}
        >{t("chat.approval.approve")}</button>
      </div>
    </section>
  );
}
