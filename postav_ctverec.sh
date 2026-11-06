#!/bin/sh
# Přestaví jeden čtverec ve všech čtyřech pohledech (po úpravě dominant nebo pravidel jen v něm) a obnoví data webu:
# ./postav_ctverec.sh 0,0        (~15 min na pohled)
# Ostatní čtverce zůstanou, jak jsou: jejich scény jsou ve výchozím pohledu, proto se u otočených pohledů ponechají
# jejich dosavadní soubory overlay (stromy u lesních silnic se v každém pohledu snižují jinak).
set -e
cd "$(dirname "$0")"
if [ -f .venv/Scripts/activate ]; then . .venv/Scripts/activate; else . .venv/bin/activate; fi
export PYTHONUTF8=1
C=$1; K=c$(echo $C | tr ',' '_')
if [ "$C" = "0,0" ]; then D=data; else D=data_$K; fi
for P in 0 1 2 3; do
  export IZO_POHLED=$P; S=site; [ "$P" = 0 ] || S=site/p$P
  [ -f $S/tiles.json ] || continue                    # pohled není postavený
  python prep.py $D $C > /dev/null && echo "pohled $P scéna $C"
  ./render_chunk.sh $C
  if [ "$C" = "0,0" ]; then python dzi.py; else python dzi.py --chunk $C; fi
  python preview.py > /dev/null
  rm -rf build/overlay_zaloha && mkdir -p build/overlay_zaloha && cp $S/overlay/*.json build/overlay_zaloha/
  python overlay.py > /dev/null
  if [ "$P" != 0 ]; then for f in build/overlay_zaloha/*.json; do [ "$(basename $f)" = "$K.json" ] || cp $f $S/overlay/; done; fi
  echo "pohled $P hotový"
done
unset IZO_POHLED
python prep.py $D $C > /dev/null                      # scéna zpět na výchozí pohled
python set_status.py hotovo
python build_html.py
