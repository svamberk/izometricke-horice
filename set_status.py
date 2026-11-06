"""Přepne stav všech čtverců (hotovo / rozpracováno) v tiles.json a overlay.json všech pohledů bez přerenderování.
python set_status.py hotovo
Zapíše i seznam hotových pohledů (site/, site/p1..p3/): prohlížeč podle něj ukáže tlačítka otáčení mapy."""
import json, os, sys, time
st = sys.argv[1] if len(sys.argv) > 1 else 'hotovo'
dirs = [(p, 'site' if not p else f'site/p{p}') for p in range(4)]
views = [p for p, d in dirs if os.path.exists(f'{d}/tiles.json') and os.path.exists(f'{d}/overlay.json')]
ver = time.strftime('%Y%m%d%H%M')      # verze dlaždic pro cache (mění se při každém nasazení)
for p, d in dirs:
    if p not in views: continue
    t = json.load(open(f'{d}/tiles.json'))
    t.setdefault('base', {})['status'] = st
    t['ver'] = ver; t['pohledy'] = views
    for c in t.get('chunks', {}).values(): c['status'] = st
    json.dump(t, open(f'{d}/tiles.json', 'w'))
    o = json.load(open(f'{d}/overlay.json'))
    o['chunks'] = {k: st for k in o.get('chunks', {})}
    json.dump(o, open(f'{d}/overlay.json', 'w'), separators=(',', ':'), ensure_ascii=False)
    print('stav', st, d, 'čtverců', len(t.get('chunks', {})) + 1)
print('pohledy', views)
