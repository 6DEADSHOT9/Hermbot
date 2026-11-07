"""Volatile observer of the native filter's accepted RFB bytes; never authorizes input."""

import base64
import hashlib
import json
import threading
import time


class AcceptedRFB:
    def __init__(self):
        self.buffer = bytearray()
        self.handshake = 14

    def feed(self, data):
        self.buffer.extend(data)
        if self.handshake:
            count = min(self.handshake, len(self.buffer))
            del self.buffer[:count]
            self.handshake -= count
        events = []
        while self.buffer and not self.handshake:
            b = self.buffer
            kind = b[0]
            size = {0: 20, 3: 10, 4: 8, 5: 6, 150: 10, 255: 12}.get(kind)
            if kind == 2 and len(b) >= 4:
                size = 4 + 4 * int.from_bytes(b[2:4], "big")
            elif kind == 6 and len(b) >= 8:
                size = 8 + abs(int.from_bytes(b[4:8], "big", signed=True))
            elif kind == 248 and len(b) >= 9:
                size = 9 + b[8]
            elif kind == 251 and len(b) >= 8:
                size = 8 + 16 * b[6]
            elif kind not in (0, 2, 3, 4, 5, 6, 150, 248, 251, 255):
                raise ValueError("Unsupported accepted RFB message")
            if size is None or len(b) < size:
                break
            packet = bytes(b[:size])
            del b[:size]
            if kind == 4:
                events.append(
                    {"kind": "key", "down": bool(packet[1]), "keysym": int.from_bytes(packet[4:8], "big")}
                )
            elif kind == 255:
                if packet[1] != 0:
                    raise ValueError("Unsupported QEMU extension")
                events.append(
                    {
                        "kind": "key",
                        "down": bool(int.from_bytes(packet[2:4], "big")),
                        "keysym": int.from_bytes(packet[4:8], "big"),
                    }
                )
            elif kind == 5:
                events.append(
                    {
                        "kind": "pointer",
                        "buttons": packet[1],
                        "x": int.from_bytes(packet[2:4], "big"),
                        "y": int.from_bytes(packet[4:6], "big"),
                    }
                )
            elif kind in (6, 251):
                events.append({"kind": "omitted", "reason": "clipboard" if kind == 6 else "geometry change"})
        return events


class Capture:
    def __init__(self):
        self.lock = threading.RLock()
        self.session = None
        self.events = []
        self.bytes = 0
        self.total = 0
        self.seq = 0
        self.generation = 0
        self.last_frame = 0
        self.last_poll = 0
        self.geometry = ""
        self.segment = 0

    def status(self):
        return {
            k: getattr(self, k, None)
            for k in ("session", "state", "reason", "generation", "seq", "epoch", "total", "revision")
        }

    def control(self, session, action, viewer, epoch, revision):
        with self.lock:
            if action == "start" and self.session != session:
                if self.session and self.state in ("recording", "paused"):
                    raise ValueError("Another recording is active")
                self.session, self.viewer, self.epoch = session, viewer, epoch
                self.events, self.bytes, self.total, self.seq = [], 0, 0, 0
                self.state, self.reason, self.revision = "paused", "", 0
            if self.session != session or (
                action not in ("finish", "discard") and (viewer != self.viewer or epoch != self.epoch)
            ):
                raise ValueError("Recording ownership changed")
            if revision == self.revision:
                if action != getattr(self, "last_action", None):
                    raise ValueError("Recording revision already identifies a different action")
                return self.status()
            if revision != self.revision + 1:
                raise ValueError("Recording revision changed")
            self.revision = revision
            self.last_action = action
            self.generation += 1
            self.last_poll = time.monotonic()
            self.state = {
                "start": "recording",
                "resume": "recording",
                "pause": "paused",
                "private": "paused",
                "finish": "ready",
                "discard": "discarded",
            }[action]
            self.reason = "private input omitted" if action == "private" else action
            if action == "discard":
                self.events, self.bytes = [], 0
            elif action == "private":
                self.append({"kind": "omitted", "reason": "private step"})
            return self.status()

    def append(self, event):
        size = len(json.dumps(event).encode())
        if self.bytes + size > 8 * 1024 * 1024 or self.total + size > 256 * 1024 * 1024:
            self.state, self.reason = "paused", "capture storage limit; finish or discard"
            self.generation += 1
            return
        self.seq += 1
        value = {**event, "seq": self.seq, "generation": self.generation, "time": time.monotonic()}
        self.events.append(value)
        self.bytes += size
        self.total += size

    def observe(self, events, viewer, epoch, geometry):
        with self.lock:
            if not events or not self.session or self.state != "recording":
                return None
            if (viewer, epoch) != (self.viewer, self.epoch) or time.monotonic() - self.last_poll > 15:
                self.pause("viewer or persistence lost")
                return None
            if geometry != self.geometry:
                self.geometry = geometry
                self.segment += 1
            for event in events:
                # Coalesce unpressed pointer movement, preserving button/drag boundaries.
                if (
                    event["kind"] == "pointer"
                    and event["buttons"] == 0
                    and self.events
                    and self.events[-1]["kind"] == "pointer"
                    and self.events[-1]["buttons"] == 0
                ):
                    continue
                self.append({**event, "geometry": geometry})
            if events and time.monotonic() - self.last_frame >= 0.5:
                self.last_frame = time.monotonic()
                return self.generation

    def pause(self, reason):
        with self.lock:
            if self.session and self.state == "recording":
                self.state, self.reason = "paused", reason
                self.generation += 1

    def disconnect(self, viewer):
        with self.lock:
            if self.session and self.viewer == viewer:
                self.pause("viewer disconnected")

    def frame(self, generation, image, geometry):
        if not image:
            return
        raw = base64.b64decode(image.split(",", 1)[1], validate=True)
        if len(raw) > 2 * 1024 * 1024:
            self.pause("frame too large")
            return
        with self.lock:
            if self.state == "recording" and generation == self.generation:
                from io import BytesIO
                from PIL import Image

                with Image.open(BytesIO(raw)) as decoded:
                    width, height = decoded.size
                self.append(
                    {
                        "image_width": width,
                        "image_height": height,
                        "kind": "frame",
                        "image": image,
                        "sha256": hashlib.sha256(raw).hexdigest(),
                        "geometry": geometry,
                    }
                )

    def batch(self, ack):
        with self.lock:
            if not self.events or ack >= self.events[0]["seq"]:
                self.last_poll = time.monotonic()
            self.events = [e for e in self.events if e["seq"] > ack]
            self.bytes = sum(len(json.dumps(e).encode()) for e in self.events)
            selected, size = [], 0
            for e in self.events:
                size += len(json.dumps(e))
                if selected and size > 3 * 1024 * 1024:
                    break
                selected.append(e)
            return {**self.status(), "events": selected}
