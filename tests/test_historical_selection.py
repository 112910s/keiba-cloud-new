import sys, tempfile, unittest
from pathlib import Path
from datetime import datetime
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import app

class HistoricalSelectionTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        original=app.DB
        app.DB=Path(self.temp.name)/'test.sqlite3'
        self.c=app.connect()
        app.DB=original
    def tearDown(self):
        self.c.close();self.temp.cleanup()
    def test_future_results_excluded_and_method_labeled(self):
        race=('帯広','20261017',1)
        entries=[{'競馬場':'帯広','競走年月日':'20261017','レース番号':'1','馬番':str(i),'馬名':f'馬{i}'} for i in range(1,5)]
        with self.c:
            for i in range(1,5):
                for d in ('20261001','20261002','20261003'):
                    self.c.execute('INSERT INTO history VALUES(?,?,?,?,?,?,?)',('帯広',d,1,i,f'馬{i}','騎手',1 if i==1 else 4))
                self.c.execute('INSERT INTO history VALUES(?,?,?,?,?,?,?)',('帯広','20261018',1,i,f'馬{i}','騎手',1))
        scores=app.historical_scores(self.c,race,entries,'20261017')
        self.assertEqual(len(scores),4)
        self.assertGreater(scores[1],scores[2])
        tickets=[(f'{a}-{b}-{c}',120+a+b+c) for a in range(1,5) for b in range(1,5) for c in range(1,5) if len({a,b,c})==3]
        selected,method=app.choose_tickets(tickets,scores)
        self.assertEqual(method,'historical_heuristic_v1')
        self.assertEqual(len(selected),10)
    def test_insufficient_history_falls_back(self):
        picks,method=app.choose_tickets([('1-2-3',150)],{})
        self.assertEqual(method,'odds_only_baseline_v1')
        self.assertEqual(len(picks),1)
if __name__=='__main__':unittest.main()
