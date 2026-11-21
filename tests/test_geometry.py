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
        arena.max_slope = math.tan(math.radians(50))
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
        self.assertGreater(len(goal["waypoints"]), 1)
        self.assertFalse(arena._walkable([0, 20, 0], goal["target"], 2.5, 9.0))
        for start, end in zip(([0, 20, 0], *goal["waypoints"]), goal["waypoints"]):
            self.assertTrue(arena._walkable(start, end, 2.5, 9.0))

    def test_cover_behavior_routes_then_holds_after_losing_sight(self):
        arena = BOT.Arena.__new__(BOT.Arena)
        arena.ok = True
        arena.CELL = 30.0
        arena.max_step = 1.2
        arena.max_slope = math.tan(math.radians(50))
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
        bot.last_combat = 2.0
        bot.cover_goal = None
        bot.cover_cooldown = -1.0
        enemy = {"id": 7, "kind": "mech", "pos": [0, 20, 0]}
        me = {"pos": [0, 20, 0], "grounded": True, "hp": 1000, "max_hp": 1400,
              "stats": {"radius": 2.5, "height": 9.0, "regen_delay": 5.0}}
        obs = {"t": 0.0, "self": me, "mechs": [enemy]}
        route = bot._cover_behavior(obs, enemy, False, 200.0, (20.0, 20.0, 200.0))
        self.assertIsNotNone(route)
        self.assertFalse(route["hold"])

        goal = bot.cover_goal
        for step, waypoint in enumerate(goal["waypoints"][:-1], start=1):
            me["pos"] = [*waypoint, 0.0]
            obs["t"] = float(step)
            self.assertFalse(bot._cover_behavior(obs, enemy, False, 200.0, (20.0, 20.0, 200.0))["hold"])
        obs["mechs"] = []
        me["pos"] = [*goal["target"], 0.0]
        obs["t"] = 3.0
        hold = bot._cover_behavior(obs, None, False, 200.0, (20.0, 20.0, 200.0))
        self.assertTrue(hold["hold"])
        obs["t"] = 7.0
        self.assertTrue(bot._cover_behavior(obs, None, False, 200.0, (20.0, 20.0, 200.0))["hold"])
        obs["t"] = 8.5
        self.assertIsNone(bot._cover_behavior(obs, None, False, 200.0, (20.0, 20.0, 200.0)))

    def test_cover_depth_ignores_walls_below_the_firing_line(self):
        prism = {"ring": [10, 10, 30, 10, 30, 30, 10, 30], "holes": [], "base": 0, "top": 12}
        self.assertGreater(BOT.Arena._cover_depth(prism, [-10, 20, 0], [36, 20, 0]), 8)
        self.assertEqual(BOT.Arena._cover_depth(prism, [-10, 20, 75], [36, 20, 0]), 0)


if __name__ == "__main__":
    unittest.main()
