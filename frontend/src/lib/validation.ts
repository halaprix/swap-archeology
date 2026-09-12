/**
 * Validation helpers for API endpoints.
 * Keeps local paths, internal URLs, and credential details out of public responses.
 */

import { QuoteApiRequest } from "./types";

const BLOCK_NUMBER_REGEX = /^[1-9][0-9]*$/;
const BLOCK_HASH_REGEX = /^0x[0-9a-fA-F]{64}$/;
const DECIMAL_AMOUNT_REGEX = /^[0-9]{1,78}(\.[0-9]{1,36})?$/;
const SOLVERS = new Set(["baseline", "search"]);

export interface ValidationError {
  code: string;
  message: string;
}

export function validateQuoteRequest(payload: unknown): {
  data?: QuoteApiRequest;
  error?: ValidationError;
} {
  if (!payload || typeof payload !== "object" || Array.isArray(payload)) {
    return {
      error: {
        code: "invalid_request",
        message: "Request body must be a JSON object",
      },
    };
  }

  const p = payload as Record<string, unknown>;

  // Block validation
  const rawBlock = p.block;
  if (typeof rawBlock !== "string" && typeof rawBlock !== "number") {
    return {
      error: {
        code: "invalid_block",
        message: "block must be a decimal block number or 0x-prefixed 32-byte hash",
      },
    };
  }
  const block = String(rawBlock).trim();
  if (!BLOCK_NUMBER_REGEX.test(block) && !BLOCK_HASH_REGEX.test(block)) {
    return {
      error: {
        code: "invalid_block",
        message: "block must be a decimal block number (e.g. 25896003) or 0x-prefixed 64-hex hash",
      },
    };
  }

  // Token in validation
  if (typeof p.tokenIn !== "string" || p.tokenIn.trim() === "") {
    return {
      error: {
        code: "invalid_token_in",
        message: "tokenIn must be a non-empty symbol or address string",
      },
    };
  }
  const tokenIn = p.tokenIn.trim();
  if (tokenIn.length > 128) {
    return {
      error: {
        code: "invalid_token_in",
        message: "tokenIn exceeds maximum allowed length",
      },
    };
  }

  // Token out validation
  if (typeof p.tokenOut !== "string" || p.tokenOut.trim() === "") {
    return {
      error: {
        code: "invalid_token_out",
        message: "tokenOut must be a non-empty symbol or address string",
      },
    };
  }
  const tokenOut = p.tokenOut.trim();
  if (tokenOut.length > 128) {
    return {
      error: {
        code: "invalid_token_out",
        message: "tokenOut exceeds maximum allowed length",
      },
    };
  }

// Amount validation: up to 78 integer digits and 36 fraction digits
  if (typeof p.amount !== "string" || p.amount.trim() === "") {
    return {
      error: {
        code: "invalid_amount",
        message: "amount must be a non-empty decimal string (e.g. '100' or '100.5')",
      },
    };
  }
  const amount = p.amount.trim();
  if (!DECIMAL_AMOUNT_REGEX.test(amount) || amount === "0" || /^0+(\.0+)?$/.test(amount)) {
    return {
      error: {
        code: "invalid_amount",
        message: "amount must be a positive decimal number (up to 78 integer and 36 fractional digits)",
      },
    };
  }

  // Solver validation
  const solver = (typeof p.solver === "string" ? p.solver.trim() : "baseline") as "baseline" | "search";
  if (!SOLVERS.has(solver)) {
    return {
      error: {
        code: "invalid_solver",
        message: "solver must be 'baseline' or 'search'",
      },
    };
  }

  // Sources validation (optional, max 16 items, max 64 chars each)
  let sources: string[] | undefined = undefined;
  if (p.sources !== undefined && p.sources !== null) {
    if (!Array.isArray(p.sources)) {
      return {
        error: {
          code: "invalid_sources",
          message: "sources must be an array of family name strings",
        },
      };
    }
    if (p.sources.length === 0) {
      return {
        error: {
          code: "invalid_sources",
          message: "sources array cannot be empty when supplied",
        },
      };
    }
    if (p.sources.length > 32) {
      return {
        error: {
          code: "invalid_sources",
          message: "sources cannot contain more than 32 families",
        },
      };
    }
    for (const item of p.sources) {
      if (typeof item !== "string" || item.trim() === "" || item.length > 64) {
        return {
          error: {
            code: "invalid_sources",
            message: "each source in sources must be a non-empty string under 64 characters",
          },
        };
      }
    }
    sources = Array.from(new Set(p.sources.map((s) => s.trim())));
  }

  return {
    data: {
      block,
      tokenIn,
      tokenOut,
      amount,
      solver,
      sources,
    },
  };
}

/**
 * Sanitizes upstream error messages so they never reveal internal ports, local paths, or URLs.
 */
export function sanitizeErrorMessage(rawMessage: string): string {
  let cleaned = rawMessage.replace(/\n+/g, " ");
  // Redact URLs
  cleaned = cleaned.replace(/https?:\/\/[^\s]+/g, "[internal bridge]");
  // Redact local file paths
  cleaned = cleaned.replace(/\/home\/[^\s:]+/g, "[internal path]");
  return cleaned.slice(0, 240) || "An unexpected error occurred.";
}
