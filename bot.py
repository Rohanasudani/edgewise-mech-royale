#!/usr/bin/env python3
"""A survival-first, map-aware bot for the mechfight/1 protocol.

The controller is deliberately standard-library-only.  It loads the arena during
the generous hello budget, then keeps every decision small and deterministic.
"""

from __future__ import annotations

from array import array
from collections import defaultdict, deque
import json
import math
import os
import queue
import random
import sys
import threading
import traceback


TAU = 2.0 * math.pi
EPS = 1e-9


def clamp(value, low, high):
    return max(low, min(high, value))


def wrap(angle):
    return (angle + math.pi) % TAU - math.pi


def angle_diff(a, b):
    return wrap(a - b)


def length2(v):
    return math.hypot(v[0], v[1])


def distance2(a, b):
    return math.hypot(a[0] - b[0], a[1] - b[1])


def unit(v, fallback=(1.0, 0.0)):
    size = length2(v)
    if size < EPS:
        return fallback
    return (v[0] / size, v[1] / size)


def dot2(a, b):
    return a[0] * b[0] + a[1] * b[1]


def point_in_ring(x, y, ring):
    """Odd-even polygon test. ring is a flat [x0,y0,...] array."""
    inside = False
    j = len(ring) - 2
    for i in range(0, len(ring), 2):
        xi, yi = ring[i], ring[i + 1]
        xj, yj = ring[j], ring[j + 1]
        if (yi > y) != (yj > y):
            cross_x = (xj - xi) * (y - yi) / (yj - yi) + xi
            if x < cross_x:
                inside = not inside
        j = i
    return inside


def segment_distance_sq(px, py, ax, ay, bx, by):
    dx, dy = bx - ax, by - ay
    denom = dx * dx + dy * dy
    if denom <= EPS:
        return (px - ax) ** 2 + (py - ay) ** 2
    t = clamp(((px - ax) * dx + (py - ay) * dy) / denom, 0.0, 1.0)
    qx, qy = ax + t * dx, ay + t * dy
    return (px - qx) ** 2 + (py - qy) ** 2


def near_ring_edge(x, y, ring, radius_sq):
    j = len(ring) - 2
    for i in range(0, len(ring), 2):
        if segment_distance_sq(x, y, ring[j], ring[j + 1], ring[i], ring[i + 1]) < radius_sq:
            return True
        j = i
    return False


