"use client";

import React, { forwardRef, SelectHTMLAttributes } from "react";

export interface SelectOption {
  value: string;
  label: string;
}

export interface SelectProps extends SelectHTMLAttributes<HTMLSelectElement> {
  label?: string;
  helperText?: string;
  error?: string;
  options?: SelectOption[];
}

export const Select = forwardRef<HTMLSelectElement, SelectProps>(
  ({ label, helperText, error, options, children, disabled, id, className = "", style, ...props }, ref) => {
    const selectId = id || (label ? `select-${label.toLowerCase().replace(/\s+/g, "-")}` : undefined);

    return (
      <div style={{ display: "flex", flexDirection: "column", gap: "var(--space-1)" }}>
        {label && (
          <label
            htmlFor={selectId}
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
            position: "relative",
            display: "flex",
            alignItems: "center",
          }}
        >
          <select
            ref={ref}
            id={selectId}
            disabled={disabled}
            aria-invalid={Boolean(error)}
            style={{
              width: "100%",
              height: "34px",
              padding: "6px 28px 6px 10px",
              borderRadius: "var(--radius-sm)",
              border: `1px solid ${error ? "var(--color-error)" : "var(--color-border-strong)"}`,
              backgroundColor: disabled ? "var(--color-surface-muted)" : "var(--color-surface)",
              color: "var(--color-text)",
              fontSize: "var(--font-size-sm)",
              fontFamily: "var(--font-sans)",
              appearance: "none",
              cursor: disabled ? "not-allowed" : "pointer",
              transition: "border-color var(--transition-fast), box-shadow var(--transition-fast)",
              ...style,
            }}
            className={className}
            {...props}
          >
            {options
              ? options.map((opt) => (
                  <option key={opt.value} value={opt.value}>
                    {opt.label}
                  </option>
                ))
              : children}
          </select>
          {/* Native-looking crisp SVG arrow */}
          <svg
            viewBox="0 0 16 16"
            width="12"
            height="12"
            fill="none"
            stroke="currentColor"
            strokeWidth="1.5"
            strokeLinecap="round"
            strokeLinejoin="round"
            style={{
              position: "absolute",
              right: "10px",
              pointerEvents: "none",
              color: "var(--color-text-muted)",
            }}
            aria-hidden="true"
          >
            <polyline points="4 6 8 10 12 6" />
          </svg>
        </div>
        {error ? (
          <span
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

Select.displayName = "Select";
