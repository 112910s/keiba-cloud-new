#!/usr/bin/env python3
"""Small, auditable NAR paper-trading collector. Python standard library only."""
import argparse, csv, hashlib, html, io, json, os, sqlite3, sys, urllib.parse, urllib.request, zipfile
from collections import Counter
from collections import defaultdict
from datetime import datetime, date, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT=Path(__file__).resolve().parent
DB=ROOT/'state'/'keiba.sqlite3'
DOCS=ROOT/'docs'
JST=ZoneInfo('Asia/Tokyo')
START=date(2026,10,16); END=date(2026,11,1)
HOST='https://www.keiba.go.jp'
TYPES=('racelist','horselist','odds','payback')

def now(): return datetime.now(JST)
def stamp(t=None): return (t or now()).isoformat(timespec='seconds')
def digits(v): return ''.join(c for c in str(v or '') if c.isdigit())
def num(v):
    try: return float(str(v).replace(',','').strip())
    except (TypeError,ValueError): return None

def connect():
    DB.parent.mkdir(parents=True,exist_ok=True)
    c=sqlite3.connect(DB)
    c.execute('PRAGMA busy_timeout=15000')
    c.executescript('''
    CREATE TABLE IF NOT EXISTS races (venue TEXT, day TEXT, race INTEGER, post TEXT, name TEXT, surface TEXT, runners TEXT, updated TEXT, PRIMARY KEY(venue,day,race));
    CREATE TABLE IF NOT EXISTS entries (venue TEXT,day TEXT,race INTEGER,horse INTEGER,name TEXT,jockey TEXT,finish TEXT,updated TEXT,PRIMARY KEY(venue,day,race,horse));
    CREATE TABLE IF NOT EXISTS payouts (venue TEXT,day TEXT,race INTEGER,ticket TEXT,yen INTEGER,updated TEXT,PRIMARY KEY(venue,day,race,ticket));
    CREATE TABLE IF NOT EXISTS picks (venue TEXT,day TEXT,race INTEGER,ticket TEXT,odds REAL,stake INTEGER,captured TEXT,source_hash TEXT,method TEXT,PRIMARY KEY(venue,day,race,ticket));
    CREATE TABLE IF NOT EXISTS closed (venue TEXT,day TEXT,race INTEGER,captured TEXT,reason TEXT,PRIMARY KEY(venue,day,race));
    CREATE TABLE IF NOT EXISTS fetches (at TEXT,kind TEXT,status TEXT,rows INTEGER,sha256 TEXT,message TEXT);
    CREATE TABLE IF NOT EXISTS odds_observations (venue TEXT,day TEXT,race INTEGER,ticket TEXT,odds REAL,observed TEXT,source_hash TEXT,PRIMARY KEY(venue,day,race,ticket,observed));
    CREATE TABLE IF NOT EXISTS history (venue TEXT,day TEXT,race INTEGER,horse INTEGER,name TEXT,jockey TEXT,finish INTEGER,PRIMARY KEY(venue,day,race,horse));
    ''')
    c.commit(); return c

def read_csv(data):
    try: s=data.decode('utf-8-sig')
    except UnicodeDecodeError: s=data.decode('cp932')
    reader=csv.DictReader(io.StringIO(s, newline=''))
    if reader.fieldnames is None: return []
    reader.fieldnames=[v.strip().lstrip('\ufeff') for v in reader.fieldnames]
    return [{(k.strip() if k else k):(v.strip() if isinstance(v,str) else v) for k,v in row.items() if k is not None} for row in reader if any(v and str(v).strip() for v in row.values() if v is not None)]

def getzip(kind, timeout=60):
    endpoint='RaceDataDownload' if kind=='race' else 'OddsDataDownload'
    url=HOST+'/KeibaWeb/DataDownload/'+endpoint+'?'+urllib.parse.urlencode({'type':'daily'})
    req=urllib.request.Request(url,headers={'User-Agent':'Mozilla/5.0 (compatible; KeibaResearch/1.0)','Accept':'application/zip,application/octet-stream,*/*','Referer':HOST+'/KeibaWeb/TodayRaceInfo/TodayRaceInfoTop'})
    with urllib.request.urlopen(req,timeout=timeout) as r:
        data=r.read(35*1024*1024+1)
    if len(data)>35*1024*1024: raise ValueError('ZIP exceeds 35 MB safety limit')
    return data

