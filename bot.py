#!/usr/bin/env python3
"""A survival-first, map-aware bot for the mechfight/1 protocol.

The controller is deliberately standard-library-only.  It loads the arena during
the generous hello budget, then keeps every decision small and deterministic.
"""

from __future__ import annotations

from array import array
from collections import defaultdict, deque
import heapq
import json
import math
import os
import queue
import random
import sys
import threading
import time
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
        self.max_step = float(hello["config"]["mech"]["step_up"])
        self.max_slope = math.tan(math.radians(float(hello["config"]["mech"]["max_slope_deg"])))
        try:
            map_dir = hello["map"]["dir"]
            with open(os.path.join(map_dir, "v2", "world.json"), "r", encoding="utf-8") as handle:
                world = json.load(handle)
            self.bounds = world["bounds"]
            terrain = world["terrain"]
            self.origin_x = float(terrain["minX"])
            self.origin_y = float(terrain["minY"])
            self.inv_step = 1.0 / float(terrain["step"])
            self.width = int(terrain["nx"])
            self.rows = int(terrain["ny"])
            self.height_scale = float(terrain.get("scale", 0.01))
            raw = array("h")
            with open(os.path.join(map_dir, "v2", terrain["file"]), "rb") as handle:
                raw.fromfile(handle, self.width * self.rows)
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
        fx = (x - self.origin_x) * self.inv_step
        fy = (y - self.origin_y) * self.inv_step
        nx = self.width
        if 0.0 <= fx < nx - 1 and 0.0 <= fy < self.rows - 1:
            i, j = int(fx), int(fy)
            tx, ty = fx - i, fy - j
        else:
            i, j = int(clamp(fx, 0.0, nx - 1.001)), int(clamp(fy, 0.0, self.rows - 1.001))
            tx, ty = clamp(fx - i, 0.0, 1.0), clamp(fy - j, 0.0, 1.0)
        p = j * nx + i
        h00 = self.heights[p]
        h10 = self.heights[p + 1]
        h01 = self.heights[p + nx]
        h11 = self.heights[p + nx + 1]
        value = h00 * (1.0 - tx) * (1.0 - ty) + h10 * tx * (1.0 - ty) + h01 * (1.0 - tx) * ty + h11 * tx * ty
        return value * self.height_scale

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


