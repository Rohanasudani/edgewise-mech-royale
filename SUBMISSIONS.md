# Practice submission ledger

Keep every submitted commit public until the event results are published. The Builders scorer accepts only one active entry per team, allows four entries per rolling hour, and counts rejected entries against that limit.

| Tag | Commit | Submitted (ET) | Hypothesis | Practice score | Build/check | Notes |
| --- | --- | --- | --- | ---: | --- | --- |
| `submission-01` | `9719bc6` | 2026-11-20 8:12 PM ET | Survival-first edge play beats the 1.15 starter baseline. | 1.56 | Passed and scored | Receipt `BB-DB770448`; +35.7% over baseline; selected as best/final entry |
| `submission-02` | `eceadfc` | 2026-11-20 8:22 PM ET | Breaking contact below 72% HP creates enough quiet time to regenerate. | 0.46 | Passed and scored | Receipt `BB-E5AF82EF`; rejected hypothesis; `submission-01` remains selected |
| `submission-03` | `2e34703` | 2026-11-20 8:32 PM ET | Pressure-triggered roof climbs break coordinated pursuit. | 1.38 | Passed and scored | Receipt `BB-9AA67944`; hypothesis underperformed; `submission-01` remains selected |
| `v0.4.1` | `fe564ad` | 2026-11-20 9:05 PM ET | Walkable routes to building cover and correctly timed repairs improve survival. | 1.44 | Passed and scored | Receipt `BB-7C4A40DF`; `submission-01` remains selected |
| `submission-05` | `4422412` | 2026-11-21 11:45 AM ET | Improve firing uptime and use the second weapon earlier with 0.1 survival behavior. | 2.13 | Passed and scored | Receipt `BB-190C56CE`; selected as best entry |
| `submission-06` | `aa98b03` | 2026-11-21 11:58 AM ET | Preserve ammunition and firing opportunities by checking shots against arena geometry. | 2.33 | Passed and scored | Receipt `BB-761B95FD`; selected as best entry |
| `submission-07` | `353ee35` | 2026-11-21 12:07 PM ET | Acquire the second weapon earlier with safe opening routes. | 2.10 | Passed and scored | Receipt `BB-D931975C`; `submission-06` remains selected |
| `submission-08` | `b921074` | 2026-11-21 12:17 PM ET | Avoid incoming fire by predicting the shot path and ground impact. | 2.56 | Passed and scored | Receipt `BB-539F0286`; selected as best entry |

## Candidate checklist

- `python3 -m unittest discover -s tests -v`
- Official `check/check.py` passes with no missed decisions.
- Docker image builds for `linux/amd64`.
- Exact container conformance check passes as uid 65534 with no network and a read-only root filesystem.
- Repository is public and under 20 MB at the candidate commit.
- Commit is pushed and has an immutable annotated `submission-NN` tag.
- Only one intentional strategic change separates consecutive candidates.
- Score and scorer feedback are copied into this ledger after completion.
