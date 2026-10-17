import sys, tempfile, unittest
from pathlib import Path
from datetime import datetime
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import app

class AutoHistoryTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        old=app.DB; app.DB=Path(self.tmp.name)/'db.sqlite3'
        self.c=app.connect(); app.DB=old
    def tearDown(self):
        self.c.close(); self.tmp.cleanup()
    def test_only_prior_confirmed_results_promoted_and_idempotent(self):
        with self.c:
            for day,finish in [('20261015','1'),('20261016','2'),('20261017','3')]:
                self.c.execute('INSERT INTO entries VALUES(?,?,?,?,?,?,?,?)',('佐賀',day,1,1,'馬A','騎手',finish,'now'))
            for day in ('20261015','20261017'):
                self.c.execute('INSERT INTO payouts VALUES(?,?,?,?,?,?)',('佐賀',day,1,'1-2-3',12000,'now'))
        when=datetime(2026,10,17,tzinfo=app.JST)
        self.assertEqual(app.promote_settled_history(self.c,when),1)
        self.assertEqual(app.promote_settled_history(self.c,when),0)
        self.assertEqual(self.c.execute('SELECT day FROM history').fetchall(),[('20261015',)])
        self.assertEqual(app.promote_settled_history(self.c,datetime(2026,10,18,tzinfo=app.JST)),1)
if __name__=='__main__': unittest.main()
