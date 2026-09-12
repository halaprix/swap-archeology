"use client";

import React from "react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import Image from "next/image";

export const Header: React.FC = () => {
  const pathname = usePathname();

  const navLinks = [
    { href: "/", label: "Dashboard", badge: "Index" },
    { href: "/october-gap", label: "October Gap", badge: "Every block" },
    { href: "/crash-gaps", label: "Crash Gaps", badge: "5 Crashes" },
    { href: "/quote-lab", label: "Quote Lab", badge: "API" },
  ];

  return (
    <header
      style={{
        borderBottom: "1px solid var(--color-border)",
        backgroundColor: "var(--color-surface)",
        position: "sticky",
        top: 0,
        zIndex: 50,
      }}
    >
      <div
        className="header-inner"
        style={{
          maxWidth: "1440px",
          margin: "0 auto",
          padding: "var(--space-3) var(--space-6)",
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          gap: "var(--space-4)",
        }}
      >
        {/* Left: Brand Identity */}
        <div className="header-brand" style={{ display: "flex", alignItems: "center", gap: "var(--space-3)" }}>
          <Link
            href="/"
            style={{
              display: "flex",
              alignItems: "center",
              gap: "var(--space-2-5)",
              textDecoration: "none",
            }}
          >
            <Image
              src="/icon.svg"
              alt="Swap Archeology Mark"
              width={26}
              height={26}
              priority
              style={{ display: "block" }}
            />
            <div>
              <div
                style={{
                  fontSize: "var(--font-size-base)",
                  fontWeight: "var(--font-weight-bold)",
                  color: "var(--color-text)",
                  letterSpacing: "-0.01em",
                  lineHeight: "1.2",
                }}
              >
                SWAP ARCHEOLOGY
              </div>
              <div
                style={{
                  fontSize: "11px",
                  color: "var(--color-text-muted)",
                  letterSpacing: "0.04em",
                  textTransform: "uppercase",
                  lineHeight: "1",
                  marginTop: "2px",
                }}
              >
                Historical Route Workbench
              </div>
            </div>
          </Link>

          <span
            style={{
              height: "20px",
              width: "1px",
              backgroundColor: "var(--color-border-strong)",
              margin: "0 var(--space-1)",
            }}
            aria-hidden="true"
          />

          <span
            className="font-mono"
            style={{
              fontSize: "11px",
              color: "var(--color-text-secondary)",
              padding: "2px 6px",
              borderRadius: "var(--radius-xs)",
              backgroundColor: "var(--color-surface-muted)",
              border: "1px solid var(--color-border-subtle)",
            }}
          >
            ETH Chain 1 · Read-Only
          </span>
        </div>

        {/* Center/Right: Navigation */}
        <nav
          className="header-nav"
          aria-label="Main Navigation"
          style={{
            display: "flex",
            alignItems: "center",
            gap: "var(--space-1)",
          }}
        >
          {navLinks.map((link) => {
            const isActive =
              link.href === "/"
                ? pathname === "/"
                : pathname.startsWith(link.href);

            return (
              <Link
                key={link.href}
                href={link.href}
                aria-current={isActive ? "page" : undefined}
                style={{
                  display: "inline-flex",
                  alignItems: "center",
                  gap: "var(--space-1-5)",
                  padding: "5px 12px",
                  fontSize: "var(--font-size-sm)",
                  fontWeight: isActive
                    ? "var(--font-weight-semibold)"
                    : "var(--font-weight-medium)",
                  color: isActive
                    ? "var(--color-teal)"
                    : "var(--color-text-secondary)",
                  backgroundColor: isActive
                    ? "var(--color-teal-light)"
                    : "transparent",
                  border: `1px solid ${
                    isActive ? "var(--color-teal-border)" : "transparent"
                  }`,
                  borderRadius: "var(--radius-sm)",
                  textDecoration: "none",
                  transition: "all var(--transition-fast)",
                }}
              >
                <span>{link.label}</span>
                {link.badge && (
                  <span
                    className="font-mono"
                    style={{
                      fontSize: "10px",
                      padding: "1px 5px",
                      borderRadius: "var(--radius-full)",
                      backgroundColor: isActive
                        ? "var(--color-surface)"
                        : "var(--color-surface-muted)",
                      color: isActive
                        ? "var(--color-teal)"
                        : "var(--color-text-muted)",
                    }}
                  >
                    {link.badge}
                  </span>
                )}
              </Link>
            );
          })}
        </nav>
      </div>
    </header>
  );
};