class Arena:
    """Small spatial index used for local steering, not global pathfinding."""

    CELL = 30.0

    def __init__(self, hello):
        self.ok = False
        self.prisms = []
        self.cells = defaultdict(list)
        self.heights = None
        self.terrain = None
        self.max_step = float(hello["config"]["mech"]["step_up"])
        self.max_slope = math.tan(math.radians(float(hello["config"]["mech"]["max_slope_deg"])))
        try:
            map_dir = hello["map"]["dir"]
            with open(os.path.join(map_dir, "v2", "world.json"), "r", encoding="utf-8") as handle:
                world = json.load(handle)
            self.terrain = world["terrain"]
            raw = array("h")
            with open(os.path.join(map_dir, "v2", self.terrain["file"]), "rb") as handle:
                raw.fromfile(handle, int(self.terrain["nx"]) * int(self.terrain["ny"]))
            if sys.byteorder != "little":
                raw.byteswap()
            self.heights = raw
            for item in world["prisms"]:
                ring = item.get("ring") or []
                if len(ring) < 6:
                    continue
                xs, ys = ring[0::2], ring[1::2]
                prism = {
                    "base": float(item["base"]),
                    "top": float(item["top"]),
                    "ring": ring,
                    "holes": item.get("holes") or [],
                    "bbox": (min(xs), min(ys), max(xs), max(ys)),
                }
                index = len(self.prisms)
                self.prisms.append(prism)
                min_x, min_y, max_x, max_y = prism["bbox"]
                for gx in range(math.floor((min_x - 4.0) / self.CELL), math.floor((max_x + 4.0) / self.CELL) + 1):
                    for gy in range(math.floor((min_y - 4.0) / self.CELL), math.floor((max_y + 4.0) / self.CELL) + 1):
                        self.cells[(gx, gy)].append(index)
            self.ok = True
        except Exception as exc:
            print(f"map load disabled: {exc}", file=sys.stderr, flush=True)

    def ground(self, x, y):
        if not self.ok:
            return 0.0
        terrain = self.terrain
        fx = (x - float(terrain["minX"])) / float(terrain["step"])
        fy = (y - float(terrain["minY"])) / float(terrain["step"])
        nx, ny = int(terrain["nx"]), int(terrain["ny"])
        i = int(math.floor(clamp(fx, 0.0, nx - 1.001)))
        j = int(math.floor(clamp(fy, 0.0, ny - 1.001)))
        tx, ty = fx - i, fy - j
        tx, ty = clamp(tx, 0.0, 1.0), clamp(ty, 0.0, 1.0)
        p = j * nx + i
        h00 = self.heights[p]
        h10 = self.heights[p + 1]
        h01 = self.heights[p + nx]
        h11 = self.heights[p + nx + 1]
        value = h00 * (1.0 - tx) * (1.0 - ty) + h10 * tx * (1.0 - ty) + h01 * (1.0 - tx) * ty + h11 * tx * ty
        return value * float(terrain.get("scale", 0.01))

    def _nearby(self, x, y):
        return self.cells.get((math.floor(x / self.CELL), math.floor(y / self.CELL)), ())

    def shot_clear(self, origin, endpoint, arc_rise=0.0):
        """Sample the line or projectile arc against arena solids and terrain."""
        if not self.ok:
            return True
        dx, dy, dz = (b - a for a, b in zip(origin, endpoint))
        steps = max(2, math.ceil(math.hypot(dx, dy, dz) / 1.5))
        for step in range(1, steps):
            fraction = step / steps
            x = origin[0] + dx * fraction
            y = origin[1] + dy * fraction
            z = origin[2] + dz * fraction + arc_rise * fraction * (1.0 - fraction)
            if self.ground(x, y) > z + 0.25:
                return False
            for index in self._nearby(x, y):
                prism = self.prisms[index]
                min_x, min_y, max_x, max_y = prism["bbox"]
                if (
                    prism["base"] + 0.1 < z < prism["top"] - 0.1
                    and min_x < x < max_x and min_y < y < max_y
                    and point_in_ring(x, y, prism["ring"])
                    and not any(point_in_ring(x, y, hole) for hole in prism["holes"])
                ):
                    return False
        return True

    def blocked(self, x, y, feet_z, radius, height):
        if not self.ok:
            return False
        radius_sq = radius * radius
        for index in self._nearby(x, y):
            prism = self.prisms[index]
            min_x, min_y, max_x, max_y = prism["bbox"]
            if x < min_x - radius or x > max_x + radius or y < min_y - radius or y > max_y + radius:
                continue
            # We can pass below a bridge or move on top of a roof we already reached.
            if feet_z + height <= prism["base"] + 0.1 or feet_z >= prism["top"] - self.max_step:
                continue
            in_outer = point_in_ring(x, y, prism["ring"])
            in_hole = any(point_in_ring(x, y, hole) for hole in prism["holes"])
            if in_outer and not in_hole:
                return True
            if near_ring_edge(x, y, prism["ring"], radius_sq):
                return True
            if any(near_ring_edge(x, y, hole, radius_sq) for hole in prism["holes"]):
                return True
        return False

    def direction_clear(self, pos, heading, radius, height):
        if not self.ok:
            return True
        ux, uy = math.cos(heading), math.sin(heading)
        base_ground = self.ground(pos[0], pos[1])
        for distance in (3.0, 6.0, 10.0):
            x, y = pos[0] + ux * distance, pos[1] + uy * distance
            if self.blocked(x, y, pos[2], radius + 0.35, height):
                return False
            rise = self.ground(x, y) - base_ground
            if rise > self.max_step and rise / distance > self.max_slope:
                return False
        return True

    def ground_leg_clear(self, start, goal, radius, height):
        """Check a short loot approach on the same ground layer."""
        if not self.ok:
            return True
        if abs(start[2] - self.ground(start[0], start[1])) > 3.0:
            return False
        if abs(goal[2] - self.ground(goal[0], goal[1])) > 4.0:
            return False
        distance = distance2(start, goal)
        steps = max(1, math.ceil(distance / 2.5))
        max_rise = max(self.max_step, self.max_slope * distance / steps)
        last_z = start[2]
        for index in range(1, steps + 1):
            fraction = index / steps
            x = start[0] + (goal[0] - start[0]) * fraction
            y = start[1] + (goal[1] - start[1]) * fraction
            z = self.ground(x, y)
            if abs(z - last_z) > max_rise or self.blocked(x, y, z, radius + 0.25, height):
                return False
            last_z = z
        return True

    def steer(self, pos, desired, previous, radius, height, zone):
        heading = math.atan2(desired[1], desired[0])
        previous_heading = math.atan2(previous[1], previous[0]) if length2(previous) > 0.05 else heading
        offsets = (0, 0.30, -0.30, 0.62, -0.62, 0.95, -0.95, 1.30, -1.30, 1.65, -1.65, 2.1, -2.1, math.pi)
        best_score, best = -1e9, heading
        for offset in offsets:
            candidate = wrap(heading + offset)
            clear = self.direction_clear(pos, candidate, radius, height)
            alignment = math.cos(offset)
            continuity = math.cos(angle_diff(candidate, previous_heading))
            nx, ny = pos[0] + math.cos(candidate) * 9.0, pos[1] + math.sin(candidate) * 9.0
            zone_margin = zone[2] - distance2((nx, ny), zone[:2])
            zone_penalty = min(0.0, zone_margin - radius - 3.0) * 0.08
            score = 3.2 * alignment + 0.55 * continuity + zone_penalty + (0.0 if clear else -20.0)
            if score > best_score:
                best_score, best = score, candidate
        return (math.cos(best), math.sin(best)), self.direction_clear(pos, heading, radius, height)


UPGRADE_SCORE = {
    "armor": 125,
    "plating": 120,
    "servos": 113,
    "jets": 108,
    "nanites": 104,
    "capacitor": 96,
    "reactor": 94,
    "sensors": 88,
    "gyros": 80,
    "laser_charge": 76,
    "laser_damage": 72,
    "mortar_reload": 72,
    "mortar_splash": 70,
    "lightning_range": 70,
    "lightning_efficiency": 68,
    "gun_rate": 66,
    "gun_damage": 65,
    "gun_mag": 61,
    "mortar_velocity": 60,
    "gun_velocity": 58,
    "lightning_chain": 56,
    "laser_pierce": 54,
}


