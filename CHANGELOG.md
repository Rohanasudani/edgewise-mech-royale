# Changelog

All notable bot changes are recorded here. Practice submissions also belong in `SUBMISSIONS.md` with their exact Git commit and score.

## 0.10.0 - 2026-11-21

- Prioritize inward movement when the closing zone becomes urgent under fire.
- Keep ground acceleration for projectile dodges and reserve jumps for getting unstuck.
- Use sustained boost bursts under pressure and preserve energy between fights.

## 0.9.0 - 2026-11-21

- Use recent damage and weapon availability to identify favorable isolated fights and stay within effective range.
- Shorten laser charges when the projected damage is enough to finish a weak target.
- Collect unclaimed power cubes along walkable approaches when nearby opponents cannot contest them.
- Clear stale threat state on a confirmed kill and acquire early upgrades from safe nearby crates or drones.

## 0.8.0 - 2026-11-21

- Restore the 0.6 opening after the early pickup experiment reduced practice placement.
- Dodge on the side opposite the predicted shot, including when already moving.
- Ignore overhead bullets and evaluate nearby mortar blasts at their projected ground impact.

## 0.7.0 - 2026-11-21

- Plan bounded ground routes to early weapon pickups, including detours around arena obstacles.
- Remember briefly obscured pickups and reserve boost energy while approaching.
- Yield to incoming projectiles, urgent zone movement, and unsafe approaches.

## 0.6.0 - 2026-11-21

- Check lead-corrected gun lines, beams, and mortar arcs against buildings and terrain before firing.
- Prefer an opponent with a clear firing lane when the first choice is behind a solid obstacle.

## 0.5.0 - 2026-11-21

- Return to the 0.1 survival and steering policy.
- Fall back to the gun while the mortar cools and account for projectile travel, barrel offset, and target size.
- Favor wounded opponents and acquire a second weapon earlier when safe.

## 0.4.1

- Check ground clearance along cover routes and follow waypoints around obstacles.
- Account for elevation when evaluating building cover.
- Time repairs from the last damage dealt or received, with separate limits for travel and holding cover.

## 0.4.0 - 2026-11-20

- Return to the 0.1 combat and energy behavior after the rooftop experiment underperformed.
- Pre-sample building walls and outward normals from the real arena geometry.
- Route to nearby ground cover only when a building provides at least eight meters of solid protection along the attack line.
- Hold quietly after breaking sight, re-plan when the pursuer changes angle, and abandon cover when the zone becomes urgent.

## 0.3.0 - 2026-11-20

- Restore the active return-fire behavior from 0.1 after the disengagement experiment scored poorly.
- Index safe interior points and clearance for building and stadium roofs.
- Commit to reachable jet-assisted roof climbs when pressured and safely inside the zone.
- Reserve enough energy to complete climbs and apply timeouts and cooldowns to failed attempts.

## 0.2.0 - 2026-11-20

- Add persistent disengagement when damaged below 72% health.
- Suppress distant and unnecessary fire so health regeneration can start and the bot makes less noise.
- Keep facing threats during disengagement to protect rear armor.
- Permit close-range escape fire and late-game aggression.
- Avoid loot detours while repairing and spend boost energy more readily under pressure.

## 0.1.0 - 2026-11-20

- Add a deterministic `mechfight/1` protocol loop.
- Add map-aware local steering and terrain checks.
- Add survival-first zone, threat, projectile, loot, upgrade, and combat policies.
- Add a pinned, unprivileged Docker image and standard-library-only runtime.
- Pass the starter kit's 300-observation conformance check.
