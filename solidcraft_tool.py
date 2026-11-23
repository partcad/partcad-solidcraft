#
# PartCAD, 2026
#
# Licensed under Apache License, Version 2.0.
#
"""Find SolidCraft's command line tool, or fetch it.

SolidCraft (https://github.com/tangency-labs/solidcraft, MIT or Apache-2.0) is a
native program rather than a Python package, so pip cannot install it into a
sandbox. In order of preference, the 'solidcraft-cli' used is:

  1. one already on the PATH -- a machine that installed SolidCraft, or this
     package's image ('Dockerfile'), which PartCAD runs the scripts in when its
     'docker' sandbox is in use and the image can be pulled;
  2. the release this module pins, downloaded once into the package's cache
     directory and verified against the checksum below before it is unpacked.

Runs inside the sandbox, so it uses nothing but the standard library.
"""

import hashlib
import os
import platform
import shutil
import tarfile
import tempfile
import urllib.request
import zipfile

CLI = "solidcraft-cli"
VERSION = "0.47.0"
RELEASE_URL = "https://github.com/tangency-labs/solidcraft/releases/download/v%s/" % VERSION

# (operating system, machine) -> (asset, SHA-256), from the release's
# 'SHA256SUMS.txt'. Each holds the command line tool beside the application.
ASSETS = {
    ("linux", "x86_64"): (
        "solidcraft-%s-linux-x86_64.tar.gz" % VERSION,
        "40669dcb2f64ea377161b79183f22779397f55088fe529750a1c8501cc55b972",
    ),
    ("linux", "aarch64"): (
        "solidcraft-%s-linux-aarch64.tar.gz" % VERSION,
        "d29b2fe019a383036c65b6bae1bd601e7a9a7bf50ffdd6a64a2560fbe0dbf66d",
    ),
    ("freebsd", "x86_64"): (
        "solidcraft-%s-freebsd-x86_64.tar.gz" % VERSION,
        "20469255ccede4500b36cf234fe21da31039cd62b520d426579d5f5c1ee8ae67",
    ),
    ("darwin", "x86_64"): (
        "solidcraft-cli-%s-macos-universal.zip" % VERSION,
        "6b0a3419e313f45faf7cb4b644c9af6652bb66929e7bec67ccb105e81b2f4758",
    ),
    ("darwin", "aarch64"): (
        "solidcraft-cli-%s-macos-universal.zip" % VERSION,
        "6b0a3419e313f45faf7cb4b644c9af6652bb66929e7bec67ccb105e81b2f4758",
    ),
    ("windows", "x86_64"): (
        "solidcraft-%s-windows-x64-portable.zip" % VERSION,
        "394e86dae142b2f819d8d64efdbc99c9096a8a2a92e62aef0a56fe17e035a2db",
    ),
    ("windows", "aarch64"): (
        "solidcraft-%s-windows-arm64-portable.zip" % VERSION,
        "e07bd1ee9b9debae1452be7132743879fc4738d4266b0d63fbdcd00fe2210332",
    ),
}

MACHINES = {"amd64": "x86_64", "x86_64": "x86_64", "arm64": "aarch64", "aarch64": "aarch64"}


# What 'solidcraft-cli' is run with, on top of the caller's environment. SolidCraft
# asks GitHub for a newer release and sends anonymous usage statistics unless
# told not to; run headless by PartCAD on somebody's behalf, it is told not to
# (see "Privacy" in SolidCraft's documentation).
QUIET_ENVIRONMENT = {"SOLIDCRAFT_NO_TELEMETRY": "1", "SOLIDCRAFT_UPDATE_CHECK": "0"}


def environment():
    """The environment to run 'solidcraft-cli' in: this one, with nothing sent anywhere."""
    return {**os.environ, **QUIET_ENVIRONMENT}


def host():
    """This machine as ASSETS spells it: ('linux', 'x86_64') and so on."""
    return platform.system().lower(), MACHINES.get(platform.machine().lower(), platform.machine().lower())


def default_cache_dir():
    """This package's own directory, for a PartCAD that hands scripts no 'cache_dir'."""
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), ".cache")


def executable(cache_dir=None):
    """The path of a 'solidcraft-cli' to run, downloading the pinned release if need be."""
    found = shutil.which(CLI)
    if found:
        return found

    system, machine = host()
    if (system, machine) not in ASSETS:
        raise RuntimeError(
            "SolidCraft publishes no build for %s/%s; install it and put %s on the PATH" % (system, machine, CLI)
        )
    asset, sha256 = ASSETS[(system, machine)]
    exe_name = CLI + (".exe" if system == "windows" else "")
    target = os.path.join(cache_dir or default_cache_dir(), "solidcraft", VERSION, "%s-%s" % (system, machine))
    exe = os.path.join(target, exe_name)
    if os.path.isfile(exe):
        return exe

    os.makedirs(os.path.dirname(target), exist_ok=True)
    # Unpacked beside the target and moved into place in one rename, so that
    # two parts converted at once never see half a download: the loser of the
    # race finds the winner's copy and uses that.
    staging = tempfile.mkdtemp(prefix=".download-", dir=os.path.dirname(target))
    try:
        archive = os.path.join(staging, asset)
        with urllib.request.urlopen(RELEASE_URL + asset, timeout=600) as response, open(archive, "wb") as f:
            shutil.copyfileobj(response, f)
        digest = hashlib.sha256()
        with open(archive, "rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                digest.update(chunk)
        if digest.hexdigest() != sha256:
            raise RuntimeError("%s does not match its published checksum; refusing to run it" % asset)

        unpacked = os.path.join(staging, "unpacked")
        if asset.endswith(".zip"):
            with zipfile.ZipFile(archive) as z:
                z.extractall(unpacked)
        else:
            with tarfile.open(archive) as t:
                if hasattr(tarfile, "data_filter"):
                    t.extractall(unpacked, filter="data")
                else:
                    t.extractall(unpacked)

        payload = os.path.join(staging, "payload")
        os.makedirs(payload)
        for root, _dirs, files in os.walk(unpacked):
            if exe_name in files:
                shutil.move(os.path.join(root, exe_name), os.path.join(payload, exe_name))
                break
        else:
            raise RuntimeError("%s holds no %s" % (asset, exe_name))
        os.chmod(os.path.join(payload, exe_name), 0o755)

        try:
            os.replace(payload, target)
        except OSError:
            if not os.path.isfile(exe):
                raise
    finally:
        shutil.rmtree(staging, ignore_errors=True)
    return exe
