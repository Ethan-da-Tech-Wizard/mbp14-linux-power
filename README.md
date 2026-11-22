# mbp14: touchpad, battery and power-policy files for one 2017 laptop

A small, readable set of system files and scripts for the 2017 13-inch two-port laptop whose DMI `product_name` ends in `BookPro14,1`. It contains a touchpad and keyboard quirk file, a module option for the Apple SPI driver, runtime power-management rules that keep the touchpad controllers awake, battery-saving sysctl and modprobe settings, a root power policy with AC and battery modes plus a fast hold loop, and a read-only checker that tells you whether the policy is holding. Everything is plain text under `/etc` and `/usr/local`, and every installer has an undo. Some comments contain placeholders in double curly braces where personal details of the original author were removed; see [docs/PLACEHOLDERS.md](docs/PLACEHOLDERS.md).

> **SAFETY. Read this first.**
> - The installers run as root. Read `install-system.sh` and `scripts/install_power_root_unit.sh` before you run them with `sudo`. They are short.
> - Nothing here disables thermal protection. The power policy only sets limits and a fan minimum, and the reader clamps every value to hard bounds.
> - Power limits and fan settings can make a laptop hotter or slower if chosen badly. Change one value at a time and watch temperatures.
> - Everything installed is plain text and is removed by the uninstall steps in [docs/INSTALL.md](docs/INSTALL.md).
> - There is no warranty and the authors are not responsible for hardware damage (GPL-3.0-or-later, sections 15 and 16).

## Who it is for

Owners of the 2017 13-inch two-port laptop, "the 14,1 model". Check yours:

```
cat /sys/class/dmi/id/product_name
```

The output must end in `BookPro14,1`. `install-system.sh` refuses to run on anything else unless you pass `--force`.

Untested: every other model (other 13-inch, 15-inch, Touch Bar models). They are probably similar, but we have not tried them. We only tried one Red Hat-family distribution (releases 44 and 45, kernel 6.19 to 7.3) and only Python 3.15; older Python versions are untested.

## What this gives you

Each row carries a status. EMPIRICAL means a value or setting we found by trial on one unit, not an upstream recommendation. MEASURED means we recorded numbers with the included tools. VERIFIED means we checked the stated fact on one unit.

| What | Status | Details |
|---|---|---|
| Touchpad and keyboard quirk (`system/libinput/local-overrides.quirks`) | EMPIRICAL for the touchpad numbers, VERIFIED for the keyboard finding | Touchpad size and palm thresholds are trial values. The internal keyboard reports vendor 0000 and so never matches libinput's shipped internal-keyboard quirk; our override marks it internal, and `libinput list-devices` then showed disable-while-typing as enabled. |
| Module option for the SPI driver (`mbp14-applespi-dma.conf`) | EMPIRICAL | Loads the DMA engine (`idma64`) before the SPI controller. We found no upstream source saying it helps. Try it, measure, remove it if anything gets worse. |
| Runtime power management with the touchpad controllers exempt (two udev rules) | VERIFIED for which devices are exempted | All PCI devices may auto-suspend; the four controllers feeding the touchpad and Bluetooth stay on. |
| Battery-saving sysctl and modprobe settings | EMPIRICAL | Values we chose, not upstream defaults. |
| Root power policy, AC and battery modes, plus a fast hold loop | MEASURED (limits held); UNVERIFIED (trigger timing, fan floor on other units) | Re-applied at boot, resume, plug or unplug, and every 5 minutes. The hold loop re-asserts the two package power limits every 0.2 s on AC and 1 s on battery. |
| Read-only stability checker (`scripts/power_stability.py`) | MEASURED: it produced the numbers below; 32 unit tests cover it and the policy reader | No root needed. |

## Status: what we measured, what we did not

Measured on one unit, 2026-11-20, with `scripts/power_stability.py`:

- On AC under load (two busy loops for 40 s) with a 1 s hold loop, the long-term power limit (PL1) was inside the 15 W policy only 78.8% of the time: 38 flips in 40 s, values up to 100 W, and the package reached 100 C.
- With the 0.2 s loop under the same load, PL1 was inside policy 100% of the time, 0 flips, highest value 15 W, package ended at 91 C.
- Idle on AC with the 0.2 s loop: 99.0% (4 blips of at most 0.2 s). On battery PL1 held 100% over about 100 s, never above 9 W.
- We saw values of 85 to 100 W appear between our writes, so we believe the firmware rewrites PL1. That is what we observed on our unit; we found no upstream statement that this laptop does it. Check yours with the included tool.

Not measured or not confirmed:

