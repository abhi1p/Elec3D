# Elec3D — FreeCAD 1.1 addon

## Release notes

See [RELEASE_NOTES.md](RELEASE_NOTES.md) for the complete version history.

## Normal workflow

1. Select the **Elec3D** workbench. The custom 2D UI opens as an MDI
   tab (or a separate window if configured).
2. Open the **File** menu in the viewer header: *Import footprints…* for
   individual `.kicad_mod` files, or *Import netlist…* for the four-step KiCad
   schematic/netlist workflow.
3. Pan, zoom, inspect layers and pads, select footprints, and drag them directly
   in the custom scene. With a netlist loaded, the ratsnest follows component moves.
4. Import the board body with *File > Import board solid (STEP)…* or
   *File > Board solid from FreeCAD…*. Every copper layer's routing boundary is
   derived from it automatically (see **Board solid** below).
5. Press **2** for the editable 2D layout and **3** for the document-free 3D board
   preview. *File > Reload selected* reparses the selected footprint from its
   original file.
6. For 3D bodies, point the viewer at your STEP files with *File > 3D models
   folder…*. This works for any import, not just a netlist. A footprint that
   names no model, or names one that cannot be found, can be given a file
   directly with *File > Load 3D model for selection…*.

The in-memory path is:

```text
.kicad_mod/.net -> parser -> in-memory board models -> custom Qt 2D/3D viewer
```

There is no FreeCAD document in that path.

## Exporting STEP / FreeCAD geometry

Normal import and editing remain document-free. Geometry is created only when an
export command is explicitly requested from the viewer's **File** menu.

**Export STEP…** writes a `.step`/`.stp` assembly using FreeCAD's OpenCascade STEP
exporter. The dialog can independently include routed tracks/vias, footprint pads
and placed component STEP models, plus the **Board solid** itself as its own body.
**Fuse touching copper** Boolean-unions overlapping copper; **Export as negative
(cavity)** subtracts the selected content from the imported board solid and
therefore forces fusing. The cavity source is the exact B-Rep of the board solid
(from memory, its STEP file or its FreeCAD object, in that order), placed exactly
like the viewer places it; only when none of those is available is the embedded
mesh sewn into a solid.
**Omit P-curves** controls STEP surface-curve output. STEP serialization uses a
hidden temporary FreeCAD document and does not switch the visible MDI tab. Missing
model files are reported but do not prevent the remaining board geometry from being
exported.

**Export to FreeCAD…** exposes the same geometry choices and creates a separate
FreeCAD document tab directly from the generated B-Reps. The add-on viewer tab
remains open. Repeating the export defaults to updating the linked document in
place: only the managed `Elec3D PCB Export` assembly is replaced, so the same
document/tab, save path, and unrelated user objects are preserved. Uncheck
**Update existing FreeCAD document** to create a new snapshot document instead.
No temporary STEP file is written and re-imported.

## Viewer UI

The sidebar defaults to **Footprints**, **Properties**, **Board solid**, **Routing**,
**Footprint graphics** and **Pads**. All groups are collapsible and
their startup state/order can be customised in Options.

### Properties

Splits into two groups:

- **Basic Properties** — editable footprint controls: the copper design-layer
  dropdown and rotation in degrees. Positive rotation is counter-clockwise.
  Multiple selected footprints can be assigned or rotated together; differing
  rotations display as mixed. Selecting a pad hides these footprint controls.
- **Fields** — collapsible, read-only detail: reference, value, description,
  tags, attributes, pad count, graphics layer count, extent, position,
  3D model and source file. Rows that do not apply are omitted rather
  than shown blank. The group remembers whether you left it open; a pad,
  graphic or text selection forces it open, since there is nothing else to show
  for those.

Selecting several footprints shows the *intersection* of what they have in
common: a row appears only when every selected footprint owns that property,
and when they all own it but the values differ the row stays visible and reads
`— mixed —`. Picking a layer while several are selected moves all of them at
once.

### Board solid

The board shape is imported, not drawn. Use *File > Import board solid (STEP)…*,
*File > Board solid from FreeCAD…* (any Part/PartDesign solid in an open document;
the current 3D-view selection is offered first), or the two buttons in the
**Board solid** sidebar section.

