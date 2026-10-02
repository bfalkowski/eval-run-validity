`manifest.json` is the fingerprint of a clean run that every other run is compared against.

Current baseline: `gh37010661003-clean-loud-none`, a clean run in GitHub Actions on the real Jev API
(Python 3.11, pinned dependencies, commit cd0753a). Its results are on the `data` branch.

To replace it, copy the manifest of a newer clean CI run:

    cp runs/<clean run id>/manifest.json baselines/manifest.json