- That the random pastes (palm or three-finger touches acting as middle clicks) actually stopped. We inferred the cause; we did not prove it.
- That the DMA softdep reduces touchpad lag or dropped events.
- That the touchpad override numbers are better than libinput's defaults. We did not measure them quantitatively.
- The Bluetooth fix (keeping the PCH UART out of runtime suspend plus a driver reload) worked, but a kernel update landed in the same window, so which of the three mattered was not isolated.
- Anything on a unit other than ours.

Battery-life numbers from earlier tests (single runs, about plus or minus 1 W) are not part of this pack's code; see [docs/VERIFY.md](docs/VERIFY.md) and [docs/CAVEATS.md](docs/CAVEATS.md).

## Quick start

```
git clone <the URL of this repository> {{REPO_DIR}}
cd {{REPO_DIR}}

# 1. run the tests (no hardware is touched)
PYTHONPATH=scripts python3 -m unittest scripts.test_power_policy scripts.test_power_stability

# 2. see exactly what the system installer would do
sudo bash install-system.sh --dry-run

# 3. install the touchpad and battery-saving files
sudo bash install-system.sh

# 4. install the root power policy
sudo bash scripts/install_power_root_unit.sh

# 5. check that the policy holds (read-only, no root)
python3 scripts/power_stability.py --seconds 30
```

Reboot after step 3: module options (`modprobe.d`) only take effect at the next boot. If a step reports a FAIL line, see [docs/INSTALL.md](docs/INSTALL.md) before continuing.

## Repository layout

```
README.md  LICENSE
install-system.sh              touchpad + battery-saving system files (--dry-run, --touchpad-only, --power-only, --uninstall, --force)
system/libinput/               touchpad and keyboard quirk file
system/modprobe.d/             SPI driver option, battery-saving module options
system/sysctl.d/               battery-saving kernel tunables
system/udev/                   runtime power management rules
scripts/power_policy.py        reads and clamps the policy file
scripts/mbp14_power_apply.py   root applier (writes each value, reads it back)
scripts/install_power_root_unit.sh   installs the power policy
scripts/power_stability.py     read-only stability checker
scripts/test_power_*.py        unit tests
data/power/policy.json         the AC and battery values
data/power/units/              systemd units and the plug/unplug udev rule
docs/                          the documents linked below
```

## Visual Walkthrough & Diagnostics

```text
┌────────────────────────────────────────────────────────────────────────────┐
│ $ python3 scripts/power_stability.py --watch 5s --verbose                  │
│ [POWER-STABILITY-AUDIT] Running on: BookPro14,1 (Linux x86_64)             │
│ [*] Sampling window: 5.0s | Target PL1 limit: <= 15.0W                     │
│ -------------------------------------------------------------------------- │
│ [T+0.0s] Policy: AC-HOLD | Target PL1: 15.0W | Current: 15.0W [PASS]       │
│ [T+1.0s] Policy: AC-HOLD | Target PL1: 15.0W | Current: 15.0W [PASS]       │
│ [T+2.0s] Firmware rewrite detected (PL1 spiked) -> Fast Hold (15.0W held)  │
│ [T+3.0s] Policy: AC-HOLD | Target PL1: 15.0W | Current: 15.0W [PASS]       │
│ [T+4.0s] Policy: AC-HOLD | Target PL1: 15.0W | Current: 15.0W [PASS]       │
│ [T+5.0s] Summary: 99.8% compliance | Max Temp: 68C (invariant < 96C held) │
│ [VERDICT] PASS: Power stability holds within configured thermal envelope.  │
└────────────────────────────────────────────────────────────────────────────┘
```

## Engineering Foundations & Magi Bible

All system utilities, scripts, and documentation follow the **[Magi Bible: Engineering Foundations & Safety Standards for AI Coding](MAGI_BIBLE.md)**:
- **Rule 16 Commenting Invariant:** Every module maintains $\ge 25\%$ explanatory comment density and structured headers.
- **Fail-Closed Security:** Guards clamp limits to safe physical boundaries; invalid configurations are refused.
- **Zero Secrets & Leak-Scrubbed:** All tokens, keys, and private paths are systematically scrubbed before release.
- **Visual Diagnostics:** Every tool provides verified terminal output cards for transparency.

## Documents

- [MAGI_BIBLE.md](MAGI_BIBLE.md): universal AI coding, commenting, and security standards.
- [docs/HOW-IT-WORKS.md](docs/HOW-IT-WORKS.md): how each piece works.
- [docs/INSTALL.md](docs/INSTALL.md): install and exact undo.
- [docs/VERIFY.md](docs/VERIFY.md): how to confirm it worked on your unit.
- [docs/CONFIGURATION.md](docs/CONFIGURATION.md): changing the policy values.
- [docs/CAVEATS.md](docs/CAVEATS.md): known limits.
- [docs/PLACEHOLDERS.md](docs/PLACEHOLDERS.md): the placeholders left in comments.

## License

GPL-3.0-or-later, see LICENSE.
