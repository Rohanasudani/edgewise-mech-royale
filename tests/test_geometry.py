import importlib.util
import math
from pathlib import Path
import unittest


SPEC = importlib.util.spec_from_file_location("mech_bot", Path(__file__).parents[1] / "bot.py")
BOT = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(BOT)


class GeometryTests(unittest.TestCase):
    def test_wrap(self):
        self.assertAlmostEqual(BOT.wrap(3 * math.pi), -math.pi)
        self.assertAlmostEqual(BOT.angle_diff(0.1, 2 * math.pi + 0.1), 0.0)

    def test_polygon(self):
        square = [0, 0, 10, 0, 10, 10, 0, 10]
        self.assertTrue(BOT.point_in_ring(5, 5, square))
        self.assertFalse(BOT.point_in_ring(15, 5, square))
        self.assertTrue(BOT.near_ring_edge(10.5, 5, square, 1.0))

    def test_unit(self):
        self.assertEqual(BOT.unit((0, 0)), (1.0, 0.0))
        x, y = BOT.unit((3, 4))
        self.assertAlmostEqual(x, 0.6)
        self.assertAlmostEqual(y, 0.8)

    def test_ring_centroid_and_closest_boundary_point(self):
        square = [10, 10, 30, 10, 30, 30, 10, 30]
        self.assertEqual(BOT.ring_centroid(square), (20.0, 20.0))
        self.assertEqual(BOT.closest_point_on_ring(0, 20, square), (10.0, 20.0))

    def test_escape_roof_selects_reachable_interior_target(self):
        arena = BOT.Arena.__new__(BOT.Arena)
        arena.ok = True
        arena.CELL = 30.0
        arena.max_step = 1.2
        arena.prisms = [{
            "kind": "building",
            "base": 0.0,
            "top": 12.0,
            "ring": [10, 10, 30, 10, 30, 30, 10, 30],
            "holes": [],
            "bbox": (10, 10, 30, 30),
            "interior": (20.0, 20.0),
            "clearance": 10.0,
        }]
        arena.cells = {(0, 0): [0], (1, 0): [0], (0, 1): [0], (1, 1): [0]}
        goal = arena.escape_roof([0, 20, 0], 100.0, 2.5, (0.0, 0.0, 200.0))
        self.assertIsNotNone(goal)
        self.assertEqual(goal["top"], 12.0)
        self.assertTrue(BOT.point_in_ring(goal["target"][0], goal["target"][1], arena.prisms[0]["ring"]))


if __name__ == "__main__":
    unittest.main()
