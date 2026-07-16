import unittest
from example import Redis

class TestRedis(unittest.TestCase):
    def test_placeholder(self) -> None:
        obj = Redis()
        self.assertEqual(obj.run(), "Executing Redis operations...")

if __name__ == "__main__":
    unittest.main()
