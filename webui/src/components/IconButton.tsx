import type { ComponentPropsWithoutRef } from "react";
import * as Tooltip from "@radix-ui/react-tooltip";
import type { LucideIcon } from "lucide-react";
import "./iconButton.css";

export { Provider as IconButtonProvider } from "@radix-ui/react-tooltip";

type IconButtonProps = Omit<
  ComponentPropsWithoutRef<"button">,
  "type" | "children" | "title" | "aria-label"
> & {
  icon: LucideIcon;
  label: string;
  tooltipContainer?: HTMLElement | null;
};

export function IconButton({ icon: Icon, label, className, tooltipContainer, ...buttonProps }: IconButtonProps) {
  return (
    <Tooltip.Root>
      <Tooltip.Trigger asChild>
        <button
          {...buttonProps}
          type="button"
          className={className ? `icon-button ${className}` : "icon-button"}
          aria-label={label}
        >
          <Icon size={16} strokeWidth={1.65} aria-hidden="true" focusable="false" />
        </button>
      </Tooltip.Trigger>
      <Tooltip.Portal container={tooltipContainer}>
        <Tooltip.Content
          className="icon-button-tooltip"
          side="bottom"
          sideOffset={6}
          collisionPadding={8}
          hideWhenDetached
        >
          {label}
        </Tooltip.Content>
      </Tooltip.Portal>
    </Tooltip.Root>
  );
}
