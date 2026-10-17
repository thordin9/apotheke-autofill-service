# Hardware smoke fixtures

Synthetic photos (mug, screwdriver, bottle, books) used by
- The only trigger is `workflow_dispatch` (run by hand); there is no schedule or push trigger. There is no `pull_request` or `pull_request_target`, and a header comment in the file says never to add them.

They are drawn from simple shapes by `scripts/make_hardware_fixtures.py`, so
there are no third-party rights in them. They are dedicated to the public domain
(CC0 1.0). They are 1024×768 JPEGs, the size Apotheke sends, and each is about 12 KB.

They test the contract, latency and failure handling against the real model.
They do not measure suggestion quality, so don't tune prompts against them.
