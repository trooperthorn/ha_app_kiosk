# Changelog
## KNOWN ISSUES
- Install slower to add chrome, 1-5 minutes. I can't fix that.

## 2026.08.20.1
- Fix: add-on failed to start ("app fails to load") because the touch
  calibration udev rule write to /etc/udev/rules.d/ was denied by the
  Supervisor's default AppArmor confinement, and that failure was fatal
  under bashio's errexit. Set `apparmor: false` in config.yaml (required
  for udev rule writes / udevadm control) and made the rule write
  non-fatal in run.sh so the kiosk display always starts even if that
  write fails for any other reason.

## v1.999 - August 2026

- Initial private release