def parse_archive(blob):
    out={}
    with zipfile.ZipFile(io.BytesIO(blob)) as z:
        infos=[i for i in z.infolist() if not i.is_dir()]
        if len(infos)>15 or sum(i.file_size for i in infos)>250*1024*1024: raise ValueError('unsafe ZIP')
        if any(i.flag_bits & 1 for i in infos): raise ValueError('encrypted ZIP is unsupported')
        for i in infos:
            basename=Path(i.filename).name.lower()
            kind=next((k for k in TYPES if basename.endswith('_'+k+'.csv')),None)
            if kind:
                if i.file_size>150*1024*1024: raise ValueError('CSV too large')
                out[kind]=read_csv(z.read(i))
    return out

def key(row):
    try:
        venue=str(row.get('競馬場','')).strip(); day=digits(row.get('競走年月日'))[:8]; race=int(row.get('レース番号'))
        if not venue or len(day)!=8 or race<1 or race>20: return None
        return venue,day,race
    except (TypeError,ValueError): return None

def race_datetime(day, post):
    d=digits(post)
    if len(d) not in (3,4): return None
    d=d.zfill(4)
    try: return datetime.strptime(day+d,'%Y%m%d%H%M').replace(tzinfo=JST)
    except ValueError: return None

def historical_scores(c, race_key, entries, cutoff_day):
    """Past-only empirical horse scores; never inspect finishes on/after race day.

    Scores are descriptive and not calibrated winning probabilities.
    Require three historical starts for every horse considered eligible.
    """
    names={}
    for row in entries:
        k=key(row)
        if k!=race_key: continue
        try: horse=int(row.get('馬番'))
        except (TypeError, ValueError): continue
        name=str(row.get('馬名','')).strip()
        if name: names[horse]=name
    if len(names)<3: return {}
    scores={}
    for horse,name in names.items():
        n,w,top3=c.execute("""SELECT COUNT(*),
             COALESCE(SUM(CASE WHEN finish=1 THEN 1 ELSE 0 END),0),
             COALESCE(SUM(CASE WHEN finish<=3 THEN 1 ELSE 0 END),0)
             FROM history WHERE name=? AND day<?""",(name,cutoff_day)).fetchone()
        if n>=3:
            scores[horse]=round((w+1)/(n+10)+0.3*(top3+1)/(n+10),6)
    return scores


def choose_tickets(options, scores):
    """Rank eligible combinations by historical scores when coverage is sufficient.

    Fall back to explicitly labeled odds-only baseline otherwise.
    """
    unique={ticket:odd for ticket,odd in options}
    candidates=list(unique.items())
    scored=[]
    if scores:
        for ticket,odd in candidates:
            try: a,b,d=(int(v) for v in ticket.split('-'))
            except ValueError: continue
            if all(h in scores for h in (a,b,d)):
                # Ordered finish weights are a heuristic, not a probability.
                scored.append((ticket,odd,0.55*scores[a]+0.30*scores[b]+0.15*scores[d]))
    if len(scored)>=10:
        return [(t,o) for t,o,_ in sorted(scored,key=lambda v:(-v[2],v[0]))[:10]],'historical_heuristic_v1'
    return sorted(candidates,key=lambda x:(abs(x[1]-150),x[0]))[:10],'odds_only_baseline_v1'


