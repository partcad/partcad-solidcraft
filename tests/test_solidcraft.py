#
# PartCAD, 2026
#
# Licensed under Apache License, Version 2.0.
#
"""The SolidCraft plugin, as a workspace uses it.

Two kinds of test, and the split is what a contributor can run where:

* The scripts' own logic -- which commands a part's parameters become, which
  release is fetched for which machine, that a download whose checksum does not
  match is refused -- needs nothing but the standard library and pytest. It is
  checked offline, with a fake release served from memory.
* Everything else needs PartCAD: the package beside this file imports the
  plugin and declares one part per partType. Those tests are skipped where
  PartCAD is not installed, or is older than the file-backed partTypes this
  plugin is built on, and the ones that build a part are marked 'slow': the first
  of them downloads SolidCraft and builds PartCAD's sandbox.

Run them with `pytest`; `pytest -m "not slow"` for the quick ones only.
"""

import asyncio
import hashlib
import importlib.util
import io
import os
import re
import tarfile
import zipfile

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
PLUGIN = os.path.dirname(HERE)


def _module(name):
    spec = importlib.util.spec_from_file_location("solidcraft_plugin_" + name, os.path.join(PLUGIN, name + ".py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def tool():
    return _module("solidcraft_tool")


# --------------------------------------------------------------------------- #
# The scripts, without PartCAD                                                #
# --------------------------------------------------------------------------- #


def test_a_part_s_parameters_become_the_design_s():
    script = _module("read_part")
    assert script.parameter_script({"height": 35, "width": 12.5, "flag": True, "unset": None}) == [
        {"command": "parameters.change", "params": {"name": "height", "expression": "35"}},
        {"command": "parameters.change", "params": {"name": "width", "expression": "12.5"}},
    ]


def test_every_platform_names_an_asset_of_the_pinned_release(tool):
    for (system, machine), (asset, sha256) in tool.ASSETS.items():
        assert tool.VERSION in asset, (system, machine)
        assert re.fullmatch(r"[0-9a-f]{64}", sha256), (system, machine)


def test_the_image_installs_the_release_the_scripts_download(tool):
    """The Dockerfile and 'solidcraft_tool.py' each pin SolidCraft, and must agree."""
    with open(os.path.join(PLUGIN, "Dockerfile"), encoding="utf-8") as f:
        dockerfile = f.read()
    assert "SOLIDCRAFT_VERSION=%s;" % tool.VERSION in dockerfile
    for machine in ("x86_64", "aarch64"):
        _asset, sha256 = tool.ASSETS[("linux", machine)]
        assert "%s) SOLIDCRAFT_ARCH=%s; SHA256=%s" % (machine, machine, sha256) in dockerfile


def test_one_on_the_path_is_used_as_it_is(tool, monkeypatch, tmp_path):
    monkeypatch.setattr(tool.shutil, "which", lambda name: "/opt/solidcraft/bin/" + name)
    assert tool.executable(str(tmp_path)) == "/opt/solidcraft/bin/solidcraft-cli"
    assert list(tmp_path.iterdir()) == []


def _serve(tool, monkeypatch, archive: bytes, sha256: str):
    """Make the pinned release for this machine be 'archive', checked against 'sha256'."""
    monkeypatch.setattr(tool.shutil, "which", lambda _name: None)
    monkeypatch.setattr(tool, "host", lambda: ("linux", "x86_64"))
    monkeypatch.setitem(tool.ASSETS, ("linux", "x86_64"), ("solidcraft-test.tar.gz", sha256))
    fetched = []

    def urlopen(url, timeout=None):
        fetched.append(url)
        return io.BytesIO(archive)

    monkeypatch.setattr(tool.urllib.request, "urlopen", urlopen)
    return fetched


def _release():
    """A release archive laid out as SolidCraft's Linux one is."""
    payload = b"#!/bin/sh\necho solidcraft-cli\n"
    out = io.BytesIO()
    with tarfile.open(fileobj=out, mode="w:gz") as archive:
        info = tarfile.TarInfo("solidcraft-0.0.0-linux-x86_64/bin/solidcraft-cli")
        info.size = len(payload)
        archive.addfile(info, io.BytesIO(payload))
    return out.getvalue(), payload


def test_a_release_is_downloaded_once_into_the_cache(tool, monkeypatch, tmp_path):
    archive, payload = _release()
    fetched = _serve(tool, monkeypatch, archive, hashlib.sha256(archive).hexdigest())

    exe = tool.executable(str(tmp_path))
    assert exe == str(tmp_path / "solidcraft" / tool.VERSION / "linux-x86_64" / "solidcraft-cli")
    with open(exe, "rb") as f:
        assert f.read() == payload
    assert os.access(exe, os.X_OK)

    # A second part finds it there.
    assert tool.executable(str(tmp_path)) == exe
    assert len(fetched) == 1
    # Nothing is left of the download but the tool.
    assert sorted(os.listdir(tmp_path / "solidcraft" / tool.VERSION)) == ["linux-x86_64"]


def test_a_release_that_does_not_match_its_checksum_is_refused(tool, monkeypatch, tmp_path):
    archive, _payload = _release()
    _serve(tool, monkeypatch, archive, "0" * 64)

    with pytest.raises(RuntimeError, match="checksum"):
        tool.executable(str(tmp_path))
    assert not (tmp_path / "solidcraft" / tool.VERSION / "linux-x86_64").exists()


def test_a_machine_with_no_release_is_told_what_to_do(tool, monkeypatch, tmp_path):
    monkeypatch.setattr(tool.shutil, "which", lambda _name: None)
    monkeypatch.setattr(tool, "host", lambda: ("sunos", "sparc"))
    with pytest.raises(RuntimeError, match="PATH"):
        tool.executable(str(tmp_path))


def test_without_a_cache_directory_the_package_s_own_is_used(tool):
    assert tool.default_cache_dir() == os.path.join(PLUGIN, ".cache")


# --------------------------------------------------------------------------- #
# The package, through PartCAD                                                #
# --------------------------------------------------------------------------- #


def _partcad():
    """PartCAD, if it is installed and new enough to read a file through a partType."""
    pc = pytest.importorskip("partcad")
    if not hasattr(pc.output, "package_cache_dir"):
        pytest.skip("this PartCAD's partTypes cannot read a file; it is older than this plugin")
    return pc


@pytest.fixture(scope="module")
def ctx():
    pc = _partcad()
    return pc.Context(os.path.join(HERE, "partcad.yaml"))


def test_the_tests_are_not_part_of_the_plugin():
    pc = _partcad()
    pytest.importorskip("partcad_utils.pcignore")
    plugin = pc.Context(PLUGIN)
    names = [p["name"] if isinstance(p, dict) else p for p in plugin.get_all_packages(has_stuff=False)]
    assert not any("tests" in name for name in names)
    # ...and this folder is a workspace of its own, not a corner of the plugin's.
    assert pc.Context(HERE).root_path == HERE


def test_the_plugin_declares_its_part_types_export_and_application(ctx):
    plugin = ctx.get_project("//solidcraft")
    assert plugin is not None
    for name, extension in (("solidcraft", "solidcraft"), ("rhino", "3dm"), ("iges", "igs")):
        assert plugin.get_part_type_config(name)["extension"] == extension
    assert "solidcraft" in plugin.config_obj["export"]
    assert plugin.config_obj["open"]["solidcraft"]["formats"][0] == "solidcraft"


@pytest.mark.parametrize(
    "name, file", [("plate", "plate.solidcraft"), ("slab", "slab.3dm"), ("cylinder", "cylinder.igs")]
)
def test_the_file_is_the_workspace_s(ctx, name, file):
    part = ctx.get_part(":" + name)
    assert os.path.abspath(part.path) == os.path.join(HERE, file)
    assert os.path.abspath(part.path) in [os.path.abspath(p) for p in part.cache_dependencies]


def test_the_image_and_the_cache_are_the_plugin_s(ctx):
    pc = _partcad()
    plugin = ctx.get_project("//solidcraft")
    assert plugin.docker_image_declared.startswith("ghcr.io/partcad/partcad-solidcraft:")
    # The workspace declares no image, so the one the part is keyed under -- and
    # its sandbox built from, where Docker is in use -- is the plugin's.
    assert ctx.get_part(":plate").environment_cache_key.endswith(";image=" + plugin.docker_image_declared)
    assert pc.output.package_cache_dir(ctx, plugin) == os.path.join(PLUGIN, ".cache")


def _measured(ctx, name):
    from partcad import shape_envelope

    part = ctx.get_part(name)
    envelope = asyncio.run(part.get_wrapped(ctx))
    assert envelope is not None, part.errors
    return envelope[shape_envelope.KEY_METADATA][shape_envelope.METADATA_MEASUREMENTS]


@pytest.mark.slow
def test_the_parts_are_what_solidcraft_reads(ctx):
    assert _measured(ctx, ":plate")["volume"] == pytest.approx(40 * 30 * 20)
    assert _measured(ctx, ":slab")["bbox"] == pytest.approx([0, 0, 0, 25, 15, 5], abs=1e-6)
    cylinder = _measured(ctx, ":cylinder")
    assert cylinder["bbox"] == pytest.approx([-5, -5, 0, 5, 5, 12], abs=1e-6)
    assert cylinder["volume"] == pytest.approx(3.14159265 * 25 * 12, rel=2e-3)


@pytest.mark.slow
def test_a_parameter_rebuilds_the_design(ctx):
    assert _measured(ctx, ":plate;height=35")["bbox"] == pytest.approx([0, 0, 0, 40, 30, 35], abs=1e-6)


@pytest.mark.slow
def test_an_object_exports_as_a_solidcraft_design(ctx, tmp_path):
    target = tmp_path / "slab.solidcraft"
    part = ctx.get_part(":slab")
    asyncio.run(part.render_async(ctx, format_name="solidcraft:solidcraft", filepath=str(target)))
    assert target.is_file(), part.errors
    with zipfile.ZipFile(target) as design:
        names = design.namelist()
    assert "document.json" in names
    assert any(name.endswith(".step") for name in names)
