import io, sys, tempfile, unittest, zipfile
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import app

class HistoryTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.old=app.DB
        app.DB=Path(self.tmp.name)/'db.sqlite3'
        self.c=app.connect()
        app.DB=self.old
    def tearDown(self):
        self.c.close();self.tmp.cleanup()
    def test_history_cutoff_excludes_future(self):
        with self.c:
            for i,day in enumerate(['20261001','20261002','20261003','20261018']):
                self.c.execute('INSERT INTO history VALUES(?,?,?,?,?,?,?)',('帯広',day,1,1,'テスト馬','騎手',1 if i<3 else 4))
        r=app.history_report(self.c,'20261017')
        self.assertEqual(r[0]['starts'],3)
        self.assertEqual(r[0]['wins'],3)
    def test_history_import_and_no_backdating(self):
        csv='競馬場,競走年月日,レース番号,馬番,馬名,騎手名,着順\n帯広,20261001,1,1,テスト馬,テスト騎手,1\n'
        path=Path(self.tmp.name)/'history.zip'
        with zipfile.ZipFile(path,'w') as z:z.writestr('20261001_horselist.csv',csv.encode('utf-8-sig'))
        self.assertEqual(app.import_history_zip(self.c,path),1)
        self.assertEqual(self.c.execute('SELECT COUNT(*) FROM picks').fetchone()[0],0)
        self.assertEqual(self.c.execute('SELECT COUNT(*) FROM history').fetchone()[0],1)
    def test_duplicate_odds_ticket_one_pick(self):
        from datetime import datetime
        from zoneinfo import ZoneInfo
        at=datetime(2026,10,17,14,40,tzinfo=ZoneInfo('Asia/Tokyo'))
        race={'競馬場':'帯広','競走年月日':'20261017','レース番号':'1','発走時刻':'15:00'}
        odd={'競馬場':'帯広','競走年月日':'20261017','レース番号':'1','賭式':'３連単','番号1':'1','番号2':'2','番号3':'3','オッズ':'150'}
        app.ingest(self.c,{'racelist':[race],'odds':[odd,odd]},at,'test')
        self.assertEqual(self.c.execute('SELECT COUNT(*) FROM picks').fetchone()[0],1)

if __name__=='__main__': unittest.main()
