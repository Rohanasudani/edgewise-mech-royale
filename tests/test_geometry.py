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

    def test_ring_winding(self):
        square = [10, 10, 30, 10, 30, 30, 10, 30]
        clockwise = [10, 30, 30, 30, 30, 10, 10, 10]
        self.assertGreater(BOT.ring_signed_area2(square), 0.0)
        self.assertLess(BOT.ring_signed_area2(clockwise), 0.0)

    def test_cover_target_puts_building_between_mech_and_threat(self):
        arena = BOT.Arena.__new__(BOT.Arena)
        arena.ok = True
        arena.CELL = 30.0
        arena.max_step = 1.2
        ring = [10, 10, 30, 10, 30, 30, 10, 30]
        arena.prisms = [{
            "kind": "building",
            "base": 0.0,
            "top": 12.0,
            "ring": ring,
            "holes": [],
            "bbox": (10, 10, 30, 30),
            "cover_edges": BOT.Arena._cover_edges(ring),
        }]
        arena.cells = {(0, 0): [0], (1, 0): [0], (0, 1): [0], (1, 1): [0]}
        arena.ground = lambda x, y: 0.0
        goal = arena.cover_target([0, 20, 0], [-80, 20, 0], 2.5, 9.0, (20.0, 20.0, 200.0))
        self.assertIsNotNone(goal)
        self.assertFalse(BOT.point_in_ring(goal["target"][0], goal["target"][1], ring))
        self.assertGreater(goal["target"][0], 30.0)
        self.assertGreaterEqual(goal["depth"], 8.0)

    def test_cover_behavior_routes_then_holds_after_losing_sight(self):
        arena = BOT.Arena.__new__(BOT.Arena)
        arena.ok = True
        arena.CELL = 30.0
        arena.max_step = 1.2
        ring = [10, 10, 30, 10, 30, 30, 10, 30]
        arena.prisms = [{
            "kind": "building",
            "base": 0.0,
            "top": 12.0,
            "ring": ring,
            "holes": [],
            "bbox": (10, 10, 30, 30),
            "cover_edges": BOT.Arena._cover_edges(ring),
        }]
        arena.cells = {(0, 0): [0], (1, 0): [0], (0, 1): [0], (1, 1): [0]}
        arena.ground = lambda x, y: 0.0

        bot = BOT.RoyaleBot.__new__(BOT.RoyaleBot)
        bot.arena = arena
        bot.attacker = 7
        bot.damage_bearing = 0.0
        bot.damage_source = [0, 20, 0]
        bot.damage_until = 3.2
        bot.cover_goal = None
        bot.cover_cooldown = -1.0
        enemy = {"id": 7, "kind": "mech", "pos": [0, 20, 0]}
        me = {"pos": [0, 20, 0], "stats": {"radius": 2.5, "height": 9.0}}
        obs = {"t": 0.0, "self": me, "mechs": [enemy]}
        route = bot._cover_behavior(obs, enemy, False, 200.0, (20.0, 20.0, 200.0))
        self.assertIsNotNone(route)
        self.assertFalse(route["hold"])

        me["pos"] = [bot.cover_goal["target"][0], bot.cover_goal["target"][1], 0.0]
        obs["t"] = 1.0
        obs["mechs"] = []
        hold = bot._cover_behavior(obs, None, False, 200.0, (20.0, 20.0, 200.0))
        self.assertTrue(hold["hold"])


if __name__ == "__main__":
    unittest.main()
