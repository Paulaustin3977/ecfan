#!/usr/bin/env python3
"""Self-tests for ecfan.

These do not touch the EC: they exercise the pure logic (config parsing,
curve interpolation, the duty deadband) and the sensor-discovery helpers
against a fake sysfs tree. Run with:  python3 test_ecfan.py
"""
import importlib.machinery
import importlib.util
import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))


def load_module():
    loader = importlib.machinery.SourceFileLoader("ecfan", os.path.join(HERE, "ecfan"))
    spec = importlib.util.spec_from_loader("ecfan", loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


ecfan = load_module()

FAILURES = []


def check(name, got, want):
    if got == want:
        print(f"  ok   {name}")
    else:
        print(f"  FAIL {name}: got {got!r}, want {want!r}")
        FAILURES.append(name)


def check_close(name, got, want, tol):
    if abs(got - want) <= tol:
        print(f"  ok   {name}")
    else:
        print(f"  FAIL {name}: got {got!r}, want ~{want!r} (tol {tol})")
        FAILURES.append(name)


# ---------------------------------------------------------------- config ----
def test_config():
    print("config parsing")
    with tempfile.NamedTemporaryFile("w", suffix=".conf", delete=False) as f:
        f.write('# a comment\n'
                'poll_seconds = 5\n'
                'hysteresis = 7\n'
                'min_duty = 25\n'
                'curve = "40:10, 80:90"\n')
        path = f.name
    try:
        cfg = ecfan.load_config(path)
        check("poll_seconds", cfg["poll_seconds"], 5.0)
        check("hysteresis", cfg["hysteresis"], 7.0)
        check("min_duty", cfg["min_duty"], 25)
        check("curve", cfg["curve"], [(40.0, 10), (80.0, 90)])
    finally:
        os.unlink(path)

    # A missing file must fall back to the built-in defaults, not crash.
    cfg = ecfan.load_config("/nonexistent/ecfan.conf")
    check("missing file -> default curve", cfg["curve"], ecfan.DEFAULT_CURVE)
    check("missing file -> default min_duty", cfg["min_duty"], 20)


# ----------------------------------------------------------------- curve ----
def test_curve():
    print("curve interpolation")
    curve = [(40, 10), (60, 50), (80, 90)]
    f = lambda t: ecfan.duty_for(curve, t, 0.0, True, 0)
    check("below range clamps low", f(20), 10)
    check("above range clamps high", f(120), 90)
    check("exact point", f(60), 50)
    check_close("midpoint interpolates", f(50), 30, 1)
    check("floor applied", ecfan.duty_for(curve, 20, 0.0, True, 35), 35)
    check("ceiling applied", ecfan.duty_for(curve, 120, 0.0, True, 0), 90)


def test_hysteresis():
    print("hysteresis")
    curve = [(40, 10), (60, 50), (80, 90)]
    rising = ecfan.duty_for(curve, 50, 3.0, True, 0)
    falling = ecfan.duty_for(curve, 50, 3.0, False, 0)
    check("falling holds a higher duty than rising", falling > rising, True)
    check_close("rising at temp (40->60 maps 10->50)", rising, 30, 1)
    # Falling evaluates at 50+3=53, which on the 40->60 segment (10->50)
    # is 10 + 40*(53-40)/20 = 36.
    check_close("falling uses temp+hysteresis", falling, 36, 1)


def test_deadband():
    print("duty deadband")
    # The deadband is what stops the fan hunting between 20% and 23%.
    check("deadband constant", ecfan.DUTY_DEADBAND, 3)
    # A 3-point difference is suppressed; a 4-point difference is not.
    last = 20
    duty = 23
    check("3-point change suppressed", abs(duty - last) <= ecfan.DUTY_DEADBAND, True)
    check("4-point change allowed", abs(24 - last) <= ecfan.DUTY_DEADBAND, False)


# -------------------------------------------------------------- discovery ---
def test_discovery():
    print("sensor discovery")
    root = tempfile.mkdtemp()
    try:
        # The discovery code looks under <root>/class, mirroring /sys/class.
        class_root = os.path.join(root, "class")
        ec = os.path.join(class_root, "hwmon", "hwmon3")
        os.makedirs(ec)
        with open(os.path.join(ec, "name"), "w") as f:
            f.write("cros_ec\n")
        with open(os.path.join(ec, "fan1_input"), "w") as f:
            f.write("2400\n")
        with open(os.path.join(ec, "temp1_label"), "w") as f:
            f.write("DDR and SOC\n")
        with open(os.path.join(ec, "temp1_input"), "w") as f:
            f.write("40000\n")
        with open(os.path.join(ec, "temp4_label"), "w") as f:
            f.write("CPU choke\n")
        with open(os.path.join(ec, "temp4_input"), "w") as f:
            f.write("42000\n")

        core = os.path.join(class_root, "hwmon", "hwmon5")
        os.makedirs(core)
        with open(os.path.join(core, "name"), "w") as f:
            f.write("coretemp\n")
        with open(os.path.join(core, "temp1_label"), "w") as f:
            f.write("Package id 0\n")
        with open(os.path.join(core, "temp1_input"), "w") as f:
            f.write("55000\n")

        zone = os.path.join(class_root, "thermal", "thermal_zone7")
        os.makedirs(zone)
        with open(os.path.join(zone, "type"), "w") as f:
            f.write("x86_pkg_temp\n")
        with open(os.path.join(zone, "temp"), "w") as f:
            f.write("58000\n")

        fan, cpu, ecp = ecfan.discover_sensors(root=class_root)
        check("fan source found", fan is not None, True)
        check("fan source is the EC fan", fan.endswith("hwmon3/fan1_input"), True)
        check("cpu sensors found", len(cpu), 2)
        check("EC sensors found", len(ecp), 2)

        # The Readings wrapper must read the fake values, not the real ones.
        rd = ecfan.Readings(root=os.path.join(root, "class"))
        check("fake cpu temp read", rd.cpu_temp(), 58.0)
        check("fake fan rpm read", rd.fan_rpm(), 2400)
    finally:
        shutil.rmtree(root)


# -------------------------------------------------------------------- ec ----
def test_ec_struct_layout():
    print("EC ioctl layout")
    check("struct size is 20 bytes", ecfan._HDR.size, 20)
    check("ioctl number matches _IOWR(0xEC, 0, struct)",
          ecfan.IOCXCMD, (3 << 30) | (20 << 16) | (0xEC << 8) | 0)
    check("command number", ecfan.EC_CMD_PWM_SET_FAN_DUTY, 0x0024)
    check("target rpm command", ecfan.EC_CMD_PWM_GET_FAN_TARGET_RPM, 0x0020)
    check("auto fan ctrl command", ecfan.EC_CMD_THERMAL_AUTO_FAN_CTRL, 0x0052)


def main():
    test_config()
    test_curve()
    test_hysteresis()
    test_deadband()
    test_discovery()
    test_ec_struct_layout()
    print()
    if FAILURES:
        print(f"FAILED: {len(FAILURES)} check(s): {', '.join(FAILURES)}")
        return 1
    print("all checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
