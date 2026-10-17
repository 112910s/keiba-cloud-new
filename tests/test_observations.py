import sys, tempfile, unittest
from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import app

class Observations(unittest.TestCase):
    def test_history_not_overwritten_and_no_backdated_picks(self):
        with tempfile.TemporaryDirectory() as tmp:
            original=app.DB
            try:
                app.DB=Path(tmp)/'db.sqlite3'
                c=app.connect()
            finally:
                app.DB=original
            race={'競馬場':'帯広','競走年月日':'20261017','レース番号':'2','発走時刻':'15:00'}
            odd={'競馬場':'帯広','競走年月日':'20261017','レース番号':'2','賭式':'３連単','番号1':'1','番号2':'2','番号3':'3','オッズ':'120'}
            t=datetime(2026,10,17,14,40,tzinfo=ZoneInfo('Asia/Tokyo'))
            app.ingest(c,{'racelist':[race],'odds':[odd]},t,'hash1')
            odd['オッズ']='160'
            from datetime import timedelta
            app.ingest(c,{'odds':[odd]},t+timedelta(minutes=10),'hash2')
            self.assertEqual(c.execute('SELECT COUNT(*) FROM odds_observations').fetchone()[0],2)
            self.assertEqual(c.execute('SELECT COUNT(*) FROM picks').fetchone()[0],1)
            self.assertEqual(c.execute('SELECT odds FROM picks').fetchone()[0],120)
            c.close()
if __name__=='__main__': unittest.main()
