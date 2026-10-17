import io, sqlite3, unittest, sys, tempfile, zipfile
from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import app

class Tests(unittest.TestCase):
    def setUp(self):
        self.c=sqlite3.connect(':memory:')
        # Build schema using app.connect on temporary file (same schema as production)
        self.tmp=tempfile.TemporaryDirectory(); old=app.DB
        app.DB=Path(self.tmp.name)/'test.sqlite3';self.db=app.connect();app.DB=old
        self.t=datetime(2026,10,17,14,40,tzinfo=ZoneInfo('Asia/Tokyo'))
    def tearDown(self):self.db.close();self.c.close();self.tmp.cleanup()
    def test_prestart_lock_and_payout(self):
        races=[{'競馬場':'東京地方','競走年月日':'20261017','レース番号':'1','発走時刻':'15:00','レース名':'テスト'}]
        odds=[{'競馬場':'東京地方','競走年月日':'20261017','レース番号':'1','賭式':'３連単','番号1':'1','番号2':'2','番号3':'3','オッズ':'150'}]
        app.ingest(self.db,{'racelist':races,'odds':odds},self.t,'sha')
        self.assertEqual(self.db.execute('select count(*) from picks').fetchone()[0],1)
        later=datetime(2026,10,17,16,0,tzinfo=ZoneInfo('Asia/Tokyo'))
        app.ingest(self.db,{'odds':odds},later,'sha2')
        self.assertEqual(self.db.execute('select count(*) from picks').fetchone()[0],1)
        app.ingest(self.db,{'payback':[{'競馬場':'東京地方','競走年月日':'20261017','レース番号':'1','３連単組番馬番1':'1','３連単組番馬番2':'2','３連単組番馬番3':'3','３連単払戻金（円）':'15000'}]},later,'sha3')
        self.assertEqual(self.db.execute('select yen from payouts').fetchone()[0],15000)
    def test_no_retroactive_picks(self):
        late=datetime(2026,10,17,16,0,tzinfo=ZoneInfo('Asia/Tokyo'))
        app.ingest(self.db,{'racelist':[{'競馬場':'A','競走年月日':'20261017','レース番号':'1','発走時刻':'15:00'}], 'odds':[{'競馬場':'A','競走年月日':'20261017','レース番号':'1','賭式':'３連単','番号1':'1','番号2':'2','番号3':'3','オッズ':'250'}]},late,'sha')
        self.assertEqual(self.db.execute('select count(*) from picks').fetchone()[0],0)
    def test_header_only_zip(self):
        buf=io.BytesIO()
        with zipfile.ZipFile(buf,'w') as z:z.writestr('20261017_odds.csv','競馬場,競走年月日,レース番号,賭式,番号1,番号2,番号3,オッズ\n')
        self.assertEqual(app.parse_archive(buf.getvalue()),{'odds':[]})

if __name__=='__main__':unittest.main()
