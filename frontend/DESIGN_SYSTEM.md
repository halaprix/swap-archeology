# Swap Archeology — Design System & Workspace Tokens

## 1. Aesthetic Philosophy: Editorial Data Workbench

Swap Archeology is an academic and empirical research workbench designed for analyzing Ethereum DEX routing topologies, multi-pool split routes, and solver execution bounds.

Unlike commercial marketing landing pages or generic dashboards with saturated neon gradients, this interface is built on:
- **Warm offwhite canvas (`#fbfaf8`)**: Calming, high-legibility background resembling archival research papers.
- **Crisp near-black typography (`#141416`)**: High-contrast text without harsh pitch-black glare.
- **Research Teal Accent (`#0f766e`)**: Disciplined, intellectual green-teal for active selections, optimal route paths, and baseline gains.
- **Thin, structural borders (`#e6e3da`, `#d2cfc4`)**: Clear geometric hierarchy and tabular division.
- **Tabular figures & BigInt precision**: Monospace font stack and `font-feature-settings: 'tnum', 'zero'` for strict decimal alignment and exact raw integer audit trails.
- **Zero remote font network fetches**: Builds completely offline using native modern system font stacks.

---

## 2. Token Catalog (`src/app/globals.css`)

### 2.1 Surfaces & Canvas
| Token | Value | Purpose |
| :--- | :--- | :--- |
| `--color-bg` | `#fbfaf8` | Root page canvas background |
| `--color-surface` | `#ffffff` | Primary card and table container surface |
| `--color-surface-muted` | `#f4f3ed` | Secondary panels, filter bars, and inactive tabs |
| `--color-surface-hover` | `#f0eee7` | Interactive hover states |
| `--color-border` | `#e6e3da` | Standard 1px divider and component border |
| `--color-border-strong` | `#d2cfc4` | Focused/selected element boundary |

### 2.2 Editorial Typography
| Token | Value | Purpose |
| :--- | :--- | :--- |
| `--color-text` | `#141416` | Primary body and metric text |
| `--color-text-secondary` | `#575653` | Subtitles, labels, and secondary context |
| `--color-text-muted` | `#7e7d77` | Captions, timestamps, and raw integer subtext |
| `--font-sans` | `-apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif` | Clean, native system UI font |
| `--font-mono` | `ui-monospace, "SF Mono", "Cascadia Code", "Roboto Mono", Consolas, monospace` | Numeric tables, hashes, addresses, and raw values |

### 2.3 Primary Accent & Status Semantics
| Token | Value | Purpose |
| :--- | :--- | :--- |
| `--color-teal` | `#0f766e` | Active tab, primary button, best split highlight |
| `--color-teal-light` | `#f0fdfa` | Selected row/card background wash |
| `--color-teal-border` | `#99f6e4` | Highlighted route stroke and border |
| `--color-success` | `#15803d` | Positive baseline gain, supported source |
| `--color-warning` | `#b45309` | Unqualified pool, baseline outperforming |
| `--color-error` | `#b91c1c` | Missing snapshot state, validation rejection |

### 2.4 Protocol Venue Palette
Each protocol family receives distinct, consistent semantic coloring (extensible beyond the original 15 families):
- **Uniswap v3**: Deep berry pink (`--venue-uniswap-v3: #be185d`, background `#fdf2f8`)
- **Uniswap v2**: Crimson rose (`--venue-uniswap-v2: #e11d48`, background `#fff1f2`)
- **PancakeSwap v3**: Warm amber / golden pancake (`--venue-pancake-v3: #d97706`, background `#fffbeb`, border `#fde68a`, text `#92400e`)
- **Curve**: Classic financial blue (`--venue-curve: #1d4ed8`, background `#eff6ff`)
- **Balancer v3**: Indigo slate (`--venue-balancer: #4338ca`, background `#eef2ff`)
- **Lido**: Cerulean sky (`--venue-lido: #0284c7`, background `#f0f9ff`)
- **Maker / Sky PSM**: Amber gold (`--venue-maker: #b45309`, background `#fffbeb`)
- **Origin ARM**: Deep teal (`--venue-origin: #0f766e`, background `#f0fdfa`)
- **Fluid DEX**: Emerald green (`--venue-fluid: #059669`, background `#ecfdf5`)
- **Ekubo**: Cyan slate (`--venue-ekubo: #0e7490`, background `#ecfeff`)
- **ERC-4626**: Steel slate (`--venue-erc4626: #475569`, background `#f8fafc`)
- **Lista Stable**: Royal violet (`--venue-lista: #7e22ce`, background `#faf5ff`)
- **Spark**: Burnt copper (`--venue-spark: #c2410c`, background `#fff7ed`)
- **Uniswap v4**: Vivid purple (`--venue-uniswap-v4: #9333ea`, background `#fbf5ff`)
- **Bebop & 0x RFQ**: Neutral zinc (`--venue-bebop`, `--venue-zerox: #374151`, background `#f3f4f6`)

