"""Bounded ASCII line reader for the F407 UART protocol."""
import time

import serial


def open_port(name):
    return serial.Serial(name, 115200, timeout=0.1, write_timeout=2)


class Link:
    def __init__(self, port):
        self.port = port
        self.pending = bytearray()
        self.discard = False
        self.invalid = 0

    def send(self, *fields):
        data = (",".join(map(str, fields)) + "\n").encode("ascii")
        if self.port.write(data) != len(data):
            raise IOError("partial UART write")

    def read(self, deadline):
        while time.monotonic() < deadline:
            pos = self.pending.find(b"\n")
            if pos >= 0:
                line = self.pending[:pos]
                del self.pending[:pos + 1]
                if self.discard:
                    self.discard = False
                    continue
                try:
                    return line.rstrip(b"\r").decode("ascii")
                except UnicodeDecodeError:
                    self.invalid += 1
                    continue
            if len(self.pending) > 1024:
                self.invalid += int(not self.discard)
                self.pending.clear()
                self.discard = True
            self.pending.extend(self.port.read(max(1, min(256, self.port.in_waiting))))
        return None

    def expect(self, kind, request_id=None, timeout=4):
        deadline = time.monotonic() + timeout
        while (line := self.read(deadline)) is not None:
            fields = line.split(",")
            if fields[0] == "ERR":
                raise RuntimeError(f"board returned {line}")
            if fields[0] == kind:
                if request_id is not None and fields[1:2] != [str(request_id)]:
                    raise ValueError(f"unexpected reply {line}")
                return fields
        raise TimeoutError(f"timed out waiting for {kind} ({request_id})")