- **Reference plane.** The XY plane through the solid's **lowest point** is
  `z = 0`. The stack-up's **bottom margin** is the height of the bottom copper
  reference plane above it; every further plane is one separation higher. There
  is no top margin — the solid decides where the board ends.
- **Derived outlines.** Each copper layer's routing boundary is the
  cross-section of the solid at that layer's reference-plane height. Steps,
  tapers, pockets, cut-outs and holes are all honoured, layer by layer. Changing
  the stack-up, adding/removing a layer or moving the solid re-derives every
  boundary immediately.
- **Placement.** *up axis* re-orients a solid modelled with another axis up,
  *rotation* turns it about the vertical axis and *offset X/Y* moves it in the
  board plane. **Center** centres its footprint on the editor origin. All
  placement edits are undoable.
- **Layer sections.** The card lists every copper layer with its plane height and
  a ✓ when the plane cuts the solid. A plane that misses the solid is flagged:
  nothing can be placed or routed on it until the stack-up is fixed.
- **Reload** re-reads the STEP file or FreeCAD object (placement is kept);
  **Replace** swaps in a revised solid with the same placement; **Remove** makes
  routing unconstrained again.

In 2D the active layer's boundary is drawn solid with the area outside the board
softly shaded; other visible layers are faint dashed references. The board icon
on the right tool rail toggles that overlay. In 3D the solid itself is rendered
as the translucent substrate with its crease edges, and each reference plane is
marked by its own cross-section.

`resources/samples/filleted_board.step` (50 × 30 × 1.6 mm, R3 corners, two 3 mm
holes and a 0.5 mm pocket in the top face) is a small solid to try this with.

Boards drawn with per-layer outlines in releases before 0.17 are converted into
an equivalent solid when opened, so their routing boundaries do not change.

### Design layers

Copper layers are listed in the bottom layer bar, top plane last. Every footprint
sits on exactly one copper plane.

- clicking a layer makes it active — new imports land there and footprints on
  other copper planes are dimmed
- **+** stacks a plane on top and relabels the stack the way KiCad does: `B.Cu`
  at the bottom, `F.Cu` at the top, `In1.Cu`… in between. A plane you rename
  keeps its name.
- *Edit > Stackup…* edits copper thicknesses, separations and the bottom margin,
  with a live preview of every plane's height and whether it cuts the solid.

A board always keeps at least one copper plane, so the last copper plane cannot
be removed.

### Selection

The funnel button on the right-hand 2D tool rail opens the **Selection Filter**.
Footprints, Pads, Tracks and Vias can be enabled independently; filtering
changes only what can be picked/rubber-band selected and does not hide geometry.
**Ctrl+click** toggles objects into/out of a mixed multi-selection. Ctrl+clicking a
pad promotes its owning footprint when footprint selection is enabled.

Pad picking still outranks the owning footprint when both filters are enabled:

- clicking a pad selects/highlights the pad for fine-grained Properties readout
- clicking elsewhere inside a footprint selects the footprint
- clicking a routed track selects the segment under the cursor; clicking a via selects the via
- footprint graphics/text remain visual reference and select their owning footprint rather than becoming independent objects
- clicking a row in the **Pads** list selects that pad on the canvas

Dragging empty canvas creates a rubber-band selection containing only object classes
currently admitted by the Selection Filter. Ctrl+A likewise selects the eligible
scene objects. Selected footprints remain highlighted in the **Footprints** list.

Clicking empty canvas, or pressing Escape, deselects: Properties returns to
**No selection** and no row stays highlighted in the Footprints list.

The rest of the UI provides:

- footprint list
- graphics/layer visibility, with copper swatches matching the design tint
- pads list
- grid and snap controls
- hidden-text control
- zoom/pan/fit
- keyboard editing: Ctrl+Z/Ctrl+Y undo/redo, R/Shift+R rotate, Delete removes the selected track segment/object, and U promotes routed copper to complete-route selection
- a **File** menu in the viewer header for project create/open/save/close, import, reload, 3D-model and export actions
- an **Edit** menu for undo/redo, Design Rules, Stackup and Preferences, with Preferences kept last
- a **Tools → DRC Check…** command for an explicit whole-board design-rule report
- always-visible add-on version number in the viewer header
- automatic light/dark appearance matching the current FreeCAD theme

