/**
 * Venue metadata, colors, and badge styling definitions.
 */

export interface VenueInfo {
  family: string;
  name: string;
  colorVar: string;
  badgeBg: string;
  badgeBorder: string;
  badgeText: string;
  description: string;
}

export const KNOWN_FAMILIES: Record<string, VenueInfo> = {
  uniswap_v3: {
    family: "uniswap_v3",
    name: "Uniswap v3",
    colorVar: "var(--venue-uniswap-v3)",
    badgeBg: "var(--venue-uniswap-v3-bg)",
    badgeBorder: "var(--venue-uniswap-v3-border)",
    badgeText: "var(--venue-uniswap-v3-text)",
    description: "Concentrated liquidity AMM (ticks & fee tiers 500, 3000, 10000)",
  },
  uniswap_v2: {
    family: "uniswap_v2",
    name: "Uniswap v2",
    colorVar: "var(--venue-uniswap-v2)",
    badgeBg: "var(--venue-uniswap-v2-bg)",
    badgeBorder: "var(--venue-uniswap-v2-border)",
    badgeText: "var(--venue-uniswap-v2-text)",
    description: "Constant product pool (x * y = k, 0.3% fee)",
  },
  curve: {
    family: "curve",
    name: "Curve",
    colorVar: "var(--venue-curve)",
    badgeBg: "var(--venue-curve-bg)",
    badgeBorder: "var(--venue-curve-border)",
    badgeText: "var(--venue-curve-text)",
    description: "StableSwap / Stableswap-NG low-slippage invariant pools",
  },
  balancer_v3: {
    family: "balancer_v3",
    name: "Balancer v3",
    colorVar: "var(--venue-balancer)",
    badgeBg: "var(--venue-balancer-bg)",
    badgeBorder: "var(--venue-balancer-border)",
    badgeText: "var(--venue-balancer-text)",
    description: "Weighted & composable stable pools",
  },
  lido: {
    family: "lido",
    name: "Lido",
    colorVar: "var(--venue-lido)",
    badgeBg: "var(--venue-lido-bg)",
    badgeBorder: "var(--venue-lido-border)",
    badgeText: "var(--venue-lido-text)",
    description: "Liquid staking mint / withdrawal queue semantics",
  },
  maker_sky_psm: {
    family: "maker_sky_psm",
    name: "Maker / Sky PSM",
    colorVar: "var(--venue-maker)",
    badgeBg: "var(--venue-maker-bg)",
    badgeBorder: "var(--venue-maker-border)",
    badgeText: "var(--venue-maker-text)",
    description: "Peg Stability Module 1:1 fixed conversion",
  },
  origin_arm: {
    family: "origin_arm",
    name: "Origin ARM",
    colorVar: "var(--venue-origin)",
    badgeBg: "var(--venue-origin-bg)",
    badgeBorder: "var(--venue-origin-border)",
    badgeText: "var(--venue-origin-text)",
    description: "Automated Redemption Market yield routing",
  },
  fluid_dex: {
    family: "fluid_dex",
    name: "Fluid DEX",
    colorVar: "var(--venue-fluid)",
    badgeBg: "var(--venue-fluid-bg)",
    badgeBorder: "var(--venue-fluid-border)",
    badgeText: "var(--venue-fluid-text)",
    description: "Smart debt & collateral native DEX pools",
  },
  ekubo: {
    family: "ekubo",
    name: "Ekubo",
    colorVar: "var(--venue-ekubo)",
    badgeBg: "var(--venue-ekubo-bg)",
    badgeBorder: "var(--venue-ekubo-border)",
    badgeText: "var(--venue-ekubo-text)",
    description: "Singleton concentrated liquidity AMM",
  },
  erc4626: {
    family: "erc4626",
    name: "ERC-4626",
    colorVar: "var(--venue-erc4626)",
    badgeBg: "var(--venue-erc4626-bg)",
    badgeBorder: "var(--venue-erc4626-border)",
    badgeText: "var(--venue-erc4626-text)",
    description: "Standardized yield vault wrapper conversion",
  },
  lista_stable: {
    family: "lista_stable",
    name: "Lista Stable",
    colorVar: "var(--venue-lista)",
    badgeBg: "var(--venue-lista-bg)",
    badgeBorder: "var(--venue-lista-border)",
    badgeText: "var(--venue-lista-text)",
    description: "Collateralized stablecoin swap pools",
  },
  spark: {
    family: "spark",
    name: "Spark",
    colorVar: "var(--venue-spark)",
    badgeBg: "var(--venue-spark-bg)",
    badgeBorder: "var(--venue-spark-border)",
    badgeText: "var(--venue-spark-text)",
    description: "Sky/Maker liquidity market integration",
  },
  uniswap_v4: {
    family: "uniswap_v4",
    name: "Uniswap v4",
    colorVar: "var(--venue-uniswap-v4)",
    badgeBg: "var(--venue-uniswap-v4-bg)",
    badgeBorder: "var(--venue-uniswap-v4-border)",
    badgeText: "var(--venue-uniswap-v4-text)",
    description: "Hook-enabled singleton AMM",
  },
  pancake_v3: {
    family: "pancake_v3",
    name: "PancakeSwap v3",
    colorVar: "var(--venue-pancake-v3)",
    badgeBg: "var(--venue-pancake-v3-bg)",
    badgeBorder: "var(--venue-pancake-v3-border)",
    badgeText: "var(--venue-pancake-v3-text)",
    description: "Concentrated liquidity AMM (ticks & fee tiers 100, 500, 2500, 10000)",
  },
  bebop: {
    family: "bebop",
    name: "Bebop",
    colorVar: "var(--venue-bebop)",
    badgeBg: "var(--venue-bebop-bg)",
    badgeBorder: "var(--venue-bebop-border)",
    badgeText: "var(--venue-bebop-text)",
    description: "Off-chain RFQ aggregator (historical quotes unavailable)",
  },
  zerox_rfq: {
    family: "zerox_rfq",
    name: "0x RFQ",
    colorVar: "var(--venue-zerox)",
    badgeBg: "var(--venue-zerox-bg)",
    badgeBorder: "var(--venue-zerox-border)",
    badgeText: "var(--venue-zerox-text)",
    description: "Private market maker RFQ (historical quotes unavailable)",
  },
};

export const ALL_SOURCE_FAMILIES = Object.keys(KNOWN_FAMILIES);

export function getVenueInfo(family: string): VenueInfo {
  return (
    KNOWN_FAMILIES[family] || {
      family,
      name: family.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase()),
      colorVar: "var(--color-text-muted)",
      badgeBg: "var(--color-surface-muted)",
      badgeBorder: "var(--color-border)",
      badgeText: "var(--color-text)",
      description: "Custom protocol family",
    }
  );
}