class RoyaleBot:
    name = "edgewise-v9"

    def __init__(self, hello):
        self.cfg = hello["config"]
        self.rng = random.Random(int(hello.get("seed", 0)))
        self.arena = Arena(hello)
        self.last_move = (0.0, 0.0)
        self.last_pos = None
        self.last_t = 0.0
        self.stuck_ticks = 0
        self.last_jump = -10.0
        self.scan_yaw = self.rng.uniform(-math.pi, math.pi)
        self.damage_bearing = None
        self.damage_until = -1.0
        self.attacker = None
        self.damage_window = deque()
        self.last_kill = -math.inf
        self.turn_sign = 1.0 if self.rng.random() < 0.5 else -1.0
        self.pickup_radius = float(self.cfg["loot"]["pickup_radius"])

    def _inside_margin(self, point, center, radius):
        return radius - distance2(point, center)

    def _upgrade_pick(self, offer):
        choices = offer.get("choices") or []
        return max(range(len(choices)), key=lambda i: UPGRADE_SCORE.get(choices[i].get("id"), 40)) if choices else None

    def _weapon_slots(self, me):
        return {weapon["kind"]: (i, weapon) for i, weapon in enumerate(me["weapons"]) if weapon is not None}

    def _record_combat(self, obs, now):
        events = obs.get("events", ())
        defeated = {event.get("target") for event in events if event.get("type") == "kill"}
        if defeated:
            self.last_kill = now
            if self.attacker in defeated:
                self.attacker = self.damage_bearing = None
                self.damage_until = -1.0
        for event in events:
            kind = event.get("type")
            if kind == "hit" and event.get("target") is not None:
                self.damage_window.append((now, event["target"], float(event["amount"])))
            elif kind == "damaged" and event.get("by") not in defeated:
                self.damage_window.append((now, event.get("by"), -float(event["amount"])))
                if event.get("bearing") is not None:
                    self.damage_bearing = float(event["bearing"])
                    self.damage_until = now + 3.2
                    self.attacker = event.get("by")
        while self.damage_window and now - self.damage_window[0][0] > 3.0:
            self.damage_window.popleft()

    def _safe_ground_goal(self, obs, goal, urgent=False):
        me = obs["self"]
        zone = obs["zone"]
        future_margin = self._inside_margin(goal, zone["next_center"], zone["next_radius"])
        needed = max(0.0, 10.0 - future_margin)
        time_to_safety = (needed + distance2(me["pos"], goal)) / float(me["stats"].get("walk_speed", 11.7))
        return (
            not urgent and abs(goal[2] - me["pos"][2]) <= 3.8
            and self._inside_margin(goal, zone["center"], zone["radius"]) >= 18.0
            and (future_margin >= 10.0 or time_to_safety + 8.0 < zone["shrink_end"] - obs["t"])
            and self.arena.ground_leg_clear(
                me["pos"], goal, float(me["stats"].get("radius", 2.5)),
                float(me["stats"].get("height", 9.0)),
            )
        )

    def _duel_vector(self, obs, target, enemies, urgent, current_margin):
        """Keep a safe, favorable one-on-one fight inside weapon range."""
        if target is None or urgent or current_margin < 27.0:
            return None
        me = obs["self"]
        pos = me["pos"]
        foe = target["pos"]
        distance = distance2(pos, foe)
        enemy_hp = target.get("hp")
        if enemy_hp is None or not 14.0 <= distance <= 105.0:
            return None
        minimum_health = 0.52 if enemy_hp <= 220.0 else 0.65
        if me["hp"] < me["max_hp"] * minimum_health:
            return None
        if any(
            enemy["id"] != target["id"] and min(distance2(pos, enemy["pos"]), distance2(foe, enemy["pos"])) < 88.0
            for enemy in enemies
        ):
            return None
        if obs["t"] < self.damage_until and self.attacker not in (None, target["id"]):
            return None
        zone = obs["zone"]
        if self._inside_margin(foe, zone["center"], zone["radius"]) < 19.0:
            return None

        weapons = self._weapon_slots(me)
        if not self.arena.shot_clear(*self._direct_line(me, target)):
            return None
        outgoing = sum(amount for _, identifier, amount in self.damage_window if identifier == target["id"] and amount > 0)
        incoming = -sum(amount for _, _, amount in self.damage_window if amount < 0)
        enemy_weapons = target.get("weapons") or ()
        early_advantage = (
            len(weapons) > 1 and set(enemy_weapons) == {"gun"}
            and me["hp"] > me["max_hp"] * 0.78 and enemy_hp <= me["hp"] * 1.05
        )
        finishing = outgoing > 0 and enemy_hp <= min(335.0, me["hp"] * 0.67) and incoming < me["hp"] * 0.36
        winning_trade = outgoing >= 100.0 and outgoing > incoming * 1.25 and enemy_hp < me["hp"] * 0.80
        if not (early_advantage or finishing or winning_trade):
            return None
        preferred = 50.0
        if "lightning" in weapons and me["energy"] >= 27.0:
            preferred = float(weapons["lightning"][1]["stats"]["range"]) * 0.76
        elif "laser" in weapons:
            preferred = 62.0
        if finishing and "lightning" not in enemy_weapons:
            preferred = min(preferred, 46.0)
        if "lightning" in enemy_weapons and "lightning" not in weapons:
            preferred = max(57.0, preferred)
        away = unit((pos[0] - foe[0], pos[1] - foe[1]))
        radial = clamp((preferred - distance) / 20.0, -1.0, 0.9)
        tangent = (-away[1] * self.turn_sign, away[0] * self.turn_sign)
        direction = unit((tangent[0] * 0.80 + away[0] * radial * 1.40,
                          tangent[1] * 0.80 + away[1] * radial * 1.40))
        return direction, distance > preferred + 18.0

    def _safe_cube(self, obs, enemies, urgent, danger):
        """Collect nearby rewards only when our arrival beats nearby opponents."""
        now = float(obs["t"])
        me = obs["self"]
        pos = me["pos"]
        cubes = obs.get("cubes") or ()
        recent_attack = now < self.damage_until - 1.8 and (self.attacker is None or self.attacker < 100)
        if urgent or danger or not cubes or recent_attack:
            return None
        distance_limit = 32.0 if now - self.last_kill < 8.0 else 22.0
        speed = float(me["stats"].get("walk_speed", 11.7))
        candidates = []
        for cube in cubes:
            goal = cube["pos"]
            distance = distance2(pos, goal)
            if distance > distance_limit or abs(pos[2] - goal[2]) > 3.8:
                continue
            arrival = max(0.0, distance - 3.3) / speed
            if any(
                distance2(goal, enemy["pos"]) < max(32.0, (arrival + 0.8) * 19.5)
                for enemy in enemies
            ):
                continue
            cluster = sum(distance2(goal, other["pos"]) < 6.0 for other in cubes)
            candidates.append((distance - min(cluster, 8) * 3.0, cube["id"], cube))
        for _, _, cube in sorted(candidates)[:3]:
            if self._safe_ground_goal(obs, cube["pos"]):
                return cube
        return None

    def _farm_target(self, obs, enemies, urgent, danger):
        """Take short, quiet-window opportunities for the first few upgrades."""
        me = obs["self"]
        pos = me["pos"]
        recent_attack = obs["t"] < self.damage_until and (self.attacker is None or self.attacker < 100)
        if (
            urgent or danger or recent_attack or me.get("cubes", 0) >= 5
            or me["hp"] < me["max_hp"] * 0.82
            or any(distance2(pos, enemy["pos"]) < 145.0 for enemy in enemies)
        ):
            return None
        if any(
            sound.get("age", 9.0) < 0.35 and sound.get("dist") in {"near", "mid"}
            and sound["kind"] in {"gun", "laser", "lightning", "mortar", "boost"}
            for sound in obs.get("sounds", ())
        ):
            return None
        gun = self._weapon_slots(me)["gun"][1]
        if gun.get("ammo", 12) <= 1 or gun.get("reload", 0.0) > 0.25:
            return None
        candidates = []
        for actor in obs.get("mechs", ()):
            if actor["kind"] == "npc" and 5.0 < distance2(pos, actor["pos"]) <= 27.0:
                candidates.append((distance2(pos, actor["pos"]) + 0.035 * actor.get("hp", 350) - 16.0, actor))
        for crate in obs.get("crates", ()):
            if 5.0 < distance2(pos, crate["pos"]) <= 29.0:
                candidates.append((distance2(pos, crate["pos"]) + 0.035 * crate.get("hp", 300), dict(crate, kind="crate")))
        for _, target in sorted(candidates, key=lambda item: (item[0], item[1]["id"]))[:2]:
            if self._safe_ground_goal(obs, target["pos"]) and self.arena.shot_clear(*self._direct_line(me, target)):
                return target
        return None

    def _select_pickup(self, obs):
        me = obs["self"]
        if all(me["weapons"]):
            return None
        pos = me["pos"]
        held = {w["kind"] for w in me["weapons"] if w}
        zone = obs["zone"]
        choices = [
            pickup for pickup in obs.get("pickups", [])
            if pickup["kind"] != "gun" and pickup["kind"] not in held
            and abs(pickup["pos"][2] - pos[2]) <= 4.0
            and self._inside_margin(pickup["pos"], zone["center"], zone["radius"]) > 10.0
        ]
        if not choices:
            return None
        preference = {"laser": 8.0, "mortar": 5.0, "lightning": 0.0}
        return min(choices, key=lambda p: distance2(pos, p["pos"]) - preference.get(p["kind"], 0.0))

    def _zone_plan(self, obs):
        me = obs["self"]
        pos = me["pos"]
        zone = obs["zone"]
        current = zone["center"]
        next_center = zone["next_center"]
        radius = float(zone["radius"])
        next_radius = float(zone["next_radius"])
        radius_me = float(me["stats"].get("radius", 2.5))
        safe_pad = radius_me + 10.0
        current_margin = self._inside_margin(pos, current, radius)
        next_margin = self._inside_margin(pos, next_center, next_radius)
        required = max(0.0, safe_pad - next_margin)
        remaining = max(0.1, float(zone["shrink_end"]) - float(obs["t"]))
        speed = max(1.0, float(me["stats"].get("walk_speed", 11.7)))
        urgent = current_margin < safe_pad or required / speed > max(0.0, remaining - 7.0)

        if current_margin < safe_pad:
            vector = unit((current[0] - pos[0], current[1] - pos[1]))
        elif urgent or next_margin < safe_pad:
            vector = unit((next_center[0] - pos[0], next_center[1] - pos[1]))
        else:
            radial = unit((pos[0] - next_center[0], pos[1] - next_center[1]), fallback=(1.0, 0.0))
            desired_radius = max(0.0, next_radius - safe_pad - 5.0)
            radial_error = distance2(pos, next_center) - desired_radius
            tangent = (-radial[1] * self.turn_sign, radial[0] * self.turn_sign)
            vector = unit((tangent[0] - radial[0] * clamp(radial_error / 35.0, -1.0, 1.0), tangent[1] - radial[1] * clamp(radial_error / 35.0, -1.0, 1.0)))
        return vector, urgent, current_margin, next_margin

    def _projectile_dodge(self, obs):
        me = obs["self"]
        pos = me["pos"]
        velocity = me["vel"]
        radius = float(me["stats"].get("radius", 2.5))
        height = float(me["stats"].get("height", 9.0))
        mortar = self.cfg["weapons"]["mortar"]
        gravity = float(mortar["gravity"])
        splash = float(mortar["splash_radius"])
        dodge = [0.0, 0.0]
        nearest_hazard = None
        for projectile in obs.get("projectiles", []):
            origin = projectile["pos"]
            drift = projectile["vel"]
            rel = (origin[0] - pos[0], origin[1] - pos[1])
            relative_vel = (drift[0] - velocity[0], drift[1] - velocity[1])
            relative_speed_sq = dot2(relative_vel, relative_vel)
            is_mortar = projectile["kind"] == "mortar"
            direct = None
            if relative_speed_sq > EPS:
                approach = -dot2(rel, relative_vel) / relative_speed_sq
                if 0.0 <= approach <= 0.8:
                    miss = (rel[0] + relative_vel[0] * approach, rel[1] + relative_vel[1] * approach)
                    z = origin[2] + drift[2] * approach - (0.5 * gravity * approach ** 2 if is_mortar else 0.0)
                    feet = pos[2] + (0.0 if me["grounded"] else velocity[2] * approach)
                    if length2(miss) < radius + 1.0 and feet - 0.7 <= z <= feet + height + 0.7:
                        endpoint = (origin[0] + drift[0] * approach, origin[1] + drift[1] * approach, z)
                        if is_mortar or self.arena.shot_clear(origin, endpoint):
                            direct = (approach, miss, 1.0)
            impact = self._mortar_impact(me, origin, drift, gravity, splash + radius + 0.9) if is_mortar else None
            hazard = min((risk for risk in (direct, impact) if risk is not None), default=None)
            if hazard is None:
                continue
            approach, miss, severity = hazard
            perpendicular = unit((-relative_vel[1] * self.turn_sign, relative_vel[0] * self.turn_sign))
            if dot2(perpendicular, self.last_move) < -0.1:
                perpendicular = (-perpendicular[0], -perpendicular[1])
            # The signed miss vector defines the safe side. The fallback only
            # breaks a tie when the predicted path goes through our center.
            away = unit((-miss[0], -miss[1]), fallback=perpendicular)
            weight = severity * (1.2 - approach * 0.5)
            dodge[0] += away[0] * weight
            dodge[1] += away[1] * weight
            if nearest_hazard is None or approach < nearest_hazard[0]:
                nearest_hazard = (approach, away)
        if nearest_hazard is not None and length2(dodge) < 0.2:
            return list(nearest_hazard[1]), True
        return dodge, nearest_hazard is not None

    def _mortar_impact(self, me, origin, drift, gravity, radius):
        """Estimate the near-term ground impact in the game simulation."""
        pos = me["pos"]
        if distance2(pos, origin) > (length2(drift) + length2(me["vel"])) * 0.9 + radius:
            return None
        previous_t = 0.0
        previous_height = origin[2] - self.arena.ground(origin[0], origin[1])
        for tick in range(1, 10):
            elapsed = tick * 0.1
            x = origin[0] + drift[0] * elapsed
            y = origin[1] + drift[1] * elapsed
            z = origin[2] + drift[2] * elapsed - 0.5 * gravity * elapsed ** 2
            height = z - self.arena.ground(x, y)
            if height <= 0.0:
                elapsed = previous_t + 0.1 * clamp(previous_height / max(EPS, previous_height - height), 0.0, 1.0)
                offset = tuple(origin[i] - pos[i] + (drift[i] - me["vel"][i]) * elapsed for i in range(2))
                if length2(offset) < radius:
                    return elapsed, offset, 1.6
                return None
            previous_t, previous_height = elapsed, height
        return None

    def _threat_vector(self, obs, enemies):
        pos = obs["self"]["pos"]
        hp_fraction = obs["self"]["hp"] / max(1.0, obs["self"]["max_hp"])
        result = [0.0, 0.0]
        closest = 1e9
        for actor in obs.get("mechs", []):
            distance = distance2(pos, actor["pos"])
            if actor["kind"] == "npc":
                if distance < 52.0:
                    away = unit((pos[0] - actor["pos"][0], pos[1] - actor["pos"][1]))
                    strength = 0.7 * (1.0 - distance / 52.0)
                    result[0] += away[0] * strength
                    result[1] += away[1] * strength
                continue
            closest = min(closest, distance)
            reach = 155.0 if hp_fraction < 0.55 else 125.0
            if distance < reach:
                away = unit((pos[0] - actor["pos"][0], pos[1] - actor["pos"][1]))
                strength = (1.0 - distance / reach) ** 1.4 * (2.2 if distance < 48.0 else 1.0)
                tangent = (-away[1] * self.turn_sign, away[0] * self.turn_sign)
                result[0] += away[0] * strength + tangent[0] * strength * 0.55
                result[1] += away[1] * strength + tangent[1] * strength * 0.55
        if self.damage_bearing is not None and obs["t"] < self.damage_until:
            result[0] -= 2.0 * math.cos(self.damage_bearing)
            result[1] -= 2.0 * math.sin(self.damage_bearing)
        noisy = {"gun", "mortar", "explosion", "laser", "laser_charge", "lightning", "boost", "jet"}
        for sound in obs.get("sounds", []):
            if sound["kind"] not in noisy or sound.get("age", 9.0) > 0.45:
                continue
            weight = {"near": 0.42, "mid": 0.17, "far": 0.04}.get(sound.get("dist"), 0.05)
            result[0] -= weight * math.cos(sound["bearing"])
            result[1] -= weight * math.sin(sound["bearing"])
        return result, closest

    def _primary_enemy(self, obs, enemies):
        if len(enemies) < 2:
            return enemies[0] if enemies else None
        me = obs["self"]
        pos = me["pos"]
        def score(enemy):
            value = distance2(pos, enemy["pos"])
            if enemy["id"] == self.attacker and obs["t"] < self.damage_until:
                value -= 32.0
            hp = enemy.get("hp")
            if hp is not None:
                value += 49.0 * hp / max(1.0, enemy.get("max_hp", hp))
            if enemy.get("charging"):
                value -= 18.0
            return value
        ranked = sorted(enemies, key=score)
        for enemy in ranked[:3]:
            if self.arena.shot_clear(*self._direct_line(me, enemy)):
                return enemy
        return ranked[0]

    def _direct_line(self, me, target, speed=None):
        muzzle = (me["pos"][0], me["pos"][1], me["pos"][2] + float(me["stats"].get("muzzle_height", 7.0)))
        if target["kind"] == "mech":
            center_height = 4.5 * (1.0 + 0.05 * target.get("cubes", 0))
        else:
            center_height = 1.75 if target["kind"] == "npc" else 1.4
        target_pos = (target["pos"][0], target["pos"][1], target["pos"][2] + center_height)
        velocity = target.get("vel", (0.0, 0.0, 0.0))
        lead = 0.05  # One simulation tick for the next action to take effect.
        if speed:
            barrel = float(me["stats"].get("radius", 2.5)) + 0.5
            for _ in range(3):
                predicted = tuple(point + rate * lead for point, rate in zip(target_pos, velocity))
                flight = clamp((math.dist(muzzle, predicted) - barrel) / speed, 0.0, 1.2)
                lead = flight + 0.05
        endpoint = tuple(point + rate * lead for point, rate in zip(target_pos, velocity))
        return muzzle, endpoint

    @staticmethod
    def _aim_between(origin, endpoint):
        dx, dy, dz = (b - a for a, b in zip(origin, endpoint))
        return [math.atan2(dy, dx), math.atan2(dz, math.hypot(dx, dy))]

    def _direct_aim(self, me, target, speed=None):
        return self._aim_between(*self._direct_line(me, target, speed))

    def _mortar_aim(self, me, target, weapon):
        muzzle = (me["pos"][0], me["pos"][1], me["pos"][2] + float(me["stats"].get("height", 9.0)) + 0.5)
        speed = float(weapon["stats"]["speed"])
        gravity = float(weapon["stats"]["gravity"])
        predicted = [target["pos"][0], target["pos"][1], target["pos"][2] + 2.5]
        solution = None
        for _ in range(4):
            dx, dy, dz = predicted[0] - muzzle[0], predicted[1] - muzzle[1], predicted[2] - muzzle[2]
            horizontal = math.hypot(dx, dy)
            disc = speed ** 4 - gravity * (gravity * horizontal ** 2 + 2.0 * dz * speed ** 2)
            if horizontal < 1.0 or disc < 0.0:
                return None
            tangent = (speed ** 2 - math.sqrt(disc)) / (gravity * horizontal)
            pitch = math.atan(tangent)
            flight = horizontal / max(1.0, speed * math.cos(pitch))
            predicted = [
                target["pos"][0] + target["vel"][0] * flight,
                target["pos"][1] + target["vel"][1] * flight,
                target["pos"][2] + target["vel"][2] * min(0.3, flight) + 2.5,
            ]
            solution = [math.atan2(dy, dx), pitch]
        yaw, pitch = solution
        if not me["stats"].get("min_pitch", -0.7) <= pitch <= me["stats"].get("max_pitch", 1.3):
            return None
        direction = (math.cos(yaw) * math.cos(pitch), math.sin(yaw) * math.cos(pitch), math.sin(pitch))
        barrel = float(me["stats"].get("radius", 2.5)) + 0.5
        origin = tuple(point + barrel * component for point, component in zip(muzzle, direction))
        flight = max(0.0, flight - barrel / speed)
        endpoint = [point + speed * component * flight for point, component in zip(origin, direction)]
        drop = 0.5 * gravity * flight ** 2
        endpoint[2] -= drop
        if not self.arena.shot_clear(muzzle, origin) or not self.arena.shot_clear(origin, endpoint, drop):
            return None
        return solution

    def _laser_charge_needed(self, me, target, stats):
        full = float(stats["charge"])
        hp = target.get("hp")
        if hp is None or "damage" not in stats:
            return full
        cubes = self.cfg["cubes"]
        scale = 1.0 + cubes["damage_per_cube"] * min(me.get("cubes", 0), cubes["stat_cap"])
        multiplier = 1.0 + stats.get("cookoff", 0.6) if "mortar" in (target.get("weapons") or ()) else 1.0
        # Add tolerance for armor and leave full charge as the default for stronger foes.
        damage = float(stats["damage"]) * scale * multiplier * 0.84
        min_fraction = float(stats.get("min_frac", 0.35))
        fraction = clamp((hp / damage - min_fraction) / max(EPS, 1.0 - min_fraction), 0.0, 1.0)
        return max(float(stats.get("min_charge", 0.15)), full * fraction)

    def _combat(self, obs, target):
        me = obs["self"]
        fire = [False, False]
        slots = self._weapon_slots(me)
        gun_slot, gun = slots["gun"]
        ammo = gun.get("ammo", 12)
        reloading = gun.get("reload", 0.0) > 0.0
        if target is None:
            reload_now = not reloading and (ammo == 0 or (ammo <= 6 and obs["t"] >= self.damage_until))
            if self.damage_bearing is not None and obs["t"] < self.damage_until:
                return [self.damage_bearing, 0.0], fire, reload_now
            fresh = [s for s in obs.get("sounds", []) if s["kind"] in {"gun", "mortar", "laser", "laser_charge", "lightning"} and s.get("age", 9) < 0.35]
            if fresh:
                return [fresh[0]["bearing"], 0.0], fire, reload_now
            self.scan_yaw = wrap(self.scan_yaw + 0.27)
            return [self.scan_yaw, 0.0], fire, reload_now

        distance = distance2(me["pos"], target["pos"])
        gun_line = self._direct_line(me, target, float(gun["stats"]["speed"]))
        gun_aim = self._aim_between(*gun_line)
        aim = gun_aim

        def aligned(direction):
            return abs(angle_diff(me["aim"][0], direction[0])) < 0.11 and abs(me["aim"][1] - direction[1]) < 0.13

        if "laser" in slots:
            slot, weapon = slots["laser"]
            charge = float(weapon.get("charge", 0.0))
            stats = weapon["stats"]
            laser_line = self._direct_line(me, target)
            laser_aim = self._aim_between(*laser_line)
            clear = self.arena.shot_clear(*laser_line)
            if charge > 0.0:
                needed = self._laser_charge_needed(me, target, stats)
                if charge >= needed - 0.15:
                    aim = laser_aim
                fire[slot] = charge < needed - 0.025 or not (clear and aligned(laser_aim))
            elif weapon.get("cooldown", 0.0) <= 0.05 and distance <= stats["range"]:
                fire[slot] = clear and aligned(laser_aim)
        elif "mortar" in slots and target["kind"] == "mech":
            slot, weapon = slots["mortar"]
            splash = float(weapon["stats"].get("splash_radius", 7.0))
            max_distance = 160.0 if length2(target["vel"]) < 3.0 else 105.0
            if splash + 8.0 < distance < max_distance and weapon.get("cooldown", 0.0) <= 0.15:
                mortar_aim = self._mortar_aim(me, target, weapon)
                if mortar_aim is not None:
                    aim = mortar_aim
                    fire[slot] = aligned(aim)
        elif "lightning" in slots and target["kind"] == "mech":
            slot, weapon = slots["lightning"]
            stats = weapon["stats"]
            lightning_line = self._direct_line(me, target)
            yaw = self._aim_between(*lightning_line)[0]
            cone = math.radians(stats["cone_deg"]) * 0.5
            fire[slot] = (
                distance < stats["range"] * 0.97 and me["energy"] >= stats["energy"] + 8.0
                and weapon.get("cooldown", 0.0) <= 0.1
                and abs(angle_diff(me["aim"][0], yaw)) < cone * 0.85
                and abs(angle_diff(aim[0], yaw)) < cone * 0.85
                and self.arena.shot_clear(*lightning_line)
            )

        compatible_aim = abs(angle_diff(aim[0], gun_aim[0])) < 0.015 and abs(aim[1] - gun_aim[1]) < 0.02
        fire[gun_slot] = (
            not reloading and ammo > 0 and compatible_aim and aligned(aim)
            and distance < gun["stats"].get("range", 230.0)
            and self.arena.shot_clear(*gun_line)
        )
        reload_now = not reloading and (ammo == 0 or (ammo <= 2 and target["kind"] == "mech" and not compatible_aim))
        return aim, fire, reload_now

    def act(self, obs):
        me = obs["self"]
        pos = me["pos"]
        now = float(obs["t"])
        self._record_combat(obs, now)

        enemies = [actor for actor in obs.get("mechs", []) if actor["kind"] == "mech"]
        target = self._primary_enemy(obs, enemies)
        zone_vector, urgent, current_margin, next_margin = self._zone_plan(obs)
        threat, closest = self._threat_vector(obs, enemies)
        dodge, projectile_danger = self._projectile_dodge(obs)
        threatened = closest < 105.0 or now < self.damage_until or projectile_danger
        duel = self._duel_vector(obs, target, enemies, urgent, current_margin)
        reward = self._safe_cube(obs, enemies, urgent, projectile_danger) if duel is None else None

        objective = zone_vector
        objective_speed = 0.40  # below the default footstep threshold when walking
        pickup = self._select_pickup(obs)
        pickup_distance = distance2(pos, pickup["pos"]) if pickup else 1e9
        if pickup and not urgent and current_margin > 18.0 and (
            (pickup_distance < 70.0 and not threatened)
            or (pickup_distance < 19.0 and closest > 55.0 and not projectile_danger and now >= self.damage_until)
        ):
            objective = unit((pickup["pos"][0] - pos[0], pickup["pos"][1] - pos[1]))
            objective_speed = 0.86
        crate = None
        if not threatened and not urgent and pickup_distance >= 70.0:
            cubes = [cube for cube in obs.get("cubes", []) if distance2(pos, cube["pos"]) < 32.0]
            if cubes:
                cube = min(cubes, key=lambda item: distance2(pos, item["pos"]))
                objective = unit((cube["pos"][0] - pos[0], cube["pos"][1] - pos[1]))
                objective_speed = 0.65
            else:
                viable_drops = [drop for drop in obs.get("drops", []) if drop.get("lands_in", 99) <= 6.0 and distance2(pos, drop["pos"]) < 48.0 and drop.get("holder") in (None, me["id"])]
                if viable_drops and me["hp"] < me["max_hp"] * 0.72:
                    drop = min(viable_drops, key=lambda item: distance2(pos, item["pos"]))
                    objective = unit((drop["pos"][0] - pos[0], drop["pos"][1] - pos[1]))
                    objective_speed = 0.75
                elif not enemies and now < 45.0 and not all(me["weapons"]) and me["hp"] >= me["max_hp"] * 0.95:
                    zone = obs["zone"]
                    crates = [
                        item for item in obs.get("crates", [])
                        if 6.0 < distance2(pos, item["pos"]) < 35.0
                        and abs(item["pos"][2] - pos[2]) <= 4.0
                        and self._inside_margin(item["pos"], zone["center"], zone["radius"]) > 16.0
                    ]
                    if crates:
                        crate = min(crates, key=lambda item: distance2(pos, item["pos"]))
                        objective = unit((crate["pos"][0] - pos[0], crate["pos"][1] - pos[1]))
                        objective_speed = 0.65 if distance2(pos, crate["pos"]) > 9.0 else 0.0

        farm = None
        if reward is not None:
            goal = reward["pos"]
            objective = unit((goal[0] - pos[0], goal[1] - pos[1]))
            objective_speed = 0.90
        elif target is None and (pickup_distance >= 50.0 or all(me["weapons"])):
            farm = self._farm_target(obs, enemies, urgent, projectile_danger)
            if farm is not None:
                goal = farm["pos"]
                objective = unit((goal[0] - pos[0], goal[1] - pos[1]))
                objective_speed = 0.75 if distance2(pos, goal) > 11.0 else 0.0

        desired = [objective[0] * (1.6 if urgent else 0.9), objective[1] * (1.6 if urgent else 0.9)]
        if duel is not None:
            direction, _ = duel
            desired = [direction[i] * 2.1 + zone_vector[i] * 0.20 + dodge[i] * 3.3 for i in range(2)]
        else:
            threat_weight = 0.24 if reward is not None or farm is not None else 1.75
            desired[0] += threat[0] * threat_weight + dodge[0] * 2.8
            desired[1] += threat[1] * threat_weight + dodge[1] * 2.8
        desired = unit(desired, fallback=objective)

        commanded_before = length2(self.last_move)
        if commanded_before > 0.55 and math.hypot(me["vel"][0], me["vel"][1]) < 0.8:
            self.stuck_ticks += 1
        else:
            self.stuck_ticks = max(0, self.stuck_ticks - 2)
        stuck = self.stuck_ticks >= 7
        if stuck:
            angle = math.atan2(desired[1], desired[0]) + self.turn_sign * 1.15
            desired = (math.cos(angle), math.sin(angle))

        current_zone = (obs["zone"]["center"][0], obs["zone"]["center"][1], float(obs["zone"]["radius"]))
        steered, direct_clear = self.arena.steer(
            pos,
            desired,
            self.last_move,
            float(me["stats"].get("radius", 2.5)),
            float(me["stats"].get("height", 9.0)),
            current_zone,
        )

        move_scale = 1.0 if (threatened or urgent or stuck) and farm is None else objective_speed
        move = [steered[0] * move_scale, steered[1] * move_scale]
        energy = float(me["energy"])
        boost = bool((urgent and energy > 22.0) or (threatened and energy > 46.0) or (stuck and energy > 25.0))
        if duel is not None and not projectile_danger and not stuck:
            boost = duel[1] and energy > 66.0
        elif farm is not None and not stuck:
            boost = False
        jump = bool(me["grounded"] and (projectile_danger or stuck or (closest < 32.0 and now < self.damage_until)) and now - self.last_jump > 1.15)
        if jump:
            self.last_jump = now

        crate_target = dict(crate, kind="crate") if crate is not None and reward is None else None
        aim, fire, reload_now = self._combat(obs, target or farm or crate_target)
        action = {"move": move, "boost": boost, "jump": jump, "aim": aim, "fire": fire}
        if reload_now:
            action["reload"] = True
        offer = obs.get("offer")
        if offer:
            pick = self._upgrade_pick(offer)
            if pick is not None:
                action["pick"] = pick
        if pickup and pickup_distance <= self.pickup_radius + 0.15:
            empty = next((i for i, weapon in enumerate(me["weapons"]) if weapon is None), None)
            if empty is not None:
                action["take"] = pickup["id"]

        self.last_move = tuple(move)
        self.last_pos = tuple(pos)
        self.last_t = now
        return action


