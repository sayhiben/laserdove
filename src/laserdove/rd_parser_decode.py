from __future__ import annotations

import sys


class RuidaParserDecodeMixin:
    # ---------------- Decode loop ----------------
    """Mixin implementing the RD decode loop."""

    def token_method(self, c):
        """Return token method."""
        consumed, msg = 0, None
        if len(c) == 2:
            return c[1](self)
        if len(c) == 3:
            return c[1](self, c[2])
        if len(c) >= 4:
            consumed, msg = c[1](self, c[2], c[3:])
            if msg is None:
                label = c[3] if isinstance(c[3], str) else ""
                msg = "(" + label + ")" if label else ""
            else:
                label = c[3] if isinstance(c[3], str) else ""
                if label:
                    msg += " (" + label + ")"
        return consumed, msg

    def decode(self, buf: bytes | None = None, *, debug: bool = True) -> None:
        """Decode."""
        debugfile = sys.stderr
        if debug not in (True, False):
            debug = True
            debugfile = sys.stdout
        if buf is not None:
            self._buf = buf
        pos = -1
        while len(self._buf):
            b0 = self._buf[0]
            self._buf = self._buf[1:]
            pos += 1
            self._current_pos = pos
            tok = self.rd_decoder_table.get(b0)

            if tok:
                if isinstance(tok, dict):
                    if not self._buf:
                        if debug:
                            print(f"{pos:5d}: {b0:02x} ERROR: truncated", file=debugfile)
                        break
                    b1 = self._buf[0]
                    c = tok.get(b1)
                    if c:
                        self._buf = self._buf[1:]
                        pos += 1
                        if isinstance(c, dict):
                            if not self._buf:
                                if debug:
                                    print(
                                        f"{pos:5d}: {b0:02x} {b1:02x} ERROR: truncated",
                                        file=debugfile,
                                    )
                                break
                            b2 = self._buf[0]
                            c2 = c.get(b2)
                            if c2:
                                self._buf = self._buf[1:]
                                pos += 1
                                label = c2[0]
                                self._count_label(label)
                                out = f"{pos:5d}: {b0:02x} {b1:02x} {b2:02x} {label}"
                                consumed, msg = self.token_method(c2)
                                if msg is not None:
                                    out += " " + msg
                                if debug:
                                    print(out, file=debugfile)
                                self._buf = self._buf[consumed:]
                                pos += consumed
                            else:
                                if debug:
                                    print(
                                        f"{pos:5d}: {b0:02x} {b1:02x} {b2:02x} unknown nested token",
                                        file=debugfile,
                                    )
                                self._count_unknown(f"UNKNOWN_{b0:02X}_{b1:02X}_{b2:02X}")
                        else:
                            label = c[0]
                            self._count_label(label)
                            out = f"{pos:5d}: {b0:02x} {b1:02x} {label}"
                            consumed, msg = self.token_method(c)
                            if msg is not None:
                                out += " " + msg
                            if debug:
                                print(out, file=debugfile)
                            self._buf = self._buf[consumed:]
                            pos += consumed
                    else:
                        self._count_unknown(f"UNKNOWN_{b0:02X}_{self._buf[0]:02X}")
                        if debug:
                            print(
                                f"{pos:5d}: {b0:02x} {self._buf[0]:02x} second byte not defined in rd_dec",
                                file=debugfile,
                            )
                else:
                    label = tok[0]
                    self._count_label(label)
                    out = f"{pos:5d}: {b0:02x} {label}"
                    consumed, msg = self.token_method(tok)
                    if msg is not None:
                        out += " " + msg
                    if debug:
                        print(out, file=debugfile)
                    self._buf = self._buf[consumed:]
                    pos += consumed
            else:
                self._count_unknown(f"UNKNOWN_{b0:02X}")
                if debug:
                    print(
                        f"{pos:5d}: {b0:02x} ERROR: ----------- token not found in rd_dec",
                        file=debugfile,
                    )
