# SolidCraft for PartCAD

A [PartCAD](https://github.com/partcad/partcad) plugin package for
[SolidCraft](https://github.com/tangency-labs/solidcraft), the open-source parametric CAD application written in Rust.
It adds what SolidCraft reads and PartCAD does not, and what SolidCraft draws:

| What | Declared as | Reads / writes |
|---|---|---|
| A SolidCraft design (`.solidcraft`, or its plain JSON form) | `type: solidcraft:solidcraft` | read, with the part's parameters set on the design |
| A Rhino file (`.3dm`), its solids | `type: solidcraft:rhino` | read |
| An IGES file (`.igs`, `.iges`), its solids and sewn surfaces | `type: solidcraft:iges` | read |
| A SolidCraft design holding any PartCAD object | `pc export -t solidcraft:solidcraft` | write |
| A technical drawing of any part or assembly, by SolidCraft: SVG, PDF, DXF or PNG | `pc render -t pdf -e //pub/feature/cad/solidcraft` | write |
| SolidCraft itself, to view and edit an object in | `pc ide open --with solidcraft`, or *Open in SolidCraft* in the PartCAD extension for VS Code | — |

`solidcraft:` is the name the workspace imports this package under; use whatever name the dependency is given.

It needs PartCAD 0.8.228 or newer, the first whose partTypes can read a file -- the `extension:` of a
`partType` -- which is what hands a script the part's file and this package's cache directory. `partcad.yaml` says
so, and an older PartCAD refuses the package rather than failing every part.

## Using it

```yaml
dependencies:
  solidcraft:
    type: git
    url: https://github.com/partcad/partcad-solidcraft
    track: main

parts:
  plate:
    type: solidcraft:solidcraft   # reads plate.solidcraft
    parameters:
      height:                     # the design's user parameter of the same name
        type: float
        default: 20
  slab:
    type: solidcraft:rhino        # reads slab.3dm
  bracket:
    type: solidcraft:iges
    path: vendor/bracket.iges     # relative to this package, as for any file-backed part
```

It is also in PartCAD's public index, as `//pub/feature/cad/solidcraft`, which needs no dependency of its own:

```yaml
parts:
  plate:
    type: //pub/feature/cad/solidcraft:solidcraft
```

Each part is one file, declared exactly like a STEP or an STL part: `path` relative to the package declaring the
part, `<name>.<extension>` when it is left out, and `fileFrom`/`fileUrl` to fetch it. The file is what the part's
cache entry depends on, so editing it rebuilds the part.

A SolidCraft design is parametric. Every parameter the part declares is set on the design's user parameter of the
same name before it is rebuilt, so `plate;height=35` is the plate at 35 mm.

What PartCAD gets from any of the three is the solid SolidCraft writes out as STEP: a design's history stays in the
design.

## Drawings

SolidCraft has a drawing workspace of its own, and this package's `render:` section puts it behind PartCAD's
`pc render`: the object is opened in SolidCraft, and SolidCraft lays out its front, top and right views in the
drawing's projection and an isometric one, on an ISO sheet with its border and title block, and writes the sheet as
SVG, PDF, DXF or PNG ([`render_drawing.py`](render_drawing.py)).

```shell
pc render -t pdf -e //pub/feature/cad/solidcraft //your/package:bracket
pc render -t svg -e //pub/feature/cad/solidcraft //your/package:bracket
```

`-e` reads the file type from this package rather than from PartCAD's own `svg`, `dxf` and `png`, which project
the shape flat; nothing changes in the package being drawn, and a workspace importing this package for its
partTypes keeps PartCAD's own file types. In the PartCAD extension for VS Code, the **Draft** tab offers it in
**Drawn by**, beside `//pub/feature/render/draftwright`.

What the title block says, the sheet and the views are parameters of the file type, set for a whole package or for
one object, and the ones [draftwright](https://github.com/partcad/partcad-render-draftwright) also takes mean the
same here -- so a package that configures its drawings once gets the same title block from either:

```yaml
render:
  pdf:
    title: Mounting Plate   # Title; default: the object's name
    number: PC-EX-0001      # Identification number
    material: AL 6061-T6    # Material
    drawn_by: A. Engineer   # Created by
    date: "2026-08-09"      # Date of issue
    company: ACME Corp.     # Legal owner
    revision: B
    page: A3                # ISO A0 to A4; default A3 for a part, A2 for an assembly
    scale: 2                # 2:1, or "paper:model" text; default: fitted to the sheet
    projection: first       # default: third
```

`partcad.yaml` lists the rest, which are SolidCraft's own (`approved_by`, `finish`, `template`, `view`, `iso`,
`line_group`, `font`, ...).

Two things it does not do. It places **no dimensions**: SolidCraft brings a design's own dimensions into its
drawings, but what PartCAD hands it is a solid, with no sketch or feature behind it to have them -- for dimensions,
draw with draftwright, or open the object in SolidCraft and dimension it there. And it is **not reproducible** byte for
byte: SolidCraft has no switch that pins what it writes, so `reproducible: true` is answered with a warning rather
than a promise. A sketch is refused; PartCAD's own `svg` and `dxf` draw one as it is.

SolidCraft checks every command's parameters against its contract (`--param-check strict`), so a parameter that
does not reach it as SolidCraft spells it fails the render, naming it, rather than being dropped.

## Where SolidCraft comes from

Everything here runs SolidCraft's command line tool, `solidcraft-cli`. Whichever comes first is used:

1. **A `solidcraft-cli` on the `PATH`.** A machine that installed SolidCraft, or this package's image.
2. **This package's image**, `ghcr.io/partcad/partcad-solidcraft:<tag>`, named as the package's `dockerImage`.
   PartCAD runs the scripts in it where its `docker` sandbox is in use and the image can be pulled, and falls back to
   its own sandbox, with a note, where it cannot.
3. **A download on first use.** The release pinned in [`solidcraft_tool.py`](solidcraft_tool.py), verified against
   its published SHA-256 before it is unpacked, kept in this package's cache directory -- `.cache/` beside this file,
   which in a workspace is PartCAD's cached copy of this package -- and reused from then on. A new release of the
   package is a new directory and a new download.

`pc ide open --with solidcraft` finds an installed SolidCraft (the `PATH`, `SolidCraft.app`, Windows' program
directories, the `io.tangencylabs.solidcraft` Flatpak) and otherwise runs the same image, pulled on demand, with the
display forwarded into it.

Editing in SolidCraft: SolidCraft is offered a SolidCraft design first, then STEP and IGES, then meshes; anything
else is converted to STEP on the way. glTF is deliberately not on the list, because SolidCraft cannot write it back.

## The image

[`Dockerfile`](Dockerfile) builds it `FROM` PartCAD's Python sandbox image, adding SolidCraft and the libraries its
application opens at run time; [`verify_image.py`](verify_image.py) runs at build time and refuses an image whose
SolidCraft cannot build a part. [`.github/workflows/image.yml`](.github/workflows/image.yml) builds it for `amd64`
and `arm64` on every pull request and publishes it from `main`, tagged with a hash of what it is built from
([`image-tag.sh`](image-tag.sh)) rather than a version. The tag is immutable, and `partcad.yaml` pins it twice --
as the package's `dockerImage` and as the `open:` entry's container -- which the workflow checks before it builds.

After changing the `Dockerfile` or `verify_image.py`, run `./image-tag.sh` and put what it prints into both pins in
the same commit.

## Tests

[`tests/`](tests) is a PartCAD package of its own: a workspace that imports this plugin through a `local`
dependency and declares one part per partType. The empty `.pcignore` in it keeps it out of this package's tree, so
it is never listed as the plugin's subpackage wherever the plugin is used.

```shell
pip install pytest "partcad>=0.8.228"
pytest                 # everything; the first part built downloads SolidCraft
pytest -m "not slow"   # the scripts' own logic, and the configuration as PartCAD reads it
```

Without PartCAD installed, the tests that need it skip themselves, which is what
[`.github/workflows/test.yml`](.github/workflows/test.yml) runs first; its second job installs PartCAD and builds
the parts.

## Updating SolidCraft

Change `VERSION` and every checksum in `solidcraft_tool.py` (from the release's `SHA256SUMS.txt`), the version and
both Linux checksums in the `Dockerfile` (a test holds the two to each other), then run `./image-tag.sh` and update
both pins in `partcad.yaml`. Raise `CACHE_VERSION` in `read_part.py`, `export_solidcraft.py` and `render_drawing.py`
if what they produce changes.

## Licences

This package is Apache-2.0. SolidCraft is MIT OR Apache-2.0, and is downloaded from its own releases rather than
redistributed here; the image carries a copy of it, licences included.
