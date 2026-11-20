# How it works, and why each piece exists

This is the explanatory document of the pack. The README says what to run. This file says what each file does, why it is
there, and how sure we are that it helps. If you only want to install, read the README. If you want to understand,
change, or doubt something, read on.

The target is one specific machine: the 2017 13-inch two-port laptop whose DMI product name ends in `BookPro14,1`
(Intel Core i5-7360U, 2 cores / 4 threads). Check yours:

```sh
cat /sys/class/dmi/id/product_name      # should end in BookPro14,1
```

Everything below was written and measured on ONE unit of that model, on a Red Hat-family distribution, releases 44/45 (kernel 6.19 to 7.3). It is probably
similar on other models, but that is untested and we say nothing about them.

## How claims are labelled

| Label | Meaning |
|---|---|
| MEASURED | We ran a test on our unit and recorded numbers. One unit, usually single runs. |
| VERIFIED | We checked a fact on our unit with a command you can run yourself. |
| READ | We read it in kernel documentation, upstream source or a man page. We did not test it ourselves. |
| EMPIRICAL | A value we picked by trial. No upstream source recommends it. |
| UNVERIFIED | We believe it or intended it, but did not confirm it. |

Safety first, in plain words:

- The installers and the applier run as root. Read the scripts before you run them. They are short and are plain text.
- Nothing in this pack disables thermal protection. The CPU and firmware keep their own limits.
- Everything installed is a small plain-text file under `/etc` or `/usr/local`, and the uninstall steps remove it.
- Bad power limits or fan settings can make a laptop hotter or slower. Change one value at a time and watch temperatures.
- No warranty. The authors are not responsible for hardware damage (GPL-3.0-or-later, sections 15 and 16).

---

## 1. The big picture

### The problem

This laptop has a 15 W class, two-core processor in a thin case. Two things go wrong in practice:

1. The touchpad and keyboard are not on USB or PS/2. They hang off an Intel serial bus, and generic input defaults are not
   tuned for them (palm touches, thumb touches, power-saving that suspends their controller).
2. The power settings "never stick". You set a power limit and a few seconds later something else has rewritten it. On
   our unit we SAW the sustained package limit (PL1) jump to 85-100 W between our writes. That, on a chip rated for 15 W,
   is how a thin laptop ends up hot with the fan loud.

### How the pack answers it

Two halves, installed separately:

- Touchpad and general battery files: static text files in `/etc` (`install-system.sh`). They are read once, by libinput,
  udev, modprobe or sysctl, and then they do nothing at all while running.
- Power and fan policy: a small root program that writes values into sysfs when something happens, plus a loop that
  keeps two of those values from being overwritten (`scripts/install_power_root_unit.sh`).

### Who writes what, and when

```text
 data/power/policy.json  (in the repo; people edit this)
        |
        | install_power_root_unit.sh copies it
        v
 /etc/mbp14/power-policy.json  (root-owned; the root program reads THIS one first)
        |
        v
 power_policy.py  ---- clamps every value to hard limits ----+
        |                                                    |
        +--> mbp14-power-policy auto   (one-shot applier)    |
        |       writes: EPP, no_turbo, max freq, PL1, PL2,   |
        |               fan MINIMUM; reads each back         |
        |       runs at: boot / resume (+3 s) /              |
        |                plug or unplug (udev) / 5 min after the last run |
        |                                                    |
        +--> mbp14-power-policy hold   (long-running loop)   |
                writes ONLY PL1 and PL2, ONLY when they      |
                differ; every 0.2 s on AC, 1 s on battery    |
                                                             |
 scripts/power_stability.py  (no root, read-only) <----------+
        reads the same policy and the live sysfs values,
        reports how much of the time they matched

 Separate and static, applied by udev/modprobe/sysctl/libinput:
   /etc/libinput/local-overrides.quirks   touchpad size and palm rules
   /etc/modprobe.d/*.conf                 module load order and options
   /etc/udev/rules.d/90-..., 99-...       runtime power management for PCI
   /etc/sysctl.d/90-mbp14-power.conf      kernel tunables
```

The important design point is the first arrow: there is ONE policy file. The applier and the checker both load it
through `power_policy.py`, so they cannot disagree about what "correct" means. We learned that the hard way: an earlier
pair of scripts disagreed about the battery energy preference and one reported drift forever (the history is in the
header of `scripts/power_policy.py`).

---

## 2. Touchpad and keyboard

### The bus

