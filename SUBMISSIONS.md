# Practice submission ledger

Keep every submitted commit public until the event results are published. The Builders scorer accepts only one active entry per team, allows four entries per rolling hour, and counts rejected entries against that limit.

| Tag | Commit | Submitted (ET) | Hypothesis | Practice score | Build/check | Notes |
| --- | --- | --- | --- | ---: | --- | --- |
| `submission-01` | _fill after commit_ | _not submitted_ | Survival-first edge play beats the 1.15 starter baseline. | — | Local check passed | Initial candidate |

## Candidate checklist

- `python3 -m unittest discover -s tests -v`
- Official `check/check.py` passes with no missed decisions.
- Docker image builds for `linux/amd64`.
- Exact container conformance check passes as uid 65534 with no network and a read-only root filesystem.
- Repository is public and under 20 MB at the candidate commit.
- Commit is pushed and has an immutable annotated `submission-NN` tag.
- Only one intentional strategic change separates consecutive candidates.
- Score and scorer feedback are copied into this ledger after completion.
