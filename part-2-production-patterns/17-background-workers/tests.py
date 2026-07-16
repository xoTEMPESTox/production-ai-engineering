import unittest
from example import Backgroundworkers

class TestBackgroundworkers(unittest.TestCase):
    def test_placeholder(self) -> None:
        obj = Backgroundworkers()
        self.assertEqual(obj.run(), "Executing Background Workers operations...")

if __name__ == "__main__":
    unittest.main()