On this model the keyboard and touchpad are on the Apple SPI bus and are driven by the in-kernel `applespi` driver
(VERIFIED on our unit; `dmesg` shows "Apple SPI Touchpad" attached under `pxa2xx-spi.3`). The SPI controller is one of the
Intel Sunrise Point-LP "Serial IO" PCI functions (driver `intel-lpss`).

Check the driver's real parameters on your kernel:

```sh
modinfo applespi | grep -E '^(parm|version|filename)'
```

On kernel 7.3.4 they are `fnmode`, `fnremap`, `iso_layout` and `touchpad_dimensions`. There is no `debug` parameter, so do
not look for one.

### The libinput quirk file

libinput decides how to treat a touchpad from its device quirks. It already ships a GENERIC quirk for this class of device
(READ and VERIFIED present on our machine):

```text
/usr/share/libinput/50-system-apple.quirks
  section "Apple Laptop Touchpad (SPI) applespi driver"
  SizeHint 104x75, TouchSizeRange 150:130, PalmSizeThreshold 1600
```

Our override matches the device BY NAME and gives this model its own numbers. This is the real touchpad section of
`system/libinput/local-overrides.quirks`:

```ini
[Apple SPI Touchpad (14,1)]
MatchName=Apple SPI Touchpad
MatchUdevType=touchpad
MatchBus=spi
ModelAppleTouchpad=1
AttrSizeHint=135x83
AttrTouchSizeRange=50:30
AttrPalmSizeThreshold=1000
AttrThumbSizeThreshold=800
```

What each key means, in one line:

| Key | What it does |
|---|---|
| `MatchName`, `MatchUdevType`, `MatchBus` | Which device this section applies to. All three must match. |
| `ModelAppleTouchpad=1` | Marks the device as an Apple-style touchpad model in libinput's quirk system (READ from the quirk file format; we did not trace what it changes in the code). |
| `AttrSizeHint=135x83` | Physical size in millimetres, for when the kernel does not report it. EMPIRICAL. |
| `AttrTouchSizeRange=50:30` | The touch-size range libinput should treat as "a finger", high to low. EMPIRICAL. |
| `AttrPalmSizeThreshold=1000` | Touches bigger than this are treated as a palm and ignored. EMPIRICAL. |
| `AttrThumbSizeThreshold=800` | Touches bigger than this are treated as a resting thumb. EMPIRICAL. |

The four numbers are EMPIRICAL: chosen by trial on one unit, not upstream recommendations. We did not measure their effect
quantitatively. If palm touches register as clicks, or real touches are ignored, change one number at a time. All the
key names we use appear in libinput's own shipped quirk files on our machine (VERIFIED), and the format is described at
https://wayland.freedesktop.org/libinput/doc/latest/device-quirks.html (READ).

Two cautions straight from that page and from the file header: quirks are libinput internal API and can change between
versions, and a parse error disables ALL quirks. So after any edit, check that the device still matches:

```sh
sudo libinput list-devices | grep -A3 -i 'Apple SPI'
libinput quirks list /dev/input/eventN        # N from the line above; package libinput-utils
```

### The keyboard "internal" quirk, and disable-while-typing

This section is the keyboard half of the same file:

```ini
[Apple SPI Keyboard (14,1)]
MatchName=Apple SPI Keyboard
MatchUdevType=keyboard
MatchBus=spi
AttrKeyboardIntegration=internal
```

Why it exists (VERIFIED on our unit): the "Apple SPI Keyboard" reports Vendor 0000 on the SPI bus, but libinput's
shipped quirk that marks an Apple keyboard as internal requires vendor 0x05AC, so it never matches. libinput only pairs a
keyboard with a touchpad for "disable touchpad while typing" when both are internal.

Our inference, NOT proven: without the pairing, a palm or a three-finger touch while typing can register as a middle
click, which pastes the highlighted text. With the override, `libinput list-devices` reported the
touchpad's disable-while-typing as enabled (VERIFIED).

Honest status: THAT THE RANDOM PASTES ACTUALLY STOPPED IS NOT CONFIRMED. The setting is enabled; whether it fixed the
symptom is unknown. If you want to check on your unit:

```sh
sudo libinput list-devices | grep -B6 -A12 'Apple SPI Touchpad' | grep -i -E 'Device|Disable-w-typing'
sudo libinput debug-events        # type while resting a palm: you would expect touchpad events to be suppressed shortly after key presses; we did not verify this
sudo evtest                       # choose the touchpad node to see raw events
```

(`libinput debug-events` and `evtest` need root or membership of the `input` group.)

### The DMA soft dependency (EMPIRICAL)

From `system/modprobe.d/mbp14-applespi-dma.conf`:

```text
softdep spi_pxa2xx_platform pre: idma64
softdep applespi pre: idma64
```

A `softdep ... pre:` line asks the module loader to load `idma64` (the Intel LPSS DMA engine) first. The intent is that the
SPI controller can then use DMA instead of programmed I/O for touchpad packets.

STATUS, stated plainly: this is an EMPIRICAL local setting. We found NO upstream source saying it reduces touchpad lag or
dropped events. In addition, a 2026 kernel patch series (a patch series disabling DMA for one other Apple laptop model, v13;
https://lkml.iu.edu/2607.2/05228.html) reports DMA handshake failures on a DIFFERENT Apple model and adds a force-PIO
parameter. We do not know whether it merged (UNVERIFIED), and it is about another model. So treat this as something to try
and measure.

How to test it and how to remove it:

```sh
lsmod | grep -E 'idma64|spi_pxa2xx|applespi'        # is it loaded, and is it in use?
dmesg | grep -i -E 'applespi|idma64|pxa2xx'          # look for errors or timeouts
sudo rm /etc/modprobe.d/mbp14-applespi-dma.conf      # remove the softdep
sudo reboot                                          # module order is decided at boot
# then compare scrolling feel and dmesg again
```

`install-system.sh --uninstall` also removes the file (and restores any file of yours it backed up). The same file also
sets `options applespi fnmode=1`, which is the driver's default (F-keys need the Fn key), written out so it is visible.

### Runtime power management: the pair of udev rules

Linux PCI devices can be put into runtime suspend when idle. `90-mbp14-runtime-pm.rules` turns that on for every PCI
device:

```text
ACTION=="add", SUBSYSTEM=="pci", ATTR{power/control}="auto"
```

That saves battery on Wi-Fi, the camera, NVMe and the chipset. But a controller that feeds the touchpad or the Bluetooth
UART should not fall asleep, so `99-mbp14-touchpad-awake.rules` flips four functions back:

```text
ACTION=="add|change", SUBSYSTEM=="pci", KERNEL=="0000:00:1e.0", ATTR{power/control}="on"
ACTION=="add|change", SUBSYSTEM=="pci", KERNEL=="0000:00:1e.3", ATTR{power/control}="on"
ACTION=="add|change", SUBSYSTEM=="pci", KERNEL=="0000:00:15.0", ATTR{power/control}="on"
ACTION=="add|change", SUBSYSTEM=="pci", KERNEL=="0000:00:15.2", ATTR{power/control}="on"
```

plus one rule for the touchpad input device itself, where the kernel exposes a `power/control` file for it.

Why the NAME matters. udev applies rules in file-name order. The `99-` file sorts AFTER the `90-` file, so the general
"auto" is applied first and the four exceptions overwrite it. If you rename the 99- file so that it sorts before 90-, the
general rule wins and the touchpad controllers auto-suspend again. This is the single most fragile thing in the pack, and
the file header says so.

The four functions (VERIFIED with `lspci` on one unit; all are Intel Sunrise Point-LP Serial IO, driver `intel-lpss`):

| Address | What it is | What is attached |
|---|---|---|
| `00:1e.0` | UART #0 | The Bluetooth chip |
| `00:1e.3` | SPI #1 | The keyboard and touchpad |
| `00:15.0` | I2C #0 | NOT verified |
| `00:15.2` | I2C #2 | NOT verified |

The two I2C functions are kept awake on the same precaution only; we do not know what is attached to them. Verify
the addresses on YOUR unit before trusting them, because a wrong address silently does nothing:

```sh
lspci -nn -s 00:1e.3
readlink /sys/bus/pci/devices/0000:00:1e.3/driver      # expect .../intel-lpss
journalctl -k -b | grep -i -E "pxa2xx-spi|Apple SPI"
cat /sys/bus/pci/devices/0000:00:1e.3/power/control     # expect: on
```

The Bluetooth story behind the UART exemption: in earlier tests the desktop's Bluetooth switch flipped back off and
`dmesg` showed `BCM: Reset failed (-110)`. What worked was keeping that UART out of runtime suspend plus a driver reload.
Honest caveat: a kernel update landed in the same window, so which of those three changes mattered was NOT isolated.

---

## 3. Battery and power policy

### The two modes and every knob

There are two modes, `bat` and `ac`. Each sets six values. The values are the original owner's preferences
(a 10-hour battery design on battery; performance with active cooling on AC), not upstream defaults.

| Knob (sysfs) | Battery | AC | What it does | Docs |
|---|---|---|---|---|
| EPP, `energy_performance_preference` | `power` | `balance_performance` | Hint to the CPU's hardware P-state engine about how to trade speed for energy. Applied to every core. | intel_pstate: https://docs.kernel.org/admin-guide/pm/intel_pstate.html |
| `intel_pstate/no_turbo` | 1 | 0 | 1 forbids turbo P-states. Turbo is the biggest battery drain. | same page |
| `scaling_max_freq` | 2,300,000 kHz | 3,600,000 kHz | Upper frequency cap. 2.3 GHz is the chip's base clock, 3.6 GHz its maximum turbo (Intel ARK). | same page |
| `intel-rapl:0/constraint_0_power_limit_uw` ("PL1") | 9 W | 15 W | Long-term package power limit, in microwatts. 15 W is the chip's rated TDP. | powercap: https://docs.kernel.org/power/powercap/powercap.html |
| `intel-rapl:0/constraint_1_power_limit_uw` ("PL2") | 13 W | 25 W | Short-term (burst) package limit. | same page |
| `fan1_min` in the applesmc hwmon directory | 1200 RPM | 2400 RPM | The fan MINIMUM (a floor). Speed above it stays automatic. | not a kernel-doc item; see below |

Notes on that table, with labels:

- EPP hint strings are listed on your machine in `energy_performance_available_preferences` (READ in the kernel doc).
- "PL1" and "PL2" are the common names; the kernel calls them constraint 0 (long term) and constraint 1 (short term) (READ).
- 25 W is OUR choice for the burst limit. It is a third-party number, not one shown on Intel ARK. The 15 W TDP and the
  2.3 / 3.6 GHz figures are on ARK (READ), with a configurable TDP-down of 9.5 W, which is close to our 9 W battery limit
  but is not the same thing.
- The 1200 RPM firmware minimum and the 2400 RPM raised AC floor are the owner's observation and choice. Whether `fan1_min`
  is writable on every unit was NOT verified; on ours the root script wrote it and read it back.
- Check where the fan file lives on your unit: `ls -l /sys/class/hwmon/hwmon*/fan1_min` and
  `grep -l applesmc /sys/class/hwmon/hwmon*/name`.

Look at the live values yourself:

```sh
cat /sys/devices/system/cpu/cpu0/cpufreq/energy_performance_preference
cat /sys/devices/system/cpu/intel_pstate/no_turbo
cat /sys/devices/system/cpu/cpu0/cpufreq/scaling_max_freq
cat /sys/class/powercap/intel-rapl:0/constraint_0_power_limit_uw
cat /sys/class/powercap/intel-rapl:0/constraint_1_power_limit_uw
```

### policy.json is the single source of truth

`data/power/policy.json` holds both modes. Each knob has a `value` and a `why`. A real excerpt:

```json
"pl1_uw": {"value": 15000000, "why": "15 W sustained: the chip's rated TDP (Intel ARK)."},
"fan_min_rpm": {"value": 2400, "why": "A raised floor (firmware minimum is 1200) so the fan is already moving before heat builds: ..."},
```

The repo copy is what people edit. `install_power_root_unit.sh` copies it to `/etc/mbp14/power-policy.json`. This matters:
the reader tries the installed copy first, then a repo copy next to the scripts, then built-in defaults. The installed root
program lives in `/usr/local/libexec/mbp14/`, where no repo copy exists, so it uses the root-owned file; the repo fallback
matters only when you run the read-only checker from a clone. After editing the policy, re-run the installer; it is idempotent.

### The clamps in power_policy.py

The reader does not trust the file. Every value passes through hard-coded limits:

```python
HARD = {"epp": {"power", "balance_power", "balance_performance", "performance", "default"},
        "max_freq_khz": (800000, 3600000), "pl_uw": (5000000, 28000000), "fan_min_rpm": (1200, 3500)}
```

| Value | Allowed | If outside or unparsable |
|---|---|---|
| EPP | one of the five strings above | built-in default for that mode |
| max frequency | 800,000 to 3,600,000 kHz | clamped to the nearest bound (default if not a number) |
| PL1 and PL2 | 5 W to 28 W | clamped to the nearest bound (default if not a number) |
| fan minimum | 1200 to 3500 RPM | clamped to the nearest bound (default if not a number) |
| fan policy | always `auto` | forced to `auto` |

The code states why: a typo or a tampered file cannot push the hardware outside these bounds, and a missing or invalid
file falls back to built-in defaults, "never to no limits". The `limits` block inside policy.json is documentation of the
same ranges; the Python constants are what actually bind. These limits are guard rails against mistakes, not a promise that
every value inside them is good for your unit. 28 W is still more than the rated burst, so choose with care.

### The applier: mbp14_power_apply.py

Installed as `/usr/local/libexec/mbp14/mbp14-power-policy`. Run by hand to see what it does (needs root to write):

```sh
sudo /usr/local/libexec/mbp14/mbp14-power-policy auto
```

Its shape, in order:

1. Mode detection. `detect_mode` looks at every power-supply entry's `online` file; if any mains supply says 1 the mode is
   `ac`, otherwise `bat`. If nothing is readable it falls back to `bat`, the conservative choice.
2. Write, then read back. The core is a small helper: write the wanted value, read the file again, compare as strings.

```python
_write(path, wanted)
got = _read(path)
rows.append((name, wanted, got, str(got) == str(wanted), note))
```

3. Honest reporting. Each row prints `ok` or `FAIL` with the value wanted and the value actually read. The exit code is 0
   only if every row read back correctly, otherwise 1. A file that does not exist on this hardware is reported as "not
   present" and does not count as a failure, so the report tells you which knobs were skipped. The PL1 row carries a note:
   if it does not read back, something else (thermal daemon or firmware) owns the limit.
4. Fan MINIMUM only. It writes `fan1_min`. It never writes a manual fan speed, and `power_policy.py` forces fan policy
   to `auto`. The reasoning is in the file: speed above the floor stays automatic so the fan can always cool the machine.
5. One quirk worth knowing: it also writes the `powersave` scaling governor on every core before setting EPP. That write is
   not read back. We did not verify why it is needed.

### The units and triggers, and why each exists

| Unit or rule | Trigger | Reason |
|---|---|---|
| `mbp14-power-policy.service` (one-shot) | boot (`WantedBy=multi-user.target`) | sysfs values start at firmware or kernel defaults on every boot. |
| `92-mbp14-power-policy.rules` | udev `change` on a Mains power supply | Plugging or unplugging changes which mode is right. The rule only starts the service (`--no-block`). |
| `mbp14-power-policy-resume.service` | after suspend, hibernate, hybrid-sleep, suspend-then-hibernate | The firmware or kernel can reset power settings on wake. It waits 3 s first. |
| `mbp14-power-policy.timer` | 1 min after boot, then 5 min after the last run (a plug, unplug or resume run restarts the countdown) | Self-heal: whatever drifted gets re-applied with no human action. Accuracy 30 s. |
| `mbp14-power-policy-hold.service` | boot, runs forever | Holds PL1/PL2 against rewrites between the other triggers. Next section. |

On the resume unit: it uses `After=` the sleep targets, not `Before=`. `Before=` would run it as the machine goes to
sleep, which is the wrong moment. The file comment says so.

All four trigger paths run the same code on the same policy, which is the point. There is no second writer in this pack.

### The hold loop

Why it exists. The one-shot applier is correct for a moment. We then SAW PL1 rewritten to 85-100 W between our writes: with
sampling every 5 s on 2026-11-19 the value flipped between 100 W and 95 W continuously, seconds after the applier had set
15 W, while the thermal daemon sat idle (it had used about a second of CPU in four hours). The obvious suspect was
innocent. A one-shot apply or a 5-minute timer cannot hold a value that is being overwritten every few seconds.

Who rewrites it. We believe it is the firmware or embedded controller. That is our reading of the evidence, not a
statement from any upstream source; we found none saying this laptop's firmware does it, and we do not claim every unit does.
The `throttled` project (https://github.com/erpalma/throttled) reports the same kind of behaviour on some other laptops,
mostly ThinkPads, and re-writes the limits every few seconds. Check yours with the included tool (section 4); do not
take our word for it.

What the loop does, from `mbp14_power_apply.py`:

```python
for name, f, want in (("rapl_pl1", "constraint_0_power_limit_uw", t["pl1_uw"]),
                      ("rapl_pl2", "constraint_1_power_limit_uw", t["pl2_uw"])):
    ...
    if str(_read(path)) != str(want):
        _write(path, want)
```

Each pass re-reads the charger state and the policy, then rewrites PL1 and PL2 only if they differ. A quiet system causes no
sysfs writes. It logs only a change of state, so the journal is not flooded, and it logs a distinct line if a value does not
read back after writing ("something else owns it").

The numbers. All MEASURED on one unit on 2026-11-20 with `scripts/power_stability.py`; single runs:

| Condition | Loop interval | PL1 in policy | PL1 flips | Highest PL1 | Package temperature at end |
|---|---|---|---|---|---|
| AC, under load | 1 s | 78.8% | 38 in 40 s | up to 100 W | 100 C |
| AC, same load | 0.2 s | 100% | 0 | 15 W | 91 C |
| AC, idle | 0.2 s | 99.0% | 4 blips of at most 0.2 s | not recorded here | not recorded |
| Battery | 1 s | 100% (about 100 s observed) | not recorded | never above 9 W | not recorded |

"Same load" means two busy loops at nice 19 for 40 s. Caveats you should take seriously: this is one unit, single runs, no
repeats, and the temperature figures are one reading at the end, not a controlled thermal test. The direction (a faster loop
holds PL1 better under load) is clear in our data; the exact percentages are not a promise.

Why two speeds. On AC under load the firmware rewrote PL1 about once a second, so a 1 s loop is too slow and the 0.2 s loop
is needed. On battery a 1 s loop held PL1 for the whole ~100 s run we observed, and each wakeup costs battery, so battery stays gentle. The
unit file passes both values explicitly:

```text
ExecStart=/usr/local/libexec/mbp14/mbp14-power-policy hold --interval 0.2 --battery-interval 1
```

The floor. The code never lets the loop run faster than 0.05 s between passes (20 passes a second):

```python
MIN_HOLD_INTERVAL = 0.05   # never spin: 20 passes a second is the floor on how fast the loop may run
```

Even if you pass a smaller `--interval`, `max(MIN_HOLD_INTERVAL, ...)` raises it. The service also runs at `Nice=10` and is
restarted after 10 s if it exits.

What the loop does NOT touch: fan, EPP, frequency, turbo, or any thermal protection. Only the two RAPL limits. It cannot
stop the firmware writing; it can only write back sooner than the damage accumulates. There is a small window between a
firmware write and our correction, which is exactly what the 99.0% idle figure shows.

To stop it: `sudo systemctl disable --now mbp14-power-policy-hold.service`.

### The plug/unplug race, and why there is one writer

We once saw, on a real unplug, the energy preference pass through `balance_performance`, then `balance_power`, then
`power`. An older second writer existed on the original machine; it is NOT in this pack. Two programs both reacting to the
same plug event will fight, and for a moment the machine runs at an in-between setting. In this pack there is a single
writer (the applier) and a single policy file, so the transition goes straight from one mode's values to the other. If you
add your own power tool (a TLP-style daemon, a thermal tuner), expect the same fight and turn one of them off.

The same reasoning applies to the thermal daemon. On the original machine `thermald` was active. We do not use or
recommend any particular one here, and we make no claim about what `thermald` does on this model. The fan-control tool
`mbpfan` was installed but disabled there, and could not work because that machine exposes no manual-fan-control file;
it reads `coretemp` and sets fan speed through applesmc (https://github.com/dgraziotin/mbpfan), and it does not touch
power limits. This pack does not use it.

### The other battery files

`install-system.sh` also installs three small files, all "values we chose, not upstream defaults":

- `system/modprobe.d/90-mbp14-power.conf`: `snd_hda_intel power_save=1 power_save_controller=Y` (audio codec powers down
  after 1 s of silence) and `nvme_core default_ps_max_latency_us=25000` (allow NVMe states with up to 25 ms exit latency).
  Both trade a little wake-up latency for power.
- `system/sysctl.d/90-mbp14-power.conf`: `vm.laptop_mode = 5`, `vm.dirty_writeback_centisecs = 1500`,
  `kernel.nmi_watchdog = 0`, `kernel.timer_migration = 0`. Disabling the NMI watchdog turns off a debugging aid, not a
  thermal protection.
- `system/udev/90-mbp14-runtime-pm.rules`: described in section 2, plus SATA link power management where a SATA host exists
  and audio codec runtime PM.

We did not measure each of these individually. Earlier idle-power tests (not part of this pack's code) are summarised in the
next paragraph; read it as single runs.

What earlier testing found, MEASURED on one unit, single runs, about plus or minus 1 W noise: a starting draw of 13.0 to
14.1 W at 100% brightness on battery. Unbinding the Thunderbolt driver took one measurement from 9.76 W to 8.32 W (kernel
6.19.10). After all fixes at 23% brightness with nothing plugged in we measured 6.56 W over 60 s, about 6.9 h on a 45 Wh
battery at 82% health, not re-measured on kernel 7.2.13. Unbinding Thunderbolt was expected to cost Thunderbolt devices, USB 3
SuperSpeed on the ports and video out over USB-C (untested), and is NOT part of this pack. Forcing PCIe ASPM
(`pcie_aspm=force`) showed no change (14.1 W against 13.9 W) and carries stability risk; this pack does not use or
recommend it. Software tweaks such as turning off indexing on battery gave about -0.24 W, inside the noise.

---

## 4. Measuring: power_stability.py

### Why a single snapshot misleads

Our own earlier checker took one snapshot and said COMPLIANT while PL1 was out of policy about a third of the time
(36% of 0.1 s samples on AC with the 5 s hold loop, MEASURED 2026-11-20). A snapshot catches the value on a good
instant. The firmware does its damage in between. So the checker that ships in this pack samples over time.

### What it does

```sh
python3 scripts/power_stability.py --seconds 30
```

It needs no root and writes nothing to `/sys`. Per sample (default every 0.1 s, minimum 0.02 s, run length clamped to 1 to
600 s) it reads all six knobs, and it reads the charger state on EVERY sample. So if you plug or unplug during the run,
each stretch is judged against its own mode's targets instead of the whole run against one mode.

| Output item | Meaning |
|---|---|
| samples per mode | How many readings fell in AC and in battery stretches. |
| "% in policy" per knob | Share of readable samples that exactly equalled the policy value for that mode. |
| status per knob | `OK` at 99% or above, `DRIFTING` below that, `unavailable (not verified)` if never readable. |
| PL1 flips | How often PL1 changed between in-policy and out-of-policy. |
| longest out-of-policy stretch | Longest run of consecutive out-of-policy PL1 samples, times the interval. |
| highest seen | The highest PL1 value read, in watts. |
| transitions | For each plug or unplug during the run: time until every knob except PL1 matched the new mode, and the energy preferences passed through on the way. |

The settle test leaves PL1 out on purpose, because the firmware keeps flipping it; PL1 is judged separately.

The overall verdict:

| Verdict | Meaning | Exit code |
|---|---|---|
| `STABLE` | Every knob in policy 99% of the time or more. | 0 |
| `DRIFTING` | At least one knob below 99%. | 1 |
| `PARTIAL` | Nothing wrong where it could be read, but something could not be read. | 1 |
| `UNAVAILABLE` | Nothing could be read (or the charger state is unknown). | 2 |

"Unreadable means not verified": a value it cannot read is never counted as OK. PARTIAL is not a pass. One practical
consequence: the fan minimum may be unreadable on some units, which would give PARTIAL even though nothing is wrong.

A few more behaviours worth knowing:

- A single knob "in policy" is an exact string comparison with what the policy asks for. If you edit the policy and forget
  to re-run the installer, the checker reads the installed copy and the repo copy in the same order as the applier, so
  both are comparing the same values.
- With several cores, the EPP and the max-frequency readings are joined with commas if the cores disagree, which fails the
  comparison on purpose.
- `--mode ac` or `--mode bat` forces which mode to compare against, and prints a warning if the charger says otherwise.
- `--json` prints the full result; `--receipt` also writes `data/health/POWER_STABILITY.<host>.json` atomically
  (a temporary file, then a rename), so a reader never sees a half-written receipt.

### Doing your own before/after

1. Install, reboot, then run it idle on AC: `python3 scripts/power_stability.py --seconds 30`.
2. Load the CPU for the duration (two busy loops are enough: `nice -n 19 sh -c 'while :; do :; done' &` twice, then
   `kill` both afterwards) and run it again. This is what showed our 78.8% number.
3. Unplug during a 30 s run and read the `transitions` line.
4. For the hold loop, compare with it stopped and started. Remember: one unit, single runs. Repeat before believing it.

---

## 5. Updates and reboots

### Why settings survive automatic updates

On the original machine automatic updates (dnf5-automatic, daily, `apply_updates=yes`, `reboot=never`; READ in
https://man.archlinux.org/man/dnf5-automatic.8) did not undo these settings. The reason is mechanical. Every file the
pack installs under `/etc` (modprobe.d, udev, libinput, sysctl.d, systemd) and under `/usr/local` is NOT owned by any RPM
package, which we checked with `rpm -qf`. A package update only replaces files that a package owns.

Check yours, with the real installed paths:

```sh
rpm -qf /etc/libinput/local-overrides.quirks /etc/modprobe.d/mbp14-applespi-dma.conf \
        /etc/udev/rules.d/99-mbp14-touchpad-awake.rules /etc/systemd/system/mbp14-power-policy.service \
        /usr/local/libexec/mbp14/mbp14-power-policy
# each should say: file ... is not owned by any package
```

This is evidence on our machine for the installed layout, not a guarantee about every future update. A package that starts
shipping a file with the same name would conflict. We ship distinctive names (`mbp14-`), and the quirk override goes in libinput's local-overrides file rather than a shipped one; both choices are meant to avoid clashes (our intent, not tested against a clash).

### What resets, and what restores it

| What | Survives reboot? | How it comes back |
|---|---|---|
| Files in `/etc` and `/usr/local` | Yes | They are ordinary files. |
| libinput quirks | Yes | Read when libinput starts. A newer libinput could change the format. |
| udev rules | Yes | Applied as devices appear (`add`) or change. |
| modprobe options and softdeps | Yes, but applied at module load | Changing them needs a reboot (or a module reload). |
| sysctl values | Yes | `sysctl --system` at boot. |
| EPP, no_turbo, max freq, PL1, PL2, fan minimum | NO: sysfs values | Applier at boot; again 1 min after boot and every 5 min by timer; after resume; on every plug/unplug; PL1/PL2 also by the hold loop. |

So after a reboot or resume there is a short window where the sysfs values are at their defaults. The longest we claim is
5 minutes (the timer), but in practice the boot unit, the resume unit (3 s after wake) and the udev rule act much sooner.
We did not time a boot in detail (UNVERIFIED).

---

## 6. Design choices worth knowing

- Read-only checker. `power_stability.py` never writes to `/sys` and needs no root, so you can run it on any machine at
  any time without risk, and the thing that judges the policy is not the thing that applies it.
- No manual fan speeds. The applier sets only a MINIMUM; above it, firmware and the thermal daemon stay in charge. A bug in
  our code therefore cannot stop the fan. Raising the AC floor to 2400 RPM is a deliberate trade: more noise for earlier
  cooling.
- Clamps in code, not just in data. If the JSON is edited badly, the hardware still stays inside the hard bounds.
- A single policy file, loaded by one shared reader. No two scripts can disagree about the targets.
- Single writer. Fewer moving parts and no plug/unplug race.
- Read back, and say so. The applier never claims success it did not read back. It would rather print `FAIL` every 5
  minutes than hide a value that did not take.
- Atomic receipts. Written to a temporary file and renamed, so an interrupted run does not leave a corrupt file.
- Plain text and reversible. Every installed file is a small readable text file; `install-system.sh --uninstall` removes
  exactly the files it installs and restores any file of yours it moved aside as `<name>.mbp14-backup`. The root unit's
  undo commands are in the header of `scripts/install_power_root_unit.sh`. `install-system.sh --dry-run` shows what it
  would copy without changing anything, and it refuses to run on a machine whose product name does not end in
  `BookPro14,1` unless you pass `--force`.
- Tests that touch no hardware. From the repo root:

```sh
PYTHONPATH=scripts python3 -m unittest scripts.test_power_policy scripts.test_power_stability
```

  They run 32 tests against fake sysfs trees.

---

## 7. How to adapt it to your unit

The rule: change ONE value at a time, then measure, then keep or revert. Several changes at once make it impossible to know
what helped.

1. Install and measure the defaults first: `python3 scripts/power_stability.py --seconds 30`. A reading of `STABLE` on
   battery and on AC is your baseline. Also record temperatures (`sensors`, package from `coretemp`) under load.
2. Edit one value in `data/power/policy.json`, for example the AC `pl1_uw` or the battery `max_freq_khz`.
3. Re-run `sudo bash scripts/install_power_root_unit.sh`. It copies the new policy to `/etc/mbp14/power-policy.json`. The
   hold loop re-reads the policy every pass, so PL1/PL2 changes take effect there quickly; other values wait for the next
   apply (the installer runs one immediately).
4. Run the stability tool under load and note temperature and, if you care about battery, power draw
   (`current_now` times `voltage_now` from `/sys/class/power_supply/BAT*/`, averaged over tens of seconds).
5. If anything gets worse, revert that one change.

Things to remember while tuning:

- A lower PL1 runs cooler and slower. A higher one runs hotter and faster, and can pin the fan. Your thermal paste, dust and
  room differ from ours.
- If you change the hold intervals, edit the `ExecStart` in `mbp14-power-policy-hold.service` and re-run the installer. The
  code enforces a 0.05 s floor.
- If `hold` or the applier reports that PL1 does not read back after writing, something else owns the limit on your unit. Find
  out what before adding more writers.
- If PL1 is stable on yours without the hold loop (the stability tool will tell you), you may not need it:
  `sudo systemctl disable --now mbp14-power-policy-hold.service`. We only know what our unit did.
- If you have a different model, you are on your own. It might be similar. We did not test it, and the PCI addresses, the
  touchpad numbers and the fan file location may all differ.

To remove everything, use `sudo bash install-system.sh --uninstall` for the static files, and the undo lines in the header of
`scripts/install_power_root_unit.sh` for the power units.
