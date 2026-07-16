import unittest
from example import Monitoring

class TestMonitoring(unittest.TestCase):
    def test_placeholder(self) -> None:
        obj = Monitoring()
        self.assertEqual(obj.run(), "Executing Monitoring operations...")

if __name__ == "__main__":
    unittest.main()
