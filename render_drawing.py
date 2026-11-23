#
# PartCAD, 2026
#
# Licensed under Apache License, Version 2.0.
#
"""Render a PartCAD part or assembly as a technical drawing made by SolidCraft.

A render, not an export: what this writes is a drawing sheet -- views of the
object, a border and a title block -- which a person reads, and which no CAD
tool can go on modelling with. So the file types are declared under 'render:'.

SolidCraft does the drawing; this script only says what to draw. The object is
written to STEP, opened in SolidCraft as a design, and a script of SolidCraft's
own drawing commands is run on it by 'solidcraft-cli run':

    drawing.create          a drawing from one of SolidCraft's ISO templates,
                            with one sheet, named so the commands below can
                            find it ('SHEET')
    drawing.view.standard   front, top and right views lined up in the
                            drawing's projection, and an isometric one, at the
                            largest ISO 5455 scale that fits (or 'scale')
    drawing.sheet.set       what the title block says where the template reads
    drawing.properties.set  it from somewhere a script cannot point at: the
    drawing.revision.add    title and the identification number are typed
                            over their cells, the legal owner is the drawing's
                            'organization', and who drew it, who approved it
                            and when are a revision row
    drawing.export          the sheet as SVG, PDF, DXF or PNG

Only the template and the views are SolidCraft's choice. What a drawing package
is asked for is the same whoever draws it, so the parameters PartCAD's Draft
tab and a package's 'render:' section set mean here what they mean to
'//pub/feature/render/draftwright' wherever SolidCraft has the same notion:
'title', 'number', 'revision', 'drawn_by', 'company', 'material', 'date',
'page', 'scale' and 'projection'. A package that configures its drawings once
gets the same title block from either. The rest are SolidCraft's own: see
'partcad.yaml'.

No dimensions are placed. SolidCraft brings a design's own dimensions into a
drawing ('drawing.dimension.from_model'), but what PartCAD hands over is a
solid, with no sketch or feature behind it to have dimensions -- whatever the
object was built with, a SolidCraft design included, since its history stays
in the design. Dimensioning a solid by hand is a person's job in SolidCraft;
'pc ide open --with solidcraft' opens the object there.

SolidCraft checks each command's parameters against its contract and stops at
one it does not know ('--param-check strict'), so a parameter this script spells
wrongly fails the render, naming it, rather than being dropped without a word.

Run by PartCAD's output wrapper with the shape in 'request["wrapped"]', the
file to write as 'path', and 'request["cache_dir"]' to where 'solidcraft-cli'
is kept.
"""

import json
import os
import re
import subprocess
import sys
import tempfile

# Siblings are imported by bare name, and the sandbox sets PYTHONSAFEPATH.
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

# Raise with any change to what this produces (see PartCAD's 'code_version').
CACHE_VERSION = 1

# What 'drawing.export' writes.
FORMATS = ("svg", "pdf", "dxf", "png")

# The one sheet a drawing made here has, named in 'drawing.create' so that the
# commands after it can name it too: a script cannot read an id out of the
# reply to an earlier step.
SHEET = "Sheet 1"

# SolidCraft's built-in templates are 'iso-<page>-part' and
# 'iso-<page>-assembly', ISO A4 to A0, each with the ISO border, its title block
# and its kind's general tolerances.
PAGES = ("a0", "a1", "a2", "a3", "a4")

# The page each kind of object is drawn on when none is named: SolidCraft's own
# defaults (A3 for a part, A2 for an assembly). Named here rather than left to
# SolidCraft because SolidCraft picks by whether the design places components,
# and every STEP file it opens is placed as one -- so it would draw every part on
# an assembly's sheet, with an assembly's general tolerances.
DEFAULT_PAGES = {"part": "a3", "assembly": "a2"}

# What the main view may look from (the front by default).
MAIN_VIEWS = ("front", "back", "top", "bottom", "left", "right")

PROJECTIONS = ("first", "third")

# The drawing's properties, as '<parameter>: <property>'. The first four are
# draftwright's names; the rest are SolidCraft's. Of these the built-in title
# blocks show one, 'organization', as the legal owner; the rest are kept with
# the drawing, where a template of the user's own may show them.
PROPERTIES = {
    "number": "part_number",
    "revision": "revision",
    "drawn_by": "author",
    "company": "organization",
    "description": "description",
    "project": "project",
    "license": "license",
}

# The cells typed over, as '<parameter>: <cell>'. The built-in title blocks read
# these from what a script cannot name -- the title and the material from the
# component a sheet documents, which is named by an id the script never sees,
# and the identification number from a drawing number no command sets -- so the
# text is written over the cell instead. The title is always written: it is the
# object's name unless 'title' says otherwise.
CELLS = {
    "title": "title",
    "number": "identification_number",
    "material": "material",
    "finish": "finish",
}

# The revision row, as '<parameter>: <field>': who drew it, who approved it,
# and when, which the built-in title blocks read from the drawing's latest row.
# A row is added only when one of these is set.
REVISION = {
    "revision": "revision",
    "drawn_by": "designer",
    "approved_by": "checker",
    "date": "date",
}

