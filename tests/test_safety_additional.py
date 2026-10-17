import unittest
import tempfile
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import app
from datetime import datetime

class SafetyTests(unittest.TestCase):
    def test_race_datetime_invalid(self):
        self.assertIsNone(app.race_datetime('20261017','25:99'))
    def test_csv_header_only_is_empty(self):
        self.assertEqual(app.read_csv('競馬場,競走年月日,レース番号\n'.encode('utf-8')),[])
    def test_empty_ticket_payout_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            old=app.DB
            try:
                app.DB=Path(d)/'db.sqlite3'
                c=app.connect()
                app.ingest(c,{'payback':[{'競馬場':'帯広','競走年月日':'20261017','レース番号':'1','３連単組番馬番1':'','３連単組番馬番2':'2','３連単組番馬番3':'3','３連単払戻金（円）':'1000'}]},datetime(2026,10,17,12,tzinfo=app.JST),'hash',False)
                self.assertEqual(c.execute('SELECT COUNT(*) FROM payouts').fetchone()[0],0)
                c.close()
            finally: app.DB=old

if __name__=='__main__':unittest.main()