---

## 3. Reusable UI Primitives

All primitives are located in `src/components/ui/`:

### 3.1 `Button`
- **Variants**: `primary`, `secondary`, `outline`, `ghost`, `danger`.
- **Sizes**: `sm` (28px height), `md` (34px height), `lg` (40px height).
- **States**: `isLoading` (renders CSS spinner), `disabled` (50% opacity, pointer-events none), visible `:focus-visible` keyboard ring.

### 3.2 `Badge`
- **Variants**: `neutral`, `success`, `warning`, `error`, `info`, `venue`.
- **Venue Support**: Pass `venueFamily="uniswap_v3"` to automatically apply designated protocol palette tokens.
- **Typography**: Monospace, compact padding, 1px border.

### 3.3 `Card` & `CardHeader`
- **Semantic Structure**: Rendered with clean 1px borders, subtle paper shadow (`--shadow-sm`), and optional subtitle and action buttons.
- **Variants**: `default` (white), `muted` (`--color-surface-muted`), `flat` (no shadow, subtle border).

### 3.4 `Input` & `Select`
- **Labels**: Uppercase tracked micro-labels (`letter-spacing: 0.05em`).
- **Precision**: Monospace mode (`isMono`) for addresses, blocks, and raw amounts.
- **Prefix / Suffix**: Built-in support for token units (`wei`, `USDC`) and address icons.
- **Validation**: Accessible `aria-invalid` and `role="alert"` error message linking.

### 3.5 `Metric`
- **Purpose**: Large tabular presentation for financial outputs, baselines, and execution deltas.
- **Components**: Metric title, prominent numeric value (`--font-size-xl`), unit label, gain percentage badge, subtext, and exact raw integer audit string.
- **Tabular Numbers**: Uses CSS `font-variant-numeric: tabular-nums` to ensure numbers do not jitter during updates.

### 3.6 `TabNav`
- **Keyboard Access**: Implements ARIA `role="tablist"` and `role="tab"` with `aria-selected` and roving keyboard focus.
- **Variants**: `underline` (editorial border) and `pill` (compact filter chips).

---

## 4. Accessibility & Theme Extension

### 4.1 Accessibility Standards
- **Skip Links**: `<a href="#main-content" class="skip-link">Skip to main content</a>` allows immediate keyboard bypass of header navigation.
- **Keyboard Navigation**: Route flow steps and table rows can be traversed with `ArrowLeft`, `ArrowRight`, `Tab`, and activated with `Enter` or `Space`.
- **Screen Reader Announcements**: SVGs include explicit `role="img"`, `<title>`, and `<desc>` elements; tables provide `<caption>` and `<th scope="col">`.
- **Reduced Motion**: All animations and transitions are automatically disabled if the user agent requests `prefers-reduced-motion: reduce`.

### 4.2 Theme Extension Points
The design system is strictly parameterized via CSS custom properties. To introduce an alternate theme (such as a dark researcher theme or high-contrast theme), override the root CSS variables:
```css
[data-theme="dark"] {
  --color-bg: #121214;
  --color-surface: #1a1a1d;
  --color-surface-muted: #242428;
  --color-border: #2e2e34;
  --color-text: #f4f4f6;
  --color-text-secondary: #a1a1aa;
  --color-text-muted: #71717a;
  --color-teal: #14b8a6;
  --color-teal-light: #134e4a;
}
```
All UI primitives and route flow diagrams dynamically inherit these variables with zero code changes.

## Route panel

The route view uses a localized dark theme (`--route-panel*` variables in
`globals.css`) and existing venue colors. Operations are placed in dependency
columns, so consumed intermediate outputs always advance to the right. Bands
show propagated original-input share; exact amounts remain in the inspector.
The SVG fills desktop width and scrolls horizontally on narrow screens.
