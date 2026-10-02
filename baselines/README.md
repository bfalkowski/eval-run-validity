Each `manifest_<dataset>.json` is the fingerprint of a clean run that every other run on that dataset is compared against.

- `manifest_v1.json`: `gh37010661003-clean-loud-none`, a clean run in GitHub Actions on the real Jev API (Python 3.11, pinned dependencies, commit cd0753a). Its results are on the `data` branch. Jev scored 60 of 60. It predates moving the Jev stand-in out of `agents/jev.py`, so a new v1 run will show `agent_code_hash` drift against it; make a new v1 baseline if v1 is used again.
- `manifest_v2.json`: `gh37011788500-v2-clean-loud-none`, the first clean CI run on dataset v2 (real Jev API). Jev scored 52 of 60.

To set or replace one, copy the manifest of a clean CI run:

    cp runs/<clean run id>/manifest.json baselines/manifest_<dataset>.json
