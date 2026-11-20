#!/usr/bin/env bash
# PROGRAM:       install_power_root_unit.sh
# AUTHOR:    {{AUTHOR}}
# LAST MODIFIED: 2026-11-19
# PURPOSE:       ONE-TIME root install of the power and fan policy: copies the applier, the policy and the
#                systemd/udev files into place, enables them (boot, AC plug/unplug, resume, 5-minute timer),
#                runs the applier once, and verifies. Idempotent: safe to run again after editing policy.json.
# RUN:           sudo bash scripts/install_power_root_unit.sh
# UNDO:          sudo systemctl disable --now mbp14-power-policy.timer mbp14-power-policy-resume.service mbp14-power-policy.service mbp14-power-policy-hold.service
#                sudo rm -f /etc/systemd/system/mbp14-power-policy* /etc/udev/rules.d/92-mbp14-power-policy.rules /etc/mbp14/power-policy.json /usr/local/libexec/mbp14/mbp14-power-policy /usr/local/libexec/mbp14/power_policy.py
#                sudo systemctl daemon-reload && sudo udevadm control --reload
#                sudo rm -rf /etc/mbp14 /usr/local/libexec/mbp14
# CHANGE HISTORY
#   2026-11-19  {{AUTHOR}}  Created.
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
if [ "$(id -u)" -ne 0 ]; then
  echo "This installs system files, so it needs root. Run: sudo bash $HERE/scripts/install_power_root_unit.sh" >&2
  exit 1
fi
say() { echo "[install_power_root_unit] $*"; }
say "1/6 validating the policy"
python3 -I -c "import json,sys; json.load(open('$HERE/data/power/policy.json'))"
say "2/6 installing the applier and the shared reader to /usr/local/libexec/mbp14"
install -d -m 0755 /usr/local/libexec/mbp14 /etc/mbp14
install -m 0755 "$HERE/scripts/mbp14_power_apply.py" /usr/local/libexec/mbp14/mbp14-power-policy
install -m 0644 "$HERE/scripts/power_policy.py" /usr/local/libexec/mbp14/power_policy.py
say "3/6 installing the policy copy to /etc/mbp14/power-policy.json (root-owned: the root unit never reads a user-writable file)"
install -m 0644 "$HERE/data/power/policy.json" /etc/mbp14/power-policy.json
say "4/6 installing units and the udev rule"
install -m 0644 "$HERE"/data/power/units/mbp14-power-policy.service "$HERE"/data/power/units/mbp14-power-policy-resume.service "$HERE"/data/power/units/mbp14-power-policy.timer "$HERE"/data/power/units/mbp14-power-policy-hold.service /etc/systemd/system/
install -m 0644 "$HERE/data/power/units/92-mbp14-power-policy.rules" /etc/udev/rules.d/
# (The original machine also had an older second power writer; it is not part of this pack, so nothing here orders itself after it.)
systemctl daemon-reload
udevadm control --reload
say "5/6 enabling: boot, resume, 5-minute timer; udev handles plug/unplug"
systemctl enable mbp14-power-policy.service mbp14-power-policy-resume.service mbp14-power-policy-hold.service
systemctl restart mbp14-power-policy-hold.service
systemctl enable --now mbp14-power-policy.timer
say "6/6 applying once and verifying"
systemctl start mbp14-power-policy.service || true
echo "----- applier result (journal) -----"
journalctl -u mbp14-power-policy.service -n 14 --no-pager -o cat || true
for u in mbp14-power-policy.service mbp14-power-policy-resume.service mbp14-power-policy-hold.service mbp14-power-policy.timer; do
  echo "$u enabled: $(systemctl is-enabled "$u" 2>&1)"
done
echo "timer active: $(systemctl is-active mbp14-power-policy.timer)"
echo "hold active: $(systemctl is-active mbp14-power-policy-hold.service)  (it re-asserts PL1/PL2 every 0.2 s on AC and 1 s on battery against the firmware)"
say "done. Any FAIL line above means the hardware or thermald refused that value; the applier reports it every 5 minutes instead of hiding it."
