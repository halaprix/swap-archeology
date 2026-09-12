/**
 * Precision Math & String Formatting Helpers
 *
 * CRITICAL RULE: Never pass raw token amounts or wei balances to JS Number().
 * All arithmetic uses BigInt or string manipulation to avoid precision loss on uint256.
 */

/**
 * Format raw integer token amounts to decimal display strings with thousand separators.
 */
export function formatRawUnits(
  rawAmount: string | null | undefined,
  decimals: number | string,
  maxFractionDigits: number = 4
): string {
  if (!rawAmount || rawAmount.trim() === "" || rawAmount === "0") {
    return "0";
  }

  const clean = rawAmount.trim();
  const isNegative = clean.startsWith("-");
  const digits = isNegative ? clean.slice(1) : clean;

  if (!/^\d+$/.test(digits)) {
    return rawAmount;
  }

  const dec = typeof decimals === "string" ? parseInt(decimals, 10) : decimals;
  if (isNaN(dec) || dec < 0) {
    return rawAmount;
  }

  if (dec === 0) {
    const formattedInt = digits.replace(/\B(?=(\d{3})+(?!\d))/g, ",");
    return isNegative ? `-${formattedInt}` : formattedInt;
  }

  let intPart = "0";
  let fracPart = "";

  if (digits.length <= dec) {
    intPart = "0";
    fracPart = digits.padStart(dec, "0");
  } else {
    intPart = digits.slice(0, digits.length - dec);
    fracPart = digits.slice(digits.length - dec);
  }

  const formattedInt = intPart.replace(/\B(?=(\d{3})+(?!\d))/g, ",");

  // Truncate to maxFractionDigits
  const truncatedFrac = fracPart.slice(0, maxFractionDigits);
  // Trim trailing zeros from the truncated fraction
  const trimmedFrac = truncatedFrac.replace(/0+$/, "");

  const result = trimmedFrac.length > 0 ? `${formattedInt}.${trimmedFrac}` : formattedInt;
  return isNegative ? `-${result}` : result;
}

/**
 * Format raw integer token amounts preserving all non-zero fractional digits.
 */
export function formatRawUnitsFull(
  rawAmount: string | null | undefined,
  decimals: number | string
): string {
  return formatRawUnits(rawAmount, decimals, 18);
}

/**
 * Convert human decimal string (e.g. "100.5") into exact raw integer string.
 */
export function parseDecimalToRaw(
  decimalAmount: string,
  decimals: number | string
): string {
  const dec = typeof decimals === "string" ? parseInt(decimals, 10) : decimals;
  if (isNaN(dec) || dec < 0) {
    throw new Error("Invalid decimals count");
  }

  const clean = decimalAmount.trim();
  if (!/^\d+(\.\d+)?$/.test(clean)) {
    throw new Error("Amount must be a valid positive decimal number");
  }

  const [intPart, fracPart = ""] = clean.split(".");
  if (fracPart.length > dec) {
    throw new Error(`Amount has more than ${dec} decimal places`);
  }

  const paddedFrac = fracPart.padEnd(dec, "0");
  const raw = `${intPart}${paddedFrac}`.replace(/^0+/, "");
  return raw === "" ? "0" : raw;
}

export interface BaselineGain {
  absoluteDeltaRaw: string;
  formattedDelta: string;
  percentGain: string;
  basisPoints: number;
  isGain: boolean;
  isZero: boolean;
}

/**
 * Calculates baseline gain vs single pool baseline without converting raw amounts to Number.
 */
export function calculateBaselineGain(
  baselineAmountOut: string | null | undefined,
  splitAmountOut: string | null | undefined,
  decimalsOut: number | string
): BaselineGain | null {
  if (!baselineAmountOut || !splitAmountOut) {
    return null;
  }

  const cleanBase = baselineAmountOut.trim();
  const cleanSplit = splitAmountOut.trim();

  if (!/^\d+$/.test(cleanBase) || !/^\d+$/.test(cleanSplit)) {
    return null;
  }

  try {
    const base = BigInt(cleanBase);
    const split = BigInt(cleanSplit);
    const zero = BigInt(0);
    const tenThousand = BigInt(10000);
    const hundred = BigInt(100);

    if (base <= zero) {
      return null;
    }

    const delta = split - base;
    const isZero = delta === zero;
    const isGain = delta > zero;
    const absDelta = delta >= zero ? delta : -delta;

    // Basis points = (delta * 10000) / base
    const bpsBigInt = (delta * tenThousand) / base;
    const bpsNumber = Number(bpsBigInt);

    const absBps = bpsBigInt >= zero ? bpsBigInt : -bpsBigInt;
    const pctInt = absBps / hundred;
    const pctFrac = (absBps % hundred).toString().padStart(2, "0");
    const sign = delta > zero ? "+" : delta < zero ? "-" : "";
    const percentGain = isZero ? "0.00%" : `${sign}${pctInt}.${pctFrac}%`;

    const formattedDelta = isZero ? "0" : `${sign}${formatRawUnits(absDelta.toString(), decimalsOut, 4)}`;

    return {
      absoluteDeltaRaw: delta.toString(),
      formattedDelta,
      percentGain,
      basisPoints: bpsNumber,
      isGain,
      isZero,
    };
  } catch {
    return null;
  }
}

/**
 * Parses a pool identifier like "uniswap_v3:0x1f98...:0x88e6..."
 */
export function parsePoolId(poolId: string): {
  family: string;
  factory: string | null;
  address: string;
  displayLabel: string;
} {
  const parts = poolId.split(":");
  if (parts.length >= 3) {
    const family = parts[0];
    const factory = parts[1];
    const address = parts[2];
    return {
      family,
      factory,
      address,
      displayLabel: `${family} (${shortenHash(address, 6, 4)})`,
    };
  }
  if (parts.length === 2) {
    const family = parts[0];
    const address = parts[1];
    return {
      family,
      factory: null,
      address,
      displayLabel: `${family} (${shortenHash(address, 6, 4)})`,
    };
  }
  return {
    family: "unknown",
    factory: null,
    address: poolId,
    displayLabel: shortenHash(poolId, 6, 4),
  };
}

/**
 * Formats a Unix timestamp string into ISO/UTC date representation.
 */
export function formatTimestamp(timestamp: string | number | undefined): string {
  if (!timestamp) return "—";
  const num = typeof timestamp === "string" ? parseInt(timestamp, 10) : timestamp;
  if (isNaN(num)) return "—";

  const date = new Date(num * 1000);
  return date.toISOString().replace("T", " ").replace(/\.\d+Z$/, " UTC");
}

/**
 * Shortens a hex address or hash with an ellipsis.
 */
export function shortenHash(
  hash: string | null | undefined,
  lead: number = 6,
  tail: number = 4
): string {
  if (!hash) return "";
  if (hash.length <= lead + tail + 2) return hash;
  return `${hash.slice(0, lead)}...${hash.slice(-tail)}`;
}
