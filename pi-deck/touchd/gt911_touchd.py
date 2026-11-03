#!/usr/bin/python3
"""GT911 touchscreen poller for the HyperPixel 4. Runs as root via pi-deck-touch.service; stdlib only.

The HyperPixel 4 overlay gives the GT911 an interrupt on GPIO 27 but no reset or INT GPIOs, so the kernel's
goodix driver can't do the INT handshake. The chip comes up in a random state (I2C address 0x5d or 0x14)
and the interrupt line floats, firing thousands of spurious interrupts per second: 24-31 % of a core in the
IRQ thread, or the kernel disables the IRQ and touch dies. Polling the chip's status register over I2C costs
~1.7 % of a core idle. So goodix_ts is blacklisted, and this daemon reads touches itself and republishes
them through uinput as an ordinary multitouch touchscreen (labwc maps it to DPI-1 by DEVICE_NAME).
"""

from __future__ import annotations

import ctypes
import logging
import os
import signal
import struct
import sys
import time
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger("gt911-touchd")

I2C_BUS = 11
ADDRESSES = (0x5D, 0x14)
REG_PRODUCT_ID = 0x8140
REG_STATUS = 0x814E  # bit 7: new data ready, bits 0-3: number of touch points
POINT_SIZE = 8  # per point: track id, x lo, x hi, y lo, y hi, size lo, size hi, reserved
MAX_POINTS = 5
GOODIX_DRIVER = Path("/sys/bus/i2c/drivers/Goodix-TS")
DEVICE_NAME = "pi-deck GT911 Touchscreen"

IDLE_INTERVAL_S = 1 / 25
ACTIVE_INTERVAL_S = 1 / 60
ACTIVE_HOLD_S = 0.5  # keep polling fast this long after the last contact
REPROBE_AFTER_ERRORS = 50

# linux/input-event-codes.h
EV_SYN, EV_KEY, EV_ABS = 0x00, 0x01, 0x03
SYN_REPORT = 0
BTN_TOUCH = 0x14A
ABS_X, ABS_Y = 0x00, 0x01
ABS_MT_SLOT, ABS_MT_POSITION_X, ABS_MT_POSITION_Y, ABS_MT_TRACKING_ID = 0x2F, 0x35, 0x36, 0x39
INPUT_PROP_DIRECT = 0x01
BUS_I2C = 0x18
MAX_SLOT = 15  # GT911 track ids are 4 bits


