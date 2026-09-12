/**
 * Shared Token Metadata Resolver for Route Flow & Table
 * Resolves exact token symbols and decimals using catalog tokens and report request metadata.
 *
 * CRITICAL RULES:
 * 1. Exact address comparison (no substring matching).
 * 2. Intermediate tokens (e.g. USDC, USDT) must resolve to their actual decimals (e.g. 6).
 * 3. Unknown tokens must return decimals: null, NEVER defaulting to 18.
 * 4. No JS Number math on raw amounts.
 */

import { CatalogToken, QuoteRequestDetails } from "./types";
import { formatRawUnits, shortenHash } from "./formatting";

export interface TokenMetadata {
  symbol: string;
  address: string;
  decimals: number | null; // null if unknown
  isKnown: boolean;
}

/**
 * Known canonical token registry for research universe assets, ensuring
 * intermediary route steps (e.g. WBTC 8 decimals, USDS 18 decimals)
 * resolve accurately even when public catalog token lists are partial.
 */
export const CANONICAL_TOKENS: Record<string, { symbol: string; decimals: number }> = {
  "0x2260fac5e5542a773aa44fbcfedf7c193bc2c599": { symbol: "WBTC", decimals: 8 },
  "0xdc035d45d973e3ec169d2276ddab16f1e407384f": { symbol: "USDS", decimals: 18 },
  "0xc02aaa39b223fe8d0a0e5c4f27ead9083c756cc2": { symbol: "WETH", decimals: 18 },
  "0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48": { symbol: "USDC", decimals: 6 },
  "0xdac17f958d2ee523a2206206994597c13d831ec7": { symbol: "USDT", decimals: 6 },
  "0x6b175474e89094c44da98b954eedeac495271d0f": { symbol: "DAI", decimals: 18 },
  "0x7f39c581f595b53c5cb19bd0b3f8da6c935e2ca0": { symbol: "wstETH", decimals: 18 },
  "0x9d39a5de30e57443bff2a8307a4256c8797a3497": { symbol: "sUSDe", decimals: 18 },
};

/**
 * Resolves metadata for a given token address.
 */
export function resolveTokenMetadata(
  address: string,
  catalogTokens: CatalogToken[] = [],
  reportRequest?: QuoteRequestDetails | null
): TokenMetadata {
  if (!address || typeof address !== "string") {
    return {
      symbol: "UNKNOWN",
      address: address || "",
      decimals: null,
      isKnown: false,
    };
  }

  const cleanAddr = address.toLowerCase().trim();

  // 1. Check report request tokens (token_in and token_out)
  if (reportRequest) {
    if (reportRequest.token_in && reportRequest.token_in.toLowerCase().trim() === cleanAddr) {
      const dec = parseInt(reportRequest.decimals_in, 10);
      return {
        symbol: reportRequest.symbol_in,
        address,
        decimals: isNaN(dec) ? null : dec,
        isKnown: true,
      };
    }
    if (reportRequest.token_out && reportRequest.token_out.toLowerCase().trim() === cleanAddr) {
      const dec = parseInt(reportRequest.decimals_out, 10);
      return {
        symbol: reportRequest.symbol_out,
        address,
        decimals: isNaN(dec) ? null : dec,
        isKnown: true,
      };
    }
  }

  // 2. Check catalog tokens
  for (const token of catalogTokens) {
    if (token.address && token.address.toLowerCase().trim() === cleanAddr) {
      return {
        symbol: token.symbol,
        address,
        decimals: typeof token.decimals === "number" ? token.decimals : parseInt(String(token.decimals), 10),
        isKnown: true,
      };
    }
  }

  // 3. Check canonical research tokens (e.g. WBTC 8 dec, USDS 18 dec intermediaries)
  const canonical = CANONICAL_TOKENS[cleanAddr];
  if (canonical) {
    return {
      symbol: canonical.symbol,
      address,
      decimals: canonical.decimals,
      isKnown: true,
    };
  }

  // 4. Fallback for unknown token: display shortened address and NO assumed decimals
  return {
    symbol: shortenHash(address, 6, 4),
    address,
    decimals: null,
    isKnown: false,
  };
}

/**
 * Formats a raw amount in token units using its resolved metadata.
 * If decimals are unknown, formats as raw units without assuming 18 decimals.
 */
export function formatTokenAmount(
  rawAmount: string | null | undefined,
  metadata: TokenMetadata,
  maxFractionDigits: number = 4
): string {
  if (!rawAmount || rawAmount.trim() === "" || rawAmount === "0") {
    return "0";
  }

  if (metadata.decimals === null || !metadata.isKnown) {
    // Format raw integer with thousand separators, never assume 18 decimals
    return formatRawUnits(rawAmount, 0, 0) + " (raw)";
  }

  return formatRawUnits(rawAmount, metadata.decimals, maxFractionDigits);
}
