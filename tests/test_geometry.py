import importlib.util
import math
from collections import deque
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

    def test_ground_route_finds_a_way_around_a_wall(self):
        class FlatArena:
            ok = True
            bounds = {"minX": -35, "maxX": 35, "minY": -35, "maxY": 35}

            @staticmethod
            def ground(x, y):
                return 0.0

            @staticmethod
            def blocked(x, y, z, radius, height):
                return abs(x) < 4 + radius and abs(y) < 17 + radius

            def ground_leg_clear(self, start, end, radius, height):
                for i in range(1, 11):
                    x, y, z = (a + (b - a) * i / 10 for a, b in zip(start, end))
                    if self.blocked(x, y, z, radius, height):
                        return False
                return True

        arena = FlatArena()
        start, goal = (-21, 0, 0), (21, 0, 0)
        route = BOT.GroundRoutes(arena).route(start, goal, 5)
        self.assertFalse(arena.ground_leg_clear(start, goal, 3, 9))
        self.assertGreaterEqual(max(abs(point[1]) for point in route), 21)
        self.assertLessEqual(BOT.distance2(route[-1], goal), 5)


class EvasionTests(unittest.TestCase):
    def setUp(self):
        self.bot = BOT.RoyaleBot.__new__(BOT.RoyaleBot)
        self.bot.cfg = {"weapons": {"mortar": {"gravity": 22.0, "splash_radius": 7.0}}}
        self.bot.arena = BOT.Arena.__new__(BOT.Arena)
        self.bot.arena.ok = False
        self.bot.last_move = (0.0, 0.0)
        self.bot.turn_sign = -1.0
        self.me = {"pos": [0, 0, 0], "vel": [0, 0, 0], "grounded": True, "stats": {}}

    def dodge(self, origin, velocity, kind="gun"):
        obs = {"self": self.me, "projectiles": [{"kind": kind, "pos": origin, "vel": velocity}]}
        return self.bot._projectile_dodge(obs)

    def test_dodges_away_from_both_sides_of_the_shot(self):
        for side in (-1, 1):
            with self.subTest(side=side):
                move, danger = self.dodge([-50, side * 1.5, 5], [250, 0, 0])
                self.assertTrue(danger)
                self.assertLess(move[1] * side, 0)

    def test_ignores_a_shot_well_above_body(self):
        self.assertFalse(self.dodge([-50, 1, 21], [250, 0, 0])[1])

    def test_accounts_for_moving_into_a_shot(self):
        self.me["vel"] = [0, 18, 0]
        move, danger = self.dodge([-100, 8, 5], [250, 0, 0])
        self.assertTrue(danger)
        self.assertLess(move[1], 0)

    def test_dodges_predicted_mortar_landing(self):
        move, danger = self.dodge([-24, 4, 14], [40, 0, -20], "mortar")
        self.assertTrue(danger)
        self.assertLess(move[1], 0)

    def test_ignores_nearby_mortar_moving_toward_distant_impact(self):
        self.assertFalse(self.dodge([5, 0, 22], [50, 0, -4], "mortar")[1])

    def test_considers_a_second_shot_when_steering(self):
        self.me["weapons"] = []
        heading = (1, 0)
        obs = {"self": self.me, "projectiles": [
            {"kind": "gun", "pos": [-90, -4, 5], "vel": [252, 0, 0]},
        ]}
        self.assertEqual(self.bot._steer_around_shots(obs, heading, heading, False, True), heading)
        obs["projectiles"].append({"kind": "gun", "pos": [5, -90, 5], "vel": [0, 252, 0]})
        move = self.bot._steer_around_shots(obs, heading, heading, False, True)
        self.assertGreater(move[1], 0.8)

    def test_shot_forecast_preserves_urgent_zone_progress(self):
        self.me["weapons"] = []
        heading = (0, -1)
        obs = {"self": self.me, "projectiles": [
            {"kind": "gun", "pos": [-90, -4, 5], "vel": [252, 0, 0]},
        ]}
        move = self.bot._steer_around_shots(obs, heading, heading, True, True)
        self.assertGreater(BOT.dot2(move, heading), 0.35)


