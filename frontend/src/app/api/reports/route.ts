import { NextResponse } from "next/server";

const SWAPARCH_API_URL = process.env.SWAPARCH_API_URL;

export const dynamic = "force-dynamic";

export async function GET() {
  if (!SWAPARCH_API_URL || SWAPARCH_API_URL.trim() === "") {
    return NextResponse.json({ reports: [] }, { status: 200 });
  }

  const targetUrl = `${SWAPARCH_API_URL.replace(/\/+$/, "")}/reports`;

  try {
    const controller = new AbortController();
    const timeoutId = setTimeout(() => controller.abort(), 4000);

    const res = await fetch(targetUrl, {
      headers: { Accept: "application/json" },
      signal: controller.signal,
    });

    clearTimeout(timeoutId);

    if (!res.ok) {
      return NextResponse.json({ reports: [] }, { status: 200 });
    }

    const data = await res.json();
    return NextResponse.json(data, { status: 200 });
  } catch {
    // If backend is offline, return empty list gracefully so the frontend works with static catalog
    return NextResponse.json({ reports: [] }, { status: 200 });
  }
}
