import unittest
from example import Costtracking

class TestCosttracking(unittest.TestCase):
    def test_placeholder(self) -> None:
        obj = Costtracking()
        self.assertEqual(obj.run(), "Executing Cost Tracking operations...")

if __name__ == "__main__":
    unittest.main()
