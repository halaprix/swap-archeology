"""Standalone explorer for saved, historical swap quote reports.

The explorer only visualizes reports supplied to it.  It does not quote, fetch,
or re-optimize routes in the browser.
"""

from __future__ import annotations

import argparse
import glob
import gzip
import json
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any


def _as_strings(value: Any) -> Any:
    """Keep every JSON integer exact when it reaches JavaScript."""
    if isinstance(value, bool) or value is None or isinstance(value, str):
        return value
    if isinstance(value, int):
        return str(value)
    if isinstance(value, list):
        return [_as_strings(item) for item in value]
    if isinstance(value, Mapping):
        return {str(key): _as_strings(item) for key, item in value.items()}
    raise ValueError(f"report contains unsupported JSON value {type(value).__name__}")


def _report_paths(paths: Iterable[str | Path]) -> list[Path]:
    found: set[Path] = set()
    for value in paths:
        text = str(value)
        if glob.has_magic(text):
            names = glob.glob(text)
            if text.endswith(".json"):
                names.extend(glob.glob(text + ".gz"))
            matches = [Path(item) for item in names]
        else:
            matches = [Path(text)]
        for path in matches:
            if path.is_dir():
                found.update(path.glob("*.json"))
                found.update(path.glob("*.json.gz"))
            elif path.is_file():
                found.add(path)
            elif not glob.has_magic(text):
                raise FileNotFoundError(f"saved quote-report path does not exist: {path}")
    if not found:
        raise FileNotFoundError("no saved quote-report JSON files matched")
    return sorted(found)


def _read_json(path: Path) -> Any:
    """Read a plain or gzip-compressed saved report."""
    if path.name.endswith(".gz"):
        with gzip.open(path, "rt", encoding="utf-8") as handle:
            return json.load(handle)
    return json.loads(path.read_text())


def _validate(report: Any, path: Path) -> dict[str, Any] | None:
    """Accept the CLI quote schema and skip its directory-level summary.json."""
    if not isinstance(report, dict):
        raise TypeError(f"{path}: quote report must be a JSON object")
    is_legacy_summary = "checks" in report
    is_matrix_summary = (all(key in report for key in ("block", "request", "best_split", "sources"))
                         and report["block"] is None and report["request"] is None
                         and report["best_split"] is None and report["sources"] is None)
    if is_legacy_summary or is_matrix_summary:
        return None
    request = report.get("request")
    split = report.get("best_split")
    required_request = ("symbol_in", "symbol_out", "decimals_in", "decimals_out", "amount_in")
    if ("best_split" not in report or not isinstance(request, dict)
            or (split is not None and not isinstance(split, dict))
            or type(report.get("block")) is not int
            or any(key not in request for key in required_request)
            or (split is not None and "amount_out" not in split)):
        raise ValueError(f"{path}: not a saved swaparch quote report")
    raw_fields = ("amount_in",)
    if (report["block"] < 0 or any(type(request[field]) is not int or request[field] <= 0
                                     for field in raw_fields)
            or any(type(request[field]) is not int or not 0 <= request[field] <= 255
                   for field in ("decimals_in", "decimals_out"))):
        raise ValueError(f"{path}: invalid block, raw input amount, or token decimals")
    if split is not None and (type(split["amount_out"]) is not int or split["amount_out"] < 0):
        raise ValueError(f"{path}: invalid raw output amount")
    return report


def _subset(report: Mapping[str, Any]) -> tuple[str, ...] | None:
    """Only expose source choices backed by an explicitly recomputed report."""
    selected = report.get("selected_families")
    if isinstance(selected, list) and all(isinstance(source, str) for source in selected):
        return tuple(sorted(selected))
    value = report.get("source_subset")
    if not isinstance(value, Mapping) or value.get("recomputed") is not True:
        return None
    sources = value.get("sources")
    if not isinstance(sources, list) or not all(isinstance(source, str) for source in sources):
        return None
    return tuple(sorted(sources))


