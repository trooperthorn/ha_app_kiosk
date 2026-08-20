# Changelog
## KNOWN ISSUES
- Install slower to add chrome, 1-5 minutes. I can't fix that.
- Locally-installed add-on can end up with a stale Supervisor
  registration/token (constant "Unable to access the API, forbidden") after
  the add-on's slug changes, until it's fully uninstalled and reinstalled.
  Supervisor-side, not fixable from this repo.

## 2026.08.20.2
- **Revert**: removed `apparmor: false` (added in 2026.08.20.1). Confirmed
  by a field log that it broke `/dev/dri` device cgroup access (Supervisor
  ties cgroup device rules to the AppArmor profile), crashing Cage on every
  boot with `Failed to open device: '/dev/dri/card0': Operation not
  permitted`. The udev rule write it was meant to unblock actually works
  fine under the default confined profile.
- Fix: `swayidle` was started before Cage's Wayland socket existed, so it
  failed instantly every run ("Unable to connect to the compositor").
  It now waits for the socket the same way the rotation logic already did
  (both now share one `wait_for_wayland_socket` helper).
- Fix: `udevadm control --reload-rules` / `udevadm trigger` had no timeout
  and were observed hanging ~60s waiting for a reply that never came,
  adding a full minute to every boot. Both are now bounded with `timeout 5`.
- Improved the Supervisor-API-unreachable log message to call out the
  stale-registration-after-slug-change case (see Known Issues) alongside
  the existing transient-race case.

## 2026.08.20.1
- Fix (see revert above): add-on failed to start ("app fails to load")
  because the touch calibration udev rule write to /etc/udev/rules.d/ was
  believed to be denied by the Supervisor's default AppArmor confinement.
  Set `apparmor: false` in config.yaml and made the rule write non-fatal in
  run.sh.

## v1.999 - August 2026

- Initial private release
