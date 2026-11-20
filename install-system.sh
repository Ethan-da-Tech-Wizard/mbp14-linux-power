#!/usr/bin/env bash
# install-system.sh - install (or remove) the touchpad and battery system files for the 2017 13-inch laptop whose DMI
# product_name ends in "BookPro14,1". READ THIS FILE BEFORE RUNNING IT WITH sudo: it copies a handful of small text files into
# /etc and reloads udev. It does not change the kernel, the bootloader or any package.
#
# USAGE
#   sudo bash install-system.sh --dry-run            show exactly what would be copied, change nothing
#   sudo bash install-system.sh                      install touchpad + battery-saving files
#   sudo bash install-system.sh --touchpad-only      only the three touchpad files
#   sudo bash install-system.sh --power-only         only the three battery-saving files
#   sudo bash install-system.sh --uninstall          remove exactly the files this script installs
#   DESTDIR=/tmp/try bash install-system.sh --force  copy into a scratch folder instead of /etc (used by the tests)
#
# SAFETY
#   * Refuses to run on a machine whose DMI product_name does not end in BookPro14,1 unless you pass --force.
#   * Never overwrites a DIFFERENT existing file: it is moved to <name>.mbp14-backup first, and --uninstall puts it back.
#   * Every file here is plain text; undo is deleting it (what --uninstall does). Nothing needs a rebuild of the initramfs to be
#     undone, but module options and udev rules take effect after a reboot (or `udevadm control --reload` + `udevadm trigger`).
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEST="${DESTDIR:-}"                      # empty = the real root file system
DRY=0; UNINSTALL=0; FORCE=0; DO_TOUCHPAD=1; DO_POWER=1

# --- argument parsing --------------------------------------------------------------------------------------------------
for arg in "$@"; do
  case "$arg" in
    --dry-run) DRY=1 ;;
    --uninstall) UNINSTALL=1 ;;
    --force) FORCE=1 ;;
    --touchpad-only) DO_POWER=0 ;;
    --power-only) DO_TOUCHPAD=0 ;;
    -h|--help) sed -n '2,19p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "unknown option: $arg (try --help)" >&2; exit 2 ;;
  esac
done

# --- what gets installed: "source-in-this-folder|absolute-destination" ---------------------------------------------------
TOUCHPAD_FILES=(
  "system/libinput/local-overrides.quirks|/etc/libinput/local-overrides.quirks"
  "system/modprobe.d/mbp14-applespi-dma.conf|/etc/modprobe.d/mbp14-applespi-dma.conf"
  "system/udev/99-mbp14-touchpad-awake.rules|/etc/udev/rules.d/99-mbp14-touchpad-awake.rules"
)
POWER_FILES=(
  "system/udev/90-mbp14-runtime-pm.rules|/etc/udev/rules.d/90-mbp14-runtime-pm.rules"
  "system/modprobe.d/90-mbp14-power.conf|/etc/modprobe.d/90-mbp14-power.conf"
  "system/sysctl.d/90-mbp14-power.conf|/etc/sysctl.d/90-mbp14-power.conf"
)
FILES=()
[ "$DO_TOUCHPAD" = 1 ] && FILES+=("${TOUCHPAD_FILES[@]}")
[ "$DO_POWER" = 1 ] && FILES+=("${POWER_FILES[@]}")

say() { echo "[install-system] $*"; }
run() { if [ "$DRY" = 1 ]; then say "would run: $*"; else "$@"; fi; }

# --- guards ----------------------------------------------------------------------------------------------------------------
if [ -z "$DEST" ] && [ "$(id -u)" -ne 0 ] && [ "$DRY" = 0 ]; then
  echo "This writes to /etc, so it needs root. Run: sudo bash $0 $*   (or add --dry-run to just look)" >&2
  exit 1
fi
if [ "$FORCE" = 0 ] && [ -z "$DEST" ]; then
  product="$(cat /sys/class/dmi/id/product_name 2>/dev/null || true)"
  case "$product" in
    *BookPro14,1) ;;                                                # the model this was written and measured on
    *) echo "This machine's DMI product_name does not end in BookPro14,1, so these files may not fit it. Pass --force to continue anyway." >&2; exit 1 ;;
  esac
fi

# --- uninstall ---------------------------------------------------------------------------------------------------------------
if [ "$UNINSTALL" = 1 ]; then
  for pair in "${FILES[@]}"; do
    dst="${DEST}${pair#*|}"
    if [ -e "$dst" ] || [ -e "$dst.mbp14-backup" ]; then
      run rm -f "$dst"
      [ -e "$dst.mbp14-backup" ] && run mv "$dst.mbp14-backup" "$dst" && say "restored your previous $dst"
      say "removed $dst"
    fi
  done
else
  # --- install ---------------------------------------------------------------------------------------------------------------
  for pair in "${FILES[@]}"; do
    src="$HERE/${pair%%|*}"; dst="${DEST}${pair#*|}"
    [ -f "$src" ] || { echo "missing source file: $src" >&2; exit 1; }
    if [ -e "$dst" ] && ! cmp -s "$src" "$dst"; then
      run mv "$dst" "$dst.mbp14-backup"; say "kept your existing file as $dst.mbp14-backup"
    fi
    run install -D -m 0644 "$src" "$dst"
    say "installed $dst"
  done
fi

# --- make it take effect (skipped for DESTDIR copies and dry runs) -----------------------------------------------------------
if [ -z "$DEST" ] && [ "$DRY" = 0 ]; then
  command -v udevadm >/dev/null && { udevadm control --reload; udevadm trigger --subsystem-match=pci --action=add; say "udev rules reloaded"; }
  [ "$DO_POWER" = 1 ] && command -v sysctl >/dev/null && [ "$UNINSTALL" = 0 ] && sysctl --system >/dev/null && say "sysctl values applied"
fi
say "done. Module options (modprobe.d) take effect at the next boot: reboot, then run: python3 scripts/power_stability.py --seconds 30"
