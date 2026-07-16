import unittest
from example import Schedulinglongrunningtasks

class TestSchedulinglongrunningtasks(unittest.TestCase):
    def test_placeholder(self) -> None:
        obj = Schedulinglongrunningtasks()
        self.assertEqual(obj.run(), "Executing Scheduling Long Running Tasks operations...")

if __name__ == "__main__":
    unittest.main()
