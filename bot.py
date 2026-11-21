#!/usr/bin/env python3
"""A survival-first, map-aware bot for the mechfight/1 protocol.

The controller is deliberately standard-library-only.  It loads the arena during
the generous hello budget, then keeps every decision small and deterministic.
"""

from __future__ import annotations

from array import array
from collections import defaultdict
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
    name = "edgewise-v5"

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
        self.turn_sign = 1.0 if self.rng.random() < 0.5 else -1.0
        self.pickup_radius = float(self.cfg["loot"]["pickup_radius"])

    def _inside_margin(self, point, center, radius):
        return radius - distance2(point, center)

    def _upgrade_pick(self, offer):
        choices = offer.get("choices") or []
        return max(range(len(choices)), key=lambda i: UPGRADE_SCORE.get(choices[i].get("id"), 40)) if choices else None

    def _weapon_slots(self, me):
        return {weapon["kind"]: (i, weapon) for i, weapon in enumerate(me["weapons"]) if weapon is not None}

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
        pos = obs["self"]["pos"]
        dodge = [0.0, 0.0]
        danger = False
        for projectile in obs.get("projectiles", []):
            rel = (projectile["pos"][0] - pos[0], projectile["pos"][1] - pos[1])
            vel = (projectile["vel"][0], projectile["vel"][1])
            speed_sq = dot2(vel, vel)
            if speed_sq < 4.0:
                continue
            approach = -dot2(rel, vel) / speed_sq
            if 0.0 <= approach <= 0.9:
                miss = (rel[0] + vel[0] * approach, rel[1] + vel[1] * approach)
                danger_radius = 11.0 if projectile["kind"] == "mortar" else 4.5
                if length2(miss) < danger_radius:
                    side = self.turn_sign if rel[0] * vel[1] - rel[1] * vel[0] >= 0 else -self.turn_sign
                    perpendicular = unit((-vel[1] * side, vel[0] * side))
                    dodge[0] += perpendicular[0]
                    dodge[1] += perpendicular[1]
                    danger = True
            if projectile["kind"] == "mortar" and distance2(projectile["pos"], pos) < 18.0:
                away = unit((pos[0] - projectile["pos"][0], pos[1] - projectile["pos"][1]))
                dodge[0] += 1.5 * away[0]
                dodge[1] += 1.5 * away[1]
                danger = True
        return dodge, danger

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
        if not enemies:
            return None
        pos = obs["self"]["pos"]
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
        return min(enemies, key=score)

    def _direct_aim(self, me, target, speed=None):
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
        dx, dy, dz = (point + rate * lead - origin for point, rate, origin in zip(target_pos, velocity, muzzle))
        return [math.atan2(dy, dx), math.atan2(dz, math.hypot(dx, dy))]

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
        return solution

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
        gun_aim = self._direct_aim(me, target, float(gun["stats"]["speed"]))
        aim = gun_aim

        def aligned(direction):
            return abs(angle_diff(me["aim"][0], direction[0])) < 0.11 and abs(me["aim"][1] - direction[1]) < 0.13

        if "laser" in slots:
            slot, weapon = slots["laser"]
            charge = float(weapon.get("charge", 0.0))
            stats = weapon["stats"]
            laser_aim = self._direct_aim(me, target)
            if charge > 0.0:
                full = float(stats["charge"])
                if charge >= full - 0.15:
                    aim = laser_aim
                fire[slot] = charge < full - 0.05 or not aligned(laser_aim)
            elif weapon.get("cooldown", 0.0) <= 0.05 and distance <= stats["range"]:
                fire[slot] = aligned(laser_aim)
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
            yaw = self._direct_aim(me, target)[0]
            cone = math.radians(stats["cone_deg"]) * 0.5
            fire[slot] = (
                distance < stats["range"] * 0.97 and me["energy"] >= stats["energy"] + 8.0
                and weapon.get("cooldown", 0.0) <= 0.1
                and abs(angle_diff(me["aim"][0], yaw)) < cone * 0.85
                and abs(angle_diff(aim[0], yaw)) < cone * 0.85
            )

        compatible_aim = abs(angle_diff(aim[0], gun_aim[0])) < 0.015 and abs(aim[1] - gun_aim[1]) < 0.02
        fire[gun_slot] = (
            not reloading and ammo > 0 and compatible_aim and aligned(aim)
            and distance < gun["stats"].get("range", 230.0)
        )
        reload_now = not reloading and (ammo == 0 or (ammo <= 2 and target["kind"] == "mech" and not compatible_aim))
        return aim, fire, reload_now

    def act(self, obs):
        me = obs["self"]
        pos = me["pos"]
        now = float(obs["t"])
        for event in obs.get("events", []):
            if event.get("type") == "damaged" and event.get("bearing") is not None:
                self.damage_bearing = float(event["bearing"])
                self.damage_until = now + 3.2
                self.attacker = event.get("by")

        enemies = [actor for actor in obs.get("mechs", []) if actor["kind"] == "mech"]
        target = self._primary_enemy(obs, enemies)
        zone_vector, urgent, current_margin, next_margin = self._zone_plan(obs)
        threat, closest = self._threat_vector(obs, enemies)
        dodge, projectile_danger = self._projectile_dodge(obs)
        threatened = closest < 105.0 or now < self.damage_until or projectile_danger

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

        desired = [objective[0] * (1.6 if urgent else 0.9), objective[1] * (1.6 if urgent else 0.9)]
        desired[0] += threat[0] * 1.75 + dodge[0] * 2.8
        desired[1] += threat[1] * 1.75 + dodge[1] * 2.8
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

        move_scale = 1.0 if threatened or urgent or stuck else objective_speed
        move = [steered[0] * move_scale, steered[1] * move_scale]
        energy = float(me["energy"])
        boost = bool((urgent and energy > 22.0) or (threatened and energy > 46.0) or (stuck and energy > 25.0))
        jump = bool(me["grounded"] and (projectile_danger or stuck or (closest < 32.0 and now < self.damage_until)) and now - self.last_jump > 1.15)
        if jump:
            self.last_jump = now

        aim, fire, reload_now = self._combat(obs, target or (dict(crate, kind="crate") if crate else None))
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
