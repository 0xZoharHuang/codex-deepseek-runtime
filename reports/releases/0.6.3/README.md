# 0.6.3 response identity qualification

Status: candidate; platform qualification and installation readback are pending.

This release changes only the Python gateway response-identity checks. Every
explicit response model must match one of the fixed DeepSeek identity aliases;
every explicit provider must match the route selected for that request. A
provider being allowed for another request does not authorize it for this one.
Missing identity fields remain unknown. Equivalent display names and slugs are
the same identity. This is a response contract check, not an assertion that the
upstream service was observed misrouting real requests.

The request route, model and native event timing remain unchanged. A mismatch
does not trigger an adapter retry or replay. If a tool event was already
delivered, the failure does not erase it or prove that no side effect happened.

The native packages remain the exact v0.6.2 release assets and their original
recipe and complete file manifests. No native rebuild is required. The prior
[0.6.2 qualification](../0.6.2/README.md), including original failures and model
business outcomes, remains the fixed native-component evidence. It is not
relabeled as a fresh 0.6.3 run. This directory will bind the current adapter,
three-platform regression, installed readback, and source identities before
stable publication.
