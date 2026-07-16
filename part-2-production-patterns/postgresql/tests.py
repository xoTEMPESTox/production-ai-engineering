import unittest
from example import Postgresql

class TestPostgresql(unittest.TestCase):
    def test_placeholder(self) -> None:
        obj = Postgresql()
        self.assertEqual(obj.run(), "Executing Postgresql operations...")

if __name__ == "__main__":
    unittest.main()