# How long SolidCraft may take, in seconds, unless 'solidcraftTimeout' says.
DEFAULT_TIMEOUT = 600


def format_of(path):
    """Which file type the output path asks for."""
    extension = os.path.splitext(path)[1].lstrip(".").lower()
    if extension not in FORMATS:
        raise ValueError(
            "SolidCraft draws %s, not '%s' (from %s)" % (", ".join(FORMATS).upper(), extension, os.path.basename(path))
        )
    return extension


def kind_of(request):
    """'part' or 'assembly': which of SolidCraft's templates the object is drawn on."""
    kind = request.get("shape_kind") or "part"
    if kind == "sketch":
        raise ValueError(
            "SolidCraft draws solids; '%s' is a sketch, which PartCAD's own svg and dxf draw as it is"
            % request.get("shape_name")
        )
    # A scene is drawn as what it is made of, which is an assembly's sheet.
    return "assembly" if kind in ("assembly", "scene") else "part"


def template_of(request, kind):
    """The template the drawing starts from: 'template' as given, else the 'page' for this kind."""
    template = request.get("template")
    if template not in (None, ""):
        return str(template)
    page = request.get("page")
    if page in (None, ""):
        page = DEFAULT_PAGES[kind]
    page = str(page).strip().lower()
    if page not in PAGES:
        raise ValueError(
            "'page' is %r; SolidCraft's templates are ISO %s (or name one with 'template')"
            % (request.get("page"), ", ".join(p.upper() for p in PAGES))
        )
    return "iso-%s-%s" % (page, kind)


def scale_of(value):
    """A scale as SolidCraft writes one, "paper:model"; None to let it fit the sheet.

    Taken as draftwright takes it too: a number is the paper's size over the
    model's, so 2 is 2:1 and 0.5 is 1:2. Text with a colon is passed on as it is.
    """
    if value in (None, ""):
        return None
    if isinstance(value, str) and ":" in value:
        return value.strip()
    try:
        factor = float(value)
    except (TypeError, ValueError):
        raise ValueError("'scale' is %r; give a number (2 for 2:1) or 'paper:model' text" % (value,))
    if factor <= 0:
        raise ValueError("'scale' is %r; a scale is above nought" % (value,))
    if factor >= 1:
        return "%g:1" % factor
    return "1:%g" % (1 / factor)


def choice(request, key, allowed):
    """A parameter that is one of a closed list, lower-cased; None when it is not set."""
    value = request.get(key)
    if value in (None, ""):
        return None
    value = str(value).strip().lower()
    if value not in allowed:
        raise ValueError("'%s' is %r; it is one of %s" % (key, request.get(key), ", ".join(allowed)))
    return value


def flag(value, default):
    """A yes-or-no parameter, read the way PartCAD reads its own: "false" from a caller that is not YAML is false."""
    if value is None:
        return default
    if isinstance(value, str):
        return value.strip().lower() in ("true", "yes", "on", "1")
    return bool(value)


def title_of(request):
    """What the drawing is called, which is what its title block says: 'title', else the object's name."""
    return str(request.get("title") or request.get("shape_name") or "Drawing")


def fields(request, names):
    """The parameters of 'names' this request sets, under the names SolidCraft gives them, as text.

    As text because that is what each of them is on the sheet: a 'date' written
    unquoted in YAML arrives as a date, and a 'number' as a number.
    """
    values = {}
    for parameter, name in names.items():
        value = request.get(parameter)
        if value not in (None, ""):
            values[name] = str(value)
    return values


def properties_of(request):
    """The drawing properties this request sets."""
    return fields(request, PROPERTIES)


def cells_of(request):
    """The title block cells this request writes over: always the title."""
    return fields({**request, "title": title_of(request)}, CELLS)


def revision_of(request):
    """The revision row this request adds, or None when it sets none of its fields."""
    return fields(request, REVISION) or None


def script(request, file_format, target):
    """The SolidCraft commands that draw the object and write 'target'."""
    kind = kind_of(request)

    create = {"name": title_of(request), "template": template_of(request, kind), "first_sheet_name": SHEET}
    projection = choice(request, "projection", PROJECTIONS)
    if projection is not None:
        create["projection"] = projection
    for key in ("font", "language", "line_group"):
        if request.get(key) not in (None, ""):
            create[key] = request[key]

    views = {"sheet": SHEET, "iso": flag(request.get("iso"), True)}
    main = choice(request, "view", MAIN_VIEWS)
    if main is not None:
        views["view"] = main
    scale = scale_of(request.get("scale"))
    if scale is not None:
        views["scale"] = scale
    if request.get("gap_mm") not in (None, ""):
        views["gap_mm"] = float(request["gap_mm"])

    commands = [
        {"command": "drawing.create", "params": create},
        {"command": "drawing.view.standard", "params": views},
    ]
    commands.append(
        {"command": "drawing.sheet.set", "params": {"sheet": SHEET, "title_block_overrides": cells_of(request)}}
    )
    properties = properties_of(request)
    if properties:
        commands.append({"command": "drawing.properties.set", "params": {"values": properties}})
    revision = revision_of(request)
    if revision is not None:
        commands.append({"command": "drawing.revision.add", "params": {"set": revision}})

    export = {"sheet": SHEET, "format": file_format, "path": target}
    if file_format == "png":
        for key in ("dpi", "colour", "paper"):
            if request.get(key) not in (None, ""):
                export[key] = request[key]
    commands.append({"command": "drawing.export", "params": export})
    return {"commands": commands}


