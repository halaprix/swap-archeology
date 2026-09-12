import React, { HTMLAttributes } from "react";

export interface CardProps extends HTMLAttributes<HTMLDivElement> {
  variant?: "default" | "muted" | "flat";
  noPadding?: boolean;
}

export const Card: React.FC<CardProps> = ({
  children,
  variant = "default",
  noPadding = false,
  style,
  className = "",
  ...props
}) => {
  const baseStyle: React.CSSProperties = {
    borderRadius: "var(--radius-md)",
    backgroundColor: variant === "muted" ? "var(--color-surface-muted)" : "var(--color-surface)",
    border: `1px solid ${variant === "flat" ? "var(--color-border-subtle)" : "var(--color-border)"}`,
    boxShadow: variant === "flat" ? "none" : "var(--shadow-sm)",
    padding: noPadding ? 0 : "var(--space-4)",
    display: "flex",
    flexDirection: "column",
    gap: "var(--space-3)",
    overflow: "hidden",
  };

  return (
    <div style={{ ...baseStyle, ...style }} className={className} {...props}>
      {children}
    </div>
  );
};

export interface CardHeaderProps extends Omit<HTMLAttributes<HTMLDivElement>, "title"> {
  title?: React.ReactNode;
  subtitle?: React.ReactNode;
  action?: React.ReactNode;
}

export const CardHeader: React.FC<CardHeaderProps> = ({
  title,
  subtitle,
  action,
  children,
  style,
  className = "",
  ...props
}) => {
  return (
    <div
      style={{
        display: "flex",
        alignItems: "flex-start",
        justifyContent: "space-between",
        gap: "var(--space-4)",
        borderBottom: "1px solid var(--color-border-subtle)",
        paddingBottom: "var(--space-3)",
        ...style,
      }}
      className={className}
      {...props}
    >
      <div>
        {title && (
          <h3
            style={{
              fontSize: "var(--font-size-md)",
              fontWeight: "var(--font-weight-semibold)",
              color: "var(--color-text)",
              letterSpacing: "-0.01em",
            }}
          >
            {title}
          </h3>
        )}
        {subtitle && (
          <p
            style={{
              fontSize: "var(--font-size-xs)",
              color: "var(--color-text-muted)",
              marginTop: "2px",
            }}
          >
            {subtitle}
          </p>
        )}
      </div>
      {action && <div>{action}</div>}
      {children}
    </div>
  );
};
