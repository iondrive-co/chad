"""A minimal D-Bus client: just enough to export a tray icon.

Adapted from the sibling xenia project. Marshalling, the session bus
connection and EXTERNAL authentication are done here so that the tray does not
depend on dbus-python or GLib being installed next to a PyInstaller bundle.
"""

from __future__ import annotations

import os
import socket
import struct
import threading
from typing import Any, Callable

LITTLE, BIG = ord("l"), ord("B")

METHOD_CALL, METHOD_RETURN, ERROR, SIGNAL = 1, 2, 3, 4

PATH, INTERFACE, MEMBER, ERROR_NAME, REPLY_SERIAL, DESTINATION, SENDER, SIGNATURE = range(1, 9)

ALIGN = {
    "y": 1, "b": 4, "n": 2, "q": 2, "i": 4, "u": 4, "x": 8, "t": 8, "d": 8,
    "s": 4, "o": 4, "g": 1, "a": 4, "(": 8, "{": 8, "v": 1, "h": 4,
}


class DBusError(RuntimeError):
    pass


class Variant:
    __slots__ = ("signature", "value")

    def __init__(self, signature: str, value: Any) -> None:
        self.signature = signature
        self.value = value

    def __repr__(self) -> str:
        return f"Variant({self.signature!r}, {self.value!r})"

    def __eq__(self, other) -> bool:
        return (isinstance(other, Variant)
                and (self.signature, self.value) == (other.signature, other.value))


def split_signature(signature: str) -> list[str]:
    out: list[str] = []
    index = 0
    while index < len(signature):
        end = _type_end(signature, index)
        out.append(signature[index:end])
        index = end
    return out


def _type_end(signature: str, start: int) -> int:
    code = signature[start]
    if code == "a":
        return _type_end(signature, start + 1)
    if code in "({":
        closing = ")" if code == "(" else "}"
        opening = code
        depth, index = 1, start + 1
        while depth:
            if signature[index] == opening:
                depth += 1
            elif signature[index] == closing:
                depth -= 1
            index += 1
        return index
    return start + 1


def _alignment(signature: str) -> int:
    return ALIGN.get(signature[0], 1)


class Writer:
    def __init__(self, endian: int = LITTLE) -> None:
        self.buf = bytearray()
        self.prefix = "<" if endian == LITTLE else ">"

    def pad(self, alignment: int) -> None:
        while len(self.buf) % alignment:
            self.buf.append(0)

    def write(self, signature: str, value: Any) -> None:
        code = signature[0]
        self.pad(_alignment(signature))

        if code == "y":
            self.buf.append(int(value) & 0xFF)
        elif code == "b":
            self.buf += struct.pack(self.prefix + "I", 1 if value else 0)
        elif code == "n":
            self.buf += struct.pack(self.prefix + "h", int(value))
        elif code == "q":
            self.buf += struct.pack(self.prefix + "H", int(value))
        elif code == "i":
            self.buf += struct.pack(self.prefix + "i", int(value))
        elif code == "u":
            self.buf += struct.pack(self.prefix + "I", int(value))
        elif code == "x":
            self.buf += struct.pack(self.prefix + "q", int(value))
        elif code == "t":
            self.buf += struct.pack(self.prefix + "Q", int(value))
        elif code == "d":
            self.buf += struct.pack(self.prefix + "d", float(value))
        elif code in "so":
            raw = str(value).encode()
            self.buf += struct.pack(self.prefix + "I", len(raw)) + raw + b"\0"
        elif code == "g":
            raw = str(value).encode()
            self.buf += bytes((len(raw),)) + raw + b"\0"
        elif code == "v":
            variant = value if isinstance(value, Variant) else Variant("s", str(value))
            self.write("g", variant.signature)
            self.write(variant.signature, variant.value)
        elif code == "a":
            self._write_array(signature[1:], value)
        elif code == "(":
            self._write_struct(signature[1:-1], value)
        elif code == "{":
            key_sig = split_signature(signature[1:-1])[0]
            value_sig = signature[1 + len(key_sig):-1]
            key, item = value
            self.write(key_sig, key)
            self.write(value_sig, item)
        else:
            raise DBusError(f"cannot marshal type {code!r}")

    def _write_array(self, element_sig: str, value: Any) -> None:
        self.buf += struct.pack(self.prefix + "I", 0)
        length_at = len(self.buf) - 4
        self.pad(_alignment(element_sig))
        start = len(self.buf)

        items = value.items() if element_sig[0] == "{" and isinstance(value, dict) else value
        for item in items:
            self.write(element_sig, item)

        length = len(self.buf) - start
        self.buf[length_at:length_at + 4] = struct.pack(self.prefix + "I", length)

    def _write_struct(self, inner: str, value: Any) -> None:
        parts = split_signature(inner)
        if len(parts) != len(value):
            raise DBusError(f"struct ({inner}) wants {len(parts)} fields, got {len(value)}")
        for part, item in zip(parts, value):
            self.write(part, item)


