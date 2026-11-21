# Practice submission ledger

Keep every submitted commit public until the event results are published. The Builders scorer accepts only one active entry per team, allows four entries per rolling hour, and counts rejected entries against that limit.

| Tag | Commit | Submitted (ET) | Hypothesis | Practice score | Build/check | Notes |
| --- | --- | --- | --- | ---: | --- | --- |
| `submission-01` | `9719bc6` | 2026-11-20 8:12 PM ET | Survival-first edge play beats the 1.15 starter baseline. | 1.56 | Passed and scored | Receipt `BB-DB770448`; +35.7% over baseline; selected as best/final entry |
| `submission-02` | `eceadfc` | 2026-11-20 8:22 PM ET | Breaking contact below 72% HP creates enough quiet time to regenerate. | 0.46 | Passed and scored | Receipt `BB-E5AF82EF`; rejected hypothesis; `submission-01` remains selected |
| `submission-03` | `2e34703` | 2026-11-20 8:32 PM ET | Pressure-triggered roof climbs break coordinated pursuit. | 1.38 | Passed and scored | Receipt `BB-9AA67944`; hypothesis underperformed; `submission-01` remains selected |

## Candidate checklist

- `python3 -m unittest discover -s tests -v`
- Official `check/check.py` passes with no missed decisions.
- Docker image builds for `linux/amd64`.
- Exact container conformance check passes as uid 65534 with no network and a read-only root filesystem.
- Repository is public and under 20 MB at the candidate commit.
- Commit is pushed and has an immutable annotated `submission-NN` tag.
- Only one intentional strategic change separates consecutive candidates.
- Score and scorer feedback are copied into this ledger after completion.