Routing is integrated directly into the 2D editor on copper design layers. Tracks,
vias, net-aware snapping and the copper-aware ratsnest use the same in-memory board
state as the 3D preview; no FreeCAD document is created for routing.

### Interactive routing and copper editing

While the **Route** tool is active, the live route follows H/V/45 geometry and
snaps magnetically to pads, vias and track centrelines:

| Input | Action |
| --- | --- |
| `V` | arm/cancel a via at the route head |
| `/` | switch the 45-degree posture |
| `Shift` | suspend object snapping |
| `Ctrl` | suspend grid snapping |
| `Backspace` | remove the last committed vertex |
| `Enter` / double-click | finish the route |
| `Esc` | cancel the via, then the route, then the tool |

When copper of a different net blocks the path, the router **walks around it**
using 45-degree geometry. If no legal detour exists the route stops at its last
legal point instead of showing invalid copper; the pointer stays free so the
route can be steered manually.

With the **Select** tool, routed copper is edited by direct dragging:

| Input | Action |
| --- | --- |
| drag a track vertex | corner drag |
| drag mid-span | segment drag; neighbours stretch, anchored ends stay put |
| drag a via | connected track ends follow it |
| `Alt` while dragging | free the angle for this drag |
| `U` | promote the selection to the whole connected copper island |
| `M` | rigid translation of the current selection |

Drags hold H/V/45 angles by default, are checked against the design rules
continuously, use the last legal geometry as a hard barrier, and commit as a
single undoable operation. Both the 45-degree drag and walk-around routing can
be turned off under **Edit → Preferences… → Interactive routing**.

**Tools → DRC Check…** performs a non-destructive full-board pass with those same
rules and lists every unique copper, pad, courtyard and board-edge clearance
violation in a results dialog.

The workbench does not add a separate toolbar.

## Projects, board files and File > Open / Import

Elec3D projects use a normal directory layout rather than a packed archive:

```text
<ProjectName>/
  <ProjectName>.e3d_pro
  <ProjectName>.e3d_pcb
  Footprints/
    ... project-local .kicad_mod copies ...
  Models/
    ... referenced .step/.stp/.wrl copies ...
```

**Create a new project…** asks for the `.e3d_pro` name and always creates the matching project folder automatically. The project file is lightweight metadata pointing at the board and asset folders. The `.e3d_pcb` is the complete authoritative design: placements/rotations, full embedded footprint source snapshots, routing/vias, net connectivity, the board solid (embedded mesh + placement), design layers/stackup, view state, resource references, 3D-model transforms and model-offset corrections are all stored there.

Project boards use portable path variables:

```text
(paths
  (var "DESIGN_DIR" ".")
  (var "FOOTPRINT_DIR" "${DESIGN_DIR}/Footprints")
  (var "MODEL_DIR" "${DESIGN_DIR}/Models")
)
```

The embedded footprint snapshot in `.e3d_pcb` is the source of truth for restoring the saved layout. `Footprints/` is a project-local editable/reference copy and the original KiCad library path is retained for compatibility checking. On open, the add-on compares both available copies with the stored fingerprint. A changed file reports a **mismatch** warning; a missing file reports a **not found** warning; neither prevents the exact saved footprint from being displayed.

Resolvable component STEP/STP/WRL files are copied into `Models/`, and so is the
board solid's STEP file (a solid taken from FreeCAD is written there as STEP). The model file itself is not embedded in `.e3d_pcb`; its project/original paths, fingerprint, placement transform and Auto/Manual X/Y/Z correction are stored. Missing models are non-fatal and are reported as warnings.

**Open…** accepts `.e3d_pro` and `.e3d_pcb`. Opening a standalone `.e3d_pcb` restores it directly and then offers to create a project around it. **Save** updates the current project; if the current work came from a netlist, imported footprints, a cleared board or a standalone board, Save prompts to create a project. **Save As…** writes the current design into a different named project folder/path. **Close Project** prompts to Save / Discard / Cancel when needed, then closes the current `.e3d_pro` and returns to a clean empty editor without removing it from Open Recent.

`.e3d_pro`, `.e3d_pcb`, and `.kicad_mod` are registered with FreeCAD's file dialog and open in the custom document-free viewer. The viewer creates a FreeCAD `App::Document` only for the explicit STEP/FreeCAD export commands.

## Viewer hosting

