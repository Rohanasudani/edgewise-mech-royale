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


class OpeningTests(unittest.TestCase):
    def setUp(self):
        self.arena = BOT.Arena.__new__(BOT.Arena)
        self.arena.ok = True
        self.arena.bounds = None
        self.arena.max_step = 1.2
        self.arena.max_slope = math.tan(math.radians(50))
        self.arena.ground = lambda x, y: 0.0
        self.arena.prisms = []
        self.arena.cells = {}
        self.bot = BOT.RoyaleBot.__new__(BOT.RoyaleBot)
        self.bot.arena = self.arena
        self.bot.pickup_radius = 4.0
        self.bot.opening_pickups = {}
        self.bot.opening_plan = None
        self.bot.opening_retry_at = 0.0
        self.bot.damage_until = -1.0
        self.bot.damage_bearing = None
        self.me = {
            "pos": [0, 0, 0], "hp": 1400, "max_hp": 1400, "grounded": True, "stats": {},
            "energy": 100, "weapons": [{"kind": "gun"}, None],
        }
        self.pickup = {"id": 8001, "kind": "laser", "pos": [38.0, 0.0, 0.0]}
        self.obs = {
            "t": 0.0, "self": self.me, "pickups": [self.pickup], "events": [],
            "zone": {"center": [0, 0], "radius": 300.0},
        }

    def test_routes_around_a_solid_wall(self):
        self.arena.prisms = [{
            "ring": [12, -7, 22, -7, 22, 7, 12, 7], "holes": [],
            "bbox": (12, -7, 22, 7), "base": 0.0, "top": 13.0,
        }]
        self.arena.cells = {(0, -1): [0], (0, 0): [0]}
        route = self.arena.pickup_route(self.me["pos"], self.pickup["pos"], 2.9, 9.0)
        self.assertIsNotNone(route)
        self.assertGreater(len(route), 1)
        self.assertEqual(route[-1], (38.0, 0.0))
        for start, end in zip((self.me["pos"], *route[:-1]), route):
            self.assertTrue(self.arena.walkable_leg(start, end, 2.9, 9.0))

    def test_remembers_pickup_and_yields_to_projectile(self):
        move, boost, take = self.bot._opening_move(self.obs, [], False, False)
        self.assertEqual(move, [1.0, 0.0])
        self.assertTrue(boost)
        self.assertIsNone(take)
        self.obs.update(t=0.2, pickups=[])
        self.assertIsNone(self.bot._opening_move(self.obs, [], True, False))
        self.me["pos"] = [35.0, 0.0, 0.0]
        self.assertEqual(self.bot._opening_move(self.obs, [], False, False)[2], self.pickup)
        self.obs["events"] = [{"type": "take_failed", "id": 8001, "reason": "gone"}]
        self.assertIsNone(self.bot._opening_move(self.obs, [], False, False))

    def test_skips_weapon_beside_enemy(self):
        enemy = {"pos": [48.0, 0.0, 0.0]}
        self.assertIsNone(self.bot._opening_move(self.obs, [enemy], False, False))


class CombatTests(unittest.TestCase):
    def setUp(self):
        self.bot = BOT.RoyaleBot.__new__(BOT.RoyaleBot)
        self.bot.arena = BOT.Arena.__new__(BOT.Arena)
        self.bot.arena.ok = False
        self.bot.damage_until = -1.0
        self.bot.damage_bearing = None
        self.bot.scan_yaw = 0.0
        self.bot.attacker = None
        self.gun = {"kind": "gun", "ammo": 12, "stats": {"speed": 252.0, "range": 230.0}}
        self.mortar = {"kind": "mortar", "cooldown": 2.0, "stats": {
            "speed": 72.0, "gravity": 22.0, "splash_radius": 7.0,
        }}
        self.me = {"pos": [0, 0, 0], "stats": {}, "weapons": [self.gun, self.mortar]}
        self.target = {"id": 7, "kind": "mech", "pos": [75, 0, 0], "vel": [0, 0, 0]}
        self.obs = {"self": self.me, "t": 1.0}

    def test_gun_takes_over_a_cooling_mortar(self):
        self.me["aim"] = self.bot._direct_aim(self.me, self.target, 252.0)
        aim, fire, reload_now = self.bot._combat(self.obs, self.target)
        self.assertEqual(aim, self.me["aim"])
        self.assertEqual(fire, [True, False])
        self.assertFalse(reload_now)

    def test_mortar_fires_when_ready_and_clear_of_own_splash(self):
        self.mortar["cooldown"] = 0.0
        self.me["aim"] = self.bot._mortar_aim(self.me, self.target, self.mortar)
        self.assertEqual(self.bot._combat(self.obs, self.target)[1], [False, True])
        self.target["pos"] = [12, 0, 0]
        self.me["aim"] = self.bot._direct_aim(self.me, self.target, 252.0)
        self.assertEqual(self.bot._combat(self.obs, self.target)[1], [True, False])

    def test_laser_tracks_before_releasing_charge(self):
        laser = {"kind": "laser", "charge": 0.75, "stats": {"charge": 0.75, "range": 280.0}}
        self.me["weapons"][1] = laser
        self.me["aim"] = [1.0, 0.0]
        self.assertEqual(self.bot._combat(self.obs, self.target)[1], [False, True])
        self.me["aim"] = self.bot._direct_aim(self.me, self.target)
        self.assertEqual(self.bot._combat(self.obs, self.target)[1], [True, False])

    def test_tops_up_magazine_during_lull(self):
        self.gun["ammo"] = 6
        self.assertTrue(self.bot._combat(self.obs, None)[2])
        self.bot.damage_until = 4.0
        self.assertFalse(self.bot._combat(self.obs, None)[2])

    def set_up_wall(self, top):
        arena = self.bot.arena
        arena.ok = True
        arena.ground = lambda x, y: 0.0
        arena.prisms = [{
            "base": 0.0, "top": top,
            "ring": [35, -5, 45, -5, 45, 5, 35, 5],
            "holes": [], "bbox": (35.0, -5.0, 45.0, 5.0),
        }]
        arena.cells = {(1, -1): [0], (1, 0): [0]}

    def test_does_not_fire_at_a_tracked_enemy_behind_a_wall(self):
        self.set_up_wall(20.0)
        self.mortar["cooldown"] = 0.0
        self.me["aim"] = self.bot._direct_aim(self.me, self.target, 252.0)
        self.assertEqual(self.bot._combat(self.obs, self.target)[1], [False, False])

    def test_low_wall_blocks_gun_but_not_mortar_arc(self):
        self.set_up_wall(8.0)
        self.mortar["cooldown"] = 0.0
        self.assertFalse(self.bot.arena.shot_clear(*self.bot._direct_line(self.me, self.target, 252.0)))
        self.me["aim"] = self.bot._mortar_aim(self.me, self.target, self.mortar)
        self.assertIsNotNone(self.me["aim"])
        self.assertEqual(self.bot._combat(self.obs, self.target)[1], [False, True])

    def test_selects_an_exposed_opponent_over_a_blocked_one(self):
        self.set_up_wall(20.0)
        exposed = dict(self.target, id=8, pos=[75, 30, 0])
        self.assertEqual(self.bot._primary_enemy(self.obs, [self.target, exposed]), exposed)


if __name__ == "__main__":
    unittest.main()
