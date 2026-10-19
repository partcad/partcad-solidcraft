# The runtime this package's scripts run best in, and the one `pc ide open
# --with solidcraft` runs SolidCraft in on a machine that has it nowhere else.
#
# It carries what pip cannot: SolidCraft's command line tool and its desktop
# application, both native executables. Nothing else is here, because nothing
# else is needed: the scripts use the standard library and the OpenCASCADE that
# PartCAD installs into every sandbox.
#
# It does NOT carry this package's code. PartCAD mounts the package at run time,
# so a user who fetches a newer version gets that version rather than whatever
# was baked in months ago.
#
# It is an alternative, not a requirement. `partcad.yaml` names it as the
# package's `dockerImage`, which PartCAD uses where its `docker` sandbox is in
# use and the image can be pulled; anywhere else the scripts download the same
# release of `solidcraft-cli` themselves (see `solidcraft_tool.py`).
ARG PYTHON_VERSION=3.11
ARG ARCH=amd64
FROM ghcr.io/partcad/partcad-container-python:py${PYTHON_VERSION}-${ARCH}

# The moving tag, deliberately. What this image publishes is immutable and is
# what packages pin; what it is *built from* moves, so that a base image which
# breaks this plugin fails this repository's own build rather than a user's
# render. See "Pinning" in partcad/partcad's docs/source/config_output.rst.

LABEL partcad.image="1"
LABEL org.opencontainers.image.source="https://github.com/partcad/partcad-solidcraft"

USER root
ENV DEBIAN_FRONTEND=noninteractive

# What the application opens at run time rather than links: a display (X11 or
# Wayland), a renderer (Vulkan, with Mesa's software one for a container that
# has no GPU) and D-Bus for its file dialogs. The command line tool needs none
# of it.
# hadolint ignore=DL3008
RUN apt-get update \
  && apt-get install --yes --no-install-recommends \
    libdbus-1-3 \
    libegl1 \
    libvulkan1 \
    libwayland-client0 \
    libwayland-egl1 \
    libx11-xcb1 \
    libxcb1 \
    libxcursor1 \
    libxi6 \
    libxkbcommon-x11-0 \
    libxkbcommon0 \
    libxrandr2 \
    mesa-vulkan-drivers \
  && rm -rf /var/lib/apt/lists/*

# SolidCraft itself: the release `solidcraft_tool.py` pins, checked against the
# SHA-256 the release publishes, unpacked into a prefix of its own. Keep the
# version and both checksums in step with that file.
#
# Python does the downloading because this base image has no curl, and
# installing it to fetch one file would leave it here.
RUN set -eu; \
    SOLIDCRAFT_VERSION=0.16.1; \
    case "$(uname -m)" in \
      x86_64) SOLIDCRAFT_ARCH=x86_64; SHA256=b6dd545496a792897b87ba5679292db987853088d7cf13ccee64d94f0707f42b ;; \
      aarch64) SOLIDCRAFT_ARCH=aarch64; SHA256=639fea1b84b2b6e95b78203a044090450a539b3b341201eb2720d59329a71934 ;; \
      *) echo "this image pins no SolidCraft for $(uname -m)" >&2; exit 1 ;; \
    esac; \
    python3 -c 'import hashlib, io, sys, tarfile, urllib.request; url, want, prefix = sys.argv[1:4]; blob = urllib.request.urlopen(url).read(); got = hashlib.sha256(blob).hexdigest(); got == want or sys.exit("%s hashes to %s, not the pinned %s" % (url, got, want)); archive = tarfile.open(fileobj=io.BytesIO(blob), mode="r:gz"); members = [m for m in archive.getmembers() if "/" in m.name]; [setattr(m, "name", m.name.split("/", 1)[1]) for m in members]; archive.extractall(prefix, members=members, filter="data")' \
      "https://github.com/bherbruck/solidcraft/releases/download/v${SOLIDCRAFT_VERSION}/solidcraft-${SOLIDCRAFT_VERSION}-linux-${SOLIDCRAFT_ARCH}.tar.gz" \
      "${SHA256}" /opt/solidcraft

# Where every way of looking finds them: on PATH, and in /usr/local/bin, which
# a sandbox that rebuilds PATH still searches. Symlinks rather than an edit to
# PATH for that reason.
RUN ln -s /opt/solidcraft/bin/solidcraft /opt/solidcraft/bin/solidcraft-cli /usr/local/bin/

# What a caller may run, extending the base image's allowlist rather than
# replacing it: the interpreter stays reachable, and so do both of SolidCraft's.
ENV PC_CONTAINER_ALLOWED_COMMANDS='{"python": "/usr/local/bin/python3", "solidcraft": "/usr/local/bin/solidcraft", "solidcraft-cli": "/usr/local/bin/solidcraft-cli"}'

# Two checks, and neither is redundant. The first is the base image's own: it
# says whether anything above broke the contract PartCAD talks to this image
# through. The second is this package's, and proves SolidCraft actually works
# here -- the command line tool converting a part with no GPU and no display,
# and the libraries the application opens at run time all present.
COPY verify_image.py /tmp/verify_image.py
RUN python3 /pc/verify.py \
  && python3 /tmp/verify_image.py \
  && rm /tmp/verify_image.py

USER pc
