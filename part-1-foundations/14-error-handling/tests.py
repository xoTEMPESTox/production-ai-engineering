import unittest
from example import Errorhandling

class TestErrorhandling(unittest.TestCase):
    def test_placeholder(self) -> None:
        obj = Errorhandling()
        self.assertEqual(obj.run(), "Executing Error Handling operations...")

if __name__ == "__main__":
    unittest.main()
