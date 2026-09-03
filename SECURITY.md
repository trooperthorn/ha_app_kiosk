# Security Policy

## Reporting a vulnerability

Do not open a public issue containing exploit details, credentials, private
addresses, or logs. Use GitHub's private vulnerability-reporting feature for
this repository. If private reporting is unavailable, open a minimal issue
asking the maintainer to establish a private channel; omit technical details.

Include the affected version/commit, prerequisites, impact, a minimal
reproduction, and suggested remediation. Remove tokens, API keys, cookies,
camera images, usernames, and private network details.

## Response targets

These are project targets, not an SLA: acknowledge critical/high reports in
three business days, establish severity and containment in seven, and publish
a coordinated fix/advisory as soon as safely validated. Lower-severity issues
are prioritized by exploitability and impact.

## Supported version

Only the latest published release and the default branch receive security
fixes. Operators should update Home Assistant, the app itself, and retain a
tested rollback/backup.

## Security boundaries

This app runs as a privileged Home Assistant app with GPU and input device
passthrough and `host_network: true` (see [docs/security.md](docs/security.md)
for why). It is not a sandbox: a compromise of the Chromium process or the
control API within its stated trust boundaries could affect the display and,
through `host_network`, reach other loopback-bound services on the host. Its
device permissions are minimized (explicit nodes, no `full_access`) and its
control API is bound to loopback only, but neither claim is a guarantee
against a vulnerability in Chromium, Cage, or the Alpine base image itself.
