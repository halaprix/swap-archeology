import { NextResponse } from "next/server";

const SWAPARCH_API_URL = process.env.SWAPARCH_API_URL;

export const dynamic = "force-dynamic";

export async function GET() {
  if (!SWAPARCH_API_URL || SWAPARCH_API_URL.trim() === "") {
    return NextResponse.json(
      { status: "unavailable", mode: "offline", configured: false },
      { status: 503 }
    );
  }

  const targetUrl = `${SWAPARCH_API_URL.replace(/\/+$/, "")}/health`;

  try {
    const controller = new AbortController();
    const timeoutId = setTimeout(() => controller.abort(), 2000);

    const res = await fetch(targetUrl, {
      headers: { Accept: "application/json" },
      signal: controller.signal,
    });

    clearTimeout(timeoutId);

    if (!res.ok) {
      return NextResponse.json(
        { status: "unavailable", mode: "offline", configured: true },
        { status: 503 }
      );
    }

    const data = await res.json();
    return NextResponse.json(
      { status: "ok", mode: data?.mode || "offline", configured: true },
      { status: 200 }
    );
  } catch {
    return NextResponse.json(
      { status: "unavailable", mode: "offline", configured: true },
      { status: 503 }
    );
  }
}
