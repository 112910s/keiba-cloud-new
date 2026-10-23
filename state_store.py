"""Persist SQLite as validated, small compressed Git blobs (stdlib only)."""
import argparse, hashlib, json, sqlite3, zlib
from pathlib import Path
DB=Path('state/keiba.sqlite3'); ARCH=Path('state/archive'); CHUNK=20*1024*1024

def save():
    DB.parent.mkdir(parents=True,exist_ok=True)
    if not DB.exists(): raise SystemExit('Database missing; refusing to overwrite archive')
    ARCH.mkdir(parents=True,exist_ok=True)
    snap=DB.with_suffix('.snapshot.sqlite3')
    try:
        with sqlite3.connect(DB) as source, sqlite3.connect(snap) as target: source.backup(target)
        raw=snap.read_bytes(); compressed=zlib.compress(raw,9)
        chunks=[compressed[i:i+CHUNK] for i in range(0,len(compressed),CHUNK)]
        if not chunks: raise ValueError('empty compressed archive')
        for i,part in enumerate(chunks): (ARCH/f'part-{i:04d}.bin').write_bytes(part)
        manifest={'format':1,'size':len(raw),'sha256':hashlib.sha256(raw).hexdigest(),'parts':len(chunks),'compressed_sha256':hashlib.sha256(compressed).hexdigest()}
        (ARCH/'manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
        for old in ARCH.glob('part-*.bin'):
            if int(old.stem.split('-')[1])>=len(chunks):old.unlink()
        print(f'Saved database {len(raw)} bytes as {len(compressed)} compressed bytes in {len(chunks)} part(s)')
    finally: snap.unlink(missing_ok=True)

def restore():
    m=ARCH/'manifest.json'
    if not m.exists():
        print('No archive yet: using existing database if present')
        return
    info=json.loads(m.read_text()); parts=[]
    for i in range(info['parts']):parts.append((ARCH/f'part-{i:04d}.bin').read_bytes())
    compressed=b''.join(parts)
    if hashlib.sha256(compressed).hexdigest()!=info['compressed_sha256']:raise ValueError('Compressed archive checksum mismatch')
    raw=zlib.decompress(compressed)
    if len(raw)!=info['size'] or hashlib.sha256(raw).hexdigest()!=info['sha256']:raise ValueError('Database checksum mismatch')
    DB.parent.mkdir(parents=True,exist_ok=True)
    temp=DB.with_suffix('.restore.tmp');temp.write_bytes(raw)
    try:
        with sqlite3.connect(temp) as c:
            if c.execute('PRAGMA integrity_check').fetchone()[0]!='ok':raise ValueError('SQLite integrity check failed')
        temp.replace(DB)
    finally:temp.unlink(missing_ok=True)
    print(f'Restored database {len(raw)} bytes')

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('action',choices=['save','restore']);args=parser.parse_args()
    (save if args.action=='save' else restore)()