def send(message):
    sys.stdout.write(json.dumps(message, separators=(",", ":"), allow_nan=False) + "\n")
    sys.stdout.flush()


def read_stdin(messages):
    for line in sys.stdin:
        if line.strip():
            messages.put(line)
    messages.put(None)


def main():
    messages = queue.Queue()
    threading.Thread(target=read_stdin, args=(messages,), daemon=True).start()
    bot = None
    last_continuous = {}
    while True:
        batch = [messages.get()]
        while True:
            try:
                batch.append(messages.get_nowait())
            except queue.Empty:
                break
        latest = None
        for raw in batch:
            if raw is None:
                return
            try:
                message = json.loads(raw)
            except ValueError:
                continue
            kind = message.get("type")
            if kind == "hello":
                try:
                    bot = RoyaleBot(message)
                except Exception:
                    traceback.print_exc(file=sys.stderr)
                send({"type": "ready", "name": RoyaleBot.name})
            elif kind == "obs":
                latest = message
            elif kind == "end":
                return
        if latest is None:
            continue
        try:
            action = bot.act(latest) if bot is not None else {}
            last_continuous = {key: action[key] for key in ("move", "boost", "aim") if key in action}
        except Exception:
            traceback.print_exc(file=sys.stderr)
            action = dict(last_continuous)
        action["tick"] = latest["tick"]
        try:
            send(action)
        except (TypeError, ValueError):
            traceback.print_exc(file=sys.stderr)
            send({"tick": latest["tick"], **last_continuous})


if __name__ == "__main__":
    main()
