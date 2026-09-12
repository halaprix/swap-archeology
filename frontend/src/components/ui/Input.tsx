"use client";

import React, { forwardRef, InputHTMLAttributes } from "react";

export interface InputProps extends InputHTMLAttributes<HTMLInputElement> {
  label?: string;
  helperText?: string;
  error?: string;
  prefixElement?: React.ReactNode;
  suffixElement?: React.ReactNode;
  isMono?: boolean;
}

export const Input = forwardRef<HTMLInputElement, InputProps>(
  (
    {
      label,
      helperText,
      error,
      prefixElement,
      suffixElement,
      isMono = false,
      disabled,
      id,
      className = "",
      style,
      ...props
    },
    ref
  ) => {
    const inputId = id || (label ? `input-${label.toLowerCase().replace(/\s+/g, "-")}` : undefined);

    return (
      <div style={{ display: "flex", flexDirection: "column", gap: "var(--space-1)" }}>
        {label && (
          <label
            htmlFor={inputId}
            style={{
              fontSize: "var(--font-size-xs)",
              fontWeight: "var(--font-weight-medium)",
              color: "var(--color-text-secondary)",
              textTransform: "uppercase",
              letterSpacing: "0.05em",
            }}
          >
            {label}
          </label>
        )}
        <div
          style={{
            display: "flex",
            alignItems: "center",
            borderRadius: "var(--radius-sm)",
            border: `1px solid ${error ? "var(--color-error)" : "var(--color-border-strong)"}`,
            backgroundColor: disabled ? "var(--color-surface-muted)" : "var(--color-surface)",
            overflow: "hidden",
            transition: "border-color var(--transition-fast), box-shadow var(--transition-fast)",
          }}
        >
          {prefixElement && (
            <div
              style={{
                padding: "0 var(--space-2)",
                fontSize: "var(--font-size-sm)",
                color: "var(--color-text-muted)",
                display: "flex",
                alignItems: "center",
                borderRight: "1px solid var(--color-border-subtle)",
                backgroundColor: "var(--color-surface-muted)",
                height: "34px",
              }}
            >
              {prefixElement}
            </div>
          )}
          <input
            ref={ref}
            id={inputId}
            disabled={disabled}
            aria-invalid={Boolean(error)}
            aria-describedby={error ? `${inputId}-error` : helperText ? `${inputId}-helper` : undefined}
            style={{
              flex: 1,
              border: "none",
              outline: "none",
              background: "transparent",
              padding: "6px 10px",
              fontSize: "var(--font-size-sm)",
              fontFamily: isMono ? "var(--font-mono)" : "var(--font-sans)",
              color: "var(--color-text)",
              height: "34px",
              width: "100%",
              ...style,
            }}
            className={className}
            {...props}
          />
          {suffixElement && (
            <div
              style={{
                padding: "0 var(--space-3)",
                fontSize: "var(--font-size-xs)",
                fontWeight: "var(--font-weight-medium)",
                color: "var(--color-text-secondary)",
                display: "flex",
                alignItems: "center",
                backgroundColor: "var(--color-surface-muted)",
                height: "34px",
                borderLeft: "1px solid var(--color-border-subtle)",
              }}
            >
              {suffixElement}
            </div>
          )}
        </div>
        {error ? (
          <span
            id={`${inputId}-error`}
            role="alert"
            style={{
              fontSize: "var(--font-size-xs)",
              color: "var(--color-error)",
              fontWeight: "var(--font-weight-medium)",
            }}
          >
            {error}
          </span>
        ) : helperText ? (
          <span
            id={`${inputId}-helper`}
            style={{
              fontSize: "var(--font-size-xs)",
              color: "var(--color-text-muted)",
            }}
          >
            {helperText}
          </span>
        ) : null}
      </div>
    );
  }
);

Input.displayName = "Input";
