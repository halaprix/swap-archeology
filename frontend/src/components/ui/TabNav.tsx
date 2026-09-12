"use client";

import React from "react";

export interface TabItem {
  id: string;
  label: React.ReactNode;
  count?: number | string;
  badge?: string;
}

export interface TabNavProps {
  tabs: TabItem[];
  activeTab: string;
  onChange: (tabId: string) => void;
  ariaLabel?: string;
  size?: "sm" | "md";
  variant?: "underline" | "pill";
}

export const TabNav: React.FC<TabNavProps> = ({
  tabs,
  activeTab,
  onChange,
  ariaLabel = "Navigation Tabs",
  size = "md",
  variant = "underline",
}) => {
  const isSm = size === "sm";

  return (
    <nav
      role="tablist"
      aria-label={ariaLabel}
      style={{
        display: "flex",
        alignItems: "center",
        gap: variant === "pill" ? "var(--space-1)" : "var(--space-2)",
        borderBottom: variant === "underline" ? "1px solid var(--color-border)" : "none",
        padding: variant === "pill" ? "2px" : "0",
        backgroundColor: variant === "pill" ? "var(--color-surface-muted)" : "transparent",
        borderRadius: variant === "pill" ? "var(--radius-sm)" : "0",
        overflowX: "auto",
      }}
    >
      {tabs.map((tab) => {
        const isActive = activeTab === tab.id;

        if (variant === "pill") {
          return (
            <button
              key={tab.id}
              role="tab"
              aria-selected={isActive}
              tabIndex={isActive ? 0 : -1}
              onClick={() => onChange(tab.id)}
              style={{
                display: "inline-flex",
                alignItems: "center",
                gap: "var(--space-1-5)",
                padding: isSm ? "3px 8px" : "5px 12px",
                fontSize: isSm ? "var(--font-size-xs)" : "var(--font-size-sm)",
                fontWeight: isActive ? "var(--font-weight-semibold)" : "var(--font-weight-medium)",
                color: isActive ? "var(--color-text)" : "var(--color-text-secondary)",
                backgroundColor: isActive ? "var(--color-surface)" : "transparent",
                borderRadius: "var(--radius-xs)",
                border: isActive ? "1px solid var(--color-border-strong)" : "1px solid transparent",
                boxShadow: isActive ? "var(--shadow-sm)" : "none",
                cursor: "pointer",
                whiteSpace: "nowrap",
                transition: "all var(--transition-fast)",
              }}
            >
              <span>{tab.label}</span>
              {tab.badge && (
                <span
                  style={{
                    fontSize: "11px",
                    padding: "1px 5px",
                    borderRadius: "var(--radius-full)",
                    backgroundColor: isActive ? "var(--color-teal-light)" : "var(--color-surface)",
                    color: isActive ? "var(--color-teal)" : "var(--color-text-muted)",
                    fontWeight: "var(--font-weight-semibold)",
                  }}
                >
                  {tab.badge}
                </span>
              )}
            </button>
          );
        }

        return (
          <button
            key={tab.id}
            role="tab"
            aria-selected={isActive}
            tabIndex={isActive ? 0 : -1}
            onClick={() => onChange(tab.id)}
            style={{
              display: "inline-flex",
              alignItems: "center",
              gap: "var(--space-1-5)",
              padding: isSm ? "6px 10px" : "8px 14px",
              fontSize: isSm ? "var(--font-size-xs)" : "var(--font-size-sm)",
              fontWeight: isActive ? "var(--font-weight-semibold)" : "var(--font-weight-medium)",
              color: isActive ? "var(--color-teal)" : "var(--color-text-secondary)",
              backgroundColor: "transparent",
              border: "none",
              borderBottom: `2px solid ${isActive ? "var(--color-teal)" : "transparent"}`,
              marginBottom: "-1px",
              cursor: "pointer",
              whiteSpace: "nowrap",
              transition: "color var(--transition-fast), border-color var(--transition-fast)",
            }}
          >
            <span>{tab.label}</span>
            {tab.badge && (
              <span
                style={{
                  fontSize: "11px",
                  padding: "1px 6px",
                  borderRadius: "var(--radius-full)",
                  backgroundColor: isActive ? "var(--color-teal-light)" : "var(--color-surface-muted)",
                  color: isActive ? "var(--color-teal)" : "var(--color-text-muted)",
                  fontWeight: "var(--font-weight-semibold)",
                }}
              >
                {tab.badge}
              </span>
            )}
          </button>
        );
      })}
    </nav>
  );
};
