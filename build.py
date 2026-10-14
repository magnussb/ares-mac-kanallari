#!/usr/bin/env python3
"""Ares maç kanalları.

1) ESPN'den (anahtarsız) önümüzdeki günlerin maçlarını alır.
2) iptv-org/epg ile çekilen Türkiye spor kanallarının yayın akışını (guide.xml) okur.
3) Programı, iki takım adı da başlıkta geçen ve saati maça uyan maçla eşleştirir.

Çıktı: mac_kanallari.json
{
  "guncellendi": "2026-10-14T12:07:00Z",
  "maclar": { "<espn_maç_id>": ["beIN SPORTS 1", "TRT 1"] }
}
Uygulama bu dosyayı okur; eşleşmeyen maçta kanal yazmaz (tahmin yok).
"""
from __future__ import annotations

import glob
import json
import os
import subprocess
import re
import sys
import unicodedata
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone

LEAGUES = [
    'tur.1', 'tur.2', 'tur.cup',
    'uefa.champions', 'uefa.europa', 'uefa.europa.conf', 'uefa.nations',
    'eng.1', 'esp.1', 'ita.1', 'ger.1', 'fra.1', 'por.1', 'ned.1',
    'fifa.worldq.uefa', 'fifa.friendly',
]

UA = ('Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 '
      '(KHTML, like Gecko) Chrome/124.0 Safari/537.36')
TR = timezone(timedelta(hours=3))

# Takım adlarında ayırt edici olmayan kelimeler.
GENERIC = {
    'fk', 'sk', 'fc', 'cf', 'sc', 'ac', 'as', 'afc', 'jk', 'bk', 'spor', 'kulubu',
    'club', 'de', 'la', 'the', 'and', 'cd', 'ud', 'rc', 'sd', 'ss', 'us', 'bb',
    'belediyespor', 'belediye', 'calcio', 'futbol', 'football', 'vfb', 'vfl', 'tsg',
    'fsv', 'sv', 'rb', 'cp', 'sl', 'if', 'al', 'el', 'st', 'olympique', 'stade',
    'real', 'atletico', 'athletic', 'sporting', 'united', 'city',
}

# ESPN (İngilizce) → Türkçe yayın akışında geçen yazım.
ALIASES = {
    'bayern munich': ['bayern'],
    'inter milan': ['inter'],
    'internazionale': ['inter'],
    'ac milan': ['milan'],
    'atletico madrid': ['atletico madrid', 'atletico'],
    'manchester united': ['manchester united', 'man united', 'man utd'],
    'manchester city': ['manchester city', 'man city'],
    'paris saint-germain': ['psg', 'paris saint', 'paris sg'],
    'olympique lyonnais': ['lyon'],
    'olympique de marseille': ['marseille'],
    'sporting cp': ['sporting'],
    'fc porto': ['porto'],
    'sl benfica': ['benfica'],
    'tottenham hotspur': ['tottenham'],
    'wolverhampton wanderers': ['wolverhampton', 'wolves'],
    'brighton & hove albion': ['brighton'],
    'red star belgrade': ['kizilyildiz', 'crvena'],
    'istanbul basaksehir': ['basaksehir'],
    'turkiye': ['turkiye', 'turkey', 'a milli'],
    'turkey': ['turkiye', 'turkey', 'a milli'],
}


def norm(s: str) -> str:  # noqa: E302
    s = s.replace('İ', 'i').replace('I', 'ı').lower()
    s = (s.replace('ı', 'i').replace('ş', 's').replace('ğ', 'g')
         .replace('ç', 'c').replace('ö', 'o').replace('ü', 'u'))
    s = unicodedata.normalize('NFKD', s)
    s = ''.join(ch for ch in s if not unicodedata.combining(ch))
    s = re.sub(r'[^a-z0-9& ]+', ' ', s)
    return re.sub(r'\s+', ' ', s).strip()


_ALIASES = {}


