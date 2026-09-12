"""Exact user amount parsing; CLI failures must not perform RPC."""

import pytest

from swaparch.cli import main, parse_amount
from swaparch.rpc.client import RpcClient


def test_amount_conversion_has_no_decimal_context_rounding():
    assert parse_amount("12345678901234567890.123456789012345678", 18) == (
        12345678901234567890123456789012345678)
    assert parse_amount("1e-6", 6) == 1
    for value in ("0", "-1", "NaN", "Infinity", "no", "0.0000001", "1e100"):
        with pytest.raises(ValueError):
            parse_amount(value, 6)


def test_invalid_cli_amount_does_not_reach_rpc(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("invalid request reached RPC")
    monkeypatch.setattr(RpcClient, "__init__", forbidden)
    assert main(["quote", "--block", "25896003", "--in", "WETH", "--out", "USDC",
                 "--amount", "NaN"]) == 1
    assert main(["quote", "--block", "25896003", "--in", "WETH", "--out", "USDC",
                 "--amount", "1", "--sources", "unknown"]) == 1


def test_offline_client_uses_cached_chain_without_credentials_or_network(tmp_path, monkeypatch):
    import swaparch.rpc.client as rpc

    def forbidden(*args, **kwargs):
        raise AssertionError("offline client accessed credentials or network")

    monkeypatch.setattr(rpc, 'resolve_rpc_url', forbidden)
    monkeypatch.setattr('requests.sessions.Session.request', forbidden)
    client = RpcClient(cache_root=tmp_path, offline=True)
    with pytest.raises(rpc.RpcError, match='offline cache miss: eth_chainId'):
        client.chain_id()
    (tmp_path / 'chain-id.json').write_text('{"response":{"result":"0x1"}}')
    assert client.chain_id() == 1
    assert client.network_requests == 0


def test_project_root_can_be_configured_for_installed_use(tmp_path, monkeypatch):
    import swaparch.rpc.client as rpc

    monkeypatch.setenv("SWAPARCH_ROOT", str(tmp_path))
    assert rpc.configured_project_root() == tmp_path.resolve()


def test_rpc_env_file_is_only_read_when_explicit(tmp_path, monkeypatch):
    import swaparch.rpc.client as rpc

    monkeypatch.delenv("ETH_RPC_URL", raising=False)
    monkeypatch.delenv("RPC_MAINNET", raising=False)
    monkeypatch.delenv("SWAPARCH_ENV_FILE", raising=False)
    monkeypatch.setattr(rpc, "PROJECT_ROOT", tmp_path)
    assert rpc.resolve_rpc_url() is None

    env_file = tmp_path / "rpc.env"
    env_file.write_text("RPC_MAINNET=https://example.invalid\n")
    monkeypatch.setenv("SWAPARCH_ENV_FILE", str(env_file))
    assert rpc.resolve_rpc_url() == "https://example.invalid"


def test_search_cli_preserves_four_hop_single_path_winner(tmp_path, monkeypatch, capsys):
    import json
    from dataclasses import asdict, replace
    from types import SimpleNamespace

    from test_general_solver import A, B, C, D, E, RateCap, record

    from swaparch import cli, universe
    from swaparch.core.types import BlockRef

    block = BlockRef(1, 100, '0xabc', 0)
    states = [RateCap(replace(record(f'{a.symbol}{b.symbol}', (a, b)),
                              config={'validated_block_hashes': [block.hash]}), rate=2)
              for a, b in ((A, B), (B, C), (C, D), (D, E))]
    path = tmp_path / '1/synthetic.json'
    path.parent.mkdir()
    path.write_text(json.dumps({'family': 'synthetic', 'status': 'supported',
                                 'pools': [asdict(s.record) for s in states]}))
    monkeypatch.setattr(universe, 'DISCOVERY_ROOT', tmp_path)
    monkeypatch.setattr(universe, 'implemented_adapters', lambda: {'synthetic': object()})
    monkeypatch.setattr(universe, 'acquire', lambda *args: (None, SimpleNamespace(unsupported={})))
    monkeypatch.setattr(universe, 'load_states', lambda *args: (states, []))
    monkeypatch.setattr(cli, 'RpcClient', lambda: SimpleNamespace(get_block=lambda _: block, network_requests=0))
    assert cli.main(['quote', '--block', '100', '--in', 'A', '--out', 'E',
                     '--amount', '0.000000000000000003', '--solver', 'search',
                     '--max-steps', '4', '--beam-width', '32', '--max-expansions', '100',
                     '--grid-parts', '1']) == 0
    result = json.loads(capsys.readouterr().out)
    assert result['requested_solver'] == 'search'
    assert result['best_single_path']['amount_out'] == 48
    assert len(result['best_single_path']['steps']) == 4
    assert result['single_pool_baseline'] is None
    assert result['network_requests'] == 0
