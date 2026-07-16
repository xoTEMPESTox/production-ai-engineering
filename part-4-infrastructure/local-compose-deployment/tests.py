import unittest
from example import Localcomposedeployment

class TestLocalcomposedeployment(unittest.TestCase):
    def test_placeholder(self) -> None:
        obj = Localcomposedeployment()
        self.assertEqual(obj.run(), "Executing Local Compose Deployment operations...")

if __name__ == "__main__":
    unittest.main()
