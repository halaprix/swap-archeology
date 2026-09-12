import React, { HTMLAttributes } from "react";
import { getVenueInfo } from "@/lib/venues";

export interface BadgeProps extends HTMLAttributes<HTMLSpanElement> {
  variant?: "neutral" | "success" | "warning" | "error" | "info" | "venue";
  venueFamily?: string;
  size?: "sm" | "md";
}

export const Badge: React.FC<BadgeProps> = ({
  children,
  variant = "neutral",
  venueFamily,
  size = "sm",
  style,
  className = "",
  ...props
}) => {
  const isSm = size === "sm";

  const baseStyle: React.CSSProperties = {
    display: "inline-flex",
    alignItems: "center",
    gap: "var(--space-1)",
    borderRadius: "var(--radius-xs)",
    fontWeight: "var(--font-weight-medium)",
    fontSize: isSm ? "var(--font-size-xs)" : "var(--font-size-sm)",
    padding: isSm ? "1px 6px" : "3px 8px",
    lineHeight: "1.3",
    border: "1px solid transparent",
    whiteSpace: "nowrap",
    fontFamily: "var(--font-mono)",
    letterSpacing: "0.01em",
  };

  let variantStyle: React.CSSProperties = {};

  if (variant === "venue" && venueFamily) {
    const venue = getVenueInfo(venueFamily);
    variantStyle = {
      backgroundColor: venue.badgeBg,
      borderColor: venue.badgeBorder,
      color: venue.badgeText,
    };
  } else {
    switch (variant) {
      case "success":
        variantStyle = {
          backgroundColor: "var(--color-success-light)",
          borderColor: "var(--color-success-border)",
          color: "var(--color-success-text)",
        };
        break;
      case "warning":
        variantStyle = {
          backgroundColor: "var(--color-warning-light)",
          borderColor: "var(--color-warning-border)",
          color: "var(--color-warning-text)",
        };
        break;
      case "error":
        variantStyle = {
          backgroundColor: "var(--color-error-light)",
          borderColor: "var(--color-error-border)",
          color: "var(--color-error-text)",
        };
        break;
      case "info":
        variantStyle = {
          backgroundColor: "var(--color-info-light)",
          borderColor: "var(--color-info-border)",
          color: "var(--color-info-text)",
        };
        break;
      case "neutral":
      default:
        variantStyle = {
          backgroundColor: "var(--color-surface-muted)",
          borderColor: "var(--color-border)",
          color: "var(--color-text-secondary)",
        };
        break;
    }
  }

  return (
    <span
      style={{
        ...baseStyle,
        ...variantStyle,
        ...style,
      }}
      className={className}
      {...props}
    >
      {children}
    </span>
  );
};