class CombatTests(unittest.TestCase):
    def setUp(self):
        self.bot = BOT.RoyaleBot.__new__(BOT.RoyaleBot)
        self.bot.arena = BOT.Arena.__new__(BOT.Arena)
        self.bot.arena.ok = False
        self.bot.damage_until = -1.0
        self.bot.damage_bearing = None
        self.bot.scan_yaw = 0.0
        self.bot.attacker = None
        self.bot.focus = None
        self.bot.focus_until = -1.0
        self.bot.damage_sources = {}
        self.gun = {"kind": "gun", "ammo": 12, "stats": {"speed": 252.0, "range": 230.0}}
        self.mortar = {"kind": "mortar", "cooldown": 2.0, "stats": {
            "speed": 72.0, "gravity": 22.0, "splash_radius": 7.0,
        }}
        self.me = {"pos": [0, 0, 0], "aim": [0.0, 0.0], "stats": {}, "weapons": [self.gun, self.mortar]}
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

    def test_releases_partial_laser_charge_to_finish_a_weak_target(self):
        laser = {"kind": "laser", "charge": 0.25, "stats": {
            "charge": 0.75, "range": 280.0, "damage": 315.0, "min_charge": 0.15, "min_frac": 0.35,
        }}
        self.bot.cfg = {"cubes": {"damage_per_cube": 0.04, "stat_cap": 10}}
        self.me["weapons"][1] = laser
        self.target["hp"] = 110
        self.me["aim"] = self.bot._direct_aim(self.me, self.target)
        self.assertFalse(self.bot._combat(self.obs, self.target)[1][1])
        self.target["hp"] = 800
        self.assertTrue(self.bot._combat(self.obs, self.target)[1][1])

    def test_tops_up_magazine_during_lull(self):
        self.gun["ammo"] = 6
        self.assertTrue(self.bot._combat(self.obs, None)[2])
        self.bot.damage_until = 4.0
        self.assertFalse(self.bot._combat(self.obs, None)[2])

    def test_laser_and_lightning_can_fire_without_a_gun(self):
        self.me.update(aim=[0.0, -0.08], energy=50)
        self.me["weapons"] = [
            {"kind": "laser", "charge": 0.75, "stats": {"charge": 0.75, "range": 280}},
            {"kind": "lightning", "stats": {"range": 45, "energy": 3, "cone_deg": 30}},
        ]
        self.target["pos"] = [32, 0, 0]
        _, fire, reload_now = self.bot._combat(self.obs, self.target)
        self.assertEqual(fire, [False, True])
        self.assertFalse(reload_now)

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

    def test_focus_finishes_a_wounded_opponent_in_clear_sight(self):
        healthy = dict(self.target, pos=[38, 0, 0], hp=1200)
        wounded = dict(self.target, id=9, pos=[77, 25, 0], hp=130)
        self.assertEqual(self.bot._primary_enemy(self.obs, [healthy, wounded]), wounded)


