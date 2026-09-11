# Security boundaries and operating requirements

This app runs with Home Assistant Protection mode enabled. It requests no
host networking, host PID/IPC/D-Bus, full_access, Docker API, Supervisor API
role escalation, or extra Linux capabilities. Device access comes from the
selected GPU and input options. The udev database is read-only. There are no
shared filesystem mappings; only the app-private /data volume persists.

## Process separation and AppArmor

Root performs bounded setup and runs seatd plus the control/watchdog service.
Cage and Chromium run as uid 1000 (kiosk). Browser launch strips Supervisor
service tokens and credentials from its environment. Options are root-only;
profile and certificate databases belong to kiosk. Profile rewrites and
certificate imports run as kiosk, never as root over browser-writable files.
Chromium's namespace and seccomp sandboxes stay enabled. The GPU shader disk
cache is disabled to avoid a reproduced pwritev2 sandbox failure in the Alpine
GPU cache path; GPU rendering and video decoding are not disabled in production.

The custom apparmor.txt confines root setup separately from Chromium. The
browser subprofile cannot read options.json or arbitrary root/shared HA files.
Its namespace capabilities apply within the unprivileged browser sandbox;
they are not host capabilities requested by config.yaml. CI must validate the
profile with an enforcing kernel. Syntax-only validation on WSL is insufficient.
The Supervisor currently supplies seccomp=unconfined at the container layer;
Chromium's own seccomp layer is separate and verified in the browser test.

## Security rating

The rating is calculated by Supervisor, not a configurable number. Its current
rating_security implementation gives this manifest an expected 8/8 once the
custom AppArmor profile is installed: base 5 + custom profile 1 + private
network/no published ports 2. The former host-network/default-profile design
scored 4/8 under the same formula. No signed-image bonus is claimed. Without a
loaded custom profile the score may be lower. Keep Protection mode enabled and
verify the installed profile; a score is not proof against vulnerabilities.

Source: https://github.com/home-assistant/supervisor/blob/b44b4acbc21768765f70089c90d1a4d9daee5d48/supervisor/apps/utils.py
Guidance: https://github.com/home-assistant/developers.home-assistant/blob/master/docs/apps/security.md

## Networking and control

Core is reached on the Supervisor bridge, normally http://homeassistant:8123.
The control API is disabled by default. When enabled, it requires a 32-character
or longer token, constant-time authentication, JSON object requests, and no
browser Origin header. It listens on the internal app interface with no host
port publication. Health is read-only and intentionally unauthenticated on
that internal interface. Chromium CDP binds only container loopback; other
apps cannot directly reach it. Other internal apps can reach the enabled API
but still require its token for commands. HTTP is appropriate to the local
app network when explicitly enabled; do not expose this API through a proxy
or publish its port without designing that additional authentication boundary.

Invalid/unreadable options fail startup. Command execution remains an explicit
binary allowlist using exec arguments, not a shell. Timed-out child commands
are killed and reaped. Browser navigation validates HTTP/HTTPS URLs and honors
allow_http and optional same-origin lockdown. Lockdown defaults on; failure
to write browser policy aborts startup. It is navigation control, not a network
firewall or Home Assistant entity permission system. Use a non-admin kiosk user.

## Local HTTP and self-signed HTTPS

allow_http defaults true, preserving normal local HTTP operation. Turning it
off requires HTTPS for configured/API navigation and blocks HTTP navigation
with browser policy. Subresource transport and external servers remain subject
to normal browser mixed-content rules; this option is not an egress firewall.

allow_self_signed is an explicit trust option, not a global certificate bypass.
It requires one public PEM certificate in self_signed_certificate. The trusted
server/CA is imported into this browser's NSS database; hostname and expiry
validation remain active. Trust is removed when disabled or replaced. The
readiness probe uses the same supplied certificate. The old
ignore_certificate_errors flag is retained only to accept old configurations;
it is ignored with a migration warning, and --ignore-certificate-errors is
never passed. Private keys are rejected and unnecessary.

## Audio and devices

audio_enabled defaults true. Disabled means browser mute, blocked sound policy,
and no usable PulseAudio connection in the desktop environment. Microphone and
camera capture are disabled by policy in either mode (camera dashboard playback
is unaffected). Supervisor's audio:true mount is static and retained to support
the toggle; a runtime option cannot remove a Supervisor mount.

Only selected gpu_devices and input_devices receive Supervisor device access.
The empty default input list must be configured for touch; do not grant all
input nodes to avoid identifying the actual screen. No device chmod/chown is
performed on the host. seatd brokers compositor access, and only the selected
GPU group IDs are added to the non-root desktop for render-node access.

## Login and stored secrets

Persistent manual login is the default. Optional credential mode retains
root-readable HA credentials in options but never passes them to argv or
keyboard focus. A single browser execution verifies the exact trusted origin,
login path, and same-origin form before filling/submitting; if the form does
not match, entry is skipped. Page compromise on the trusted HA origin remains
a risk; a non-admin account and maintained dashboard dependencies are required.

Existing loopback trusted-network rules do not identify the app after network
migration. Prefer manual login; if retaining trusted_networks, map only the
verified app address to a specific non-admin user, never the whole subnet.

## Build and release security

Test, Validate, and Security workflows all gate publication for the release
commit. Security scans all runtime Python files. The vulnerability inventory
retains unfixed findings; every fixable High/Critical finding blocks release,
with no vulnerability-ID exclusions. The unused base-image tempio executable
was removed to eliminate its embedded vulnerable Go dependencies. Startup does
not use it. The Alpine base image digest and aiohttp version are pinned.

OS packages still receive current apk security updates, so later source builds
can differ from the CI image. Retain scan inventories and verify the installed
image when an exact deployment assessment is needed. This repository distributes
source-built apps, not signed prebuilt registry images; image signing is a
separate distribution change and no signing assurance is claimed here.

## Required hardware acceptance

After installing this breaking release, verify actual AppArmor enforcement,
selected DRM/input nodes, non-root desktop, touch mapping, rotation, camera
decode, optional audio, blank/wake, login persistence, API automation routing,
and watchdog recovery on HAOS. Headless CI cannot prove physical hardware
operation. Never disable protection or add broad capabilities to hide a denial;
inspect the denial and adjust the narrow profile if required.
