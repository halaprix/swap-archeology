"use client";

import React, { useState } from "react";
import { Button } from "@/components/ui/Button";
import { Input } from "@/components/ui/Input";
import { Select } from "@/components/ui/Select";
import { Card, CardHeader } from "@/components/ui/Card";
import { Badge } from "@/components/ui/Badge";
import { Metric } from "@/components/ui/Metric";
import { TabNav } from "@/components/ui/TabNav";
import { ALL_SOURCE_FAMILIES, getVenueInfo } from "@/lib/venues";

export default function DesignSystemPage() {
  const [activeTab, setActiveTab] = useState("all");
  const [inputText, setInputText] = useState("100.5");
  const [selectVal, setSelectVal] = useState("WETH");

  const showcaseTabs = [
    { id: "all", label: "Overview & Palette" },
    { id: "primitives", label: "Controls & Inputs" },
    { id: "venues", label: "Venue Taxonomy" },
    { id: "metrics", label: "Tabular Metrics" },
  ];

  return (
    <div
      style={{
        maxWidth: "1440px",
        width: "100%",
        margin: "0 auto",
        padding: "var(--space-6)",
        display: "flex",
        flexDirection: "column",
        gap: "var(--space-6)",
      }}
    >
      {/* Header */}
      <div
        style={{
          borderBottom: "1px solid var(--color-border)",
          paddingBottom: "var(--space-4)",
        }}
      >
        <div style={{ display: "flex", alignItems: "center", gap: "var(--space-2)" }}>
          <h1
            style={{
              fontSize: "var(--font-size-2xl)",
              fontWeight: "var(--font-weight-bold)",
              color: "var(--color-text)",
              letterSpacing: "-0.02em",
            }}
          >
            Design System &amp; Workspace Tokens
          </h1>
          <Badge variant="info">Light Research Theme</Badge>
        </div>
        <p
          style={{
            fontSize: "var(--font-size-sm)",
            color: "var(--color-text-secondary)",
            marginTop: "var(--space-1)",
            maxWidth: "760px",
          }}
        >
          An editorial data workbench designed for rigorous financial archeology: warm offwhite canvas, near-black text, deep research teal, thin crisp borders, large tabular metrics, and strictly zero remote font dependencies.
        </p>
      </div>

      <TabNav
        tabs={showcaseTabs}
        activeTab={activeTab}
        onChange={setActiveTab}
        variant="underline"
      />

      {/* Section 1: Philosophy & Color Tokens */}
      {(activeTab === "all" || activeTab === "primitives") && (
        <section style={{ display: "flex", flexDirection: "column", gap: "var(--space-4)" }}>
          <h2
            style={{
              fontSize: "var(--font-size-lg)",
              fontWeight: "var(--font-weight-bold)",
              color: "var(--color-text)",
            }}
          >
            1. Core Color &amp; Surface Tokens
          </h2>

          <div
            style={{
              display: "grid",
              gridTemplateColumns: "repeat(auto-fit, minmax(200px, 1fr))",
              gap: "var(--space-3)",
            }}
          >
            {[
              { name: "--color-bg", desc: "Canvas background (#fbfaf8)", bg: "var(--color-bg)", text: "var(--color-text)", border: "var(--color-border-strong)" },
              { name: "--color-surface", desc: "Card surface (#ffffff)", bg: "var(--color-surface)", text: "var(--color-text)", border: "var(--color-border)" },
              { name: "--color-surface-muted", desc: "Muted panel (#f4f3ed)", bg: "var(--color-surface-muted)", text: "var(--color-text)", border: "var(--color-border)" },
              { name: "--color-teal", desc: "Primary accent (#0f766e)", bg: "var(--color-teal)", text: "#ffffff", border: "transparent" },
              { name: "--color-teal-light", desc: "Accent wash (#f0fdfa)", bg: "var(--color-teal-light)", text: "var(--color-teal)", border: "var(--color-teal-border)" },
              { name: "--color-success", desc: "Gain / Valid (#15803d)", bg: "var(--color-success-light)", text: "var(--color-success-text)", border: "var(--color-success-border)" },
              { name: "--color-warning", desc: "Unqualified (#b45309)", bg: "var(--color-warning-light)", text: "var(--color-warning-text)", border: "var(--color-warning-border)" },
              { name: "--color-error", desc: "Missing / Error (#b91c1c)", bg: "var(--color-error-light)", text: "var(--color-error-text)", border: "var(--color-error-border)" },
            ].map((token) => (
              <div
                key={token.name}
                style={{
                  padding: "var(--space-3)",
                  backgroundColor: token.bg,
                  color: token.text,
                  border: `1px solid ${token.border}`,
                  borderRadius: "var(--radius-sm)",
                  display: "flex",
                  flexDirection: "column",
                  gap: "var(--space-1)",
                }}
              >
                <code style={{ fontSize: "11px", fontWeight: "bold" }}>{token.name}</code>
                <span style={{ fontSize: "11px", opacity: 0.85 }}>{token.desc}</span>
              </div>
            ))}
          </div>
        </section>
      )}

      {/* Section 2: Buttons & Actions */}
      {(activeTab === "all" || activeTab === "primitives") && (
        <section style={{ display: "flex", flexDirection: "column", gap: "var(--space-4)" }}>
          <h2
            style={{
              fontSize: "var(--font-size-lg)",
              fontWeight: "var(--font-weight-bold)",
              color: "var(--color-text)",
            }}
          >
            2. Button Primitives
          </h2>

          <Card>
            <div style={{ display: "flex", flexWrap: "wrap", gap: "var(--space-3)", alignItems: "center" }}>
              <Button variant="primary">Primary Action</Button>
              <Button variant="secondary">Secondary Button</Button>
              <Button variant="outline">Outline Button</Button>
              <Button variant="ghost">Ghost Button</Button>
              <Button variant="danger">Danger Button</Button>
              <Button variant="primary" isLoading>
                Loading State
              </Button>
              <Button variant="secondary" disabled>
                Disabled
              </Button>
            </div>

            <div style={{ display: "flex", alignItems: "center", gap: "var(--space-3)", marginTop: "var(--space-2)" }}>
              <Button size="sm" variant="secondary">Small (28px)</Button>
              <Button size="md" variant="secondary">Medium (34px)</Button>
              <Button size="lg" variant="secondary">Large (40px)</Button>
            </div>
          </Card>
        </section>
      )}

      {/* Section 3: Inputs & Form Controls */}
      {(activeTab === "all" || activeTab === "primitives") && (
        <section style={{ display: "flex", flexDirection: "column", gap: "var(--space-4)" }}>
          <h2
            style={{
              fontSize: "var(--font-size-lg)",
              fontWeight: "var(--font-weight-bold)",
              color: "var(--color-text)",
            }}
          >
            3. Form Inputs &amp; Controls
          </h2>

          <Card>
            <div
              style={{
                display: "grid",
                gridTemplateColumns: "repeat(auto-fit, minmax(240px, 1fr))",
                gap: "var(--space-4)",
              }}
            >
              <Input
                label="Standard Decimal Input"
                value={inputText}
                onChange={(e) => setInputText(e.target.value)}
                helperText="Exact string representation"
                isMono
              />

              <Input
                label="With Suffix Unit"
                value="100000000000000000000"
                suffixElement="wei"
                isMono
              />

              <Input
                label="Error State Input"
                value="abc_invalid_amount"
                error="amount must be a positive decimal number"
                isMono
              />

              <Select
                label="Select Dropdown"
                value={selectVal}
                onChange={(e) => setSelectVal(e.target.value)}
                options={[
                  { value: "WETH", label: "WETH (18 decimals)" },
                  { value: "USDC", label: "USDC (6 decimals)" },
                  { value: "DAI", label: "DAI (18 decimals)" },
                  { value: "USDT", label: "USDT (6 decimals)" },
                ]}
              />
            </div>
          </Card>
        </section>
      )}

      {/* Section 4: Venue Taxonomy */}
      {(activeTab === "all" || activeTab === "venues") && (
        <section style={{ display: "flex", flexDirection: "column", gap: "var(--space-4)" }}>
          <h2
            style={{
              fontSize: "var(--font-size-lg)",
              fontWeight: "var(--font-weight-bold)",
              color: "var(--color-text)",
            }}
          >
            4. Protocol Venue Palette &amp; Badges (15 Families)
          </h2>

          <Card>
            <div
              style={{
                display: "grid",
                gridTemplateColumns: "repeat(auto-fill, minmax(260px, 1fr))",
                gap: "var(--space-3)",
              }}
            >
              {ALL_SOURCE_FAMILIES.map((fam) => {
                const info = getVenueInfo(fam);
                return (
                  <div
                    key={fam}
                    style={{
                      display: "flex",
                      flexDirection: "column",
                      gap: "var(--space-1)",
                      padding: "var(--space-2) var(--space-3)",
                      border: "1px solid var(--color-border)",
                      borderRadius: "var(--radius-xs)",
                      backgroundColor: "var(--color-surface)",
                    }}
                  >
                    <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between" }}>
                      <Badge variant="venue" venueFamily={fam}>
                        {info.name}
                      </Badge>
                      <code style={{ fontSize: "10px", color: "var(--color-text-muted)" }}>{fam}</code>
                    </div>
                    <span style={{ fontSize: "11px", color: "var(--color-text-secondary)" }}>
                      {info.description}
                    </span>
                  </div>
                );
              })}
            </div>
          </Card>
        </section>
      )}

      {/* Section 5: Tabular Metrics */}
      {(activeTab === "all" || activeTab === "metrics") && (
        <section style={{ display: "flex", flexDirection: "column", gap: "var(--space-4)" }}>
          <h2
            style={{
              fontSize: "var(--font-size-lg)",
              fontWeight: "var(--font-weight-bold)",
              color: "var(--color-text)",
            }}
          >
            5. Tabular Metric Units (BigInt Precision)
          </h2>

          <div
            style={{
              display: "grid",
              gridTemplateColumns: "repeat(auto-fit, minmax(220px, 1fr))",
              gap: "var(--space-3)",
            }}
          >
            <Metric
              label="Best Split Output"
              value="333,396.7385"
              unit="USDC"
              rawExact="333396738584"
              variant="highlight"
              badge={{ text: "+0.38%", variant: "success" }}
              subtext="2 pool steps evaluated"
            />

            <Metric
              label="Single Pool Baseline"
              value="332,132.1934"
              unit="USDC"
              rawExact="332132193469"
              subtext="Uniswap v3 (0.05% fee pool)"
            />

            <Metric
              label="Baseline Gain"
              value="+0.38%"
              subtext="Absolute: +1,264.54 USDC"
              rawExact="delta: +1264545115"
              variant="highlight"
            />

            <Metric
              label="Execution Gas"
              value="220,000 gas"
              subtext="Model adapter estimate"
              rawExact="220000"
            />
          </div>
        </section>
      )}

      {/* Section 6: Accessibility & Theme Extension */}
      <Card variant="muted">
        <CardHeader
          title="Accessibility & Theme Architecture"
          subtitle="Strict WCAG AAA readability, visible focus outlines, reduced motion, and extensible CSS variables."
        />
        <div style={{ fontSize: "var(--font-size-xs)", color: "var(--color-text-secondary)", lineHeight: "1.6" }}>
          <p>
            All colors, surfaces, metrics, and venue tags are driven by standard CSS custom properties defined in <code>globals.css</code>. To adjust or introduce a custom theme in the future, modify the root CSS variables without touching JavaScript component logic.
          </p>
        </div>
      </Card>
    </div>
  );
}