class Reader:
    def __init__(self, data: bytes, endian: int = LITTLE, offset: int = 0) -> None:
        self.data = data
        self.offset = offset
        self.prefix = "<" if endian == LITTLE else ">"

    def pad(self, alignment: int) -> None:
        while self.offset % alignment:
            self.offset += 1

    def read(self, signature: str) -> Any:
        code = signature[0]
        self.pad(_alignment(signature))

        if code == "y":
            value = self.data[self.offset]
            self.offset += 1
            return value
        for code_name, fmt, size in (("b", "I", 4), ("n", "h", 2), ("q", "H", 2),
                                     ("i", "i", 4), ("u", "I", 4), ("x", "q", 8),
                                     ("t", "Q", 8), ("d", "d", 8)):
            if code == code_name:
                value = struct.unpack_from(self.prefix + fmt, self.data, self.offset)[0]
                self.offset += size
                return bool(value) if code == "b" else value
        if code in "so":
            length = struct.unpack_from(self.prefix + "I", self.data, self.offset)[0]
            self.offset += 4
            value = self.data[self.offset:self.offset + length].decode()
            self.offset += length + 1
            return value
        if code == "g":
            length = self.data[self.offset]
            self.offset += 1
            value = self.data[self.offset:self.offset + length].decode()
            self.offset += length + 1
            return value
        if code == "v":
            inner = self.read("g")
            return Variant(inner, self.read(inner))
        if code == "a":
            element_sig = signature[1:]
            length = struct.unpack_from(self.prefix + "I", self.data, self.offset)[0]
            self.offset += 4
            self.pad(_alignment(element_sig))
            end = self.offset + length
            if element_sig == "y":
                raw = bytes(self.data[self.offset:end])
                self.offset = end
                return raw
            out = []
            while self.offset < end:
                out.append(self.read(element_sig))
            if element_sig[0] == "{":
                return dict(out)
            return out
        if code == "(":
            self.pad(8)
            return tuple(self.read(part) for part in split_signature(signature[1:-1]))
        if code == "{":
            self.pad(8)
            key_sig = split_signature(signature[1:-1])[0]
            value_sig = signature[1 + len(key_sig):-1]
            return (self.read(key_sig), self.read(value_sig))
        raise DBusError(f"cannot unmarshal type {code!r}")


