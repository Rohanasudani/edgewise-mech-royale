# Changelog

All notable bot changes are recorded here. Practice submissions also belong in `SUBMISSIONS.md` with their exact Git commit and score.

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
