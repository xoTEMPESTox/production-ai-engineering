import unittest
from example import Security

class TestSecurity(unittest.TestCase):
    def test_placeholder(self) -> None:
        obj = Security()
        self.assertEqual(obj.run(), "Executing Security operations...")

if __name__ == "__main__":
    unittest.main()
