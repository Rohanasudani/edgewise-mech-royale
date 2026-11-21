# Edgewise — Mech Royale bot

Edgewise is a deterministic, survival-first bot for the `mechfight/1` protocol. It uses only the Python standard library and is packaged as a tiny Docker image.

## Strategy

- Stay near the useful edge of the next safe circle instead of joining fights in the center.
- Walk silently while unthreatened; boost, jump, and strafe only when pressure or the zone justifies the energy and noise.
- Use the arena geometry and terrain for local collision avoidance.
- Route behind nearby buildings that provide deep, verified cover from the pursuing squad.
- Acquire one non-gun weapon without taking a dangerous detour.
- Face recent attackers to protect rear armor, dodge incoming projectiles, and retreat while returning accurate fire.
- Prefer armor, health, mobility, and recovery upgrades over damage.
- Avoid NPC farming and contested supply drops; placement is the objective.

## Build

```sh
docker build --platform linux/amd64 -t edgewise .
```

## Check

From the extracted starter-kit directory, with this repository next to it:

```sh
python3 mechfight-kit/check/check.py -- python3 -u mech-royale-bot/bot.py

docker run --rm -i --network none --cpus 1 --memory 512m \
  --read-only --tmpfs /tmp:rw,size=64m --user 65534:65534 \
  -v "$PWD/mechfight-kit/arena:/arena:ro" edgewise \
  < /dev/null
```

For the exact container conformance check:

```sh
python3 mechfight-kit/check/check.py --map-dir /arena -- \
  docker run --rm -i --network none --cpus 1 --memory 512m \
  --read-only --tmpfs /tmp:rw,size=64m --user 65534:65534 \
  -v "$PWD/mechfight-kit/arena:/arena:ro" edgewise
```

Submit this directory as the root of a public GitHub repository. The `Dockerfile` is already at repository top level.

## Versioning and submissions

`VERSION` is the human-facing bot version. Every scored candidate should also get an immutable annotated Git tag. The website submits a repository URL and keeps the commit it fetched, so never rewrite published history or delete a submitted tag.

```sh
# Before submitting
git add -A
git commit -m "bot: tune late-zone evasion"
git tag -a submission-01 -m "Mech Royale practice submission 01"
git push origin main --follow-tags

# Record the result after the scorer finishes
# Edit SUBMISSIONS.md, then:
git add SUBMISSIONS.md
git commit -m "docs: record submission-01 score"
git push origin main
```

Use `SUBMISSIONS.md` as the experiment ledger. Change one strategic idea per candidate when possible; otherwise the practice score will not tell you which change helped.

## Dependencies and attribution

The bot has no third-party runtime dependencies. It uses Python's standard library and implements the public `mechfight/1` protocol from the Builders Mech Royale starter kit. The starter kit itself is not included in the submission repository.