def ingest(c, datasets, observed, digest, allow_picks=True):
    """Atomic ingestion; picks are only written for pre-start observations."""
    counts={k:len(v) for k,v in datasets.items()}
    # Restrict evaluation period. Older/monthly data is never silently backdated.
    with c:
        for r in datasets.get('odds',[]):
            k=key(r)
            if not k or k[1]!=observed.strftime('%Y%m%d') or str(r.get('賭式','')).strip() not in ('３連単','3連単','三連単'): continue
            odd=num(r.get('オッズ'))
            parts=[str(r.get('番号'+str(i),'')).strip() for i in (1,2,3)]
            if odd is None or odd<=0 or not all(parts) or len(set(parts))!=3: continue
            c.execute('INSERT OR IGNORE INTO odds_observations VALUES(?,?,?,?,?,?,?)',(*k,'-'.join(parts),odd,stamp(observed),digest))
        for r in datasets.get('racelist',[]):
            k=key(r)
            if k and START.strftime('%Y%m%d')<=k[1]<=END.strftime('%Y%m%d'):
                c.execute('INSERT INTO races VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(venue,day,race) DO UPDATE SET post=excluded.post,name=excluded.name,surface=excluded.surface,runners=excluded.runners,updated=excluded.updated',(*k,r.get('発走時刻',''),r.get('レース名',''),r.get('芝ダート区分',''),r.get('頭数',''),stamp(observed)))
        for r in datasets.get('horselist',[]):
            k=key(r)
            try: horse=int(r.get('馬番'))
            except (ValueError,TypeError): continue
            if k and START.strftime('%Y%m%d')<=k[1]<=END.strftime('%Y%m%d'):
                c.execute('INSERT INTO entries VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(venue,day,race,horse) DO UPDATE SET name=excluded.name,jockey=excluded.jockey,finish=excluded.finish,updated=excluded.updated',(*k,horse,r.get('馬名',''),r.get('騎手名',''),r.get('着順',''),stamp(observed)))
        for r in datasets.get('payback',[]):
            k=key(r)
            if not k: continue
            parts=[str(r.get('３連単組番馬番'+str(i),'')).strip() for i in (1,2,3)]
            ticket='-'.join(parts)
            yen=num(r.get('３連単払戻金（円）'))
            if all(parts) and len(set(parts))==3 and yen is not None and yen>=0:
                c.execute('INSERT INTO payouts VALUES(?,?,?,?,?,?) ON CONFLICT(venue,day,race,ticket) DO UPDATE SET yen=excluded.yen,updated=excluded.updated',(*k,ticket,int(yen),stamp(observed)))
        # Baseline paper picks: deterministic odds-only candidates, NOT a predictive model.
        # All picks in a race are locked at once. Never fill them retroactively.
        if allow_picks:
            grouped={}
            for r in datasets.get('odds',[]):
                k=key(r)
                if not k or k[1]!=observed.strftime('%Y%m%d'): continue
                if str(r.get('賭式','')).strip() not in ('３連単','3連単','三連単'): continue
                odd=num(r.get('オッズ'))
                if odd is None or odd<100 or odd>5000: continue
                parts=[str(r.get('番号'+str(i),'')).strip() for i in (1,2,3)]
                if not all(parts) or len(set(parts))<3: continue
                grouped.setdefault(k,[]).append(('-'.join(parts),odd))
            for k,options in grouped.items():
                existing=c.execute('SELECT 1 FROM closed WHERE venue=? AND day=? AND race=?',k).fetchone()
                if existing: continue
                row=c.execute('SELECT post FROM races WHERE venue=? AND day=? AND race=?',k).fetchone()
                dt=race_datetime(k[1],row[0]) if row else None
                gap=(dt-observed).total_seconds()/60 if dt else None
                if gap is None or not 5<=gap<=25: continue
                # Avoid thousands of arbitrary combinations; this is a transparent
                # mechanical baseline, not an assertion of expected profitability.
                # Keep one quote per ticket even if upstream repeats it.
                scores=historical_scores(c,k,datasets.get('horselist',[]),k[1])
                selected,method=choose_tickets(options,scores)
                for ticket,odd in selected:
                    c.execute('INSERT OR IGNORE INTO picks VALUES(?,?,?,?,?,?,?,?,?)',(*k,ticket,odd,1000,stamp(observed),digest,method))
                c.execute('INSERT OR IGNORE INTO closed VALUES(?,?,?,?,?)',(*k,stamp(observed),'locked_before_post'))
    return counts

