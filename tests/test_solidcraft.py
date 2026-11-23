#
# PartCAD, 2026
#
# Licensed under Apache License, Version 2.0.
#
"""The SolidCraft plugin, as a workspace uses it.

Two kinds of test, and the split is what a contributor can run where:

* The scripts' own logic -- which commands a part's parameters become, which
  drawing commands a render asks SolidCraft for, which release is fetched for
  which machine, that a download whose checksum does not match is refused --
  needs nothing but the standard library and pytest. It is checked offline,
  with a fake release served from memory and a stand-in for 'solidcraft-cli'.
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
import json
import os
import re
import sys
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


@pytest.fixture
def drawing():
    return _module("render_drawing")


def _commands(drawing, request, file_format="svg", target="/out/bracket.svg"):
    """The commands a render asks SolidCraft for, by command name."""
    script = drawing.script({"shape_name": "bracket", "shape_kind": "part", **request}, file_format, target)
    return {step["command"]: step["params"] for step in script["commands"]}, [
        step["command"] for step in script["commands"]
    ]


def test_a_drawing_is_the_standard_views_on_one_named_sheet(drawing):
    params, order = _commands(drawing, {})
    assert order == ["drawing.create", "drawing.view.standard", "drawing.export"]
    # The sheet is named, because a script cannot read an id out of an earlier step's reply.
    assert params["drawing.create"] == {"name": "bracket", "template": "iso-a3-part", "first_sheet_name": "Sheet 1"}
    assert params["drawing.view.standard"] == {"sheet": "Sheet 1", "iso": True}
    assert params["drawing.export"] == {"sheet": "Sheet 1", "format": "svg", "path": "/out/bracket.svg"}


@pytest.mark.parametrize(
    "request_, template",
    [
        ({}, "iso-a3-part"),
        ({"shape_kind": "assembly"}, "iso-a2-assembly"),
        ({"shape_kind": "scene"}, "iso-a2-assembly"),
        ({"page": "A4"}, "iso-a4-part"),
        ({"page": "a1", "shape_kind": "assembly"}, "iso-a1-assembly"),
        ({"page": "A4", "template": "iso-a0-part"}, "iso-a0-part"),
    ],
)
def test_the_template_is_chosen_by_the_object_s_kind_not_left_to_solidcraft(drawing, request_, template):
    # SolidCraft would pick an assembly's template for every STEP file it opens:
    # each is placed in the design as a component.
    params, _ = _commands(drawing, request_)
    assert params["drawing.create"]["template"] == template


def test_draftwright_s_parameters_mean_the_same_here(drawing):
    params, order = _commands(
        drawing,
        {
            "title": "Mounting Plate",
            "number": "PC-EX-0001",
            "revision": "B",
            "drawn_by": "PartCAD example",
            "company": "PartCAD",
            "scale": 2,
            "projection": "First",
            "material": "AL 6061-T6",  # draftwright's, and not a drawing property of SolidCraft's
        },
    )
    assert params["drawing.create"]["name"] == "Mounting Plate"
    assert params["drawing.create"]["projection"] == "first"
    assert params["drawing.view.standard"]["scale"] == "2:1"
    assert params["drawing.properties.set"] == {
        "values": {"part_number": "PC-EX-0001", "revision": "B", "author": "PartCAD example", "organization": "PartCAD"}
    }
    assert order.index("drawing.properties.set") < order.index("drawing.export")


@pytest.mark.parametrize("value, text", [(1, "1:1"), (2, "2:1"), (0.5, "1:2"), (0.2, "1:5"), ("1:20", "1:20")])
def test_a_scale_is_written_paper_to_model(drawing, value, text):
    assert drawing.scale_of(value) == text


def test_solidcraft_s_own_parameters_reach_it(drawing):
    params, _ = _commands(
        drawing,
        {"view": "Top", "iso": "false", "gap_mm": "12", "line_group": 0.35, "font": "DejaVu Sans", "license": "MIT"},
    )
    assert params["drawing.view.standard"] == {"sheet": "Sheet 1", "iso": False, "view": "top", "gap_mm": 12.0}
    assert params["drawing.create"]["line_group"] == 0.35
    assert params["drawing.create"]["font"] == "DejaVu Sans"
    assert params["drawing.properties.set"] == {"values": {"license": "MIT"}}


def test_a_png_s_options_go_to_a_png_only(drawing):
    options = {"dpi": 150, "colour": "rgb", "paper": "blueprint"}
    png, _ = _commands(drawing, options, "png", "/out/bracket.png")
    assert png["drawing.export"] == {"sheet": "Sheet 1", "format": "png", "path": "/out/bracket.png", **options}
    pdf, _ = _commands(drawing, options, "pdf", "/out/bracket.pdf")
    assert pdf["drawing.export"] == {"sheet": "Sheet 1", "format": "pdf", "path": "/out/bracket.pdf"}


@pytest.mark.parametrize(
    "request_, message",
    [
        ({"shape_kind": "sketch"}, "sketch"),
        ({"page": "letter"}, "ISO A0, A1, A2, A3, A4"),
        ({"scale": "huge"}, "paper:model"),
        ({"scale": 0}, "above nought"),
        ({"projection": "oblique"}, "first, third"),
        ({"view": "iso"}, "front, back, top, bottom, left, right"),
    ],
)
def test_what_solidcraft_cannot_draw_is_refused_saying_why(drawing, request_, message):
    with pytest.raises(ValueError, match=re.escape(message)):
        _commands(drawing, request_)


def test_a_file_type_solidcraft_does_not_write_is_refused(drawing):
    with pytest.raises(ValueError, match="SVG, PDF, DXF, PNG"):
        drawing.format_of("/out/bracket.dwg")
    assert drawing.format_of("/out/BRACKET.PDF") == "pdf"


def test_what_solidcraft_said_about_a_drawing_it_wrote_is_a_warning(drawing):
    commands = [
        {"command": "drawing.create"},
        {"command": "drawing.view.standard"},
        {"command": "drawing.export"},
    ]
    results = [
        {"drawing": 1},
        {"scale": {"text": "5:1"}, "outside": [2, 3]},
        {
            "views": [{"view": 1, "status": "drawn"}, {"view": 4, "status": "placeholder", "reason": "no bodies"}],
            "report": {"pictures_left_out": 1},
        },
    ]
    warnings = drawing.warnings_of(commands, results)
    assert len(warnings) == 3
    assert "5:1" in warnings[0] and "'page'" in warnings[0]
    assert warnings[1] == "SolidCraft: view 4 is placeholder: no bodies"
    assert "1 shaded picture(s) left out" in warnings[2]
    assert drawing.warnings_of(commands, [{}, {"outside": []}, {"views": [{"view": 1, "status": "drawn"}]}]) == []


def test_the_reply_of_a_run_is_read_in_both_its_shapes(drawing, tmp_path):
    path = tmp_path / "results.json"
    path.write_text(json.dumps([{"a": 1}]))
    assert drawing.results_of(str(path)) == ([{"a": 1}], None)
    path.write_text(
        json.dumps({"results": [{"a": 1}], "error": {"step": 2, "command": "drawing.export", "error": "x"}})
    )
    assert drawing.results_of(str(path)) == ([{"a": 1}], {"step": 2, "command": "drawing.export", "error": "x"})
    assert drawing.results_of(str(tmp_path / "missing.json")) == ([], None)


# A stand-in for 'solidcraft-cli': records how it was run, writes the file the
# export step names and the reply '--json-out' asks for, as SolidCraft does.
FAKE_CLI = """#!%(python)s
import json, sys
args = sys.argv[1:]
with open(%(log)r, "w") as f:
    json.dump(args, f)
