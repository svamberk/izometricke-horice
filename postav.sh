#!/bin/sh
# Postaví celé město z mesto.json: data -> výšky -> les a pole -> scény -> render -> dlaždice -> web (složka site/).
# ./postav.sh            všechno (kroky, které už mají výsledek, se přeskočí – smaž soubor a krok se zopakuje)
# ./postav.sh render     jen scény + render + web (po úpravě pravidel nebo dominant)
# Čas: stahování ~2–5 min na čtverec, render ~1–2 min na čtverec a variantu (Mac s Apple Silicon).
set -e
cd "$(dirname "$0")"
if [ -f .venv/Scripts/activate ]; then . .venv/Scripts/activate; else . .venv/bin/activate; fi   # Windows / Mac a Linux
export PYTHONUTF8=1   # Windows: soubory a výpisy v UTF-8 (jinak padá na češtině)
CH=$(python -c "import geo; print(' '.join(f'{i},{j}' for i, j in sorted(geo.CHUNKS)))")
dir() { if [ "$1" = "0,0" ]; then echo data; else echo data_c$(echo $1 | tr ',' '_'); fi; }

if [ "$1" != render ]; then
  python fetch_data.py --vse                                  # Overture (OSM) + terén, místa, části obce
  [ -f assets/dmp_vysky.json ] || { python dmp_fetch.py && python dmp_vysky.py; }          # výšky budov z laseru ČÚZK
  [ -f build/les_maska.png ] || python nastroje/les.py                                     # les podle ortofota
  [ -f assets/zabaged.json ] || { python nastroje/zabaged.py && python nastroje/pole.py; } # pole a louky
fi
for c in $CH; do python prep.py $(dir $c) $c > /dev/null && echo "scéna $c"; done
for c in $CH; do ./render_chunk.sh $c; done
rm -rf site/tiles && mkdir -p site/tiles && rm -f site/tiles.json
python dzi.py
for c in $CH; do [ "$c" = "0,0" ] || python dzi.py --chunk $c; done
python preview.py
python overlay.py
python mhd.py || true                                         # jízdní řády jen když je v mesto.json mhd.gtfs
python set_status.py hotovo
python build_html.py
echo "Hotovo. Náhled: npm run nahled  a otevři http://localhost:8000"
echo "Izomapa je zdarma. Jestli ti ušetřila práci, kup autorovi kafe: https://donate.stripe.com/7sY7sE41Qg0C0DP5TY8g001 ☕"