class GroundRoutes:
    """Find walkable routes across the arena using the static ground layer."""

    SPACING = 7.0
    RADIUS = 3.0
    HEIGHT = 9.0

    def __init__(self, arena):
        self.arena = arena
        self.points = {}
        self.links = {}
        self.regions = {}
        self.gates = {}
        if not arena.ok:
            return
        spacing = self.SPACING
        bounds = arena.bounds
        for ix in range(math.ceil((bounds["minX"] + self.RADIUS) / spacing),
                        math.floor((bounds["maxX"] - self.RADIUS) / spacing) + 1):
            x = ix * spacing
            for iy in range(math.ceil((bounds["minY"] + self.RADIUS) / spacing),
                            math.floor((bounds["maxY"] - self.RADIUS) / spacing) + 1):
                y = iy * spacing
                z = arena.ground(x, y)
                if not arena.blocked(x, y, z, self.RADIUS, self.HEIGHT):
                    self.points[ix, iy] = (x, y, z)
                    self.links[ix, iy] = []
        for (ix, iy), start in self.points.items():
            for dx, dy in ((1, 0), (0, 1), (1, 1), (1, -1)):
                neighbor = (ix + dx, iy + dy)
                end = self.points.get(neighbor)
                if end is None or not arena.ground_leg_clear(start, end, self.RADIUS, self.HEIGHT):
                    continue
                cost = distance2(start, end) + 0.23 * abs(end[2] - start[2])
                self.links[ix, iy].append((neighbor, cost))
                self.links[neighbor].append(((ix, iy), cost))
        self._build_jet_gates()

    def _build_jet_gates(self):
        unseen = set(self.links)
        parts = []
        while unseen:
            members = [unseen.pop()]
            for node in members:
                self.regions[node] = len(parts)
                for neighbor, _ in self.links[node]:
                    if neighbor in unseen:
                        unseen.remove(neighbor)
                        members.append(neighbor)
            parts.append(members)
        if len(parts) < 2:
            return
        largest = max(range(len(parts)), key=lambda i: len(parts[i]))
        offsets = [(x, y) for x in range(-3, 4) for y in range(-3, 4) if 2 <= x * x + y * y <= 13]
        for region, members in enumerate(parts):
            if region == largest or len(members) < 15:
                continue
            options = []
            for x, y in members:
                start = self.points[x, y]
                for dx, dy in offsets:
                    neighbor = x + dx, y + dy
                    destination = self.regions.get(neighbor)
                    if destination is None or destination == region or len(parts[destination]) < 15:
                        continue
                    end = self.points[neighbor]
                    climb = max(0.0, end[2] - start[2])
                    if climb <= 27.0:
                        cost = distance2(start, end) + 1.3 * climb + (0.0 if destination == largest else 21.0)
                        options.append((cost, start, end, destination))
            gates = []
            for _, start, end, destination in sorted(options)[:300]:
                if any(distance2(gate[0], start) < 16.0 for gate in gates):
                    continue
                altitude = self._flight_height(start, end)
                if altitude is None:
                    continue
                energy = 16.0 + 15.0 * ((altitude - start[2]) / 8.9 + distance2(start, end) / 12.0 + 0.5)
                if energy <= 98.0:
                    gates.append((start, end, altitude, energy, destination == largest))
                if len(gates) >= 10:
                    break
            if gates:
                self.gates[region] = gates

    def _flight_height(self, start, end):
        steps = math.ceil(distance2(start, end) / 2.4)
        samples = [(start[0] + (end[0] - start[0]) * step / steps,
                    start[1] + (end[1] - start[1]) * step / steps) for step in range(steps + 1)]
        terrain_top = max(self.arena.ground(x, y) for x, y in samples)
        for altitude in range(math.ceil(terrain_top + 2.1), math.floor(start[2] + 30.0), 2):
            if any(self.arena.blocked(x, y, altitude, self.RADIUS, self.HEIGHT) for x, y in samples):
                continue
            if any(self.arena.blocked(point[0], point[1], z, self.RADIUS, self.HEIGHT)
                   for point in (start, end) for z in range(math.ceil(point[2] + 1.0), altitude, 2)):
                continue
            return altitude
        return None

    def _entry(self, origin):
        spacing = self.SPACING
        ix, iy = (round(value / spacing) for value in origin[:2])
        nodes = [(ix + dx, iy + dy) for dx in range(-2, 3) for dy in range(-2, 3)]
        nodes = sorted((node for node in nodes if self.links.get(node)),
                       key=lambda node: distance2(origin, self.points[node]))
        return next((node for node in nodes if self.arena.ground_leg_clear(
            origin, self.points[node], self.RADIUS, self.HEIGHT)), None)

    def jet_gate(self, origin, center, radius):
        if not self.gates or distance2(origin, center) < radius or abs(origin[2] - self.arena.ground(*origin[:2])) > 3.2:
            return None
        node = self._entry(origin)
        options = self.gates.get(self.regions.get(node), ())
        return min(options, key=lambda gate: distance2(origin, gate[0]) + 0.35 * distance2(center, gate[1])
                   + gate[3] * 0.25 + (0.0 if gate[4] else 45.0), default=None)

    def route(self, origin, center, radius):
        if not self.points or abs(origin[2] - self.arena.ground(*origin[:2])) > 2.2:
            return []
        deadline = time.perf_counter() + 0.014
        start = self._entry(origin)
        if start is None:
            return []

        def remaining(node):
            return max(0.0, distance2(self.points[node], center) - radius)

        costs = {start: 0.0}
        parents = {start: None}
        initial_gap = remaining(start)
        queue = [(initial_gap * 1.12, 0.0, start)]
        best = start
        best_gap = initial_gap
        while queue and time.perf_counter() < deadline:
            _, cost, node = heapq.heappop(queue)
            if cost != costs[node]:
                continue
            gap = remaining(node)
            if gap < best_gap:
                best, best_gap = node, gap
            if gap == 0.0:
                break
            for neighbor, step in self.links[node]:
                travel = cost + step
                if travel < costs.get(neighbor, math.inf):
                    costs[neighbor] = travel
                    parents[neighbor] = node
                    heapq.heappush(queue, (travel + 1.12 * remaining(neighbor), travel, neighbor))
        if best_gap > 0.0 and initial_gap - best_gap < self.SPACING:
            return []
        route = []
        while best is not None:
            route.append(self.points[best])
            best = parents[best]
        return route[::-1]


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
    name = "edgewise-v14"

    def __init__(self, hello):
        self.cfg = hello["config"]
        self.rng = random.Random(int(hello.get("seed", 0)))
        self.arena = Arena(hello)
        self.routes = GroundRoutes(self.arena)
        self.zone_route = deque()
        self.zone_route_goal = None
        self.zone_route_retry = -1.0
        self.weapon_memory = {}
        self.weapon_goal = None
        self.weapon_route = deque()
        self.weapon_route_retry = -1.0
        self.drop_goal = None
        self.drop_route = deque()
        self.drop_route_retry = -1.0
        self.jet_route = deque()
        self.jet_gate = None
        self.jet_retry = -1.0
        self.jet_phase = None
        self.jet_started = -1.0
        self.focus = None
        self.focus_until = -1.0
        self.damage_sources = {}
        self.last_move = (0.0, 0.0)
        self.last_pos = None
        self.last_t = 0.0
        self.stuck_ticks = 0
        self.last_jump = -10.0
        self.scan_yaw = self.rng.uniform(-math.pi, math.pi)
        self.damage_bearing = None
        self.damage_until = -1.0
        self.attacker = None
        self.last_kill = -math.inf
        self.escape_boost = False
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
            if self.focus in defeated:
                self.focus = None
            if self.attacker in defeated:
                self.attacker = self.damage_bearing = None
                self.damage_until = -1.0
        for event in events:
            kind = event.get("type")
            if kind == "damaged" and event.get("by") not in defeated:
                if event.get("bearing") is not None:
                    self.damage_bearing = float(event["bearing"])
                    self.damage_until = now + 3.2
                    self.attacker = event.get("by")
                if event.get("by") is not None:
                    source = event["by"]
                    when, amount = self.damage_sources.get(source, (now, 0.0))
                    self.damage_sources[source] = (now, event["amount"] + amount * math.exp(-(now - when) / 1.9))

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
        """Keep pressure on an exposed pursuer when the trade is sustainable."""
        if target is None or urgent or current_margin < 28.0:
            return None
        me = obs["self"]
        pos = me["pos"]
        foe = target["pos"]
        distance = distance2(pos, foe)
        if not 13.0 < distance < 152.0 or me["hp"] < me["max_hp"] * 0.42:
            return None
        if obs["t"] - self.last_kill < 2.0 and any(distance2(pos, c["pos"]) < 16.0 for c in obs.get("cubes", ())):
            return None
        support = sum(enemy["id"] != target["id"] and distance2(pos, enemy["pos"]) < 62.0 for enemy in enemies)
        if support > 1 or (support and obs["t"] < self.damage_until and self.attacker != target["id"]):
            return None
        weapons = self._weapon_slots(me)
        enemy_hp = target.get("hp")
        finishing = enemy_hp is not None and enemy_hp < min(420.0, me["hp"] * 0.55)
        if finishing and support:
            return None
        if len(weapons) == 1 and enemy_hp is not None and enemy_hp > me["hp"] * 1.18:
            return None
        if not finishing and not self.arena.shot_clear(*self._direct_line(me, target)):
            return None
        preferred = 91.0 if "laser" in weapons else 66.0
        if "lightning" in weapons and me["energy"] > 19.0 and not support:
            if "laser" not in weapons or "laser" in (target.get("weapons") or ()) or finishing:
                preferred = 34.0
        if finishing:
            preferred = min(preferred, 36.0)
        away = unit((pos[0] - foe[0], pos[1] - foe[1]))
        radial = clamp((preferred - distance) / 25.0, -1.4 if finishing else -0.96, 1.15)
        tangent = (-away[1] * self.turn_sign, away[0] * self.turn_sign)
        return unit((0.76 * tangent[0] + radial * away[0], 0.76 * tangent[1] + radial * away[1]))

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
        slot = self._weapon_slots(me).get("gun")
        if slot is None:
            return None
        gun = slot[1]
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
            if pickup["kind"] in {"laser", "lightning"} and pickup["kind"] not in held
            and abs(pickup["pos"][2] - pos[2]) <= 4.0
            and self._inside_margin(pickup["pos"], zone["center"], zone["radius"]) > 10.0
        ]
        if not choices:
            return None
        preference = {"laser": 8.0, "lightning": 0.0}
        return min(choices, key=lambda p: distance2(pos, p["pos"]) - preference.get(p["kind"], 0.0))

    def _arm_action(self, obs, enemies, urgent, current_margin, next_margin, danger, action):
        """Reach an uncontested secondary weapon or trade the gun for a second one."""
        me, now = obs["self"], obs["t"]
        pos = me["pos"]
        held = self._weapon_slots(me)
        if "gun" not in held:
            return
        visible = {p["id"]: p for p in obs.get("pickups", ()) if p["kind"] in {"laser", "lightning"}}
        self.weapon_memory.update((identifier, (now, pickup)) for identifier, pickup in visible.items())
        self.weapon_memory = {
            identifier: (seen, pickup) for identifier, (seen, pickup) in self.weapon_memory.items()
            if now - seen < 28.0 and (identifier in visible or distance2(pos, pickup["pos"]) > 9.0)
        }
        reachable = [p for p in visible.values() if p["kind"] not in held
                     and distance2(p["pos"], pos) <= self.pickup_radius and abs(p["pos"][2] - pos[2]) < 4.0]
        if reachable:
            pickup = min(reachable, key=lambda p: (p["kind"] != "laser", distance2(pos, p["pos"])))
            action["take"] = pickup["id"]
            if len(held) == 2:
                action["drop"] = held["gun"][0]
            return
        if not self.arena.ok or urgent or current_margin < 29.0 or not me["grounded"] or action["jump"] or danger:
            return
        nearest = min((distance2(pos, enemy["pos"]) for enemy in enemies), default=250.0)
        if nearest < (40.0 if len(held) == 1 else 54.0) or me["hp"] < 0.47 * me["max_hp"]:
            return
        max_travel = min(86.0 if len(held) == 1 else 42.0, (nearest - 29.0) * 0.84)
        zone = obs["zone"]
        choices = []
        for identifier, (_, pickup) in self.weapon_memory.items():
            point = pickup["pos"]
            gap = distance2(pos, point)
            if pickup["kind"] in held or gap > max_travel or abs(point[2] - self.arena.ground(*point[:2])) > 3.3:
                continue
            if self._inside_margin(point, zone["center"], zone["radius"]) < 21.0:
                continue
            if next_margin < 35.0 and self._inside_margin(point, zone["next_center"], zone["next_radius"]) < next_margin - 21.0:
                continue
            if any(distance2(point, foe["pos"]) < gap * 0.85 + 12.0 for foe in enemies):
                continue
            cost = gap - (21.0 if pickup["kind"] == "laser" else 0.0) - (8.0 if identifier == self.weapon_goal else 0.0)
            choices.append((cost, identifier, point))
        if not choices:
            self.weapon_goal = None
            self.weapon_route.clear()
            return
        _, identifier, point = min(choices)
        if identifier != self.weapon_goal:
            self.weapon_goal = identifier
            self.weapon_route.clear()
            self.weapon_route_retry = -1.0
        radius = me["stats"].get("radius", 2.5)
        height = me["stats"].get("height", 9.0)
        if self.arena.ground_leg_clear(pos, point, radius, height):
            waypoint = point
        else:
            if not self.weapon_route and now >= self.weapon_route_retry:
                self.weapon_route.extend(self.routes.route(pos, point[:2], 6.2))
                self.weapon_route_retry = now + 1.0
            while self.weapon_route and distance2(pos, self.weapon_route[0]) < 4.4:
                self.weapon_route.popleft()
            if not self.weapon_route or distance2(pos, self.weapon_route[0]) > 22.0:
                return
            waypoint = self.weapon_route[0]
        direction = unit((waypoint[0] - pos[0], waypoint[1] - pos[1]))
        steered, _ = self.arena.steer(pos, direction, action["move"], radius, height, (*zone["center"], zone["radius"]))
        gap = distance2(pos, point)
        speed = 0.60 if gap < 8.0 else 1.0
        action["move"] = [steered[0] * speed, steered[1] * speed]
        if nearest > 135.0 and me["energy"] > 70.0 and gap > 26.0:
            action["boost"] = True

    def _supply_action(self, obs, enemies, urgent, margin, danger, action):
        """Plan the approach while a supply drop is still inbound and hold its capture ring."""
        drops = obs.get("drops", ())
        me, zone, now = obs["self"], obs["zone"], obs["t"]
        if (not drops or not self.arena.ok or urgent or margin < 26.0 or danger or not me["grounded"]
                or self.jet_gate is not None or self.jet_phase is not None or action["jump"] or action.get("take")):
            return

        pos, stats = me["pos"], me["stats"]
        speed = stats.get("walk_speed", 11.7)
        choices = []
        for drop in drops:
            if drop.get("holder") not in (None, me["id"]):
                continue
            point = drop["pos"]
            distance = distance2(pos, point)
            arrival = max(0.0, distance - 5.0) / speed
            lands_in = drop.get("lands_in", 0.0)
            if distance > 265.0 or arrival > max(11.0, lands_in + 5.0):
                continue
            if self._inside_margin(point, zone["center"], zone["radius"]) < 24.0:
                continue
            if self._inside_margin(point, zone["next_center"], zone["next_radius"]) < 12.0 and arrival + 9.0 > zone["shrink_end"] - now:
                continue
            choices.append((arrival - lands_in * 0.22, drop["id"], drop))
        if not choices:
            self.drop_goal = None
            self.drop_route.clear()
            return

        _, identifier, drop = min(choices)
        point = drop["pos"]
        gap = distance2(pos, point)
        lands_in = drop.get("lands_in", 0.0)
        if self.weapon_goal is not None and gap > 13.0 and lands_in > 8.0 and me.get("cubes", 0) < 3:
            if all(weapon is None or weapon["kind"] == "gun" for weapon in me["weapons"]):
                return
        for enemy in sorted(enemies, key=lambda foe: distance2(pos, foe["pos"]))[:3]:
            near = distance2(pos, enemy["pos"])
            if near < 42.0 or distance2(point, enemy["pos"]) < 36.0:
                return
            if now < self.damage_until and near < 99.0:
                muzzle = (*enemy["pos"][:2], enemy["pos"][2] + 7.0)
                if self.arena.shot_clear(muzzle, (*pos[:2], pos[2] + 4.5)):
                    return

        if self.drop_goal != identifier:
            self.drop_goal = identifier
            self.drop_route.clear()
            self.drop_route_retry = -1.0
        capture_radius = self.cfg["drops"]["radius"]
        if gap < capture_radius - 2.2 and abs(pos[2] - point[2]) < 3.5:
            action.update(move=[0.0, 0.0], boost=False)
            return

        radius, height = stats.get("radius", 2.5), stats.get("height", 9.0)
        heading = unit((point[0] - pos[0], point[1] - pos[1]))
        check = min(gap, 42.0)
        probe = (pos[0] + heading[0] * check, pos[1] + heading[1] * check)
        if self.arena.ground_leg_clear(pos, (*probe, self.arena.ground(*probe)), radius, height):
            self.drop_route.clear()
        else:
            if not self.drop_route and now >= self.drop_route_retry:
                route = self.routes.route(pos, point[:2], 5.6)
                if route and distance2(route[-1], point) <= 15.0:
                    self.drop_route.extend(route)
                self.drop_route_retry = now + 1.4
            while self.drop_route and distance2(pos, self.drop_route[0]) < 4.1:
                self.drop_route.popleft()
            if not self.drop_route or distance2(pos, self.drop_route[0]) > 24.0:
                return
            x, y = self.drop_route[0][:2]
            heading = unit((x - pos[0], y - pos[1]))
        heading, _ = self.arena.steer(pos, heading, action["move"], radius, height, (*zone["center"], zone["radius"]))
        magnitude = 0.52 if gap < capture_radius + 4.0 else 1.0
        action.update(move=[heading[0] * magnitude, heading[1] * magnitude],
                      boost=gap > 28.0 and me["energy"] > 64.0 and gap / speed > lands_in + 2.0)

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

    def _zone_route_plan(self, obs, urgent, current_margin, next_margin):
        """Start the rotation early enough to take a detour around arena solids."""
        me, zone = obs["self"], obs["zone"]
        pos, now = me["pos"], obs["t"]
        if not self.arena.ok or not me["grounded"]:
            return None, urgent
        radius = me["stats"].get("radius", 2.5)
        clearance = min(radius + 13.0, 0.40 * zone["next_radius"])
        speed = me["stats"].get("walk_speed", 11.7)
        debt = max(0.0, clearance - next_margin)
        time_left = zone["shrink_end"] - now
        outside = current_margin < radius + 3.0
        heading = None
        if outside or (debt > 0.0 and time_left < 25.0 + 1.7 * debt / speed):
            use_current = outside and current_margin + 18.0 > next_margin
            safe_center = tuple(zone["center"] if use_current else zone["next_center"])
            safe_radius = max(4.0, (zone["radius"] if use_current else zone["next_radius"]) - clearance)
            dx, dy = unit((safe_center[0] - pos[0], safe_center[1] - pos[1]))
            length = min(34.0, max(2.0, distance2(pos, safe_center) - safe_radius))
            point = (pos[0] + dx * length, pos[1] + dy * length)
            target = (*point, self.arena.ground(*point))
            goal = (safe_center, safe_radius)
            if self.zone_route_goal is None or distance2(safe_center, self.zone_route_goal[0]) > 12.0 or (
                abs(safe_radius - self.zone_route_goal[1]) > 14.0
            ):
                self.zone_route.clear()
                self.zone_route_retry = -1.0
            if not self.zone_route and now >= self.zone_route_retry and not self.arena.ground_leg_clear(
                pos, target, GroundRoutes.RADIUS, GroundRoutes.HEIGHT
            ):
                self.zone_route.extend(self.routes.route(pos, safe_center, safe_radius))
                self.zone_route_goal = goal
                self.zone_route_retry = now + 0.8
            while self.zone_route and distance2(pos, self.zone_route[0]) < 4.2:
                self.zone_route.popleft()
            while len(self.zone_route) > 1 and distance2(pos, self.zone_route[1]) < 27.0 and self.arena.ground_leg_clear(
                pos, self.zone_route[1], GroundRoutes.RADIUS, GroundRoutes.HEIGHT
            ):
                self.zone_route.popleft()
            if self.zone_route:
                waypoint = self.zone_route[0]
                if distance2(pos, waypoint) <= 29.0:
                    heading = unit((waypoint[0] - pos[0], waypoint[1] - pos[1]))
                else:
                    self.zone_route.clear()
            if heading is None:
                heading = (dx, dy)
            urgent = urgent or outside or time_left < 12.0 + debt / speed or bool(self.zone_route)
        else:
            self.zone_route.clear()
        return heading, urgent

    def _exit_terrain(self, obs, next_margin, action):
        """Cross a mapped jet route when the closing circle lies beyond the walking region."""
        me, zone, now = obs["self"], obs["zone"], obs["t"]
        pos = me["pos"]
        if self.jet_phase is not None:
            if now - self.jet_started > 8.0 or (me["grounded"] and now > self.jet_started + 0.25):
                self.jet_phase = self.jet_gate = None
                self.zone_route.clear()
                return
            _, landing, altitude, _, _ = self.jet_gate
            if self.jet_phase == "climb" and pos[2] >= altitude:
                self.jet_phase = "cross"
            if self.jet_phase == "cross" and distance2(pos, landing) <= 3.6:
                self.jet_phase = "land"
            direction = unit((landing[0] - pos[0], landing[1] - pos[1])) if self.jet_phase == "cross" else (0.0, 0.0)
            action["move"] = list(direction)
            action["boost"] = self.jet_phase == "climb" or (
                self.jet_phase == "cross" and pos[2] < altitude + 2.0 and me["vel"][2] < 3.0
            )
            action["jump"] = False
            return
        if not me["grounded"] or not self.arena.ok:
            return

        travel = max(0.0, 18.0 - next_margin)
        if travel < 18.0 or zone["shrink_end"] - now > 30.0 + travel * 0.15:
            self.jet_gate = None
            return
        if self.jet_gate is None and now >= self.jet_retry:
            self.jet_retry = now + 1.5
            gate = self.routes.jet_gate(pos, zone["next_center"], max(5.0, zone["next_radius"] - 17.0))
            if gate is not None:
                route = self.routes.route(pos, gate[0], 3.4)
                if route and distance2(route[-1], gate[0]) < 4.2:
                    self.jet_gate = gate
                    self.jet_route = deque(route)
        if self.jet_gate is None:
            return

        launch, _, _, energy, _ = self.jet_gate
        if distance2(pos, launch) < 3.3:
            action.update(move=[0.0, 0.0], boost=False, jump=False)
            if me["energy"] >= energy and length2(me["vel"]) < 4.0:
                self.jet_phase = "climb"
                self.jet_started = self.last_jump = now
                action["jump"] = True
            return
        while self.jet_route and distance2(pos, self.jet_route[0]) < 3.8:
            self.jet_route.popleft()
        waypoint = self.jet_route[0] if self.jet_route else launch
        if distance2(pos, waypoint) > 21.0:
            self.jet_gate = None
            return
        direction = unit((waypoint[0] - pos[0], waypoint[1] - pos[1]))
        action.update(move=list(direction), boost=False, jump=False)

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
        hazards = []
        self.bullet_clearance = {}
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
                        direct = (approach, miss, 1.0)
            impact = self._mortar_impact(me, origin, drift, gravity, splash + radius + 0.9) if is_mortar else None
            hazard = min((risk for risk in (direct, impact) if risk is not None), default=None)
            if hazard is None:
                continue
            hazards.append((hazard, relative_vel, None if is_mortar else projectile, endpoint if direct else None))
        for hazard, relative_vel, projectile, endpoint in sorted(hazards, key=lambda hit: hit[0][0])[:10]:
            if projectile is not None:
                clear = self.arena.shot_clear(projectile["pos"], endpoint)
                self.bullet_clearance[id(projectile)] = clear
                if not clear:
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

    def _steer_around_shots(self, obs, heading, zone_vector, urgent, boost):
        """Compare reachable headings against imminent straight-line shots."""
        me = obs["self"]
        x, y, z = me["pos"]
        vx, vy = me["vel"][:2]
        stats = me["stats"]
        radius = float(stats.get("radius", 2.5))
        height = float(stats.get("height", 9.0))
        speed = float(stats.get("boost_speed", 19.5) if boost else stats.get("walk_speed", 11.7))
        if any(weapon and weapon.get("charge", 0.0) > 0.0 for weapon in me["weapons"]):
            speed *= 0.5
        horizon = 0.56
        shots = []
        for projectile in obs.get("projectiles", ()):
            if projectile["kind"] == "mortar":
                continue
            px, py, pz = projectile["pos"]
            dx, dy, dz = projectile["vel"]
            delta = px - x, py - y
            motion = dx - vx, dy - vy
            t = -dot2(delta, motion) / max(EPS, dot2(motion, motion))
            if not 0.035 <= t <= horizon or not z - 1.5 <= pz + dz * t <= z + height + 1.5:
                continue
            if math.hypot(delta[0] + motion[0] * t, delta[1] + motion[1] * t) > radius + speed * t * 1.4:
                continue
            end = px + dx * t, py + dy * t, pz + dz * t
            shots.append((t, end, dx, dy, projectile))
        if not shots:
            return heading
        incoming = []
        clearance = getattr(self, "bullet_clearance", {})
        for t, point, dx, dy, projectile in sorted(shots, key=lambda shot: shot[0])[:10]:
            clear = clearance.get(id(projectile))
            if clear is None:
                clear = self.arena.shot_clear(projectile["pos"], point)
            if clear:
                incoming.append((t, point, dx, dy))
                if len(incoming) == 8:
                    break
        if not incoming:
            return heading
        acceleration = float(self.cfg.get("mech", {}).get("ground_accel", 58.5))
        start = math.atan2(heading[1], heading[0])
        best_cost = math.inf
        best = heading
        for offset in (0.0, 0.42, -0.42, 0.84, -0.84, 1.28, -1.28, 1.78, -1.78):
            angle = start + offset
            candidate = math.cos(angle), math.sin(angle)
            if urgent and dot2(candidate, zone_vector) < min(0.35, dot2(heading, zone_vector)) - 0.06:
                continue
            if offset and not self.arena.direction_clear(me["pos"], angle, radius, height):
                continue
            dvx, dvy = candidate[0] * speed - vx, candidate[1] * speed - vy
            ramp = math.hypot(dvx, dvy) / acceleration
            cost = 0.31 * (1.0 - math.cos(offset))
            for t, point, dx, dy in incoming:
                elapsed = min(t, ramp)
                # Integrate acceleration until the commanded velocity is reached.
                push = t - elapsed + 0.5 * elapsed * elapsed / max(EPS, ramp)
                rx = point[0] - x - vx * t - dvx * push
                ry = point[1] - y - vy * t - dvy * push
                wx = dx - vx - dvx * elapsed / max(EPS, ramp)
                wy = dy - vy - dvy * elapsed / max(EPS, ramp)
                miss = abs(rx * wy - ry * wx) / max(EPS, math.hypot(wx, wy))
                risk = clamp((radius + 0.65 - miss) / 1.55, 0.0, 1.0)
                cost += risk * (1.0 - 0.55 * t)
            if cost < best_cost:
                best_cost, best = cost, candidate
        return best

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
        if len(enemies) == 1:
            self.focus = enemies[0]["id"]
            return enemies[0]
        me = obs["self"]
        now = obs["t"]
        pos = me["pos"]
        slots = self._weapon_slots(me)
        ranked = sorted(enemies, key=lambda enemy: distance2(pos, enemy["pos"]))[:6]
        scale = 1.0 + 0.04 * min(me.get("cubes", 0), 10)
        get_gun = slots.get("gun")
        get_laser = slots.get("laser")
        get_lightning = slots.get("lightning")
        gun_rate = 0.0 if get_gun is None else 90.0 * max(0.45, 1.0 - get_gun[1].get("reload", 0.0) * 0.19)
        laser_rate = 0.0 if get_laser is None else 127.0
        lightning_range = 0.0 if get_lightning is None or me["energy"] <= 13.0 else get_lightning[1]["stats"]["range"]
        best = None
        best_value = -math.inf
        for enemy in ranked:
            identifier = enemy["id"]
            dx, dy = enemy["pos"][0] - pos[0], enemy["pos"][1] - pos[1]
            distance = math.hypot(dx, dy)
            if distance > 242.0:
                continue
            yaw = math.atan2(dy, dx)
            angle = abs(angle_diff(yaw, me["aim"][0]))
            turn_time = angle / me["stats"].get("turn_rate", 4.0)
            damage_rate = 22.0 + gun_rate * clamp(1.13 - distance / 478.0, 0.35, 1.0)
            damage_rate += laser_rate * (1.6 if "mortar" in (enemy.get("weapons") or ()) else 1.0)
            if distance < lightning_range:
                damage_rate += 163.0
            enemy_aim = enemy.get("aim")
            if enemy_aim is not None and abs(angle_diff(yaw + math.pi, enemy_aim[0])) > 1.75:
                damage_rate *= 1.36
            hp = enemy.get("hp")
            hp = 1100.0 if hp is None else hp
            finish_time = hp / (damage_rate * scale)
            value = -0.21 * finish_time - 0.015 * distance - 0.68 * turn_time
            value += 0.9 if hp < 200.0 else 0.0
            value += 0.60 if enemy.get("charging") else 0.0
            timestamp, incoming = self.damage_sources.get(identifier, (now, 0.0))
            recent = incoming * math.exp(-(now - timestamp) / 2.1)
            value += min(1.8, recent / 120.0)
            if recent > 80.0 and angle > 1.6:
                value += min(1.2, (angle - 1.6) * 1.2)
            if identifier == self.focus and now < self.focus_until:
                value += 0.48
            if value > best_value and self.arena.shot_clear(*self._direct_line(me, enemy)):
                best, best_value = enemy, value
        if best is None:
            return next((enemy for enemy in ranked if enemy["id"] == self.attacker and now < self.damage_until), ranked[0])
        if best["id"] != self.focus:
            self.focus = best["id"]
            self.focus_until = now + 1.35
        return best

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
        gun_slot, gun = slots.get("gun", (None, {}))
        ammo = gun.get("ammo", 12) if gun else 0
        reloading = not gun or gun.get("reload", 0.0) > 0.0
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
        gun_line = self._direct_line(me, target, float(gun["stats"]["speed"]) if gun else None)
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
        if "lightning" in slots and target["kind"] == "mech":
            slot, weapon = slots["lightning"]
            stats = weapon["stats"]
            lightning_line = self._direct_line(me, target)
            yaw = self._aim_between(*lightning_line)[0]
            cone = math.radians(stats["cone_deg"]) * 0.5
            fire[slot] = (
                distance < stats["range"] * 0.97 and me["energy"] >= stats["energy"] + (8.0 if gun else 5.0)
                and weapon.get("cooldown", 0.0) <= 0.1
                and abs(angle_diff(me["aim"][0], yaw)) < cone * 0.85
                and abs(angle_diff(aim[0], yaw)) < cone * 0.85
                and self.arena.shot_clear(*lightning_line)
            )

        compatible_aim = abs(angle_diff(aim[0], gun_aim[0])) < 0.015 and abs(aim[1] - gun_aim[1]) < 0.02
        if gun:
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
        route_heading, urgent = self._zone_route_plan(obs, urgent, current_margin, next_margin)
        zone_vector = route_heading or zone_vector
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

        if urgent:
            desired = [zone_vector[i] * 3.6 + dodge[i] * 3.4 + threat[i] * 0.55 for i in range(2)]
        elif duel is not None:
            desired = [duel[i] * 2.1 + zone_vector[i] * 0.20 + dodge[i] * 3.3 for i in range(2)]
        else:
            threat_weight = 0.24 if reward is not None or farm is not None else 1.75
            desired = [objective[i] * 0.9 + threat[i] * threat_weight + dodge[i] * 2.8 for i in range(2)]
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
        energy = float(me["energy"])
        if urgent or stuck:
            boost = (urgent and energy > 22.0) or (threatened and energy > 46.0) or (stuck and energy > 25.0)
        elif projectile_danger and me["grounded"]:
            boost = energy > 24.0
        elif farm is not None or not me["grounded"]:
            boost = False
        else:
            close_enemies = sum(distance2(pos, enemy["pos"]) < 110.0 for enemy in enemies)
            needs_escape = close_enemies > 1 or now < self.damage_until
            boost = needs_escape and energy > (36.0 if self.escape_boost else 65.0)
        if projectile_danger and me["grounded"] and not stuck:
            steered = self._steer_around_shots(obs, steered, zone_vector, urgent, boost)
        move = [steered[0] * move_scale, steered[1] * move_scale]
        self.escape_boost = boost
        jump = bool(me["grounded"] and stuck and now - self.last_jump > 1.15)
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

        self._arm_action(obs, enemies, urgent, current_margin, next_margin, projectile_danger, action)
        self._exit_terrain(obs, next_margin, action)
        self._supply_action(obs, enemies, urgent, current_margin, projectile_danger, action)

        self.last_move = tuple(action["move"])
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