def results_of(path):
    """What 'solidcraft-cli run --results' wrote: (each step's result, the failing step or None)."""
    try:
        with open(path, encoding="utf-8") as f:
            answer = json.load(f)
    except (OSError, ValueError):
        return [], None
    if isinstance(answer, dict):
        return answer.get("results") or [], answer.get("error")
    return answer if isinstance(answer, list) else [], None


def warnings_of(commands, results):
    """What SolidCraft said about a drawing it still wrote, as lines PartCAD logs against the object."""
    warnings = []
    for step, result in zip(commands, results):
        if not isinstance(result, dict):
            continue
        command = step["command"]
        if command == "drawing.view.standard" and result.get("outside"):
            warnings.append(
                "SolidCraft: at %s, a view leaves the sheet or lies over its title block; "
                "name a larger 'page' or a smaller 'scale'" % ((result.get("scale") or {}).get("text") or "this scale")
            )
        if command == "drawing.export":
            for view in result.get("views") or []:
                if isinstance(view, dict) and view.get("status") not in (None, "drawn"):
                    reason = view.get("reason")
                    warnings.append(
                        "SolidCraft: view %s is %s%s"
                        % (view.get("view"), view.get("status"), ": %s" % reason if reason else "")
                    )
            left_out = (result.get("report") or {}).get("pictures_left_out")
            if left_out:
                warnings.append("SolidCraft: %s shaded picture(s) left out, which a DXF cannot hold" % left_out)
    return warnings


def failure(result, error):
    """Why 'solidcraft-cli run' failed, in its own words: the step that failed, else its last line of output."""
    if isinstance(error, dict) and error.get("error"):
        return "SolidCraft failed at %s: %s" % (error.get("command") or "step %s" % error.get("step"), error["error"])
    detail = (result.stderr or result.stdout or "").strip().splitlines()
    return "SolidCraft failed: %s" % (detail[-1] if detail else "exit %d" % result.returncode)


def safe_name(name):
    """A file name for the STEP file, which SolidCraft names the design's component after."""
    return re.sub(r"[^A-Za-z0-9._-]+", "_", name or "part").strip("._") or "part"


def process(path, request):
    try:
        import export_solidcraft
        import solidcraft_tool

        shape = request.get("wrapped")
        if shape is None:
            raise ValueError("No shape to draw")
        target = os.path.abspath(path)
        file_format = format_of(target)
        commands = script(request, file_format, target)

        warnings = []
        if flag(request.get("reproducible"), False):
            # PartCAD's protocol field, and a promise this package cannot keep for
            # SolidCraft: it has no switch that pins what it writes. Said rather
            # than ignored, so that a drawing kept in a repository is not taken
            # for one that is reproduced byte for byte.
            warnings.append(
                "SolidCraft has no reproducible output to ask for; the file is written as SolidCraft writes it"
            )

        cli = solidcraft_tool.executable(request.get("cache_dir"))
        os.makedirs(os.path.dirname(target), exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="partcad-solidcraft-") as tmp:
            step = os.path.join(tmp, "%s.step" % safe_name(request.get("shape_name")))
            export_solidcraft.write_step(shape, step)
            commands_file = os.path.join(tmp, "drawing.json")
            with open(commands_file, "w", encoding="utf-8") as f:
                json.dump(commands, f)
            results_file = os.path.join(tmp, "results.json")
            result = subprocess.run(
                [
                    cli,
                    "run",
                    commands_file,
                    "--in",
                    step,
                    "--param-check",
                    "strict",
                    "--results",
                    "--json-out",
                    results_file,
                ],
                capture_output=True,
                text=True,
                check=False,
                env=solidcraft_tool.environment(),
                timeout=float(request.get("solidcraftTimeout") or DEFAULT_TIMEOUT),
            )
            results, error = results_of(results_file)

        if result.returncode != 0 or error is not None:
            raise RuntimeError(failure(result, error))
        if not os.path.isfile(target) or os.path.getsize(target) == 0:
            raise RuntimeError("SolidCraft wrote no %s file: %s" % (file_format.upper(), target))
        return {"success": True, "exception": None, "warnings": warnings + warnings_of(commands["commands"], results)}
    except subprocess.TimeoutExpired as e:
        return {
            "success": False,
            "exception": "SolidCraft took longer than %ss to draw it (raise 'solidcraftTimeout')" % e.timeout,
        }
    except Exception as e:
        return {"success": False, "exception": str(e)}
