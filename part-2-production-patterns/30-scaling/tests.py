import unittest
from example import Scaling

class TestScaling(unittest.TestCase):
    def test_placeholder(self) -> None:
        obj = Scaling()
        self.assertEqual(obj.run(), "Executing Scaling operations...")

if __name__ == "__main__":
    unittest.main()
