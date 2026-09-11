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

The app runs in Protection mode with selected GPU/input devices and a custom
AppArmor profile. Its browser runs as a non-root user with Chromium sandboxing.
The optional authenticated control API is internal-network only; CDP is private
container loopback. No host network, full_access, Docker API, or extra host
capabilities are requested. See [docs/security.md](docs/security.md) for the
rating calculation, remaining trust boundaries and required hardware checks.