def _display_report(report: Mapping[str, Any]) -> dict[str, Any]:
    """Embed only data used by the artifact, not verbose solver diagnostics."""
    request = report["request"]
    best = report["best_split"]
    search = best.get("search_info") if isinstance(best, Mapping) else {}
    if not isinstance(search, Mapping):
        search = {}
    unsupported = report.get("unsupported", [])
    gaps = []
    if isinstance(unsupported, list):
        for row in unsupported:
            if isinstance(row, Mapping):
                gap = {key: row.get(key) for key in ("family", "status", "reason") if row.get(key)}
                if gap and gap not in gaps:
                    gaps.append(gap)
    return {
        "chain": report.get("chain"), "block": report["block"], "block_hash": report.get("block_hash"),
        "timestamp": report.get("timestamp"),
        "request": {key: request.get(key) for key in ("token_in", "token_out", "symbol_in", "symbol_out",
                                                         "decimals_in", "decimals_out", "amount_in")},
        "single_pool_baseline": ({"amount_out": report["single_pool_baseline"].get("amount_out")}
                                 if isinstance(report.get("single_pool_baseline"), Mapping) else None),
        "best_split": (None if best is None else {"amount_out": best.get("amount_out"),
                       "steps": best.get("steps", []), "search_info": {key: search.get(key) for key in
                       ("kind", "allocation")}, "gas_estimate": best.get("gas_estimate")}),
        "requested_solver": report.get("requested_solver"), "selected_families": report.get("selected_families"),
        "source_subset": report.get("source_subset"), "sources": report.get("sources", []),
        "unsupported": gaps, "limitations": report.get("limitations", []),
    }


def _embedded_json(value: Any) -> str:
    # Closing a script tag inside report text must not escape this data element.
    return json.dumps(value, separators=(",", ":"), ensure_ascii=False).replace("<", "\\u003c").replace(
        ">", "\\u003e").replace("&", "\\u0026").replace("\u2028", "\\u2028").replace(
            "\u2029", "\\u2029")


