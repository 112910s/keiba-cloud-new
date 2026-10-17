#!/usr/bin/env python3
"""JRA odds collection, isolated from the existing NAR collector.

No betting, no retroactive picks. Data source is an unofficial netkeiba endpoint;
check its terms and availability before sustained use.
"""
import argparse
import hashlib
import json
import os
import re
import sqlite3
import sys
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent
DB = ROOT / 'state' / 'keiba.sqlite3'
JST = ZoneInfo('Asia/Tokyo')
API = 'https://race.netkeiba.com/api/api_get_jra_odds.html'
RACE_ID_RE = re.compile(r'^\d{12}$')
TICKET_RE = re.compile(r'^\d{6}$')


def parse_odds(payload):
    if not isinstance(payload, dict) or not isinstance(payload.get('data'), dict):
        raise ValueError('unexpected API payload')
    data = payload['data'].get('odds')
    if not isinstance(data, dict) or not isinstance(data.get('8'), dict):
        raise ValueError('trifecta odds group 8 unavailable')
    rows = []
    for raw_ticket, raw_values in data['8'].items():
        if not TICKET_RE.fullmatch(str(raw_ticket)):
            continue
        a, b, c = [int(raw_ticket[i:i + 2]) for i in (0, 2, 4)]
        if not (1 <= a <= 18 and 1 <= b <= 18 and 1 <= c <= 18) or len({a, b, c}) != 3:
            continue
        if not isinstance(raw_values, (list, tuple)) or not raw_values:
            continue
        try:
            odd = float(str(raw_values[0]).replace(',', ''))
        except (TypeError, ValueError):
            continue
        if not 0 < odd < 1_000_000:
            continue
        rows.append((f'{a}-{b}-{c}', odd))
    if not rows:
        raise ValueError('no valid trifecta odds found')
    return rows


def fetch(race_id):
    url = API + '?' + urllib.parse.urlencode({'race_id': race_id, 'type': '8', 'action': 'update'})
    req = urllib.request.Request(url, headers={
        'User-Agent': 'Mozilla/5.0 (compatible; KeibaResearch/1.0)',
        'Accept': 'application/json,text/javascript,*/*',
        'Referer': f'https://race.netkeiba.com/odds/index.html?race_id={race_id}&type=b8',
    })
    with urllib.request.urlopen(req, timeout=25) as resp:
        blob = resp.read(2_000_001)
    if len(blob) > 2_000_000:
        raise ValueError('response too large')
    return blob


def setup(conn):
    conn.execute('PRAGMA busy_timeout=15000')
    conn.executescript('''
    CREATE TABLE IF NOT EXISTS jra_odds_observations (
        race_id TEXT NOT NULL, ticket TEXT NOT NULL, odds REAL NOT NULL,
        observed TEXT NOT NULL, source_hash TEXT NOT NULL,
        PRIMARY KEY (race_id, ticket, observed));
    CREATE TABLE IF NOT EXISTS jra_fetches (
        at TEXT NOT NULL, race_id TEXT NOT NULL, status TEXT NOT NULL,
        rows INTEGER NOT NULL, message TEXT NOT NULL);
    ''')


def collect(conn, race_id, blob, at):
    digest = hashlib.sha256(blob).hexdigest()
    rows = parse_odds(json.loads(blob))
    with conn:
        conn.executemany('INSERT OR IGNORE INTO jra_odds_observations VALUES (?,?,?,?,?)',
                         [(race_id, ticket, odd, at, digest) for ticket, odd in rows])
        conn.execute('INSERT INTO jra_fetches VALUES (?,?,?,?,?)',
                     (at, race_id, 'OK', len(rows), f'sha256={digest[:16]}'))
    return len(rows)


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument('--race-id', action='append', default=[], help='12-digit netkeiba JRA race ID; repeatable')
    parser.add_argument('--fixture', help='local JSON fixture for offline testing')
    args = parser.parse_args(argv)
    ids = args.race_id or [x.strip() for x in os.environ.get('JRA_RACE_IDS', '').split(',') if x.strip()]
    if not ids:
        print('JRA: SKIPPED (JRA_RACE_IDS not set; no races invented)')
        return 0
    if len(ids) > 36 or any(not RACE_ID_RE.fullmatch(x) for x in ids):
        parser.error('provide 1..36 valid 12-digit race IDs')
    DB.parent.mkdir(parents=True, exist_ok=True)
    at = datetime.now(JST).isoformat(timespec='seconds')
    failed = 0
    with sqlite3.connect(DB) as conn:
        setup(conn)
        for race_id in dict.fromkeys(ids):
            try:
                blob = Path(args.fixture).read_bytes() if args.fixture else fetch(race_id)
                count = collect(conn, race_id, blob, at)
                print(f'JRA {race_id}: OK {count} trifecta odds; observational data only')
            except Exception as exc:
                failed += 1
                msg = f'{type(exc).__name__}: {str(exc)[:180]}'
                with conn:
                    conn.execute('INSERT INTO jra_fetches VALUES (?,?,?,?,?)', (at, race_id, 'ERROR', 0, msg))
                print(f'JRA {race_id}: ERROR {msg}', file=sys.stderr)
    return 1 if failed else 0


if __name__ == '__main__':
    sys.exit(main())
