import unittest
from jra_collector import parse_odds

class JraParserTest(unittest.TestCase):
    def test_valid(self):
        self.assertEqual(parse_odds({'data': {'odds': {'8': {'010203': ['6751.5', '', '834'], '010204': ['13477.1', '', '1265']}}}}), [('1-2-3', 6751.5), ('1-2-4', 13477.1)])
    def test_reject_invalid(self):
        with self.assertRaises(ValueError):
            parse_odds({'data': {'odds': {'8': {'010101': ['100']}}}})
    def test_missing(self):
        with self.assertRaises(ValueError):
            parse_odds({'data': {}})

if __name__ == '__main__': unittest.main()
