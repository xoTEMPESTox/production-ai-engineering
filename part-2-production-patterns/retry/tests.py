import unittest
from example import Retry

class TestRetry(unittest.TestCase):
    def test_placeholder(self) -> None:
        obj = Retry()
        self.assertEqual(obj.run(), "Executing Retry operations...")

if __name__ == "__main__":
    unittest.main()