def team_keys(name: str) -> list[str]:
    """Başlıkta aranacak anahtarlar (biri geçerse takım var sayılır)."""
    n = norm(name)
    keys = list(_ALIASES.get(n, []))
    if keys:
        return keys  # bilinen takım: yalnız takma adlar (Man City ≠ Man United)
    words = [w for w in n.split() if len(w) >= 3 and w not in GENERIC]
    if not words:
        words = [w for w in n.split() if len(w) >= 3]
    # Kelimenin ilk 4 harfi yeterli (Münih/Munich, Beşiktaş/Besiktas).
    keys += [w if len(w) <= 5 else w[:4] for w in words]
    return keys


def has_team(title_words: list[str], title: str, team: str) -> bool:
    for k in team_keys(team):
        if ' ' in k:
            if k in title:
                return True
        elif any(w.startswith(k) for w in title_words):
            return True
    return False


def get_json(url: str):
    req = urllib.request.Request(url, headers={'User-Agent': UA, 'Accept': 'application/json'})
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.loads(r.read().decode('utf-8'))


def espn_matches() -> list[dict]:
    today = datetime.now(TR).date()
    out, seen = [], set()
    for off in range(-1, 4):
        ymd = (today + timedelta(days=off)).strftime('%Y%m%d')
        for lg in LEAGUES:
            url = (f'https://site.api.espn.com/apis/site/v2/sports/soccer/{lg}'
                   f'/scoreboard?dates={ymd}')
            try:
                j = get_json(url)
            except Exception as e:  # lig o gün yoksa / ağ hatası
                print(f'ESPN {lg} {ymd}: {e}', file=sys.stderr)
                continue
            for ev in j.get('events', []):
                try:
                    comp = ev['competitions'][0]
                    teams = {c['homeAway']: c['team'] for c in comp['competitors']}
                    start = datetime.fromisoformat(ev['date'].replace('Z', '+00:00'))
                    if ev['id'] in seen:
                        continue
                    seen.add(ev['id'])
                    out.append({
                        'id': ev['id'],
                        'start': start,
                        'home': teams['home'].get('displayName', ''),
                        'away': teams['away'].get('displayName', ''),
                        'league': lg,
                    })
                except Exception:
                    continue
    print(f'ESPN: {len(out)} maç')
    return out


def parse_time(s: str) -> datetime:
    # XMLTV: 20261014180000 +0000
    dt = datetime.strptime(s[:14], '%Y%m%d%H%M%S')
    tz = s[15:].strip() if len(s) > 15 else '+0000'
    sign = 1 if tz.startswith('+') else -1
    off = timedelta(hours=int(tz[1:3]), minutes=int(tz[3:5])) * sign
    return dt.replace(tzinfo=timezone(off))


def epg_programs(pattern: str) -> list[dict]:
    progs = []
    for path in sorted(glob.glob(pattern)):
        progs += _epg_file(path)
    print(f'EPG: {len(progs)} program')
    return progs


def _epg_file(path: str) -> list[dict]:
    try:
        root = ET.parse(path).getroot()
    except Exception as e:
        print(f'{path} okunamadı: {e}', file=sys.stderr)
        return []
    names = {}
    for ch in root.findall('channel'):
        dn = ch.find('display-name')
        names[ch.get('id')] = (dn.text or '').strip() if dn is not None else ch.get('id')
    progs = []
    for p in root.findall('programme'):
        t = p.find('title')
        if t is None or not t.text:
            continue
        try:
            progs.append({
                'channel': names.get(p.get('channel'), p.get('channel')),
                'title': t.text.strip(),
                'start': parse_time(p.get('start')),
                'stop': parse_time(p.get('stop')),
            })
        except Exception:
            continue
    return progs


UEFA_CLUB = {'uefa.champions', 'uefa.europa', 'uefa.europa.conf'}
TABII_APP = 'tabii'

