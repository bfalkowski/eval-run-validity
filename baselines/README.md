Each `manifest_<dataset>.json` is the fingerprint of a clean run that every other run on that dataset is compared against.

- `manifest_v1.json`: `gh37010661003-clean-loud-none`, a clean run in GitHub Actions on the real Jev API (Python 3.11, pinned dependencies, commit cd0753a). Its results are on the `data` branch. Jev scored 60 of 60. It predates moving the Jev stand-in out of `agents/jev.py`, so a new v1 run will show `agent_code_hash` drift against it; make a new v1 baseline if v1 is used again.
- `manifest_v2.json`: `gh37012503737-v2-clean-loud-none`, a clean CI run on dataset v2 after the rule 2 and rule 3 wording was tightened (commit 59ba37e, real Jev API). Jev scored 50 of 60. The earlier v2 run (`gh37011788500`, 52 of 60) used the old wording.

To set or replace one, copy the manifest of a clean CI run:

    cp runs/<clean run id>/manifest.json baselines/manifest_<dataset>.json
