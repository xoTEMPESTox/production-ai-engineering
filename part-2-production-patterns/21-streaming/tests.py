import unittest
from example import Streaming

class TestStreaming(unittest.TestCase):
    def test_placeholder(self) -> None:
        obj = Streaming()
        self.assertEqual(obj.run(), "Executing Streaming operations...")

if __name__ == "__main__":
    unittest.main()
