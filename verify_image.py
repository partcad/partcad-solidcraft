#!/usr/bin/env python3
#
# PartCAD, 2026
#
# Licensed under Apache License, Version 2.0.
#
"""Whether this image can actually run SolidCraft.

Run at build time, so that an image which cannot is never published. The base
image's own `verify.py` checks the contract PartCAD talks to a container
through; this checks what this image adds on top of it, the way the package
uses it:

* `solidcraft-cli` builds a part and writes it as STEP -- headless, with no GPU
  and no display, which is how every script in this package runs it -- and
  measures the result to be the part that was asked for;
* every library the desktop application opens at run time rather than links
  can be opened, since that is what `pc ide open` running it in here needs and
  a missing one would only show as a window that never appears.
"""

import ctypes
import json
import os
import shutil
import subprocess
import sys
import tempfile

# What the application loads with dlopen(): its display, its renderer and its
# file dialogs. The command line tool links none of it.
RUNTIME_LIBRARIES = (
    "libEGL.so.1",
    "libvulkan.so.1",
    "libwayland-client.so.0",
    "libwayland-egl.so.1",
    "libX11-xcb.so.1",
    "libxcb.so.1",
    "libXcursor.so.1",
    "libXi.so.6",
    "libxkbcommon.so.0",
    "libxkbcommon-x11.so.0",
    "libXrandr.so.2",
    "libdbus-1.so.3",
)

BOX = {
    "commands": [
        {"command": "sketch.create", "params": {"plane": "XY"}},
        {"command": "sketch.rectangle.two_point", "params": {"p0": [0, 0], "p1": [10, 20]}},
        {"command": "solid.extrude", "params": {"distance": 30}},
    ]
}


def main() -> int:
    failures = []

    for name in ("solidcraft-cli", "solidcraft"):
        if not shutil.which(name):
            failures.append("'%s' is not on PATH" % name)

    cli = shutil.which("solidcraft-cli")
    if cli:
        with tempfile.TemporaryDirectory() as tmp:
            script = os.path.join(tmp, "box.json")
            step = os.path.join(tmp, "box.step")
            with open(script, "w", encoding="utf-8") as f:
                json.dump(BOX, f)
            built = subprocess.run([cli, "run", script, "--out", step, "--quiet"], capture_output=True, text=True)
            if built.returncode != 0 or not os.path.isfile(step):
                failures.append("solidcraft-cli could not build a box: %s" % (built.stderr.strip() or built.returncode))
            else:
                measured = subprocess.run([cli, "eval", step], capture_output=True, text=True)
                try:
                    volume = json.loads(measured.stdout)["bodies"][0]["volume_mm3"]
                except (ValueError, KeyError, IndexError):
                    volume = None
                if volume is None or abs(volume - 6000.0) > 1e-6:
                    failures.append("solidcraft-cli read its own box back as %r, not 6000 mm^3" % volume)
                else:
                    print("solidcraft-cli built and measured a %g mm^3 box" % volume)

    for library in RUNTIME_LIBRARIES:
        try:
            ctypes.CDLL(library)
        except OSError as e:
            failures.append("the application could not open %s: %s" % (library, e))

    for failure in failures:
        print("verify_image: %s" % failure, file=sys.stderr)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
