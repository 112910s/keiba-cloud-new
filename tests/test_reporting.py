import tempfile, unittest, sys, json
from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import app

class ReportingTests(unittest.TestCase):
    def test_report_only_counts_settled_stake(self):
        with tempfile.TemporaryDirectory() as tmp:
            old_db,old_docs=app.DB,app.DOCS
            try:
                app.DB=Path(tmp)/'test.db';app.DOCS=Path(tmp)/'docs'
                c=app.connect()
                at=datetime(2026,10,17,14,40,tzinfo=ZoneInfo('Asia/Tokyo'))
                base={'競馬場':'帯広','競走年月日':'20261017','レース番号':'1'}
                app.ingest(c,{'racelist':[dict(base,発走時刻='15:00')], 'odds':[dict(base,賭式='３連単',番号1='1',番号2='2',番号3='3',オッズ='150')]},at,'hash')
                app.render(c,at)
                status=json.loads((app.DOCS/'status.json').read_text())
                self.assertEqual(status['totals']['unsettled_picked_races'],1)
                self.assertEqual(status['totals']['settled_stake'],0)
                self.assertIsNone(status['totals']['roi_confirmed_percent'])
                app.ingest(c,{'payback':[dict(base,**{'３連単組番馬番1':'1','３連単組番馬番2':'2','３連単組番馬番3':'3','３連単払戻金（円）':'15000'})]},at,'hash2')
                app.render(c,at)
                status=json.loads((app.DOCS/'status.json').read_text())
                self.assertEqual(status['totals']['settled_stake'],1000)
                self.assertEqual(status['totals']['returns_confirmed'],150000)
                self.assertEqual(status['totals']['roi_confirmed_percent'],15000.0)
                c.close()
            finally:
                app.DB,app.DOCS=old_db,old_docs