class ProgressionTests(unittest.TestCase):
    def setUp(self):
        self.bot = BOT.RoyaleBot.__new__(BOT.RoyaleBot)
        self.bot.arena = BOT.Arena.__new__(BOT.Arena)
        self.bot.arena.ok = False
        self.bot.damage_bearing = None
        self.bot.damage_until = -1.0
        self.bot.attacker = None
        self.bot.last_kill = -math.inf
        self.bot.escape_boost = False
        self.bot.weapon_memory = {}
        self.bot.weapon_goal = None
        self.bot.weapon_route = deque()
        self.bot.weapon_route_retry = -1.0
        self.bot.jet_phase = None
        self.bot.focus = None
        self.bot.focus_until = -1.0
        self.bot.damage_sources = {}
        self.bot.turn_sign = 1.0
        self.obs = {
            "t": 5.0, "self": {
                "id": 1, "pos": [0, 0, 0], "hp": 1200, "max_hp": 1400,
                "energy": 75, "stats": {}, "weapons": [{"kind": "gun", "ammo": 10}, None],
            },
            "zone": {"center": [0, 0], "radius": 300, "next_center": [0, 0],
                     "next_radius": 200, "shrink_end": 100},
        }
        self.foe = {"id": 2, "kind": "mech", "pos": [72, 0, 0], "hp": 210, "max_hp": 1400}

    def test_finishes_a_wounded_opponent_only_while_isolated(self):
        self.obs["events"] = [{"type": "hit", "target": 2, "amount": 90}]
        self.bot._record_combat(self.obs, 5.0)
        duel = self.bot._duel_vector(self.obs, self.foe, [self.foe], False, 300.0)
        self.assertGreater(duel[0], 0.5)
        second = dict(self.foe, id=3, pos=[51, 20, 0])
        self.assertIsNone(self.bot._duel_vector(self.obs, self.foe, [self.foe, second], False, 300.0))
        self.assertIsNone(self.bot._duel_vector(self.obs, self.foe, [self.foe], True, 300.0))

    def test_kill_releases_stale_threat_and_subsequent_cube_collect(self):
        self.bot.attacker = 2
        self.bot.damage_bearing = 0.0
        self.bot.damage_until = 7.0
        self.obs["events"] = [
            {"type": "damaged", "by": 2, "bearing": 0.0, "amount": 45},
            {"type": "kill", "target": 2},
        ]
        self.bot._record_combat(self.obs, 5.0)
        self.assertIsNone(self.bot.attacker)
        cube = {"id": 5001, "pos": [26, 0, 0]}
        self.obs["cubes"] = [cube]
        self.assertEqual(self.bot._safe_cube(self.obs, [], False, False), cube)
        rival = dict(self.foe, id=3, pos=[42, 0, 0])
        self.assertIsNone(self.bot._safe_cube(self.obs, [rival], False, False))

    def test_farm_target_yields_to_new_pressure(self):
        crate = {"id": 1001, "pos": [20, 0, 0], "hp": 200}
        self.obs["crates"] = [crate]
        self.assertEqual(self.bot._farm_target(self.obs, [], False, False)["id"], crate["id"])
        self.assertIsNone(self.bot._farm_target(self.obs, [self.foe], False, False))

    def test_farm_can_finish_a_drone_that_returns_fire(self):
        drone = {"id": 100, "kind": "npc", "pos": [20, 0, 0], "hp": 120}
        self.obs["mechs"] = [drone]
        self.bot.attacker = drone["id"]
        self.bot.damage_until = 8.0
        self.assertEqual(self.bot._farm_target(self.obs, [], False, False), drone)
        self.bot.attacker = 2
        self.assertIsNone(self.bot._farm_target(self.obs, [], False, False))

    def test_retreat_does_not_drive_outward_when_zone_is_urgent(self):
        self.bot.cfg = {"weapons": {"mortar": {"gravity": 22, "splash_radius": 7}}}
        self.bot.last_move = (0.0, 0.0)
        self.bot.stuck_ticks = 0
        self.bot.last_jump = -10.0
        self.bot.pickup_radius = 4.0
        self.obs["self"].update(pos=[108, 0, 0], vel=[0, 0, 0], grounded=True, aim=[math.pi, 0.0])
        self.obs["self"]["weapons"][0]["stats"] = {"speed": 252, "range": 230}
        attacker = dict(self.foe, pos=[42, 0, 0], vel=[0, 0, 0], hp=1200)
        self.obs.update(
            t=52.0,
            zone={"center": [0, 0], "radius": 100, "next_center": [-15, 0],
                  "next_radius": 70, "shrink_end": 56.0},
            events=[{"type": "damaged", "by": 2, "amount": 42.0, "bearing": math.pi}],
            mechs=[attacker],
        )
        action = self.bot.act(self.obs)
        self.assertLess(action["move"][0], -0.9)
        self.assertTrue(action["boost"])
        self.obs["zone"].update(radius=250, next_radius=240)
        self.obs["projectiles"] = [{
            "kind": "gun", "pos": [58, 1.5, 5], "vel": [250, 0, 0],
        }]
        self.assertTrue(self.bot._projectile_dodge(self.obs)[1])
        self.assertFalse(self.bot.act(self.obs)["jump"])

    def test_jet_crossing_waits_for_altitude_before_moving_horizontally(self):
        self.bot.jet_gate = ((0, 0, 0), (14, 0, 0), 8, 60, True)
        self.bot.jet_phase = "climb"
        self.bot.jet_started = 5.0
        self.obs["self"].update(pos=[0, 0, 4], vel=[0, 0, 4], grounded=False)
        action = {}
        self.bot._exit_terrain(self.obs, -30, action)
        self.assertEqual(action["move"], [0.0, 0.0])
        self.assertTrue(action["boost"])
        self.obs["self"]["pos"][2] = 9
        self.bot._exit_terrain(self.obs, -30, action)
        self.assertEqual(action["move"], [1.0, 0.0])


if __name__ == "__main__":
    unittest.main()
