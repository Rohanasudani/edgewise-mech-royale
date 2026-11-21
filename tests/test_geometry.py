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

    def test_combat_policy_disengages_until_repaired(self):
        bot = BOT.RoyaleBot.__new__(BOT.RoyaleBot)
        bot.damage_until = 13.2
        bot.attacker = 5
        bot.disengaging = False
        target = {"id": 5, "kind": "mech", "pos": [80, 0, 0], "vel": [0, 0, 0]}
        obs = {"t": 10.0, "alive": 12, "self": {"pos": [0, 0, 0], "hp": 900, "max_hp": 1400}}
        engage, escape_fire, healing = bot._combat_policy(obs, target, 80.0)
        self.assertTrue(healing)
        self.assertFalse(engage)
        self.assertFalse(escape_fire)

        obs["self"]["hp"] = 1300
        engage, escape_fire, healing = bot._combat_policy(obs, target, 80.0)
        self.assertFalse(healing)
        self.assertTrue(engage)

    def test_combat_policy_allows_close_escape_fire(self):
        bot = BOT.RoyaleBot.__new__(BOT.RoyaleBot)
        bot.damage_until = 13.2
        bot.attacker = 5
        bot.disengaging = True
        target = {"id": 5, "kind": "mech", "pos": [25, 0, 0], "vel": [0, 0, 0]}
        obs = {"t": 10.0, "alive": 12, "self": {"pos": [0, 0, 0], "hp": 700, "max_hp": 1400}}
        engage, escape_fire, healing = bot._combat_policy(obs, target, 25.0)
        self.assertTrue(healing)
        self.assertTrue(engage)
        self.assertTrue(escape_fire)


if __name__ == "__main__":
    unittest.main()