def import_history_zip(c, zip_path):
    """Import *past* NAR horse results for research, never as future information.

    Use only records with a known numeric finish. Does not generate backdated picks.
    """
    path=Path(zip_path)
    if path.stat().st_size>350*1024*1024:
        raise ValueError('Historical ZIP exceeds 350 MB')
    data=parse_archive(path.read_bytes())
    rows=data.get('horselist',[])
    count=0
    with c:
        for r in rows:
            k=key(r)
            if not k: continue
            try:
                horse=int(r.get('馬番'))
                finish=int(digits(r.get('着順')))
            except (TypeError,ValueError): continue
            if horse<1 or finish<1 or finish>30: continue
            c.execute('INSERT OR REPLACE INTO history VALUES(?,?,?,?,?,?,?)',(*k,horse,str(r.get('馬名','')),str(r.get('騎手名','')),finish))
            count+=1
    return count


def history_report(c, as_of):
    """Past-only empirical rates, with minimum sample size and no future leakage."""
    counts=defaultdict(lambda:[0,0])
    for name,win in c.execute("SELECT name,finish=1 FROM history WHERE day<? AND name<>''",(as_of,)):
        counts[name][0]+=1
        counts[name][1]+=int(win)
    ranked=[{'horse':name,'starts':n,'wins':w,'smoothed_win_rate':round((w+1)/(n+10),4)}
            for name,(n,w) in counts.items() if n>=3]
    return sorted(ranked,key=lambda r:(-r['smoothed_win_rate'],-r['starts'],r['horse']))


def sync(c, fixture=None, observed=None):
    observed=observed or now()
    results=[]
    for kind in ('race','odds'):
        try:
            if fixture:
                file=Path(fixture)/('race.zip' if kind=='race' else 'odds.zip')
                blob=file.read_bytes()
            else: blob=getzip(kind)
            digest=hashlib.sha256(blob).hexdigest()
            parsed=parse_archive(blob)
            counts=ingest(c,parsed,observed,digest)
            n=sum(counts.values())
            status='OK' if n else 'EMPTY'
            if kind=='odds' and len(parsed.get('odds',[]))==0: status='EMPTY_ODDS'
            if kind=='race' and len(parsed.get('racelist',[]))==0: status='EMPTY_RACES'
            message=json.dumps(counts,ensure_ascii=False)
        except Exception as exc:
            digest=''; n=0; status='ERROR'; message=str(exc)[:300]
        with c: c.execute('INSERT INTO fetches VALUES(?,?,?,?,?,?)',(stamp(observed),kind,status,n,digest,message))
        print(f'{kind}: {status} rows={n} {message}',flush=True)
        results.append(status)
    render(c,observed)
    return 1 if 'ERROR' in results else 0

