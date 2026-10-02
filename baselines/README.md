`manifest.json` is the fingerprint of a clean run that every other run is compared against.

It currently comes from a stub-backend clean run, so the plumbing tests have a baseline. Replace it with the manifest of the first clean run on the real API before collecting paper data:

    cp runs/<clean run id>/manifest.json baselines/manifest.json
