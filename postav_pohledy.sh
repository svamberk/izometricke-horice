#!/bin/sh
# Otočené pohledy mapy (od SZ, SV, JV) pro tlačítka otáčení v prohlížeči: scény, render, dlaždice a data webu do site/p1..p3/.
# Spouštět až po ./postav.sh (výchozí pohled od JZ). Každý pohled trvá zhruba jako ./postav.sh render.
# ./postav_pohledy.sh          všechny tři pohledy
# ./postav_pohledy.sh 2        jen pohled 2 (od SV)
set -e
cd "$(dirname "$0")"
if [ -f .venv/Scripts/activate ]; then . .venv/Scripts/activate; else . .venv/bin/activate; fi   # Windows / Mac a Linux
export PYTHONUTF8=1
CH=$(python -c "import geo; print(' '.join(f'{i},{j}' for i, j in sorted(geo.CHUNKS)))")
dir() { if [ "$1" = "0,0" ]; then echo data; else echo data_c$(echo $1 | tr ',' '_'); fi; }

for P in ${*:-1 2 3}; do
  export IZO_POHLED=$P
  for c in $CH; do python prep.py $(dir $c) $c > /dev/null && echo "pohled $P scéna $c"; done   # stromy u silnic podle kamery
  for c in $CH; do ./render_chunk.sh $c; done
  rm -rf site/p$P/tiles && mkdir -p site/p$P/tiles && rm -f site/p$P/tiles.json
  python dzi.py
  for c in $CH; do [ "$c" = "0,0" ] || python dzi.py --chunk $c; done
  python preview.py
  python overlay.py
  echo "pohled $P hotový"
done
unset IZO_POHLED
for c in $CH; do python prep.py $(dir $c) $c > /dev/null; done   # scény zpět na výchozí pohled (build/ pro další úpravy)
python set_status.py hotovo                                     # seznam pohledů a verze dlaždic pro prohlížeč
echo "Hotovo. Otočené pohledy jsou v site/p1..p3/, prohlížeč ukáže tlačítka ⟲ ⟳, když jsou všechny čtyři."