@dataclass(frozen=True)
class Geometry:
    """The device tree's touchscreen-* properties, applied the same way as the kernel's touchscreen helpers."""

    size_x: int
    size_y: int
    invert_x: bool = False
    invert_y: bool = False
    swap_x_y: bool = False
    x_mm: int = 0
    y_mm: int = 0

    def transform(self, x: int, y: int) -> tuple[int, int]:
        max_x, max_y = self.size_x - 1, self.size_y - 1
        if self.invert_x:
            x = max(max_x - x, 0)
        if self.invert_y:
            y = max(max_y - y, 0)
        return (y, x) if self.swap_x_y else (x, y)

    def axes(self) -> tuple[tuple[int, int], tuple[int, int]]:
        """(max, units per mm) for the reported X and Y axes."""
        x = (self.size_x - 1, self.size_x // self.x_mm if self.x_mm else 0)
        y = (self.size_y - 1, self.size_y // self.y_mm if self.y_mm else 0)
        return (y, x) if self.swap_x_y else (x, y)


def read_geometry(address: int) -> Geometry:
    node = Path(f"/sys/bus/i2c/devices/{I2C_BUS}-{address:04x}/of_node")

    def u32(name: str, default: int) -> int:
        path = node / name
        return int.from_bytes(path.read_bytes()[:4], "big") if path.exists() else default

    return Geometry(
        size_x=u32("touchscreen-size-x", 480),
        size_y=u32("touchscreen-size-y", 800),
        invert_x=(node / "touchscreen-inverted-x").exists(),
        invert_y=(node / "touchscreen-inverted-y").exists(),
        swap_x_y=(node / "touchscreen-swapped-x-y").exists(),
        x_mm=u32("touchscreen-x-mm", 0),
        y_mm=u32("touchscreen-y-mm", 0),
    )


def parse_points(count: int, data: bytes) -> dict[int, tuple[int, int]]:
    """Raw (x, y) by track id from `count` point records."""
    points = {}
    for index in range(min(count, MAX_POINTS, len(data) // POINT_SIZE)):
        record = data[index * POINT_SIZE : (index + 1) * POINT_SIZE]
        points[record[0] & 0x0F] = (record[1] | record[2] << 8, record[3] | record[4] << 8)
    return points


class Contacts:
    """Turns successive frames of {track id: (x, y)} into multitouch protocol B events."""

    def __init__(self) -> None:
        self._tracking: dict[int, int] = {}  # track id (= slot) -> tracking id
        self._next_tracking_id = 0
        self._touching = False

    def update(self, points: dict[int, tuple[int, int]]) -> list[tuple[int, int, int]]:
        events: list[tuple[int, int, int]] = []
        for slot in sorted(set(self._tracking) - set(points)):
            events += [(EV_ABS, ABS_MT_SLOT, slot), (EV_ABS, ABS_MT_TRACKING_ID, -1)]
            del self._tracking[slot]
        for slot, (x, y) in sorted(points.items()):
            events.append((EV_ABS, ABS_MT_SLOT, slot))
            if slot not in self._tracking:
                self._tracking[slot] = self._next_tracking_id
                self._next_tracking_id = (self._next_tracking_id + 1) & 0xFFFF
                events.append((EV_ABS, ABS_MT_TRACKING_ID, self._tracking[slot]))
            events += [(EV_ABS, ABS_MT_POSITION_X, x), (EV_ABS, ABS_MT_POSITION_Y, y)]

        touching = bool(points)
        if touching:
            x, y = points[min(points)]
            events += [(EV_ABS, ABS_X, x), (EV_ABS, ABS_Y, y)]
        if touching != self._touching:
            events.append((EV_KEY, BTN_TOUCH, int(touching)))
            self._touching = touching
        if events:
            events.append((EV_SYN, SYN_REPORT, 0))
        return events


# --- hardware ----------------------------------------------------------------------------------------------

I2C_RDWR = 0x0707
I2C_M_RD = 0x0001


class _I2cMsg(ctypes.Structure):
    _fields_ = [("addr", ctypes.c_uint16), ("flags", ctypes.c_uint16), ("len", ctypes.c_uint16),
                ("buf", ctypes.POINTER(ctypes.c_uint8))]


class _I2cRdwr(ctypes.Structure):
    _fields_ = [("msgs", ctypes.POINTER(_I2cMsg)), ("nmsgs", ctypes.c_uint32)]


class Gt911:
    def __init__(self, bus: int) -> None:
        self._fd = os.open(f"/dev/i2c-{bus}", os.O_RDWR)
        self.address = 0

    def probe(self) -> bool:
        for address in ADDRESSES:
            self.address = address
            try:
                if self.read(REG_PRODUCT_ID, 3) == b"911":
                    return True
            except OSError:
                pass
        return False

    def read(self, register: int, length: int) -> bytes:
        out = (ctypes.c_uint8 * 2)(register >> 8, register & 0xFF)
        buf = (ctypes.c_uint8 * length)()
        msgs = (_I2cMsg * 2)(_I2cMsg(self.address, 0, 2, out), _I2cMsg(self.address, I2C_M_RD, length, buf))
        self._ioctl(msgs, 2)
        return bytes(buf)

    def write(self, register: int, data: bytes) -> None:
        payload = bytes([register >> 8, register & 0xFF]) + data
        buf = (ctypes.c_uint8 * len(payload)).from_buffer_copy(payload)
        self._ioctl((_I2cMsg * 1)(_I2cMsg(self.address, 0, len(payload), buf)), 1)

    def _ioctl(self, msgs: ctypes.Array, count: int) -> None:
        import fcntl

        fcntl.ioctl(self._fd, I2C_RDWR, _I2cRdwr(msgs, count))


# linux/uinput.h ioctls ('U' = 0x55)
UI_DEV_CREATE = 0x5501
UI_DEV_DESTROY = 0x5502
UI_DEV_SETUP = 0x405C5503  # _IOW('U', 3, struct uinput_setup), 92 bytes
UI_ABS_SETUP = 0x401C5504  # _IOW('U', 4, struct uinput_abs_setup), 28 bytes
UI_SET_EVBIT = 0x40045564
UI_SET_KEYBIT = 0x40045565
UI_SET_ABSBIT = 0x40045567
UI_SET_PROPBIT = 0x4004556E
_SETUP = struct.Struct("<HHHH80sI")  # input_id (bustype, vendor, product, version), name, ff_effects_max
_ABS_SETUP = struct.Struct("<H2x6i")  # code, input_absinfo (value, min, max, fuzz, flat, resolution)
_EVENT = struct.Struct("llHHi")  # input_event: timeval, type, code, value


class UInputTouchscreen:
    def __init__(self, name: str, x_axis: tuple[int, int], y_axis: tuple[int, int]) -> None:
        import fcntl

        self._fd = os.open("/dev/uinput", os.O_WRONLY | os.O_NONBLOCK)
        ioctl = fcntl.ioctl
        ioctl(self._fd, UI_SET_PROPBIT, INPUT_PROP_DIRECT)
        ioctl(self._fd, UI_SET_EVBIT, EV_KEY)
        ioctl(self._fd, UI_SET_KEYBIT, BTN_TOUCH)
        ioctl(self._fd, UI_SET_EVBIT, EV_ABS)
        (x_max, x_res), (y_max, y_res) = x_axis, y_axis
        for code, maximum, resolution in (
            (ABS_X, x_max, x_res),
            (ABS_Y, y_max, y_res),
            (ABS_MT_SLOT, MAX_SLOT, 0),
            (ABS_MT_TRACKING_ID, 0xFFFF, 0),
            (ABS_MT_POSITION_X, x_max, x_res),
            (ABS_MT_POSITION_Y, y_max, y_res),
        ):
            ioctl(self._fd, UI_SET_ABSBIT, code)
            ioctl(self._fd, UI_ABS_SETUP, _ABS_SETUP.pack(code, 0, 0, maximum, 0, 0, resolution))
        ioctl(self._fd, UI_DEV_SETUP, _SETUP.pack(BUS_I2C, 0x0416, 0x0911, 1, name.encode(), 0))
        ioctl(self._fd, UI_DEV_CREATE)

    def emit(self, events: list[tuple[int, int, int]]) -> None:
        os.write(self._fd, b"".join(_EVENT.pack(0, 0, *event) for event in events))

    def close(self) -> None:
        import fcntl

        fcntl.ioctl(self._fd, UI_DEV_DESTROY)
        os.close(self._fd)


def unbind_kernel_driver() -> None:
    """Frees the chip and its IRQ if goodix_ts got loaded anyway (e.g. before the blacklist took effect)."""
    for device in GOODIX_DRIVER.glob(f"{I2C_BUS}-*"):
        (GOODIX_DRIVER / "unbind").write_text(device.name)
        log.info("unbound kernel goodix driver from %s", device.name)


def wait_for_chip(chip: Gt911) -> None:
    while not chip.probe():
        log.warning("GT911 not answering at %s; retrying", ", ".join(f"0x{a:02x}" for a in ADDRESSES))
        time.sleep(2)
    log.info("GT911 at 0x%02x", chip.address)


def run() -> None:
    unbind_kernel_driver()
    chip = Gt911(I2C_BUS)
    wait_for_chip(chip)
    geometry = read_geometry(chip.address)
    x_axis, y_axis = geometry.axes()
    log.info("%s: %s, axes x=%s y=%s", DEVICE_NAME, geometry, x_axis, y_axis)
    device = UInputTouchscreen(DEVICE_NAME, x_axis, y_axis)
    contacts = Contacts()
    active_until = 0.0
    errors = 0

    try:
        next_poll = time.monotonic()
        while True:
            now = time.monotonic()
            active = now < active_until
            try:
                # While active, read the first point in the same transaction as the status byte.
                block = chip.read(REG_STATUS, 1 + POINT_SIZE if active else 1)
                status = block[0]
                if status & 0x80:
                    count = min(status & 0x0F, MAX_POINTS)
                    data = block[1:]
                    if len(data) < count * POINT_SIZE:
                        data = chip.read(REG_STATUS + 1, count * POINT_SIZE)
                    chip.write(REG_STATUS, b"\x00")
                    raw = parse_points(count, data)
                    events = contacts.update({slot: geometry.transform(x, y) for slot, (x, y) in raw.items()})
                    if events:
                        device.emit(events)
                    if raw:  # empty "ready" frames happen without a touch; don't let them keep fast polling on
                        active_until = now + ACTIVE_HOLD_S
                errors = 0
            except OSError as error:
                errors += 1
                if errors in (1, 10):
                    log.warning("I2C error %d: %s", errors, error)
                if errors >= REPROBE_AFTER_ERRORS:
                    device.emit(contacts.update({}))
                    wait_for_chip(chip)
                    errors = 0

            next_poll += ACTIVE_INTERVAL_S if active else IDLE_INTERVAL_S
            delay = next_poll - time.monotonic()
            if delay > 0:
                time.sleep(delay)
            else:
                next_poll = time.monotonic()
    finally:
        device.close()


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s", stream=sys.stdout)
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
    run()


if __name__ == "__main__":
    main()