BEIN = 'https://beinsports.com.tr/yayin-akisi'
BEIN_DAYS = ['pazartesi', 'sali', 'carsamba', 'persembe', 'cuma', 'cumartesi', 'pazar']


def get_text(url: str) -> str:
    # curl: site Python'un isteğine farklı yanıt veriyor.
    r = subprocess.run(['curl', '-sL', '--compressed', '-m', '25', '-A', UA, url],
                       capture_output=True, check=True)
    return r.stdout.decode('utf-8', 'replace')


def bein_page(path: str) -> dict:
    h = get_text(f'{BEIN}/{path}' if path else BEIN)
    m = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', h, re.S)
    if not m:
        raise ValueError(f'veri yok ({len(h)} bayt)')
    return json.loads(m.group(1))['props']['pageProps']


def bein_channel_name(rewrite_id: str) -> str | None:
    if rewrite_id == 'beinsports':
        return 'beIN SPORTS 1'
    m = re.fullmatch(r'beinsports-(\d+)', rewrite_id)
    if m:
        return f'beIN SPORTS {m.group(1)}'
    m = re.fullmatch(r'beinsports-max-?(\d+)', rewrite_id)
    if m:
        return f'beIN SPORTS MAX {m.group(1)}'
    return None  # beIN SPORTS HABER vb. maç yayınlamaz


def bein_programs(log: list[str]) -> list[dict]:
    """beIN SPORTS'un kendi yayın akışı (Digiturk sitesi GitHub'ı engelliyor)."""
    try:
        first = bein_page('')
    except Exception as e:
        log.append(f'beIN: ana sayfa alınamadı: {e}')
        return []
    chans = [(x['rewriteId'], bein_channel_name(x['rewriteId']))
             for x in first.get('activeLeagues', [])]
    chans = [c for c in chans if c[1]]
    today = datetime.now(TR).date()
    days = [today + timedelta(days=i) for i in range(0, 4)]
    progs, ok, fail = [], 0, 0
    for rid, name in chans:
        for d in days:
            tag = BEIN_DAYS[d.weekday()]
            try:
                data = bein_page(f'{rid}/{tag}').get('data') or {}
                ok += 1
            except Exception as e:
                fail += 1
                continue
            # Sayfa haftanın o gününü gösterir; gerçek tarihi veriden al.
            try:
                ed = datetime.strptime(data.get('event_date', ''), '%Y-%m-%d').date()
            except ValueError:
                ed = d
            for g in data.get('listTvGuides') or []:
                try:
                    hh, mm, *_ = (g.get('event_time') or '').split(':')
                    start = datetime(ed.year, ed.month, ed.day, int(hh), int(mm), tzinfo=TR)
                except Exception:
                    continue
                progs.append({'channel': name, 'title': (g.get('name') or '').strip(),
                              'start': start, 'stop': start + timedelta(hours=2)})
    log.append(f'beIN: {len(chans)} kanal, {ok} sayfa ok, {fail} hata, {len(progs)} program')
    return progs


# Maç olmayan yayınlar (özet, stüdyo, tekrar).
SKIP = re.compile(r'\b(ozet|ozetler|studyo|tekrar|analiz|magazin|haber|'
                  r'programi|degerlendirme|roportaj|belgesel)\b')