with open(args[1]) as f:
    commands = json.load(f)["commands"]
results = []
for step in commands:
    if step["command"] == "drawing.export":
        if %(fail)r:
            reply = {"results": results, "error": {"step": len(results), "command": "drawing.export",
                     "error": "the view failed: nothing to draw"}}
            with open(args[args.index("--json-out") + 1], "w") as f:
                json.dump(reply, f)
            sys.exit(1)
        with open(step["params"]["path"], "w") as f:
            f.write("<svg/>")
        results.append({"views": [{"view": 4, "status": "failed", "reason": "too small"}]})
    else:
        results.append({})
with open(args[args.index("--json-out") + 1], "w") as f:
    json.dump(results, f)
"""


@pytest.fixture
def fake_cli(monkeypatch, tmp_path):
    """Run 'process()' against the stand-in, with no OCP needed to write the STEP file."""

    def make(fail=False):
        log = tmp_path / "argv.json"
        cli = tmp_path / "solidcraft-cli"
        cli.write_text(FAKE_CLI % {"python": sys.executable, "log": str(log), "fail": fail})
        cli.chmod(0o755)

        class Tool:
            @staticmethod
            def executable(cache_dir=None):
                return str(cli)

        class Export:
            @staticmethod
            def write_step(shape, path):
                with open(path, "w") as f:
                    f.write("ISO-10303-21;")

        monkeypatch.setitem(sys.modules, "solidcraft_tool", Tool)
        monkeypatch.setitem(sys.modules, "export_solidcraft", Export)
        return log

    return make


@pytest.mark.skipif(os.name == "nt", reason="the stand-in is a script run by its shebang")
def test_a_drawing_is_rendered_by_solidcraft_s_command_line(drawing, fake_cli, tmp_path):
    log = fake_cli()
    target = tmp_path / "out" / "bracket.svg"
    result = drawing.process(
        str(target), {"wrapped": object(), "shape_name": "my bracket", "shape_kind": "part", "reproducible": True}
    )
    assert result["success"], result
    assert target.read_text() == "<svg/>"
    args = json.loads(log.read_text())
    assert args[0] == "run"
    # The object is the design the commands run on, as a STEP file named after it.
    assert os.path.basename(args[args.index("--in") + 1]) == "my_bracket.step"
    # A parameter spelt wrongly here stops the run rather than being dropped.
    assert args[args.index("--param-check") + 1] == "strict"
    assert result["warnings"] == [
        "SolidCraft has no reproducible output to ask for; the file is written as SolidCraft writes it",
        "SolidCraft: view 4 is failed: too small",
    ]


@pytest.mark.skipif(os.name == "nt", reason="the stand-in is a script run by its shebang")
def test_a_drawing_solidcraft_refuses_fails_in_its_words(drawing, fake_cli, tmp_path):
    fake_cli(fail=True)
    result = drawing.process(str(tmp_path / "bracket.pdf"), {"wrapped": object(), "shape_name": "bracket"})
    assert not result["success"]
    assert result["exception"] == "SolidCraft failed at drawing.export: the view failed: nothing to draw"


def test_nothing_is_drawn_without_a_shape(drawing):
    assert drawing.process("/out/bracket.svg", {"shape_name": "bracket"})["exception"] == "No shape to draw"


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


def test_the_plugin_draws_what_the_draft_tab_offers(ctx):
    pc = _partcad()
    plugin = ctx.get_project("//solidcraft")
    # What 'render.formats' lists for the IDE's Draft tab, in this order.
    section = plugin.config_obj["render"]
    assert pc.output.format_names(section) == ["svg", "pdf", "dxf", "png"]
    for name in ("svg", "pdf", "dxf", "png"):
        assert section[name]["path"] == "render_drawing.py"
        assert section[name]["extension"] == name
        # A drawing is of the parts as they are, whatever the built-in layer under it says.
        assert section[name]["visuals"] is False


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


@pytest.mark.slow
@pytest.mark.parametrize("file_format, magic", [("svg", b"<svg"), ("pdf", b"%PDF")])
def test_an_object_renders_as_a_solidcraft_drawing(ctx, tmp_path, file_format, magic):
    target = tmp_path / ("slab." + file_format)
    part = ctx.get_part(":slab")
    # As the Draft tab asks for it: 'pc render -t <format> -e //solidcraft'.
    asyncio.run(part.render_async(ctx, format_name=file_format, filepath=str(target), options_package="//solidcraft"))
    assert target.is_file(), part.errors
    assert magic in target.read_bytes()[:4096]
