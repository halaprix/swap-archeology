import { NextRequest, NextResponse } from "next/server";
import { sanitizeErrorMessage, validateQuoteRequest } from "@/lib/validation";

// Server-only environment variable. Do NOT expose as NEXT_PUBLIC_
const SWAPARCH_API_URL = process.env.SWAPARCH_API_URL;

export const dynamic = "force-dynamic";

export async function POST(request: NextRequest) {
  // Validate content type and length
  const contentType = request.headers.get("content-type") || "";
  if (!contentType.includes("application/json")) {
    return NextResponse.json(
      {
        error: {
          code: "unsupported_media_type",
          message: "Content-Type must be application/json",
        },
      },
      { status: 415 }
    );
  }

  let body: unknown;
  try {
    const text = await request.text();
    if (text.length > 16384) {
      return NextResponse.json(
        {
          error: {
            code: "payload_too_large",
            message: "Request payload exceeds 16KB limit",
          },
        },
        { status: 413 }
      );
    }
    body = JSON.parse(text);
  } catch {
    return NextResponse.json(
      {
        error: {
          code: "invalid_json",
          message: "Request body contains invalid JSON",
        },
      },
      { status: 400 }
    );
  }

  // Validate fields
  const validation = validateQuoteRequest(body);
  if (validation.error || !validation.data) {
    return NextResponse.json(
      {
        error: validation.error,
      },
      { status: 400 }
    );
  }

  // Check backend configuration
  if (!SWAPARCH_API_URL || SWAPARCH_API_URL.trim() === "") {
    return NextResponse.json(
      {
        error: {
          code: "backend_unconfigured",
          message:
            "The quote engine is not configured. Saved historical reports remain available.",
        },
      },
      { status: 503 }
    );
  }

  // Strip trailing slash if present
  const targetUrl = `${SWAPARCH_API_URL.replace(/\/+$/, "")}/quote`;

  try {
    const controller = new AbortController();
    const timeoutId = setTimeout(() => controller.abort(), 20000); // 20s timeout for complex route search

    const upstreamResponse = await fetch(targetUrl, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        Accept: "application/json",
      },
      body: JSON.stringify(validation.data),
      signal: controller.signal,
    });

    clearTimeout(timeoutId);

    const upstreamData = await upstreamResponse.json();

    if (!upstreamResponse.ok) {
      const code = upstreamData?.error?.code || "upstream_error";
      const message = upstreamData?.error?.message
        ? sanitizeErrorMessage(upstreamData.error.message)
        : "The historical quote solver encountered an error processing this request.";

      return NextResponse.json(
        {
          error: {
            code,
            message,
          },
        },
        { status: upstreamResponse.status }
      );
    }

    return NextResponse.json(upstreamData, { status: 200 });
  } catch (err: unknown) {
    const isAbort = err instanceof Error && err.name === "AbortError";
    if (isAbort) {
      return NextResponse.json(
        {
          error: {
            code: "backend_timeout",
            message: "The quote computation timed out after 20 seconds.",
          },
        },
        { status: 504 }
      );
    }

    return NextResponse.json(
      {
        error: {
          code: "backend_unavailable",
          message:
            "The quote engine is unavailable. Please try again later.",
        },
      },
      { status: 503 }
    );
  }
}
