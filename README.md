# ecfan

Quiet, temperature-aware fan control for Chromebooks running Linux.

On a Chromebook the fan is driven by the **ChromeOS EC**, not by a Linux PWM
chip. That has two consequences on a non-ChromeOS Linux system:

1. `lm-sensors`' **`fancontrol` cannot work at all** — there is no pwmchip to
   drive. `/sys/class/pwm` is empty and `fancontrol.service` sits permanently
   `skipped` with no `/etc/fancontrol`.
2. The EC's own *automatic* fan curve depends on a host-side thermal packet
   that only ChromeOS' `powerd` sends. Without it, the EC falls back to a
   fixed high state and spins the fan at its maximum speed **regardless of
   CPU load**.

`ecfan` fixes the second problem by talking to the EC directly and driving the
fan from the CPU temperature, using a configurable curve.

## Result

On a Google Taeko / `Google_Brya` board (i3-1215U):

| | before | after |
|---|---|---|
| idle (36–46 °C) | 4388–4615 RPM | ~1900 RPM |
| 4-core load | ~4600 RPM | ramps to 5800+ RPM |
| CPU under load | 72–84 °C | 72–84 °C (unchanged) |

The fan goes quiet at idle but still has full headroom under load.

## Install

```bash
git clone https://github.com/<you>/ecfan.git
cd ecfan
sudo ./install.sh
```

That installs:

- `/usr/local/sbin/ecfan` — the daemon
- `/etc/ecfan.conf` — the curve
- `/etc/systemd/system/ecfan.service` — the unit (enabled, started)

To remove it again:

```bash
sudo ./install.sh --uninstall
```

Uninstalling hands fan control back to the EC, so the fan returns to the EC's
own automatic behaviour.

## Configuration

Everything lives in `/etc/ecfan.conf`:

```ini
poll_seconds = 2
hysteresis = 3
min_duty = 20
curve = "45:20, 50:25, 55:30, 60:35, 65:45, 70:55, 75:70, 80:85, 85:100, 90:100"
```

The `curve` is a list of `temperature_in_celsius:duty_percent` points,
interpolated linearly between them. Duty is the EC's PWM duty cycle.

- `min_duty` — floor on the duty, so the fan never stops entirely.
- `hysteresis` — degrees of temperature change required before the fan winds
  back down, so it does not hunt around a setpoint.
- `poll_seconds` — how often the EC is updated.

Edit and restart to apply:

```bash
sudo systemctl restart ecfan
```

## Commands

```bash
sudo ecfan --status    # show temperature, RPM, and the active curve
sudo ecfan --once      # apply a single update and exit
sudo ecfan --auto      # hand fan control back to the EC
```

## How it works

The daemon talks to `/dev/cros_ec`, the kernel's ChromeOS EC character
device, using the `CROS_EC_DEV_IOCXCMD` ioctl.

| EC command | Number | Purpose |
|---|---|---|
| `EC_CMD_PWM_SET_FAN_DUTY` | `0x0024` | set fan PWM duty; also switches the fan to manual mode |
| `EC_CMD_PWM_GET_FAN_TARGET_RPM` | `0x0020` | read the EC's current target RPM |
| `EC_CMD_THERMAL_AUTO_FAN_CTRL` | `0x0052` | return control to the EC's automatic mode |

Two details matter in practice:

- **The duty is re-sent on every poll**, not only when it changes, because the
  EC drops back to automatic mode when the system suspends.
- **A 3% duty deadband** prevents audible hunting. Without it, a temperature
  reading that wobbles by a single degree makes the fan switch between two
  speeds every poll.

### The ioctl

`/dev/cros_ec` takes `_IOWR(0xEC, 0, struct cros_ec_command)` where the struct
is:

```c
struct cros_ec_command {
	uint32_t version, command, outsize, insize, result;
	uint8_t  data[];
};
```

In Python the request number is `(3 << 30) | (20 << 16) | (0xEC << 8) | 0` —
the direction bits (`3` = READ|WRITE) are required, or the kernel returns
`ENOTTY`. `insize` must be at least the response length, or the LPC transport
logs "packet too long" and fails with `EMSGSIZE`.

## Measured duty → RPM

On the Taeko board's fan:

| duty | RPM |
|------|-----|
| 0–10% | 0 (stopped) |
| 12% | 798 |
| 20% | 1652 |
| 30% | 2488 |
| 50% | 3766 |
| 100% | 6182 |

Under sustained 4-core load, 30% duty already holds the CPU at ~71 °C. The
default curve is built around that, so it has real thermal headroom rather
than being a fixed quiet setting that would cook the machine.

## Compatibility

- Requires a Chromebook (or any device) with a ChromeOS EC exposed at
  `/dev/cros_ec`.
- The EC must support `EC_CMD_PWM_SET_FAN_DUTY`. The EC advertises this as
  the `PWM_FAN` feature bit; check with
  `ecfan --status` on a board that reports a working fan.
- Sensor paths are discovered at startup by name and label (`cros_ec`,
  `coretemp`, `x86_pkg_temp`), so no hwmon index is hardcoded.

## Safety

- The kernel's own critical trip points (95 °C / 105 °C) are untouched.
- If no temperature can be read, the daemon forces 100% duty rather than
  guessing.
- On `SIGTERM` the daemon returns control to the EC.

## License

MIT — see [LICENSE](LICENSE).