HTML = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Swap Archeology — saved quotes</title>
<style>
:root{color-scheme:dark;--bg:#101722;--panel:#172132;--ink:#edf3fa;--muted:#9daec2;--line:#33455d;--accent:#6ee7c8}*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:16px system-ui,sans-serif}main{max-width:1200px;margin:auto;padding:2rem 1rem 4rem}h1{margin:.2rem 0;font-size:2rem}h2{font-size:1.15rem;margin:0 0 .7rem}p,.muted{color:var(--muted)}.notice{border-left:4px solid var(--accent);padding:.7rem 1rem;background:#142a2f}.controls,.grid{display:grid;gap:1rem}.controls{grid-template-columns:repeat(auto-fit,minmax(150px,1fr));margin:1.4rem 0}.grid{grid-template-columns:repeat(auto-fit,minmax(260px,1fr));margin:1rem 0}section,.card{background:var(--panel);border:1px solid var(--line);border-radius:.5rem;padding:1rem}label{display:grid;gap:.35rem;font-weight:600}select,input{width:100%;background:#0e1621;color:var(--ink);border:1px solid var(--line);border-radius:.3rem;padding:.5rem}output{display:block;font-variant-numeric:tabular-nums;font-size:1.3rem;font-weight:700}.small{font-size:.85rem}.flow-step{display:grid;grid-template-columns:1.8rem 1fr;gap:.5rem;align-items:center;border-top:1px solid var(--line);padding:.55rem 0}.dot{width:1rem;height:1rem;border-radius:50%;margin:auto}.route{font-family:ui-monospace,SFMono-Regular,monospace;font-size:.8rem;overflow-wrap:anywhere}svg{width:100%;height:auto;background:#0e1621;border-radius:.3rem}svg text{fill:var(--muted);font-size:11px}.legend{display:flex;flex-wrap:wrap;gap:.7rem}.legend span{display:inline-flex;gap:.3rem;align-items:center}.legend i{width:.8rem;height:.8rem;border-radius:50%}ul{margin:.3rem 0;padding-left:1.2rem}.error{color:#ffb4b4}button{background:#20334b;color:var(--ink);border:1px solid var(--line);border-radius:.3rem;padding:.5rem;cursor:pointer}@media (prefers-reduced-motion:reduce){*{scroll-behavior:auto!important}}
</style></head><body><main>
<h1>Saved historical swap quotes</h1><p>This file is a read-only view of supplied quote reports. It performs no RPC calls, new quotes, or browser-side routing.</p>
<div class="notice small">Amounts remain raw integer strings until formatted with <code>BigInt</code>. Outputs are pre-gas unless a supplied report says otherwise; gas values are estimates, not gas-adjusted results.</div>
<div class="controls">
<label>Block / time <input id="block" type="range"><span id="when" class="small"></span></label>
<label>Pair <select id="pair"></select></label><label>Direction <select id="direction"></select></label>
<label>Input size <select id="size"></select></label><label>Solver <select id="solver"></select></label>
<label>Source subset <select id="sources"></select><span id="source-note" class="small"></span></label>
</div><div id="empty" class="notice error" hidden></div>
<div id="view"><div class="grid"><div class="card"><h2>Best saved split</h2><output id="out"></output><p id="price" class="small"></p></div><div class="card"><h2>Routing gain</h2><output id="gain"></output><p class="small">Compared with the saved single-pool baseline at this exact report, before gas.</p></div><div class="card"><h2>Report identity</h2><p id="identity" class="small"></p></div></div>
<section><h2>Ordered route flow</h2><p class="small">Each lane is one recorded allocation. Shared endpoints show the supplied split; intermediate merge order is represented only by the step list below.</p><svg id="flow" viewBox="0 0 760 90" role="img" aria-label="Recorded allocation lanes"></svg><div id="steps"></div><div id="legend" class="legend small"></div></section>
<div class="grid"><section><h2>Saved size / execution-price curve</h2><p class="small" id="curve-note"></p><svg id="curve" viewBox="0 0 520 220" role="img" aria-label="Saved size and execution price curve"></svg></section><section><h2>Routing gain through saved blocks</h2><p class="small">Only reports matching the selected pair, direction, size, solver, and actual source subset.</p><svg id="gain-chart" viewBox="0 0 520 220" role="img" aria-label="Routing gain through saved blocks"></svg></section></div>
<section><h2>Coverage and limits</h2><div id="coverage" class="small"></div></section></div>
</main><script id="swaparch-data" type="application/json">__DATA__</script><script>
const DATA=JSON.parse(document.getElementById('swaparch-data').textContent), R=DATA.reports;
const $=id=>document.getElementById(id), colors=['#6ee7c8','#93c5fd','#fbbf24','#fca5a5','#c4b5fd','#fdba74','#5eead4'];
const family=id=>(id||'unknown').split(':')[0], color=id=>colors[[...family(id)].reduce((n,c)=>n+c.charCodeAt(0),0)%colors.length];
const raw=v=>BigInt(v||'0'), fmt=(v,d=0)=>{d=Number(d);let s=raw(v).toString(),sign=s[0]=='-'?'-':'';if(sign)s=s.slice(1);if(!d)return sign+s.replace(/\\B(?=(\\d{3})+(?!\\d))/g,',');s=s.padStart(d+1,'0');let a=s.slice(0,-d).replace(/\\B(?=(\\d{3})+(?!\\d))/g,','),b=s.slice(-d).replace(/0+$/,'');return sign+a+(b?'.'+b:'')};
const ratio=(n,nd,d,dd,places=6)=>{nd=Number(nd);dd=Number(dd);d=raw(d);if(!d)return '—';n=raw(n);let sign=n<0n?'-':'';if(n<0n)n=-n;let scale=10n**BigInt(places+dd),x=n*scale/(d*(10n**BigInt(nd))),s=x.toString().padStart(places+1,'0');return sign+s.slice(0,-places)+'.'+s.slice(-places)};
const gain=r=>{let b=r.single_pool_baseline;if(!r.best_split||!b||!raw(b.amount_out))return null;return ratio((raw(r.best_split.amount_out)-raw(b.amount_out))*100n,0,raw(b.amount_out),0,2)};
const stamp=r=>r.timestamp?new Date(Number(r.timestamp)*1000).toISOString().replace('T',' ').replace('.000Z',' UTC'):'time not supplied';
const key=r=>[r.request.symbol_in,r.request.symbol_out].sort().join(' / '),dir=r=>r.request.symbol_in+' → '+r.request.symbol_out,solver=r=>r.requested_solver||(r.best_split&&r.best_split.solver)||'not recorded',subset=r=>Array.isArray(r.selected_families)?JSON.stringify([...r.selected_families].sort()):r.source_subset&&r.source_subset.recomputed===true?JSON.stringify([...r.source_subset.sources].sort()):'all-supplied';
const choices=(rows,value,label=value=>value)=>[...new Map(rows.map(row=>[value(row),label(row)])).entries()];
function options(id,values){let e=$(id),was=e.value;e.replaceChildren(...values.map(([value,label])=>{let o=document.createElement('option');o.value=value;o.textContent=label;return o}));if(values.some(([value])=>value===was))e.value=was}
function current(){let pair=$('pair').value,direction=$('direction').value,size=$('size').value,sol=$('solver').value,source=$('sources').value,block=blocks[Number($('block').value)];return R.find(r=>String(r.block)===block&&key(r)===pair&&dir(r)===direction&&r.request.amount_in===size&&solver(r)===sol&&subset(r)===source)}
function reconcile(){options('pair',choices(R,key));let pair=R.filter(r=>key(r)===$('pair').value);options('direction',choices(pair,dir));let direction=pair.filter(r=>dir(r)===$('direction').value);options('size',choices(direction,r=>r.request.amount_in,r=>fmt(r.request.amount_in,r.request.decimals_in)+' '+r.request.symbol_in));let sized=direction.filter(r=>r.request.amount_in===$('size').value);options('solver',choices(sized,solver));let solved=sized.filter(r=>solver(r)===$('solver').value);options('sources',choices(solved,subset,row=>subset(row)==='all-supplied'?'All supplied sources':JSON.parse(subset(row)).join(', ')));render()}
function lineChart(id,points,yLabel,scatter=false){let svg=$(id);svg.replaceChildren();let text=(x,y,v)=>{let n=document.createElementNS('http://www.w3.org/2000/svg','text');n.setAttribute('x',x);n.setAttribute('y',y);n.textContent=v;svg.append(n)};if(points.length<2){text(24,105,'Need at least two matching saved reports.');return}let xs=points.map(p=>p.x),ys=points.map(p=>p.y),xmin=Math.min(...xs),xmax=Math.max(...xs),ymin=Math.min(...ys),ymax=Math.max(...ys);if(xmin===xmax)xmax=xmin+1;if(ymin===ymax)ymax=ymin+1;let xy=p=>[36+(p.x-xmin)/(xmax-xmin)*458,186-(p.y-ymin)/(ymax-ymin)*150];if(!scatter){let poly=document.createElementNS('http://www.w3.org/2000/svg','polyline');poly.setAttribute('points',points.map(p=>xy(p).join(',')).join(' '));poly.setAttribute('fill','none');poly.setAttribute('stroke','#6ee7c8');poly.setAttribute('stroke-width','3');svg.append(poly)}text(36,210,points[0].label);text(410,210,points.at(-1).label);text(36,18,yLabel);points.forEach(p=>{let [x,y]=xy(p),c=document.createElementNS('http://www.w3.org/2000/svg','circle');c.setAttribute('cx',x);c.setAttribute('cy',y);c.setAttribute('r','4');c.setAttribute('fill','#fbbf24');svg.append(c)})}
const token=(r,address)=>address===r.request.token_in?r.request.symbol_in:address===r.request.token_out?r.request.symbol_out:(address||'unknown').slice(0,10);
function flow(r){let steps=r.best_split.steps||[],paths=r.best_split.search_info?.allocation,svg=$('flow');svg.replaceChildren();if(!Array.isArray(paths)||!paths.length){let text=document.createElementNS('http://www.w3.org/2000/svg','text');text.setAttribute('x','24');text.setAttribute('y','45');text.textContent='No allocation was saved; see the ordered step timeline below.';svg.append(text)}else{svg.setAttribute('viewBox','0 0 760 '+Math.max(90,paths.length*42+28));paths.forEach((path,lane)=>{let y=28+lane*42,ids=path.path||[],x=i=>90+i*(570/Math.max(1,ids.length-1));let line=document.createElementNS('http://www.w3.org/2000/svg','line');line.setAttribute('x1','35');line.setAttribute('x2','725');line.setAttribute('y1',y);line.setAttribute('y2',y);line.setAttribute('stroke','#9daec2');line.setAttribute('stroke-width','2');svg.append(line);ids.forEach((id,i)=>{let circle=document.createElementNS('http://www.w3.org/2000/svg','circle');circle.setAttribute('cx',x(i));circle.setAttribute('cy',y);circle.setAttribute('r','13');circle.setAttribute('fill',color(id));svg.append(circle)});let label=document.createElementNS('http://www.w3.org/2000/svg','text');label.setAttribute('x','4');label.setAttribute('y',y+4);label.textContent=fmt(path.amount_in,r.request.decimals_in)+' '+r.request.symbol_in;svg.append(label)});let start=document.createElementNS('http://www.w3.org/2000/svg','text'),end=document.createElementNS('http://www.w3.org/2000/svg','text');start.setAttribute('x','4');start.setAttribute('y','14');start.textContent=r.request.symbol_in;end.setAttribute('x','700');end.setAttribute('y','14');end.textContent=r.request.symbol_out;svg.append(start,end)}let list=$('steps');list.replaceChildren(...steps.map((s,i)=>{let row=document.createElement('div');row.className='flow-step';let dot=document.createElement('i');dot.className='dot';dot.style.background=color(s.pool_id);let text=document.createElement('div');text.innerHTML='<strong>Step '+(i+1)+'</strong> ';let code=document.createElement('span');code.className='route';code.textContent=family(s.pool_id)+' · '+fmt(s.amount_in)+' raw '+token(r,s.token_in)+' → '+fmt(s.amount_out)+' raw '+token(r,s.token_out)+' · '+s.pool_id;text.append(code);row.append(dot,text);return row}));let families=[...new Set(steps.map(s=>family(s.pool_id)))];let legend=$('legend');legend.replaceChildren(...families.map(f=>{let x=document.createElement('span'),i=document.createElement('i');i.style.background=color(f);x.append(i,document.createTextNode(f));return x}))}
function coverage(r){let c=$('coverage');c.replaceChildren();let selected=(r.sources||[]).filter(s=>s.selected!==false),usable=selected.filter(s=>s.status==='supported'&&Number(s.usable_pools)>0).map(s=>s.family),excluded=selected.filter(s=>s.status!=='supported'||Number(s.usable_pools)<=0).map(s=>s.family+': '+s.status+(Number(s.usable_pools)<=0?' (no usable pools)':'')),reasons=(r.unsupported||[]).map(s=>(s.family||'candidate')+': '+(s.reason||s.status||'excluded')),routeFamilies=new Set((r.best_split?.steps||[]).map(s=>family(s.pool_id)));if(routeFamilies.has('lido')||routeFamilies.has('origin_arm'))reasons.push('Lido/Origin ARM quote composition is not funded atomic settlement; share rounding can make nominal stETH transfer amounts differ from recipient balance deltas.');let a=document.createElement('p');a.textContent='Selected and usable: '+(usable.join(', ')||'none')+'.';let b=document.createElement('p');b.textContent='Selected but excluded or unavailable: '+(excluded.join('; ')||'none')+'.';let ul=document.createElement('ul');[...reasons,...(r.limitations||['No report limitations supplied.'])].forEach(x=>{let li=document.createElement('li');li.textContent=x;ul.append(li)});c.append(a,b,ul)}
function render(){let block=blocks[Number($('block').value)],blockReport=R.find(x=>String(x.block)===block),r=current(),empty=$('empty'),view=$('view');$('when').textContent='Block '+block+' · '+(blockReport?stamp(blockReport):'time not supplied');empty.hidden=!!r;view.hidden=!r;if(!r){empty.textContent='No saved report at this block for the selected scenario.';return}if(!r.best_split){$('out').textContent='No saved feasible route';$('price').textContent='The supplied report has no best_split; this is a coverage gap, not a zero-output quote.';$('gain').textContent='No baseline';$('identity').textContent='Chain '+r.chain+' · '+(r.block_hash||'hash not supplied')+' · solver '+solver(r)+' · no feasible route recorded';$('flow').replaceChildren();$('steps').replaceChildren();$('legend').replaceChildren();coverage(r);$('curve-note').textContent='No price curve for a report without a feasible route.';lineChart('curve',[],'output per input');lineChart('gain-chart',[],'gain %',true);return}$('out').textContent=fmt(r.best_split.amount_out,r.request.decimals_out)+' '+r.request.symbol_out;$('price').textContent='Execution price: '+ratio(r.best_split.amount_out,r.request.decimals_out,r.request.amount_in,r.request.decimals_in)+' '+r.request.symbol_out+' per '+r.request.symbol_in+' · raw output '+r.best_split.amount_out;$('gain').textContent=gain(r)===null?'No baseline':gain(r)+'%';$('identity').textContent='Chain '+r.chain+' · '+(r.block_hash||'hash not supplied')+' · solver '+solver(r)+' · '+(r.best_split.search_info?.kind||'plan kind not supplied')+' · '+(r.best_split.gas_estimate===null?'gas estimate not supplied':'gas estimate '+r.best_split.gas_estimate);flow(r);coverage(r);let scope=R.filter(x=>x.best_split&&key(x)===key(r)&&dir(x)===dir(r)&&solver(x)===solver(r)&&subset(x)===subset(r)&&String(x.block)===String(r.block));let curve=scope.map(x=>({x:Number(ratio(x.request.amount_in,x.request.decimals_in,'1',0,6)),y:Number(ratio(x.best_split.amount_out,x.request.decimals_out,x.request.amount_in,x.request.decimals_in,8)),label:fmt(x.request.amount_in,x.request.decimals_in)})).sort((a,b)=>a.x-b.x);$('curve-note').textContent=curve.length<2?'Only '+curve.length+' saved size at this block; no inferred curve.':'Observed saved sizes only; joining points does not interpolate liquidity.';lineChart('curve',curve,'output per input');let trend=R.filter(x=>x.best_split&&key(x)===key(r)&&dir(x)===dir(r)&&x.request.amount_in===r.request.amount_in&&solver(x)===solver(r)&&subset(x)===subset(r)).map(x=>({x:Number(x.block),y:gain(x),label:String(x.block)})).filter(x=>x.y!==null).map(x=>({...x,y:Number(x.y)})).sort((a,b)=>a.x-b.x);lineChart('gain-chart',trend,'gain %',true)}
let blocks=[...new Set(R.map(r=>r.block))].sort((a,b)=>Number(a)-Number(b));$('block').min=0;$('block').max=blocks.length-1;$('block').step=1;$('block').value=0;$('block').oninput=render;let hasSubsets=R.some(r=>subset(r)!=='all-supplied');$('source-note').textContent=hasSubsets?'Only recorded selected_families or explicit recomputed subsets are selectable.':'No recomputed source-subset reports were supplied; hiding source lines cannot re-optimize a quote.';$('sources').disabled=!hasSubsets;['pair','direction','size','solver','sources'].forEach(id=>$(id).onchange=reconcile);reconcile();
</script></body></html>"""


def build_explorer(paths: Iterable[str | Path], output: str | Path) -> Path:
    """Build one self-contained HTML file from saved CLI quote-report JSON files."""
    reports: list[dict[str, Any]] = []
    sources: list[str] = []
    for path in _report_paths(paths):
        report = _validate(_read_json(path), path)
        if report is not None:
            reports.append(report)
            sources.append(str(path))
    if not reports:
        raise ValueError("no saved swaparch quote reports found")
    reports.sort(key=lambda item: (item["block"], item["request"]["symbol_in"], item["request"]["symbol_out"], item["request"]["amount_in"]))
    identities: dict[tuple[Any, ...], dict[str, Any]] = {}
    block_identities: dict[int, set[tuple[Any, Any]]] = {}
    token_symbols: dict[str, set[Any]] = {}
    for report in reports:
        request = report["request"]
        block_identities.setdefault(report["block"], set()).add((report.get("chain"), report.get("block_hash")))
        for symbol, token in ((request["symbol_in"], request.get("token_in")),
                              (request["symbol_out"], request.get("token_out"))):
            if token is not None:
                token_symbols.setdefault(symbol, set()).add(token)
        identity = (report.get("chain"), report["block"], report.get("block_hash"), request.get("token_in"),
                    request.get("token_out"), request["symbol_in"], request["symbol_out"], request["amount_in"],
                    report.get("requested_solver"), _subset(report))
        previous = identities.get(identity)
        if previous is not None and _display_report(previous) != _display_report(report):
            raise ValueError(f"conflicting saved reports for block {report['block']} and selected scenario")
        identities[identity] = report
    if any(len(values) > 1 for values in block_identities.values()):
        raise ValueError("cannot combine multiple chain/hash identities for one displayed block")
    if any(len(values) > 1 for values in token_symbols.values()):
        raise ValueError("cannot combine different token addresses under one displayed symbol")
    target = Path(output)
    target.parent.mkdir(parents=True, exist_ok=True)
    display = [_display_report(report) for report in reports]
    target.write_text(HTML.replace("__DATA__", _embedded_json({"reports": _as_strings(display), "sources": sources})))
    return target


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="build a standalone saved-quote explorer")
    parser.add_argument("paths", nargs="+", help="quote JSON files, globs, or directories")
    parser.add_argument("--output", "-o", type=Path, required=True, help="HTML file to write")
    args = parser.parse_args(argv)
    try:
        print(build_explorer(args.paths, args.output))
    except (OSError, TypeError, ValueError, json.JSONDecodeError) as exc:
        parser.error(str(exc))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
