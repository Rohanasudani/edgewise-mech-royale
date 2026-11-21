# Changelog

All notable bot changes are recorded here. Practice submissions also belong in `SUBMISSIONS.md` with their exact Git commit and score.

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
