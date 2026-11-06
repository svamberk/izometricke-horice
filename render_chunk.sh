#!/bin/sh
# vyrenderuje jeden čtverec mapy: ./render_chunk.sh 0,-1 [varianty]
# okraje s řezem zeminou se berou ze scény (strany bez souseda), všechny čtverce s průhledným pozadím
set -e
C=$1; shift; V=${*:-"autumn winter spring summer lights"}
if [ "$C" = "0,0" ]; then D=build; else D=build/c$(echo $C | tr ',' '_'); fi
G="gx0=-1646.34559672906 gy1=988.7958500237901"
# otočení mapy (geo.py POHLED): IZO_POHLED=1..3 renderuje pohled od SZ, SV, JV do tiles_<varianta>_p1..3
P=${IZO_POHLED:-0}; G="$G az=$((225 + 90 * P))"; S=""; [ "$P" = 0 ] || S="_p$P"
for s in $V; do
  if [ "$s" = lights ]; then
    node render.mjs mpp=0.2 dir=$D out=$D/tiles_lights$S season=autumn mode=lights alpha=1 $G | grep -c dlaždice
  else
    node render.mjs mpp=0.2 dir=$D out=$D/tiles_$s$S season=$s alpha=1 $G | grep -c dlaždice
  fi
done
echo HOTOVO $C