class Message:
    def __init__(self, kind: int, *, path=None, interface=None, member=None,
                 destination=None, signature="", body=(), reply_serial=None,
                 error_name=None, sender=None, serial=0, no_reply=False) -> None:
        self.kind = kind
        self.path = path
        self.interface = interface
        self.member = member
        self.destination = destination
        self.signature = signature
        self.body = list(body)
        self.reply_serial = reply_serial
        self.error_name = error_name
        self.sender = sender
        self.serial = serial
        self.no_reply = no_reply

    def encode(self) -> bytes:
        body = Writer()
        for part, value in zip(split_signature(self.signature), self.body):
            body.write(part, value)

        fields = []
        for code, sig, value in (
            (PATH, "o", self.path), (INTERFACE, "s", self.interface),
            (MEMBER, "s", self.member), (ERROR_NAME, "s", self.error_name),
            (REPLY_SERIAL, "u", self.reply_serial),
            (DESTINATION, "s", self.destination),
            (SIGNATURE, "g", self.signature or None),
        ):
            if value is not None:
                fields.append((code, Variant(sig, value)))

        head = Writer()
        head.buf += bytes((LITTLE, self.kind, 0x01 if self.no_reply else 0x00, 1))
        head.buf += struct.pack("<I", len(body.buf))
        head.buf += struct.pack("<I", self.serial)
        head.write("a(yv)", fields)
        head.pad(8)
        return bytes(head.buf) + bytes(body.buf)

    @staticmethod
    def decode(data: bytes) -> "Message":
        endian = data[0]
        prefix = "<" if endian == LITTLE else ">"
        kind = data[1]
        body_len, serial = struct.unpack_from(prefix + "II", data, 4)

        reader = Reader(data, endian, 12)
        fields = reader.read("a(yv)")
        reader.pad(8)
        header_end = reader.offset

        message = Message(kind, serial=serial)
        for code, variant in fields:
            value = variant.value
            if code == PATH:
                message.path = value
            elif code == INTERFACE:
                message.interface = value
            elif code == MEMBER:
                message.member = value
            elif code == ERROR_NAME:
                message.error_name = value
            elif code == REPLY_SERIAL:
                message.reply_serial = value
            elif code == DESTINATION:
                message.destination = value
            elif code == SENDER:
                message.sender = value
            elif code == SIGNATURE:
                message.signature = value

        body_reader = Reader(data, endian, header_end)
        message.body = [body_reader.read(part)
                        for part in split_signature(message.signature or "")]
        return message

    @staticmethod
    def header_length(data: bytes) -> int:
        endian = data[0]
        prefix = "<" if endian == LITTLE else ">"
        body_len = struct.unpack_from(prefix + "I", data, 4)[0]
        fields_len = struct.unpack_from(prefix + "I", data, 12)[0]
        header = 16 + fields_len
        header += (-header) % 8
        return header + body_len


def session_bus_address() -> str:
    address = os.environ.get("DBUS_SESSION_BUS_ADDRESS")
    if address:
        return address
    runtime = os.environ.get("XDG_RUNTIME_DIR")
    if runtime and os.path.exists(os.path.join(runtime, "bus")):
        return f"unix:path={os.path.join(runtime, 'bus')}"
    raise DBusError("no session bus address in the environment")


def _socket_path(address: str) -> str:
    for part in address.split(";"):
        for piece in part.split(","):
            if piece.startswith("unix:path="):
                return piece[len("unix:path="):]
            if piece.startswith("path="):
                return piece[len("path="):]
            if piece.startswith("unix:abstract="):
                return "\0" + piece[len("unix:abstract="):]
            if piece.startswith("abstract="):
                return "\0" + piece[len("abstract="):]
    raise DBusError(f"no unix socket in bus address: {address}")


