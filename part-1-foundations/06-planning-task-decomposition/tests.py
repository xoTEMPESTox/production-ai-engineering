import unittest
from example import Planningtaskdecomposition

class TestPlanningtaskdecomposition(unittest.TestCase):
    def test_placeholder(self) -> None:
        obj = Planningtaskdecomposition()
        self.assertEqual(obj.run(), "Executing Planning Task Decomposition operations...")

if __name__ == "__main__":
    unittest.main()
