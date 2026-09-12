#!/usr/bin/env bash
# usage: inv_run_probe.sh <family> <probe.json> <block>
set -uo pipefail
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
ROOT="$(cd -- "$SCRIPT_DIR/.." && pwd -P)"
. "$SCRIPT_DIR/inv_rpc.sh"
fam=$1; pj=$2; blk=$3
args=$(python3 "$SCRIPT_DIR/inv_probe.py" "$pj")
MC=0xcA11bde05977b3631167028862bE2a173976CA11
res=$(cast call --rpc-url "$RPC_URL" --block "$blk" "$MC" 'aggregate3((address,bool,bytes)[])((bool,bytes)[])' "$args" 2>&1 | sed -E 's#https?://[^ "]+#<rpc-url>#g'); bump
mkdir -p "$ROOT/data/discovery-evidence/$fam"
python3 - "$pj" "$blk" "$res" "$ROOT/data/discovery-evidence/$fam/probe_$(basename $pj .json)_$blk.json" <<'PY'
import json,sys,re
pj,blk,res,out=sys.argv[1:5]
trips=json.load(open(pj))
items=re.findall(r'\((true|false), (0x[0-9a-fA-F]*)\)', res)
rows=[]
for t,(ok,data) in zip(trips,items):
    present = (ok=="false") or (data!="0x" and data!="")
    rows.append({"label":t["label"],"address":t["addr"],"sig":t["sig"],"success":ok=="true","raw":data,"code_present":present})
json.dump({"kind":"multicall3_probe","block":int(blk),"multicall3":"0xcA11bde05977b3631167028862bE2a173976CA11",
           "note":"a call to an address with no code succeeds and returns 0x; success+nonempty or revert proves code present",
           "results":rows},open(out,"w"),indent=1)
for r in rows: print(f'{r["label"]:45s} ok={r["success"]} code={r["code_present"]} {r["raw"][:100]}')
PY