By default the viewer is an ordinary Qt widget hosted in FreeCAD's MDI area.
Because it is not a FreeCAD document view, it can exist without an active
`App::Document`.

The Model/Tasks panels can be hidden while the custom tab is active to maximize
canvas space. Existing FreeCAD document tabs are no longer hidden or treated as
a paired view of the footprint session.

Lifecycle:

- Selecting the **Elec3D** workbench (or *Elec3D → Elec3D viewer*) opens the
  editor as a tab or a separate window, per *Preferences*.
- Once open it runs independently of FreeCAD's workflow: switching to Part
  Design or any other workbench, or between documents, never closes, hides or
  re-opens it.
- The workbench follows the editor: entering the Elec3D tab (or focusing the
  Elec3D window in Separate Window mode) activates the Elec3D workbench, and
  going back to a FreeCAD document (or closing the editor) returns to the
  workbench you were using before.
- Closing the tab or window ends the session. Unsaved changes ask Save /
  Discard / Cancel first (Cancel keeps it open); otherwise the editor and its
  whole in-memory design are released. Opening it again starts empty — reopen
  your project from *File → Open Recent*.
- Quitting FreeCAD asks the same question and closes the editor with it.

## Preferences

Viewer preferences:

- open viewer when the workbench is selected
- MDI tab or separate window (applied immediately)
- hide side panels while the MDI viewer tab is active (only while Elec3D is the
  active workbench; the tab itself stays open when you switch to Part Design or
  any other workbench)

The viewer has no independent colour-palette setting or Light/Dark button. Its
chrome and canvas palette follow FreeCAD automatically.

The old **Open the 2D viewer on import** and **Hide duplicate FreeCAD document
tab** options are obsolete and have been removed.

## Architecture

```text
elec3d/sexpr.py       S-expression parser
elec3d/footprint.py   pure Python footprint model + parser
elec3d/design.py      pure Python copper stack (design layers), no Qt
elec3d/boardsolid.py  board solid: welded mesh, placement, per-layer slicing, loaders
elec3d/meshgeom.py    pure Python polygon helpers (triangulation, loops, legacy outlines)
elec3d/designfile.py  versioned internal .e3d_pcb S-expression board state
elec3d/projectfile.py  .e3d_pro project metadata, paths and asset helpers
elec3d/export3d.py    STEP + direct FreeCAD exact-BRep export pipeline
elec3d/layers.py      KiCad layer colours and painting order
elec3d/view2d.py      QGraphicsScene/QGraphicsView rendering and interaction
elec3d/panel2d.py     complete custom viewer UI
elec3d/viewer.py      FreeCAD host for the custom widget + explicit export bridge
elec3d/commands.py    workbench commands; normal import is document-free
elec3d/builder.py     reusable footprint/pad OpenCascade geometry helpers
elec3d/interaction2d.py optional Coin3D interaction after conversion
elec3d_import.py     File > Open/Import bridge to the custom viewer
```

## Installation

Extract the `Elec3D` directory into the FreeCAD user Mod directory and
restart FreeCAD. The package targets FreeCAD **1.1.x**.

## Tests

The addon now includes `requirements-test.txt` for a standalone development/test
environment. It intentionally contains PySide6 only for external GUI testing;
it is **not** an addon runtime dependency and should not be installed into
FreeCAD's bundled Python environment.

Create a normal Python virtual environment and install the test dependency:

```bash
python -m venv .venv
python -m pip install -r requirements-test.txt
```

Parser and architecture tests can run without Qt, including the design-layer
stack tests in `tests/test_design_layers.py` — `elec3d/design.py` holds no Qt
objects precisely so that the copper stack can be verified without a display.
With PySide6 installed, the Qt viewer tests (`tests/test_view2d.py`,
`tests/test_panel_sections.py`) and the FreeCAD-host-stub tests run as well:

```bash
QT_QPA_PLATFORM=offscreen python -m unittest discover -s tests -v
```

On Windows Command Prompt, set the offscreen platform first:

```bat
set QT_QPA_PLATFORM=offscreen
python -m unittest discover -s tests -v
```

The Qt tests use small FreeCAD/FreeCADGui stubs where possible, so most custom
viewer behavior can be exercised without a FreeCAD installation. Tests that
need the real FreeCAD runtime should still be run from FreeCAD's Python
environment.