def main() -> None:
    matches = espn_matches()
    progs = epg_programs('guide_*.xml')
    blog: list[str] = []
    progs += bein_programs(blog)
    if blog:
        with open('grab_log.txt', 'a', encoding='utf-8') as f:
            f.write('\n'.join(blog) + '\n')
        print('\n'.join(blog))
    result: dict[str, list[str]] = {}
    matched_titles: set[str] = set()
    for p in progs:
        title = norm(p['title'])
        if SKIP.search(title):
            continue
        words = title.split()
        for m in matches:
            # Program başlangıcı maç saatine yakın olmalı (canlı yayın).
            diff = (p['start'] - m['start']).total_seconds() / 60
            if diff < -75 or diff > 20:
                continue
            if has_team(words, title, m['home']) and has_team(words, title, m['away']):
                matched_titles.add(p['title'])
                chans = result.setdefault(m['id'], [])
                if p['channel'] not in chans:
                    chans.append(p['channel'])
    # UEFA kulüp kupalarının Türkiye hakları TRT'de: TV'de verilmeyen her maç
    # tabii uygulamasında (Türk takımları ücretsiz, diğerleri Premium).
    # Hangi tabii spor kanalı olduğu herkese açık değil (giriş istiyor).
    for m in matches:
        if m.get('league') in UEFA_CLUB and m['id'] not in result:
            result[m['id']] = [TABII_APP]

    # Bir kaynak geçici olarak çekilemezse önceki eşleşmeler kaybolmasın
    # (yalnız hâlâ listede olan maçlar için).
    live_ids = {m['id'] for m in matches}
    try:
        with open('mac_kanallari.json', encoding='utf-8') as f:
            old = json.load(f).get('maclar', {})
        for mid, chans in old.items():
            if mid in live_ids:
                cur = result.setdefault(mid, [])
                if cur and cur != [TABII_APP]:
                    chans = [c for c in chans if c != TABII_APP]
                for c in chans:
                    if c not in cur:
                        cur.append(c)
    except Exception:
        pass
    out = {
        'guncellendi': datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'),
        'kaynak': 'ESPN fikstür + beIN SPORTS / TV+ / D-Smart yayın akışı',
        'maclar': result,
    }
    with open('mac_kanallari.json', 'w', encoding='utf-8') as f:
        json.dump(out, f, ensure_ascii=False, indent=1, sort_keys=True)
    print(f'Eşleşen maç: {len(result)}')
    write_debug(matches, progs, result, matched_titles)


def write_debug(matches, progs, result, matched_titles) -> None:
    """Eşleşmeyi iyileştirmek için rapor (repo'da durur)."""
    now = datetime.now(timezone.utc)
    lines = [f'# Rapor {now:%Y-%m-%d %H:%M} UTC', '',
             f'ESPN maç: {len(matches)} · EPG program: {len(progs)} · eşleşen maç: {len(result)}', '']
    per = {}
    for p in progs:
        per[p['channel']] = per.get(p['channel'], 0) + 1
    if os.path.exists('grab_log.txt'):
        lines += ['## Çekim', '```', open('grab_log.txt', encoding='utf-8').read().strip(), '```', '']
    lines.append('## Kanal başına program')
    lines += [f'- {k}: {v}' for k, v in sorted(per.items())]
    lines += ['', '## Eşleşmeyen, maç gibi görünen yayınlar (önümüzdeki 48 saat)']
    vs = re.compile(r'( - | – | vs\.? |\bx\b)', re.I)
    seen = set()
    for p in sorted(progs, key=lambda x: x['start']):
        if p['start'] < now - timedelta(hours=3) or p['start'] > now + timedelta(hours=48):
            continue
        t = p['title']
        if t in matched_titles or not vs.search(t) or SKIP.search(norm(t)):
            continue
        key = (p['channel'], t)
        if key in seen:
            continue
        seen.add(key)
        lines.append(f"- {p['start'].astimezone(TR):%d.%m %H:%M} {p['channel']}: {t}")
    lines += ['', '## ESPN maçları (önümüzdeki 48 saat)']
    for m in matches:
        if now - timedelta(hours=3) <= m['start'] <= now + timedelta(hours=48):
            ch = ', '.join(result.get(m['id'], [])) or '—'
            lines.append(f"- {m['start'].astimezone(TR):%d.%m %H:%M} {m['home']} - {m['away']} → {ch}")
    with open('rapor.md', 'w', encoding='utf-8') as f:
        f.write('\n'.join(lines) + '\n')


_ALIASES.update({norm(k): [norm(x) for x in v] for k, v in ALIASES.items()})

if __name__ == '__main__':
    main()