def render(c,observed=None):
    observed=observed or now(); DOCS.mkdir(exist_ok=True)
    day=observed.strftime('%Y%m%d')
    races=c.execute('SELECT venue,day,race,post,name,surface FROM races WHERE day BETWEEN ? AND ? ORDER BY day DESC,venue,race',(START.strftime('%Y%m%d'),END.strftime('%Y%m%d'))).fetchall()
    fetches=c.execute('SELECT at,kind,status,rows,message FROM fetches ORDER BY rowid DESC LIMIT 12').fetchall()
    rows=[]; totals=Counter()
    for venue,rd,rno,post,name,surface in races:
        pay=c.execute('SELECT ticket,yen FROM payouts WHERE venue=? AND day=? AND race=?',(venue,rd,rno)).fetchall()
        picks=c.execute('SELECT ticket,odds,stake,captured FROM picks WHERE venue=? AND day=? AND race=?',(venue,rd,rno)).fetchall()
        matched={ticket:yen for ticket,yen in pay}
        stake=sum(p[2] for p in picks)
        returns=sum((matched.get(p[0],0)*p[2]//100) for p in picks)
        hits=sum(p[0] in matched for p in picks)
        category='ばんえい' if '帯広' in venue or 'ばんえい' in venue else '地方'
        totals['races']+=1;totals['payout_races']+=bool(pay);totals['picked_races']+=bool(picks)
        totals['stake']+=stake;totals['returns_confirmed']+=returns if pay else 0
        totals['settled_stake']+=stake if pay else 0;totals['hits']+=hits if pay else 0
        # Explain only the actual immutable pre-race records. Never invent a prediction.
        detail=[]
        for ticket,odd,amount,captured,method in c.execute('SELECT ticket,odds,stake,captured,method FROM picks WHERE venue=? AND day=? AND race=? ORDER BY ticket',(venue,rd,rno)):
            is_hit = ticket in matched if pay else None
            reason = ('過去の着順データを使った簡易スコア（1着55%、2着30%、3着15%）で順位付け。確率推定ではありません。' if method=='historical_heuristic_v1' else '取得時オッズ100〜5000倍の候補から150倍に近い順で選択。馬の強さは評価していません。' if method=='odds_only_baseline_v1' else '選定方式が未確認です。')
            detail.append(dict(ticket=ticket,odds=odd,stake=amount,captured=captured,method=method,reason=reason,hit=is_hit,return_yen=(matched.get(ticket,0)*amount//100 if pay else None)))
        rows.append(dict(venue=venue,date=rd,race=rno,post=post,name=name,surface=surface,category=category,payout_confirmed=bool(pay),picks=len(picks),stake=stake,hit=(hits if pay else None),return_yen=(returns if pay else None),status='照合済' if pay and picks else '事前記録なし' if not picks else '払戻待ち',ticket_details=detail))
    observations=c.execute('SELECT COUNT(*) FROM odds_observations').fetchone()[0]
    totals['unsettled_picked_races']=sum(bool(r['picks']) and not r['payout_confirmed'] for r in rows)
    totals['settled_picked_races']=sum(bool(r['picks']) and r['payout_confirmed'] for r in rows)
    totals['profit_confirmed']=totals['returns_confirmed']-totals['settled_stake']
    totals['roi_confirmed_percent']=round(100*totals['returns_confirmed']/totals['settled_stake'],2) if totals['settled_stake'] else None
    totals['odds_observations']=observations
    totals['historical_model_picks']=c.execute("SELECT COUNT(*) FROM picks WHERE method='historical_heuristic_v1'").fetchone()[0]
    totals['baseline_picks']=c.execute("SELECT COUNT(*) FROM picks WHERE method='odds_only_baseline_v1'").fetchone()[0]
    totals['unpicked_races']=sum(not r['picks'] for r in rows)
    totals['empty_odds_fetches']=c.execute("SELECT COUNT(*) FROM fetches WHERE status='EMPTY_ODDS'").fetchone()[0]
    totals['fetch_errors']=c.execute("SELECT COUNT(*) FROM fetches WHERE status='ERROR'").fetchone()[0]
    totals['by_category']={category:{'races':sum(r['category']==category for r in rows),'picked_races':sum(r['category']==category and bool(r['picks']) for r in rows)} for category in ('地方','ばんえい')}
    # JRA data is observational only: do not merge into NAR paper-trade returns.
    jra_tables={x[0] for x in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    jra_count=c.execute('SELECT COUNT(*) FROM jra_odds_observations').fetchone()[0] if 'jra_odds_observations' in jra_tables else 0
    jra_races=c.execute('SELECT COUNT(DISTINCT race_id) FROM jra_odds_observations').fetchone()[0] if 'jra_odds_observations' in jra_tables else 0
    jra_recent=[dict(at=a,race_id=r,status=st,rows=n,message=m) for a,r,st,n,m in c.execute('SELECT at,race_id,status,rows,message FROM jra_fetches ORDER BY rowid DESC LIMIT 10')] if 'jra_fetches' in jra_tables else []
    totals['jra_odds_observations']=jra_count
    totals['jra_observed_races']=jra_races
    status=dict(generated_at=stamp(observed),scope='NAR収集・仮想検証 / JRAは指定レースのオッズ観測のみ',totals=dict(totals),fetches=[dict(at=a,kind=k,status=s,rows=n,message=m) for a,k,s,n,m in fetches],races=rows,limitations=['JRA中央競馬は未実装','地方の取得元CSVが空の場合はオッズ未取得','過去3走以上の馬の実績が揃った場合のみ履歴スコア方式。それ以外はオッズのみのベースライン。いずれも未検証のヒューリスティックで勝率向上は未証明','ばんえいは別分類。ただし収集できた場合のみ','確定払戻が未取得なら損益・回収率を確定しない','GitHub Actionsの実行時刻は保証されず発走前取得漏れがあり得る'])
    status['jra']={'observed_races':jra_races,'odds_observations':jra_count,'recent_fetches':jra_recent,'picks_supported':False,'payouts_supported':False,'auto_discovery_supported':False}
    status['limitations']=[x for x in status['limitations'] if x!='JRA中央競馬は未実装']
    status['limitations'].append('JRAはレースID指定の三連単オッズ観測のみ。全レース検出・予想・払戻照合は未実装')
    (DOCS/'status.json').write_text(json.dumps(status,ensure_ascii=False,indent=2),encoding='utf-8')
    def e(v):return html.escape(str(v))
    trs=''.join('<tr>'+''.join('<td>'+e(x)+'</td>' for x in (r['date'],r['venue'],r['race'],r['post'],r['name'],r['category'],r['picks'],r['status']))+'</tr>' for r in rows)
    logs=''.join('<tr>'+''.join('<td>'+e(x)+'</td>' for x in (a,k,s,n,m))+'</tr>' for a,k,s,n,m in fetches)
    htmlpage=f'''<!doctype html><html lang="ja"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>KEIBA CLOUD</title><style>body{{background:#0d1523;color:#edf2f7;font:15px system-ui,sans-serif;max-width:1100px;margin:auto;padding:20px}}h1{{font-size:28px}}.grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(160px,1fr));gap:12px}}.card{{background:#1b293b;border-radius:12px;padding:18px}}.big{{font-size:28px;font-weight:700}}.muted{{color:#a7b7c9}}table{{border-collapse:collapse;width:100%;font-size:13px}}td,th{{padding:9px;border-bottom:1px solid #34445a;text-align:left}}.scroll{{overflow-x:auto}}a{{color:#91c8ff}}.warn{{padding:12px;border:1px solid #a18442;border-radius:10px}}</style></head><body><h1>KEIBA CLOUD <small>integrated preview</small></h1><p class="muted">最終更新 {e(stamp(observed))} / 対象 2026/10/16〜11/01</p><div class="grid"><div class="card">JRAオッズ観測<div class="big">{jra_count:,}</div></div><div class="card">JRA観測レース<div class="big">{jra_races}</div></div><div class="card">地方・ばんえい登録<div class="big">{totals['races']}</div></div><div class="card">払戻確認レース<div class="big">{totals['payout_races']}</div></div><div class="card">発走前候補記録レース<div class="big">{totals['picked_races']}</div></div><div class="card">オッズ観測記録<div class="big">{totals['odds_observations']}</div></div><div class="card">確定済み仮想投資<div class="big">¥{totals['settled_stake']:,}</div></div><div class="card">確定済み仮想回収<div class="big">¥{totals['returns_confirmed']:,}</div></div><div class="card">確定済み仮想損益<div class="big">¥{totals['profit_confirmed']:,}</div></div><div class="card">確定済み回収率<div class="big">{str(totals['roi_confirmed_percent'])+'%' if totals['roi_confirmed_percent'] is not None else '未算出'}</div></div></div><p class="warn">JRAは指定レースのオッズ観測のみ。全レース自動検出・JRA予想・払戻照合は未対応。取得したデータだけ表示。事前記録がなければ的中扱いしません。候補は過去実績の簡易スコアまたはオッズのみの機械的テストであり、予想精度を保証しません。未確定レースを含む総損益は算出しません。</p><h2>レース一覧</h2><div class="scroll"><table><tr><th>日付</th><th>競馬場</th><th>R</th><th>発走</th><th>レース名</th><th>区分</th><th>候補数</th><th>状況</th></tr>{trs}</table></div><h2>取得履歴</h2><div class="scroll"><table><tr><th>日時</th><th>種類</th><th>状態</th><th>行数</th><th>詳細</th></tr>{logs}</table></div><p><a href="status.json">詳細JSON</a> | <a href="https://www.keiba.go.jp/">地方競馬情報サイト</a> | <a href="https://www.jra.go.jp/">JRA</a></p></body></html>'''
    # Mobile-first inspection and race lookup. Static only: no server-side prediction endpoint.
    mobile = r'''<!doctype html><html lang="ja"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>KEIBA CLOUD | 予想・検証</title><style>
    :root{color-scheme:dark}*{box-sizing:border-box}body{margin:0;background:#0c1423;color:#edf3ff;font:15px system-ui,sans-serif}main{max-width:850px;margin:auto;padding:20px 14px 60px}h1{font-size:25px;margin:0 0 6px}h2{font-size:20px;margin-top:28px}.sub,.muted{color:#a6b8d0}.grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:9px;margin:18px 0}.tile,.panel{background:#18263b;border:1px solid #31425a;border-radius:13px;padding:14px}.tile strong{display:block;font-size:24px;margin-top:5px}.controls{display:grid;grid-template-columns:1fr 1fr;gap:9px}input,select,button{width:100%;min-width:0;padding:12px;border:1px solid #506581;border-radius:9px;background:#101d30;color:#fff;font:inherit}button{cursor:pointer;background:#275d98}button:focus,input:focus,select:focus{outline:2px solid #7fb9ff}.race{margin:12px 0}.tag{font-size:12px;background:#31435b;padding:4px 7px;border-radius:6px}.line{display:flex;justify-content:space-between;gap:8px;align-items:center}.line strong{font-size:17px}.ticket{border-top:1px solid #34465e;padding:12px 0}.hit{color:#7ee5b1}.miss{color:#f6adad}.notice{padding:13px;border:1px solid #8b7849;border-radius:10px;color:#f3dca0;margin:16px 0}small{color:#afc0d6}p{line-height:1.65}details{margin-top:12px}summary{cursor:pointer;color:#9dccff;font-weight:700}#results:empty:after{content:'条件に合う記録がありません。';color:#a6b8d0}a{color:#a9d3ff}
    </style></head><body><main><h1>KEIBA CLOUD</h1><div class="sub">三連単の事前記録・的中照合・選定理由</div><div id="updated" class="muted"></div><div class="grid" id="stats"></div><div class="notice">これは仮想検証です。予想の優位性は未確認。発走後の結果を使って過去の予想を作ることはしません。JRAの予想は未対応です。</div><h2>レースを探す</h2><div class="controls"><label>競馬場<select id="venue"><option value="">すべて</option></select></label><label>レース番号<select id="race"><option value="">すべて</option></select></label><label>日付<input id="day" type="date"></label><label>表示<select id="filter"><option value="all">すべて</option><option value="picked">事前予想あり</option><option value="hit">的中</option><option value="pending">払戻待ち</option></select></label></div><h2>予想と結果</h2><div id="results"></div><p class="muted">レースを指定すると保存済みの事前候補を確認できます。新しいレースの即時予想は未実装です。候補がないレースに後付けの予想は表示しません。</p><p><a href="status.json">集計データ（JSON）</a></p></main><script>
    const data=__STATUS_JSON__;const races=data.races||[];const $=id=>document.getElementById(id);const yen=n=>n==null?'未確定':'¥'+Number(n).toLocaleString('ja-JP');const esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
    $('updated').textContent='最終更新：'+data.generated_at;const t=data.totals||{};const cards=[['対象レース',t.races],['予想記録レース',t.picked_races],['的中',t.hits],['確定回収率',t.roi_confirmed_percent==null?'未確定':t.roi_confirmed_percent+'%'],['確定済み投資',yen(t.settled_stake)],['確定払戻',yen(t.returns_confirmed)]];$('stats').innerHTML=cards.map(([k,v])=>'<div class="tile">'+esc(k)+'<strong>'+esc(v)+'</strong></div>').join('');
    [...new Set(races.map(r=>r.venue))].sort().forEach(v=>$('venue').add(new Option(v,v)));[...new Set(races.map(r=>r.race))].sort((a,b)=>a-b).forEach(v=>$('race').add(new Option(v+'R',v)));
    function draw(){const v=$('venue').value,n=$('race').value,d=$('day').value.replaceAll('-',''),f=$('filter').value;const selected=races.filter(r=>(!v||r.venue===v)&&(!n||String(r.race)===n)&&(!d||r.date===d)&&(f==='all'||f==='picked'&&r.picks>0||f==='hit'&&r.hit>0||f==='pending'&&r.picks>0&&!r.payout_confirmed));$('results').innerHTML=selected.map(r=>{const tickets=(r.ticket_details||[]).map(p=>'<div class="ticket"><div class="line"><strong>'+esc(p.ticket)+'</strong><span class="'+(p.hit===true?'hit':p.hit===false?'miss':'muted')+'">'+(p.hit===true?'的中':p.hit===false?'不的中':'結果待ち')+'</span></div><p>記録時オッズ '+esc(p.odds)+'倍 / 仮想 '+yen(p.stake)+' / 払戻 '+yen(p.return_yen)+'</p><small>記録：'+esc(p.captured)+'</small><p><b>選定理由：</b>'+esc(p.reason)+'</p></div>').join('');return '<article class="panel race"><div class="line"><strong>'+esc(r.venue)+' '+esc(r.race)+'R</strong><span class="tag">'+esc(r.status)+'</span></div><small>'+esc(r.date)+' / 発走 '+esc(r.post)+' / '+esc(r.category)+'</small><p>'+esc(r.name)+'</p><div class="line"><span>事前候補 '+esc(r.picks)+'点</span><span>仮想投資 '+yen(r.stake)+'</span></div><details><summary>買い目・選定理由・的中結果を見る</summary>'+(tickets||'<p class="muted">このレースの発走前予想記録はありません。</p>')+'</details></article>'}).join('')}
    ['venue','race','day','filter'].forEach(id=>$(id).addEventListener('change',draw));draw();
    </script></body></html>'''
    mobile=mobile.replace('__STATUS_JSON__',json.dumps(status,ensure_ascii=False).replace('<',r'\u003c').replace('>',r'\u003e').replace('&',r'\u0026'))
    (DOCS/'index.html').write_text(mobile,encoding='utf-8')
    print('dashboard:',DOCS/'index.html')

def main():
    p=argparse.ArgumentParser();p.add_argument('command',choices=['sync','report','test-fixture','import-history','history-report']);p.add_argument('--fixture');p.add_argument('--at',help='ISO8601 aware timestamp, fixture only');p.add_argument('--zip',help='NAR historical race ZIP');p.add_argument('--as-of',help='YYYYMMDD, historical cutoff')
    args=p.parse_args(); c=connect()
    try:
        if args.command=='sync': return sync(c)
        if args.command=='report':render(c);return 0
        if args.command=='import-history':
            if not args.zip: p.error('--zip required')
            print('Imported historical finish rows:',import_history_zip(c,args.zip));return 0
        if args.command=='history-report':
            day=args.as_of or now().strftime('%Y%m%d')
            if len(day)!=8 or not day.isdigit():p.error('--as-of must be YYYYMMDD')
            DOCS.mkdir(exist_ok=True)
            output=DOCS/'historical_rates.json'
            output.write_text(json.dumps({'as_of_exclusive':day,'minimum_starts':3,'method':'smoothed historical win rate (wins+1)/(starts+10)','horses':history_report(c,day)},ensure_ascii=False,indent=2),encoding='utf-8')
            print('Historical statistics:',output);return 0
        if args.command=='test-fixture':
            if not args.fixture: p.error('--fixture required')
            at=datetime.fromisoformat(args.at) if args.at else now()
            if at.tzinfo is None:p.error('--at must include timezone')
            return sync(c,args.fixture,at.astimezone(JST))
    finally:c.close()

if __name__=='__main__':sys.exit(main())