class Connection:
    def __init__(self, address: str | None = None) -> None:
        self.address = address or session_bus_address()
        self.unique_name = ""
        self._serial = 0
        self._lock = threading.Lock()
        self._replies: dict[int, list] = {}
        self._handlers: dict[tuple[str, str], Callable] = {}
        self._running = False
        self._sock: socket.socket | None = None
        self._thread: threading.Thread | None = None

    def connect(self) -> "Connection":
        path = _socket_path(self.address)
        self._sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self._sock.connect(path)
        self._authenticate()
        self._running = True
        self._thread = threading.Thread(target=self._reader, daemon=True,
                                        name="chad-tray-dbus")
        self._thread.start()
        self.unique_name = self.call(
            "org.freedesktop.DBus", "/org/freedesktop/DBus",
            "org.freedesktop.DBus", "Hello")[0]
        return self

    def _authenticate(self) -> None:
        assert self._sock is not None
        self._sock.sendall(b"\0")
        uid_hex = str(os.getuid()).encode().hex().encode()
        self._sock.sendall(b"AUTH EXTERNAL " + uid_hex + b"\r\n")

        reply = b""
        while b"\r\n" not in reply:
            chunk = self._sock.recv(1024)
            if not chunk:
                raise DBusError("bus closed the connection during authentication")
            reply += chunk
        if not reply.startswith(b"OK"):
            raise DBusError(f"authentication refused: {reply!r}")
        self._sock.sendall(b"BEGIN\r\n")

    def close(self) -> None:
        self._running = False
        if self._sock is not None:
            try:
                self._sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            self._sock.close()
            self._sock = None

    def _reader(self) -> None:
        buffer = b""
        while self._running:
            try:
                chunk = self._sock.recv(65536) if self._sock else b""
            except OSError:
                break
            if not chunk:
                break
            buffer += chunk
            while len(buffer) >= 16:
                total = Message.header_length(buffer)
                if len(buffer) < total:
                    break
                raw, buffer = buffer[:total], buffer[total:]
                try:
                    self._dispatch(Message.decode(raw))
                except Exception:
                    pass
        self._running = False

    def _dispatch(self, message: Message) -> None:
        if message.kind in (METHOD_RETURN, ERROR):
            with self._lock:
                slot = self._replies.get(message.reply_serial)
            if slot is not None:
                slot[0] = message
                slot[1].set()
            return

        if message.kind == METHOD_CALL:
            self._serve(message)

    def _serve(self, message: Message) -> None:
        handler = self._handlers.get((message.path, message.interface))
        if handler is None and message.interface == "org.freedesktop.DBus.Peer":
            self.send(Message(METHOD_RETURN, reply_serial=message.serial,
                              destination=message.sender))
            return
        if handler is None:
            self.send(Message(ERROR, reply_serial=message.serial,
                              destination=message.sender,
                              error_name="org.freedesktop.DBus.Error.UnknownInterface",
                              signature="s", body=[f"no {message.interface}"]))
            return
        try:
            result = handler(message)
        except Exception as exc:
            self.send(Message(ERROR, reply_serial=message.serial,
                              destination=message.sender,
                              error_name="org.freedesktop.DBus.Error.Failed",
                              signature="s", body=[str(exc)]))
            return
        if message.no_reply:
            return
        signature, body = result if result is not None else ("", [])
        self.send(Message(METHOD_RETURN, reply_serial=message.serial,
                          destination=message.sender,
                          signature=signature, body=body))

    def _next_serial(self) -> int:
        with self._lock:
            self._serial += 1
            return self._serial

    def send(self, message: Message) -> int:
        if not message.serial:
            message.serial = self._next_serial()
        if self._sock is None:
            raise DBusError("not connected")
        with self._lock:
            self._sock.sendall(message.encode())
        return message.serial

    def call(self, destination: str, path: str, interface: str, member: str,
             signature: str = "", body=(), timeout: float = 5.0) -> list:
        message = Message(METHOD_CALL, path=path, interface=interface,
                          member=member, destination=destination,
                          signature=signature, body=body)
        message.serial = self._next_serial()

        event = threading.Event()
        slot = [None, event]
        with self._lock:
            self._replies[message.serial] = slot
        try:
            self.send(message)
            if not event.wait(timeout):
                raise DBusError(f"timed out calling {interface}.{member}")
        finally:
            with self._lock:
                self._replies.pop(message.serial, None)

        reply: Message = slot[0]
        if reply.kind == ERROR:
            detail = reply.body[0] if reply.body else ""
            raise DBusError(f"{reply.error_name}: {detail}")
        return reply.body

    def emit(self, path: str, interface: str, member: str,
             signature: str = "", body=()) -> None:
        self.send(Message(SIGNAL, path=path, interface=interface, member=member,
                          signature=signature, body=body))

    def export(self, path: str, interface: str, handler: Callable) -> None:
        self._handlers[(path, interface)] = handler

    def request_name(self, name: str) -> int:
        return self.call("org.freedesktop.DBus", "/org/freedesktop/DBus",
                         "org.freedesktop.DBus", "RequestName",
                         "su", [name, 0])[0]

    def name_has_owner(self, name: str) -> bool:
        try:
            return bool(self.call("org.freedesktop.DBus", "/org/freedesktop/DBus",
                                  "org.freedesktop.DBus", "NameHasOwner",
                                  "s", [name])[0])
        except DBusError:
            return False
