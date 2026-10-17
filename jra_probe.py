#!/usr/bin/env python3
"""Read-only diagnostic for JRA trifecta odds via netkeiba. No bets or DB changes."""
import json
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime
from zoneinfo import ZoneInfo

RACE_ID = '202608040711'
BASE = 'https://race.netkeiba.com/api/api_get_jra_odds.html'
url = BASE + '?' + urllib.parse.urlencode({'race_id': RACE_ID, 'type': '8', 'action': 'update'})
req = urllib.request.Request(url, headers={
    'User-Agent': 'Mozilla/5.0',
    'Accept': 'application/json,text/javascript,*/*',
    'Referer': f'https://race.netkeiba.com/odds/index.html?race_id={RACE_ID}&type=b8',
})
print('JRA 三連単オッズ取得テスト')
print('時刻:', datetime.now(ZoneInfo('Asia/Tokyo')).isoformat(timespec='seconds'))
print('対象レース:', RACE_ID)
try:
    with urllib.request.urlopen(req, timeout=20) as response:
        body = response.read(2_000_001)
        print('HTTP:', response.status, 'bytes:', len(body))
        if len(body) > 2_000_000:
            raise ValueError('response too large')
        obj = json.loads(body)
    print('status:', obj.get('status') if isinstance(obj, dict) else 'not object')
    data = obj.get('data') if isinstance(obj, dict) else None
    print('data keys:', list(data)[:20] if isinstance(data, dict) else 'none')
    odds = data.get('odds') if isinstance(data, dict) else None
    print('odds keys:', list(odds)[:20] if isinstance(odds, dict) else 'none')
    for key, value in (odds.items() if isinstance(odds, dict) else []):
        print('odds group:', key, 'entries:', len(value) if hasattr(value, '__len__') else '?')
        print('sample:', str(list(value.items())[:2] if isinstance(value, dict) else value)[:350])
    if not odds:
        print('RESULT: NO_ODDS. API access or response format needs further investigation.')
    else:
        print('RESULT: ODDS_PRESENT. Validate ticket mapping before any ingestion.')
except (urllib.error.URLError, ValueError, json.JSONDecodeError, TimeoutError) as exc:
    print('RESULT: ERROR', type(exc).__name__, str(exc)[:300])
