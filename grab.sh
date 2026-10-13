#!/usr/bin/env bash
# Her siteyi ayrı çeker; eksik gelirse 3 kez dener, en dolu sonucu tutar.
set -u
cd epg
: > ../grab_log.txt
for f in ../kanallar/*.xml; do
  site=$(basename "$f" .xml)
  want=$(grep -c '<channel ' "$f")
  best=0
  for try in 1 2 3; do
    out="../tmp_${site}.xml"
    rm -f "$out"
    timeout 600 npm run grab --- --channels="$f" --output="$out" --days=3 \
      --maxConnections=1 --timeout=30000 --delay=300 > "../tmp_${site}.log" 2>&1 || true
    got=0; progs=0
    if [ -f "$out" ]; then
      got=$(grep -o '<programme [^>]*channel="[^"]*"' "$out" | sed 's/.*channel=//' | sort -u | wc -l)
      progs=$(grep -c '<programme ' "$out" || true)
    fi
    echo "$site deneme $try: $got/$want kanal, $progs program" | tee -a ../grab_log.txt
    if [ "$progs" -gt "$best" ]; then best=$progs; cp "$out" "../guide_${site}.xml"; fi
    [ "$got" -ge "$want" ] && break
    sleep 20
  done
  if [ "$best" -eq 0 ]; then
    echo "--- $site son log:" >> ../grab_log.txt
    grep -iE "error|fail|status|ERR|[0-9]{3} " "../tmp_${site}.log" | tail -8 >> ../grab_log.txt
  fi
done
