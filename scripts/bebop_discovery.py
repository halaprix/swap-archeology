"""Read-only Bebop liquidity leads and token discovery.

Never treats API quotes as historical quotes; flags demo prices as degraded.
Official quickstart: https://docs.bebop.xyz/rfq-api/quickstart
"""

from __future__ import annotations

import argparse
import json
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import requests

from swaparch.adapters.bebop import normalize_bebop_quote

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "outputs/bebop-discovery"

DEFAULT_TAKER = "0x5Bad996643a924De21b6b2875c85C33F3c5bBcB6"  # Official docs quickstart sample wallet
ETH_TOKENS = {
    "WETH": ("0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2", 18),
    "USDC": ("0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48", 6),
    "USDT": ("0xdAC17F958D2ee523a2206206994597C13D831ec7", 6),
    "DAI": ("0x6B175474E89094C44Da98b954EedeAC495271d0F", 18),
}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tokens-only", action="store_true", help="Discover tokens only, skip quotes")
    parser.add_argument("--taker", default=DEFAULT_TAKER, help="Taker address for RFQ quotes")
    parser.add_argument("--fixture", type=Path, help="Use offline JSON fixture instead of live network")
    args = parser.parse_args()

    OUT.mkdir(exist_ok=True, parents=True)

    session = requests.Session()
    session.headers.update({"User-Agent": "Mozilla/5.0 (compatible; SwapArcheology/1.0)"})

    def get(path: str, params: dict[str, Any] | None, name: str) -> dict[str, Any]:
        if args.fixture:
            fixture_file = args.fixture if args.fixture.is_file() else args.fixture / f"{name}.json"
            data = json.loads(fixture_file.read_text())
            return data.get("response", data)

        url = f"https://api.bebop.xyz{path}"
        response = None
        for attempt in range(3):
            try:
                response = session.get(url, params=params, timeout=20)
                if response.status_code == 429 and attempt < 2:
                    time.sleep(2 ** (attempt + 1))
                    continue
                break
            except requests.RequestException as exc:
                if attempt == 2:
                    raise RuntimeError(f"Failed to fetch {url}: {exc}") from exc
                time.sleep(1)

        if response is None:
            raise RuntimeError(f"No response from {url}")

        body = response.json()
        payload = {
            "retrievedAt": datetime.now(UTC).isoformat(),
            "endpoint": path,
            "params": params,
            "status": response.status_code,
            "historical": False,
            "is_demo": True,
            "notice": "Demo/unauthenticated quotes receive degraded pricing and are not production representative",
            "response": body,
        }
        (OUT / f"{name}.json").write_text(json.dumps(payload, indent=2) + "\n")
        if response.status_code != 200:
            raise RuntimeError(f"Bebop {response.status_code} at {path}; see {name}.json")
        time.sleep(0.3)
        return body

    print("Fetching Bebop supported chains...", flush=True)
    chains = get("/pmm/chains", None, "chains")
    print(f"Supported chains: {list(chains.keys())}", flush=True)

    print("Fetching Ethereum supported tokens...", flush=True)
    tokens_resp = get("/pmm/ethereum/v3/tokens", None, "ethereum_tokens")
    tokens = tokens_resp.get("tokens", {})
    print(f"Discovered {len(tokens)} tokens on Ethereum", flush=True)

    target_symbols = ["WETH", "USDC", "USDT", "DAI"]
    for sym in target_symbols:
        tinfo = tokens.get(sym)
        if tinfo:
            avail = tinfo.get("availability", {})
            print(f"  {sym}: canBuy={avail.get('canBuy')}, canSell={avail.get('canSell')}, priceUsd={tinfo.get('priceUsd')}")

    if args.tokens_only:
        return

    # Request bounded indicative quotes for WETH -> USDC
    cases = [
        ("WETH", "USDC", 1),
        ("WETH", "USDC", 10),
    ]

    print("\nRequesting indicative demo RFQ quotes (flagged as degraded/demo)...", flush=True)
    for sell_sym, buy_sym, n in cases:
        sell_addr, sell_dec = ETH_TOKENS[sell_sym]
        buy_addr, _ = ETH_TOKENS[buy_sym]
        amount_raw = n * 10**sell_dec
        params = {
            "sell_tokens": sell_addr,
            "buy_tokens": buy_addr,
            "sell_amounts": amount_raw,
            "taker_address": args.taker,
        }
        name = f"quote_{sell_sym}_{buy_sym}_{n}"
        try:
            body = get("/pmm/ethereum/v3/quote", params, name)
            normalized = normalize_bebop_quote(body, is_historical=False)
            rate = (normalized.amount_out / 10**normalized.token_out.decimals) / n
            print(
                f"  {n} {sell_sym} -> {normalized.amount_out / 10**normalized.token_out.decimals:.2f} {buy_sym} "
                f"(rate: {rate:.2f}, quoteId: {normalized.quote_id[:16]}..., expiry: {normalized.expiry}, demo: {normalized.is_demo})"
            )
        except (AttributeError, RuntimeError, TypeError, ValueError) as exc:
            print(f"  Failed quote {sell_sym}->{buy_sym} {n}: {exc}")


if __name__ == "__main__":
    main()
