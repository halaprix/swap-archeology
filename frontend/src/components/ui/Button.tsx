"use client";

import React, { ButtonHTMLAttributes, forwardRef } from "react";

export interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: "primary" | "secondary" | "outline" | "ghost" | "danger";
  size?: "sm" | "md" | "lg";
  isLoading?: boolean;
}

export const Button = forwardRef<HTMLButtonElement, ButtonProps>(
  (
    {
      children,
      variant = "secondary",
      size = "md",
      isLoading = false,
      disabled,
      className = "",
      type = "button",
      ...props
    },
    ref
  ) => {
    const baseStyles: React.CSSProperties = {
      display: "inline-flex",
      alignItems: "center",
      justifyContent: "center",
      gap: "var(--space-2)",
      fontFamily: "var(--font-sans)",
      fontWeight: "var(--font-weight-medium)",
      borderRadius: "var(--radius-sm)",
      cursor: disabled || isLoading ? "not-allowed" : "pointer",
      opacity: disabled ? 0.5 : 1,
      transition: "background var(--transition-fast), border-color var(--transition-fast), color var(--transition-fast)",
      whiteSpace: "nowrap",
      textDecoration: "none",
      border: "1px solid transparent",
    };

    const sizeStyles: Record<"sm" | "md" | "lg", React.CSSProperties> = {
      sm: {
        padding: "4px 10px",
        fontSize: "var(--font-size-xs)",
        height: "28px",
      },
      md: {
        padding: "6px 14px",
        fontSize: "var(--font-size-sm)",
        height: "34px",
      },
      lg: {
        padding: "8px 18px",
        fontSize: "var(--font-size-base)",
        height: "40px",
      },
    };

    const variantStyles: Record<
      "primary" | "secondary" | "outline" | "ghost" | "danger",
      React.CSSProperties
    > = {
      primary: {
        backgroundColor: "var(--color-teal)",
        color: "var(--color-text-inverse)",
        borderColor: "var(--color-teal-hover)",
      },
      secondary: {
        backgroundColor: "var(--color-surface)",
        color: "var(--color-text)",
        borderColor: "var(--color-border-strong)",
      },
      outline: {
        backgroundColor: "transparent",
        color: "var(--color-text)",
        borderColor: "var(--color-border)",
      },
      ghost: {
        backgroundColor: "transparent",
        color: "var(--color-text-secondary)",
        borderColor: "transparent",
      },
      danger: {
        backgroundColor: "var(--color-error-light)",
        color: "var(--color-error-text)",
        borderColor: "var(--color-error-border)",
      },
    };

    return (
      <button
        ref={ref}
        type={type}
        disabled={disabled || isLoading}
        style={{
          ...baseStyles,
          ...sizeStyles[size],
          ...variantStyles[variant],
        }}
        className={className}
        {...props}
      >
        {isLoading && (
          <span
            style={{
              width: "12px",
              height: "12px",
              border: "2px solid currentColor",
              borderRightColor: "transparent",
              borderRadius: "50%",
              display: "inline-block",
              animation: "spin 0.6s linear infinite",
            }}
            aria-hidden="true"
          />
        )}
        {children}
      </button>
    );
  }
);

Button.displayName = "Button";
