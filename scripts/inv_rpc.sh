#!/usr/bin/env bash
# Source-inventory verification helper. Never prints the RPC URL.
set -uo pipefail
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
ROOT="$(cd -- "$SCRIPT_DIR/.." && pwd -P)"
EV="$ROOT/data/discovery-evidence"
CNT="$EV/.rpc_call_count"
REDACT='s#https?://[^ "]+#<rpc-url>#g'
if [ -z "${ETH_RPC_URL:-}" ] && [ -z "${RPC_MAINNET:-}" ]; then
  ENV_FILE="${SWAPARCH_ENV_FILE:-$ROOT/.env}"
  if [ -f "$ENV_FILE" ]; then
    set -a
    . "$ENV_FILE"
    set +a
  fi
fi
RPC_URL="${ETH_RPC_URL:-${RPC_MAINNET:-}}"
if [ -z "$RPC_URL" ]; then
  echo "archive RPC URL is not configured (ETH_RPC_URL / RPC_MAINNET)" >&2
  exit 1
fi
[ -f "$CNT" ] || echo 0 > "$CNT"
PINS="23549991 23550060 23728292 24356381 25896003"

bump() { n=$(cat "$CNT"); echo $((n+1)) > "$CNT"; }
jesc() { python3 -c 'import json,sys;print(json.dumps(sys.stdin.read().rstrip("\n")))'; }

# codecheck <family> <label> <addr>   -> code size at first pin then last pin; if empty at first, walk all pins
codecheck() {
  fam=$1; label=$2; addr=$3
  mkdir -p "$EV/$fam"
  out="$EV/$fam/code_${label}.json"
  printf '{"kind":"code_check","family":"%s","label":"%s","address":"%s","pins":{' "$fam" "$label" "$addr" > "$out"
  first=1; prev_has=""
  for b in $PINS; do
    if [ "$prev_has" = "yes" ] && [ "$b" != "25896003" ]; then
      # skip middle pins when code already present at an earlier pin; confirmed again at last pin
      printf '%s"%s":{"code_bytes":null,"inferred":"present (code present at earlier pin and at last pin; EIP-6780 prevents post-creation SELFDESTRUCT)"}' "$( [ $first -eq 1 ] && echo '' || echo ',')" "$b" >> "$out"; first=0; continue
    fi
    raw=$(cast code --rpc-url "$RPC_URL" --block "$b" "$addr" 2>&1 | sed -E "$REDACT"); bump
    if [ "${raw:0:2}" = "0x" ]; then n=$(( (${#raw} - 2) / 2 )); else n=-1; fi
    printf '%s"%s":{"code_bytes":%s}' "$( [ $first -eq 1 ] && echo '' || echo ',')" "$b" "$n" >> "$out"; first=0
    if [ "$n" -gt 0 ]; then prev_has=yes; fi
  done
  printf '}}\n' >> "$out"
  python3 -c "import json;d=json.load(open('$out'));print('$label','$addr',{k:(v.get('code_bytes') if v.get('code_bytes') is not None else 'inferred') for k,v in d['pins'].items()})"
}

# rc <family> <label> <block> <addr> <sig> [args...]  -> cast call, save evidence
rc() {
  fam=$1; label=$2; blk=$3; addr=$4; sig=$5; shift 5
  mkdir -p "$EV/$fam"
  res=$(cast call --rpc-url "$RPC_URL" --block "$blk" "$addr" "$sig" "$@" 2>&1 | sed -E "$REDACT"); bump
  out="$EV/$fam/call_${label}.json"
  python3 - "$out" "$fam" "$label" "$blk" "$addr" "$sig" "$res" "$@" <<'PY'
import json,sys
out,fam,label,blk,addr,sig,res,*args=sys.argv[1:]
json.dump({"kind":"eth_call","family":fam,"label":label,"block":int(blk),"to":addr,"sig":sig,"args":args,"result":res},open(out,"w"),indent=1)
PY
  echo "[$label] $sig @ $blk -> $res"
}
