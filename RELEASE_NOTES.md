# Release Notes

Release history for **Elec3D — FreeCAD 1.1 addon**.

Newest releases are listed first.

## 0.19.0

- **Show components as bounding boxes.** A new **Boxes** button in the 3D
  toolbar switches every component between its 3D model and its bounding box;
  *3D model → show as* in the sidebar switches a single component. The box is
  aligned with the footprint, so it rotates with the part. Undoable and saved
  with the project.
- **Exports follow that choice.** STEP and FreeCAD exports write a solid box for
  components shown as boxes and the exact model for the rest; the export dialog
  shows how many models go out as boxes.
- **Several exports in one FreeCAD tab.** *Export to FreeCAD* now asks where to
  put the export: replace the previous export in place, **add it as a new part
  to any open document** (keeping what is already there), or create a new
  document. This replaces the *Update existing FreeCAD document* checkbox.

## 0.18.2

- **Fixed: the workbench did not always follow the Elec3D tab.** Switching
  reacted to every transient tab activation FreeCAD/Qt emits while focus moves,
  and those could undo the switch. The workbench is now paired with the tab
  that has settled in front.
- **Tab and workbench stay paired both ways.** Choosing another workbench while
  the Elec3D tab is in front brings your last FreeCAD document back to the
  front, so a FreeCAD workbench is never left driving the Elec3D tab.
- **Separate Window mode is no longer coupled to workbenches** (reverts the
  0.18.1 focus-following): the window runs alongside whatever workbench you use.

## 0.18.1

- **The workbench now also follows the editor in Separate Window mode.**
  Focusing the Elec3D window activates the Elec3D workbench; going back to
  FreeCAD's main window returns to the previous workbench. Message boxes and
  file dialogs do not trigger a switch. Changing between Tab and Separate
  Window keeps this behaviour.
- **The origin is centred in the 2D view on launch.** With nothing loaded, the
  view centres on (0, 0) and stays centred while the tab or window settles into
  its size (until you pan or zoom). *Fit* on an empty board does the same.

## 0.18.0

- **In tab mode the workbench follows the Elec3D tab.** Clicking the Elec3D tab
  from a FreeCAD document switches FreeCAD to the Elec3D workbench; going back
  to a FreeCAD document (or closing the Elec3D tab) returns to the workbench
  that was in use before. Choosing another workbench by hand while on the
  Elec3D tab is respected. The Separate Window mode is unaffected.
- **Less overhead on FreeCAD while Elec3D is not in view.** The editor's
  keyboard-shortcut hook now only watches FreeCAD's events while the editor is
  visible; a hidden tab or closed editor costs nothing.

## 0.17.2

- **Closing the Elec3D tab or window now really closes it.** Unsaved changes ask
  Save / Discard / Cancel (Cancel keeps it open); then the editor, its timers,
  its application-wide keyboard hook and the whole in-memory design are
  released. Previously the editor was only hidden and stayed in memory.
- **Fixed: a closed Separate Window reappeared when another workbench was
  selected.** Workbench switches no longer touch the editor at all — it is
  neither closed, hidden nor re-shown; only the Model/Tasks panels are handed
  back to the new workbench.
- Quitting FreeCAD tears the editor down after the save prompt, so a separate
  window can no longer outlive the main window.
- Switching between Tab and Separate Window shuts the replaced editor down
  instead of leaving its timers running in the background.

## 0.17.1

- **The Elec3D tab no longer closes when another workbench is selected.** Like
  the Separate Window, it stays open beside your FreeCAD documents; Model/Tasks
  panels are only hidden while Elec3D is the active workbench.

## 0.17.0

- **The add-on is now called Elec3D.** The workbench, menus, Python package
  (`elec3d`), commands (`Elec3D_*`), icons and preferences all use the new name.
  Projects are saved as `.e3d_pro` / `.e3d_pcb`; files from earlier releases are
  not opened by this version. Install by placing the `Elec3D` folder in FreeCAD's
  `Mod` directory (remove the previous add-on folder first).
- **The board shape is now an imported solid instead of per-layer drawn outlines.**
  *File → Import board solid (STEP)…* reads a STEP solid through FreeCAD's Part
  module (no document is created); *File → Board solid from FreeCAD…* uses any
  solid that is open in FreeCAD, offering the current 3D-view selection first.
- **Every copper layer's outline is derived automatically** as the cross-section
  of the solid at that layer's reference-plane height. Tapers, steps, pockets,
  cut-outs and holes are honoured layer by layer, and the boundaries update
  immediately when the stack-up, the layer count or the solid's placement change.
  Routing, via placement, footprint moves and *Tools → DRC Check…* use these
  derived boundaries; a plane that misses the solid blocks placement on it.
- **The stack-up is anchored to the solid's lowest point.** The XY plane through
  the bottom-most point is `z = 0`, the bottom margin is measured from it, and the
  **top margin has been removed** — the solid decides where the board ends.
  *Edit → Stackup…* now shows each plane's live height and whether it cuts the
  solid while values are being edited.
- **2D outline drawing has been removed**: the Line/Rectangle/Circle/Ellipse
  tools, outline selection/handles, the Outlines selection-filter entry and the
  outline property editor are gone. The active layer's derived boundary is drawn
  read-only with the area outside the board softly shaded; other visible layers
  appear as faint dashed references. A new board button on the tool rail toggles
  that overlay, and a Fit button joins it.
- New **Board solid** sidebar section: source and size summary, *up axis*,
  rotation and X/Y offset placement, Center / Reload / Replace / Remove, and a
  per-layer table of plane heights with ✓/⚠ section status. Placement changes,
  imports and removal are all undoable.
- **3D view** renders the imported solid itself as the translucent substrate with
  its crease edges, marks every reference plane with its own section (the active
  layer highlighted) and reports layers that fall outside the solid.
- **Export**: the cavity (negative) export subtracts from the exact board-solid
  B-Rep; a new *Board solid* option adds the board body to STEP/FreeCAD
  assemblies.
- **Projects** embed the solid's tessellated mesh (compressed) in `.e3d_pcb`, so
  boundaries always restore, and copy its STEP source into `Models/` (a solid
  taken from FreeCAD is written there as STEP). Design format version 5.
- **Old projects keep working**: per-layer outlines from earlier releases are
  converted into an equivalent lofted solid on open, preserving their routing
  boundaries and their old top margin.
- Faster S-expression string parsing (embedded footprint sources and mesh data
  are no longer tokenised one character at a time).

## 0.16.5

- Moves **Preferences…** to the final position in the **Edit** menu, after Design Rules and Stackup.
- Adds **File → Close Project**. Closing a project uses the existing Save / Discard / Cancel protection for unsaved changes, then returns the viewer to a clean empty board while keeping the recent-project list intact. The command is disabled when no `.e3d_pro` project is open.
- Adds a **Tools** menu with **DRC Check…** for an explicit whole-board design-rule check.
- The DRC scan uses the same configured clearance values and copper geometry predicates as interactive routing, then reports all unique track↔track, track↔via, track↔pad, via↔via, via↔pad, pad↔pad, courtyard↔courtyard and Outline-clearance violations in one results table instead of stopping at the first violation.
- DRC Check is non-destructive and does not mark the project modified.

## 0.16.4

- Fixes reconnecting a gap created by **segment-level Delete**: when a new route starts/ends on compatible same-layer Track endpoints, the routed bridge is welded back into the existing Track objects instead of remaining a separate overlapping/touching object.
- Re-normalizes the welded geometry, so repairing a deleted straight section restores one continuous logical segment and avoids later drag/selection artifacts at the repair points.
- Keeps true **T-junctions** separate: routing into the middle of an existing track is still represented as a branch rather than forcing branched copper into one polyline.
- Keeps width changes and different-net contacts as separate Track objects; endpoint welding only occurs when layer, width and net compatibility allow it.
- Applies the same start-endpoint welding when a route from an existing track terminates at a newly placed via.

## 0.16.3

- Normalizes routed Track geometry so consecutive **same-direction collinear pieces are one logical straight segment**. Redundant routing-click vertices are collapsed when tracks are created/restored and after interactive copper edits, preventing a long straight run from being selected or dragged as several artificial sub-segments.
- Keeps true 180-degree reversals as real routing turns, so canonicalization does not erase intentional backtracking.
- Changes **Delete** to follow segment-level selection: a normally clicked track segment deletes only that segment. Deleting a middle segment splits the surviving copper into two disconnected Track objects; deleting an end segment trims that end.
- Keeps **U** as the explicit complete-route operation. `U` promotes the connected copper island to whole-track selection, after which Delete removes that complete selected route as before.
- Preserves layer, width and net metadata on the surviving pieces and keeps the operation fully Undo/Redo aware.

## 0.16.2

- Fixes false **via-to-outline DRC blocks** during interactive via dragging and physical via moves on boards that define closed outlines on more than one copper reference layer.
- Via outline containment is now checked **independently for every copper layer spanned by the via**. Per-layer outlines are no longer merged into one even/odd polygon set, where coincident or nested boundaries could cancel and incorrectly classify an interior via as outside the board.
- Applies the same per-layer outline validation to **new via placement while routing**, preventing the same false block when inserting a via through multiple outlined layers.
- Keeps the configured outline-to-via clearance active: a via is still blocked when its physical diameter plus clearance actually reaches the boundary of any layer it spans.

## 0.16.1

- Corrects the v0.16.0 release-note description of via dragging. Interactive via dragging, connected-track following, H/V/45 preservation, Alt free-angle dragging, DRC barriers and one-step Undo were already present in v0.15.0.
- Clarifies that v0.16.0 only hardened the **first click-drag startup path** for routed copper by explicitly selecting the clicked track/via before entering the existing drag engine, avoiding dependence on Qt scene-selection ordering.
- No routing or via-dragging behavior is newly introduced by this documentation correction.

## 0.16.0

- Uses the uploaded v0.15.0 routing-engine work as the base and preserves its real-time obstacle indexing, 45-degree copper dragging and walk-around routing.
- Makes **negative/cavity STEP and FreeCAD export match the 3D board shape**: every copper layer Outline is used as a reference cross-section, missing intermediate boundaries are interpolated, and the export block is lofted through the stackup exactly like the document-free 3D preview instead of extruding one fused XY outline.
- Hardens the **first click-drag startup for tracks and vias**: routed copper is explicitly selected before entering the existing v0.15.0 interactive drag engine, so the first drag no longer depends on Qt selecting the item early enough in its mouse-press processing. The underlying via-drag geometry, connected-track following, H/V/45 behavior, Alt free-angle mode, DRC handling and Undo semantics are unchanged from v0.15.0.
- Adds a KiCad-style **2D Selection Filter** funnel on the right tool rail. Footprints, Pads, Tracks, Vias and Outlines can be independently admitted to or excluded from picking/rubber-band selection without hiding them.
- Makes **Ctrl+click multi-selection explicit and reliable across Qt versions**, including mixed footprint/track/via/outline selections. Ctrl+clicking a pad promotes/toggles its owning footprint when footprint selection is enabled.
- STEP export now serializes through a **hidden temporary FreeCAD document**, so exporting STEP no longer activates a transient MDI tab, jumps to a previously created FreeCAD export document, or visibly flashes a temporary tab before closing it.

## 0.15.0

- Is a routing-engine performance and KiCad-parity release.
- **Interactive routing and copper dragging are now real-time on large boards.**
  A uniform-grid broad phase, bounding-box rejection in every clearance
  predicate, a shared obstacle index reused across the many probes of one mouse
  move, a cached board outline and an interaction-scoped pad cache replace the
  former all-pairs geometry sweeps.
- Measured on the reference design tiled to 288 tracks / 120 vias: one routing
  mouse move fell from 400 ms to 13 ms (2.5 → 76 fps) and one copper-drag mouse
  move from 84 ms to 3.0 ms. Engine-level, `build_copper_topology` fell from
  2023 ms to 8 ms and `ratsnest_segments` from 3788 ms to 43 ms at 96 tracks;
  the interactive clip search is now flat with board size instead of linear.
- Live airwire updates during a drag are coalesced on a short timer, with a
  trailing refresh so the final geometry is always drawn.
- **Dragging copper now holds H/V/45 geometry** the way KiCad's dragger does:
  neighbouring segments keep their directions and the shared joints slide along
  them instead of drifting to arbitrary angles. Hold **Alt** during a drag for a
  free angle.
- **Track corners can be dragged directly.** Grabbing a vertex starts a corner
  drag; grabbing mid-span still starts a segment drag.
- Dragging a via rebuilds its attached track stubs on the 45-degree grid rather
  than leaving them skewed.
- **Interactive routing now walks around obstacles** instead of only stopping at
  them. When no legal detour exists the previous hard-barrier behaviour still
  applies, so the route stops at its last legal point rather than showing
  invalid copper.
- New **Edit → Preferences… → Interactive routing** options control the
  45-degree drag and walk-around behaviours; both default to on.
- Fixes a bug in the ratsnest where each net's pad grouping was built twice, the
  first result being discarded, and where the minimum spanning tree recomputed
  every group-pair distance on each iteration.

## 0.14.3

- Fixes copper selection/highlight colours in the 2D editor.
- A selected track segment now keeps the owning copper layer hue and uses a brighter shade for emphasis instead of switching to the generic blue accent.
- Selected vias likewise retain their reference copper-layer hue.
- Selected-pad outlines now derive from the pad's actual copper layer, with the existing canvas halo retained for contrast.
- Selection styling therefore no longer makes F.Cu/In*.Cu copper appear to belong to B.Cu.

## 0.14.2

- Moved the complete release history out of `README.md` into this dedicated `RELEASE_NOTES.md` file.
- Converted the historical release notes to a bullet-based format for easier scanning.
- Kept `README.md` focused on installation, workflow, UI, project files, architecture, and testing.

## 0.14.1

- Corrects copper editing semantics.
- `M` is now a physical translation tool: it rigidly moves the selected footprint/track/via objects and does not pull neighbouring copper.
- Direct mouse dragging is the connectivity-preserving edit: clicking a routed track selects the individual segment under the pointer and dragging that segment stretches adjacent geometry while retaining anchored ends; dragging a via carries exact connected track vertices with it.
- Press **U** on a selected track segment or via to promote the selection to the complete connected routed-copper island, after which `M` can translate that complete selection.
- Direct copper dragging remains grid-aware, DRC-checked, undoable as one operation, and uses the last legal geometry as a hard barrier when a drag would violate a rule.

## 0.14.0

- Introduces explicit KiCad-style movement and layer-navigation controls.
- **Preferences…** has moved from File to the Edit menu.
- `M` starts an interactive move for the current footprint/track/via selection; footprints retain direct click-drag movement, while tracks and vias are selectable but cannot be physically dragged until `M` is pressed.
- A selected track moves the segment nearest the cursor, stretching adjacent geometry and preserving anchored outer endpoints with doglegs; moving a via carries exact connected track vertices so routed connections remain attached.
- The M operation is snapshot-based, DRC-checked and committed as one undoable transform.
- `+` / `-` now walk adjacent copper reference layers (bottom-to-top / top-to-bottom), while Page Up selects the top copper layer and Page Down selects the bottom copper layer.
- These layer keys replace the former keyboard +/- zoom shortcuts; mouse-wheel zoom remains unchanged.

## 0.13.3

- Changes the bottom copper-layer selector to physical bottom-to-top order: `B.Cu` is always the leftmost layer, inner layers follow it, and the current top `F.Cu` is on the right.
- New copper layers are added above the current top plane and therefore appear immediately on the right side of the layer bar, just before the `+` button.
- Stackup physical ordering and saved layer ids are unchanged.

## 0.13.2

- Fixes a v0.13 status-notification regression.
- The Stackup and Design Rules dialogs, plus DRC rejection paths, could call the legacy `_flash_status()` helper after successfully applying their changes even though the helper had been removed during the bottom-status-bar refactor.
- The compatibility helper is restored and delegates to the current bottom status notifier, so updating Stackup/Design Rules and DRC feedback no longer raises `AttributeError`.

## 0.13.1

- Refines the per-layer Outline/stackup model introduced in v0.13.0.
- The Stackup dialog now shows each copper reference layer's colour beside its thickness row.
- Board Outline geometry uses a dedicated theme-aware orange instead of inheriting the owning copper colour, making mechanical boundaries visually distinct in both 2D and 3D.
- The 3D substrate is now built from every closed per-copper-layer Outline as a reference cross-section: bottom/top margins follow the nearest layer boundary and the material between adjacent copper planes is lofted from one layer outline to the next.
- Layers without a closed outline infer an intermediate/nearest boundary so partial and legacy designs remain renderable.

## 0.13.0

- Is a major PCB-editor UI and design-rules update.
- Copper design layers now live in an Altium-style selectable bar below the 2D canvas, with an inline **+** command for adding layers and a right-click menu for visibility/removal; the object-selection count has moved into the bottom status area.
- **Stackup…** and the new **Design Rules…** editor are available from the header **Edit** menu instead of occupying sidebar groups.
- Design rules are saved in `.e3d_pcb` and provide edge-to-edge track/track, track/via, track/pad, via/via, via/pad, pad/pad, courtyard/courtyard and outline clearances.
- Interactive routing uses those clearances in the existing hard-barrier router, while component/outline edits are rejected when they violate courtyard, pad or outline rules.
- The standalone editor **Outline** design layer has been removed: every outline primitive is owned by the copper layer that was active when it was drawn, so selecting F.Cu/B.Cu/In*.Cu also selects the outline context for that layer.
- Legacy v0.12 boards with Outline-layer geometry are migrated onto the active/top copper layer when opened.

## 0.12.10

- Keeps interactive routing inside a usable closed **Outline**.
- The existing hard-barrier router now treats the physical board boundary like another routing obstacle: the live route head stops at the furthest legal point inside the board while the pointer remains free to move, and clicking commits only that visible legal segment.
- Track-width clearance is respected so the full copper envelope, not only its centreline, remains inside the outline; vias must likewise fit completely inside the boundary.
- Rectangle, circle and ellipse outline primitives plus closed chains of line primitives are supported, while incomplete/open line outlines deliberately do not constrain routing.
- This boundary protection works even for routes started from free space before a net is known, and it composes with the existing different-net copper barrier.

## 0.12.9

- Adds a persistent **File → Open Recent** project submenu with the three most recently used `.e3d_pro` projects, newest first.
- Opening, creating, Save and Save As promote the active project to the top of the list; entries persist in FreeCAD preferences across sessions, and projects that have been moved or deleted are automatically pruned instead of leaving broken menu items.

## 0.12.8

- Changes different-net routing collision feedback from a red invalid preview to a hard interactive clearance barrier.
- When the pointer would cross different-net copper, the visible route is clipped at the furthest legal point in its normal layer colour; the pointer can continue moving so the route can be steered around the obstacle, but copper cannot pass through it.
- Clicking at a blocked position commits only the visible legal segment, and via placement uses that same legal route head.
- Collision status text remains available without colouring the route as if it were valid copper.

## 0.12.7

- Adds net-aware interactive routing collision protection.
- While routing a known net, the live candidate copper is checked against the real physical envelopes of existing tracks, vias and pads; crossing, overlapping or touching copper on a different known net is blocked, while same-net reconnection remains legal.
- Blocked previews are shown in red with the conflicting net in the status message, and mouse click, double-click, Enter and via placement all reject the invalid geometry before it reaches undo history.
- Via placement is checked across every copper layer spanned by the via.

## 0.12.6

- Adds an **Auto** via-target mode to interactive routing.
- While routing a known net, Auto follows the live ratsnest and chooses the copper layer of the nearest unresolved connected pad on a different layer; the target is recalculated as the route head moves and the Routing combo shows the resolved layer (for example `Auto → B.Cu`).
- Explicit copper-layer choices remain available as a manual override.
- If the active route has no cross-layer ratsnest target, Auto falls back to the deterministic next copper plane used by earlier releases.
- The Auto/manual choice is persisted in `.e3d_pcb`, while older board files retain their previous manual via-target behaviour.

## 0.12.5

- Fixes a false unsaved/modified marker when switching the live viewer between **Tab** and **Separate Window** hosting.
- Host rebuilds now synchronously snapshot the real dirty state instead of relying on the 200 ms polling flag, and a board that was clean before rehosting explicitly establishes the reconstructed content as the same clean save baseline.
- A board that was already modified keeps its original saved baseline and remains marked `*`.
- Changing host mode is therefore presentation-only and no longer makes an unchanged project appear edited.

## 0.12.4

- Fixes Separate Window hosting across FreeCAD workbench switches.
- FreeCAD calls the Elec3D workbench's `Deactivated()` hook whenever another workbench is selected; previous releases unconditionally hid the viewer there, so even the Separate Window mode disappeared/minimised.
- Deactivation is now host-aware: Tab mode continues to follow the workbench lifecycle, while Separate Window remains open for parallel editing.
- The standalone viewer is also hosted as a genuinely parentless top-level Qt window instead of an owned child of FreeCAD's main window, and a queued visibility guard removes any accidental minimized state after a workbench transition without raising the viewer or stealing focus from the newly selected workbench.

## 0.12.3

- Adds saved/modified-state protection to the document-free PCB editor.
- The project/board tab now shows a trailing `*` whenever persistent layout content differs from the last successful Open/Save baseline, and the marker clears automatically after Save or when Undo returns the board exactly to that baseline.
- The comparison covers footprints and their transforms/model corrections, routed tracks/vias, Outline geometry, stack/layer structure and stackup, and embedded netlist connectivity; presentation-only actions such as selection, zoom/pan, grid display, sidebar collapse and 2D/3D view changes do not dirty the design.
- FreeCAD application close is guarded with **Save / Discard / Cancel** when unsaved Elec3D changes exist; Save creates a project when necessary and a cancelled/failed save cancels application close.
- Opening another board/project and creating a new project use the same unsaved-work guard.
- The modified title propagates to the FreeCAD MDI tab and survives Tab ↔ Separate Window host rebuilds.

## 0.12.2

- Fixes portable project footprint validation when opening a `.e3d_pro` on another computer.
- If the matching project-local file already exists in `Footprints/`, the project no longer reports the missing original/source-computer KiCad library as a missing footprint.
- The embedded `.e3d_pcb` footprint remains authoritative, the project `Footprints/` copy is still fingerprint-checked and produces a mismatch warning when changed, and an installed original KiCad library is still compared when it can be found.
- If the project-local footprint itself is missing, that condition is still warned.
- Missing project 3D models continue to be reported normally.

## 0.12.1

- Fixes Windows project re-save and project-local footprint filename casing.
- After a project model had already been copied into `Models/`, a subsequent Save could try to read that same file and atomically replace it while its Windows read handle was still open, producing `WinError 5: Access is denied`; project model copying now detects source=destination and skips the copy, and all other copies close the source before replacing the destination.
- Project `Footprints/` filenames now use the canonical KiCad footprint identifier spelling (for example `C_1206_3216Metric.kicad_mod`) instead of inheriting lower-cased path tokens from older board files.
- Opening legacy `.e3d_pcb` files that predate embedded footprint snapshots now also reports accurately when a missing library means a placeholder must be used, rather than claiming that a saved board footprint was restored.

## 0.12.0

- Introduces a KiCad-style **project workflow** for portable board development.
- **File → Create a new project…** always creates a named project folder containing `<name>.e3d_pro`, `<name>.e3d_pcb`, `Footprints/`, and `Models/`.
- The `.e3d_pcb` is now self-contained for complete footprint/layout geometry: every placed footprint stores its exact source snapshot and fingerprint in the board file, so the layout still restores when project/library footprint files are missing.
- Project-local `Footprints/` copies and the current/original KiCad library footprint are compared against the embedded design snapshot; changed footprints produce mismatch warnings and missing library/project copies produce not-found warnings without changing the saved board.
- Project Save copies resolvable footprint/model assets into the project folders, while model transforms and Auto/Manual X/Y/Z correction remain in `.e3d_pcb`.
- **Open…** accepts `.e3d_pro` and `.e3d_pcb`; opening a standalone board offers to create a project.
- **Save** updates the current project or prompts for project creation after netlist/footprint import, and **Save As…** clones the current design into a new named project folder.
- FreeCAD File → Open recognises `.e3d_pro` and `.e3d_pcb`.

## 0.11.2

- Keeps direct FreeCAD export as a separate MDI document tab while preserving the add-on viewer tab, and links the current board to its exported FreeCAD document for repeat updates.
- The first **Export to FreeCAD…** creates a normal FreeCAD document; later exports default to **Update existing FreeCAD document**, replacing only the add-on-managed `Elec3D PCB Export` contents so the same document/tab, save path, and unrelated user-created objects remain intact.
- The export dialog can disable that update option to deliberately create a new snapshot document.
- The linked document name and export choices survive Tab ↔ Separate Window host rebuilds, and opening/clearing a different board resets the link.

## 0.11.1

- Fixes direct **Export to FreeCAD…** on FreeCAD 1.1.3.
- FreeCAD raises an `Unknown document` exception when `App.getDocument()` is called for a name that does not exist; v0.11.0 incorrectly used that call as a boolean existence probe before creating the export document, so a normal first export such as `board_Export` failed before `newDocument()` could run.
- Document-name collision checks now use a safe helper based on `App.listDocuments()` with an exception-safe fallback, STEP-export document restoration uses the same helper, and the newly created document is explicitly activated before its GUI view is fitted.

## 0.11.0

- Adds an explicit OpenCascade B-Rep export workflow modelled on the reference web viewer.
- **File → Export STEP…** provides session-persistent choices for routed tracks/vias, footprint pads, component STEP models, negative/cavity output, Boolean fusing of touching copper and omission of STEP P-curves; counts and cavity dimensions are computed from the same visible board state being exported.
- Tracks/vias and pads are rebuilt as analytic FreeCAD/Part solids, drilled vias/pads keep their holes, component STEP models are loaded as B-Reps and placed using the footprint model transform plus the add-on's saved Auto/Manual X/Y/Z correction, and missing/invalid component models are skipped with warnings instead of aborting the whole export.
- **File → Export to FreeCAD…** uses the exact same geometry pipeline but creates a new FreeCAD document directly—there is no temporary STEP write/read round-trip.
- Negative export uses the closed Outline and physical stackup as the board block; enabling it forces copper fusing.
- Normal viewing/editing remains document-free.

## 0.10.2

- Fixes 2D selection/hit-testing around board outlines and routed copper, and tightens new-board state handling.
- Closed Outline rectangles/circles/ellipses now expose only a narrow stroke-shaped hit corridor instead of Qt treating their entire enclosed area as selectable; while the Outline layer is inactive, Outline geometry is also ignored by hover detection so it cannot mask footprints, pads, tracks or vias beneath it.
- Routed tracks now explicitly use their physical copper stroke with round caps/joins as the selectable shape, independent of the hidden QGraphicsPathItem bounding rectangle.
- The File menu adds **Clear design**, with confirmation, to remove footprints, tracks, vias, netlist and all Outline geometry and reset design layers/stackup, routing defaults and the model-directory state.
- Importing a netlist after opening a `.e3d_pcb` now starts an unsaved board identity: the old design filename/path-variable association is cleared immediately, preventing a stale MDI-tab filename and preventing Ctrl+S from overwriting the previously opened board.

## 0.10.1

- Fixes FreeCAD-host shortcut ownership and hardens transient transform/history handling.
- **Ctrl+O**, **Ctrl+S**, and **Ctrl+Shift+S** are now intercepted by the viewer before FreeCAD's main-window actions when focus is inside the add-on; Ctrl+Z/Ctrl+Y now honor operating-system key auto-repeat for continuous undo/redo.
- The v0.10.0 design-save path no longer clears an in-progress drag snapshot: saving/opening is refused until the current mouse gesture is finished, and interrupted host rebuilds roll temporary footprint/track/via/outline transforms back to the immutable mouse-down state before session snapshotting.
- Keyboard rotation is joined to a mouse gesture only while an explicit left-button transform is active, reducing the chance that stale drag state can suppress a history command.
- Live ratsnest updates during footprint movement/rotation are now read-only with respect to routed-copper net metadata; normal release/undo/redo refreshes still synchronize it.
- A v0.9.4→v0.10.0 audit confirmed that the core drag, shared-center rotation, undo/redo, and live-ratsnest implementations themselves were unchanged between those releases.

## 0.10.0

- Adds a persistent KiCad-style board design format, **`.e3d_pcb`**.
- The viewer File menu now provides **Open design…**, **Save design**, and **Save design as…**, and FreeCAD File → Open also recognises `.e3d_pcb`.
- A design restores the committed layout state: footprint placements/rotations and per-footprint graphics visibility, design layers and physical stackup, Outline geometry, tracks/vias and routing defaults, embedded netlist connectivity/ratsnest state, selection/view/grid state, and per-footprint 3D-model Auto/Manual offset corrections.
- Footprint and 3D-model binaries remain external.
- The design stores portable `${DESIGN_DIR}`, `${FOOTPRINT_DIR}`, and `${MODEL_DIR}` path variables, automatically factors common footprint/model locations, preserves external KiCad/environment model variables, and uses atomic saves.
- Missing or malformed footprint files reopen as positioned placeholders; missing model folders/STEP references are reported as warnings while the rest of the design continues loading.

## 0.9.4

- Improves 2D crosshair visibility in both light and dark themes.
- The cursor now uses a strongly contrasting theme-aware foreground plus a subtle opposite-tone halo, making both Small and Full crosshairs remain clear over the grid, copper, pads, and footprint graphics.
- The crosshair remains cosmetic/constant-width at every zoom level.

## 0.9.3

- Enlarges the default **Small crosshair** to a 40 px total span and adds **Options → 2D view → Small crosshair length**, allowing the compact cursor length to be customised from 8 px to 200 px.
- The length remains constant in screen space at every zoom level, is stored in FreeCAD preferences, and applies immediately to an open viewer.
- The length control is disabled while Full crosshair mode is selected and, like the other add-on spin boxes, ignores mouse-wheel value changes.

## 0.9.2

- Slightly enlarges the default Small crosshair from a 20 px total span to 24 px while keeping it constant in screen space at every zoom level.
- Mouse-wheel input is now ignored by all add-on combo boxes and spin boxes, including sidebar editors, grid selection, stackup values and the Options dialog, so scrolling over a control cannot accidentally change its value; ignored wheel events remain available to the containing scroll area.

## 0.9.1

- Fixes sidebar editor alignment.
- `ControlRow` now always reserves the same fixed-width unit column, even when a control such as a combo box has no visible unit.
- This keeps suffix-less selectors such as **Routing → via target**, Properties layer selectors, routed-via start/end layer selectors and 3D model-offset mode aligned to the exact same left and right editor edges as neighbouring spin boxes.
- The fix is applied centrally so future combo/spin rows inherit the same geometry automatically.

## 0.9.0

- Adds configurable 2D-view/sidebar presentation.
- The sidebar now defaults to **Footprints → Properties → Routing → Design layers → Stackup → Footprint graphics → Pads**; all seven groups are collapsible, with Footprints, Stackup, Footprint graphics and Pads collapsed by default while Properties, Routing and Design layers start expanded.
- **Options** now lets the user reorder those groups and choose the startup expanded/collapsed state of each one, and the choices apply immediately to a live viewer and survive Tab ↔ Separate window rebuilds.
- Sidebar combo boxes now use the same fixed editor width as spin boxes for cleaner alignment, including the grid selector.
- Physical Stackup copper rows show only the KiCad layer name (for example `F.Cu`) instead of the redundant `F.Cu copper` text.
- The 2D cursor now supports **Small crosshair** and **Full crosshair** preferences, with the compact constant-screen-size crosshair as the new default.

## 0.8.9

- Fixes routed-copper selection and mixed movement.
- Selected tracks and vias now use the copper geometry itself as the selection highlight instead of Qt's generic dashed rectangular bounding box.
- Tracks and vias are movable scene items and follow the exact live translation of a mixed footprint/copper selection; on mouse release the temporary scene offset is folded back into the Track/Via board coordinates.
- A mixed footprint + track + via drag is committed as one transform history entry, so one Undo/Redo restores or reapplies all selected object positions together and preserves the mixed selection.

## 0.8.8

- Fixes the remaining compound mouse-drag + rotation state bugs.
- The 2D canvas now keeps the original mouse-down positions and angles immutable for undo while maintaining a separate post-**R** / **Shift+R** continuation baseline for live movement.
- This prevents Qt's native drag baseline from reverting a rotated group when movement resumes, and a mouse-held **move → rotate**, **rotate → move**, or **move → rotate → move** sequence is committed as one transform on release so one Undo restores the exact pre-gesture footprint positions and rotations.

## 0.8.7

- Fixes rotating a multi-selection while the left mouse button is still held.
- After **R** / **Shift+R**, the active drag now rebases both its footprint-position baseline and its rotation guard to the newly transformed group, so **hold → rotate → release** keeps the shared-center group rotation and **hold → rotate → move → release** no longer restores the pre-rotation individual angles on the first mouse movement.
- This also prevents a rotation-only hold/release gesture from being misclassified as an extra translation solely because group rotation changed item positions.

## 0.8.6

- Fixes moving a selection immediately after **R** / **Shift+R** group rotation.
- The 2D drag gesture now snapshots each footprint angle at mouse-down and preserves those angles throughout the native Qt multi-item translation, so moving a rotated group cannot revert the rotation.
- Undo/redo still records the move and rotation as separate editor transforms.

## 0.8.5

- Changes multi-footprint rotation to behave as a group.
- When two or more footprints are selected, **R** / **Shift+R** now rotates the full selection around the shared center of its combined visible bounding rectangle instead of rotating each footprint in place.
- Single-footprint rotation is unchanged.
- The grouped transform updates both footprint positions and angles as one undoable action and bypasses per-item grid snapping during the rotation so the selection keeps its geometry.

## 0.8.4

- Removes the last Outline → copper sidebar layout jump and adds KiCad-style schematic net metadata/highlighting.
- The Routing section now keeps a fixed place in the 2D sidebar and is merely disabled while Outline is active, so returning to B.Cu no longer inserts a new block and repaint/relayout the left panel.
- Net names are now read directly from the imported KiCad netlist and attached to footprint pads; saved tracks and vias inherit the unique net name of the electrical copper island they connect to, including through arbitrary-layer vias.
- Pad Properties and hover readouts show the assigned net.
- Press **`** to highlight the net of selected copper/pad(s), or the copper item under the cursor when nothing copper is selected; unrelated pads/tracks/vias/ratsnest lines are dimmed while the highlighted connection is brightened.
- As in KiCad, **~** clears net highlighting and **Ctrl+`** toggles the current highlight without changing which net is selected.
- Highlight state survives Tab ↔ Separate window host rebuilds.

## 0.8.3

- Stabilizes selection-driven sidebar updates and aligns numeric editors.
- All spin boxes in the same Properties/Routing/3D-offset group now use a common width and a common right-aligned unit column; Outline geometry editors also move the `mm` unit outside the spin box, and Stackup rows use one fixed numeric width throughout their group.
- The Fields property grid now reuses a persistent pool of label widgets instead of deleting and recreating every row on each selection change, and Properties updates are painted as one atomic sidebar refresh so Design Layers/Stackup/Graphics below it do not flash through intermediate layouts.
- Selecting the already-active footprint from a previously empty selection no longer rebuilds Footprint Graphics or Pads; only the selection-dependent Properties content changes.

## 0.8.2

- Removes the visible layer-switch repaint churn in the 2D editor.
- Design Layers rows are now persistent widgets whose active/visibility/count state is updated in place instead of destroying and recreating the whole section on every copper/Outline click.
- Routed track/via QGraphicsItems are likewise restyled in place when only the active layer or visibility changes, so switching layers no longer clears and reconstructs all routed copper.
- The Outline and Routing tool strips now live in one fixed-width stacked tool rail, preventing the canvas width from changing when the active layer changes.
- Intermediate canvas-selection signals are suppressed during the layer transition and Properties is refreshed once after the final state is applied, eliminating the repeated hide/show/repopulate cycle that produced the left-panel flash.
- Full rebuilds remain only for real topology changes such as adding/removing a design layer or adding/removing geometry.

## 0.8.1

- Makes the 3D viewer use one physical copper finish for all conductive board geometry.
- Pad copper, routed tracks and vias now share the same gold/copper material regardless of their design layer, so F.Cu/B.Cu/internal-layer routing is no longer shown in red/green/blue in 3D.
- Layer colours remain unchanged in the 2D editor where they are useful for identifying the active/reference layer.
- Reference-plane outlines, silkscreen/fabrication graphics and STEP model materials are unaffected.

## 0.8.0

- Adds interactive KiCad-style PCB routing to the document-free 2D editor.
- Press **X** or use the copper-layer Route tool to start tracks; live routing uses horizontal/vertical/45-degree geometry and **/** toggles the alternate corner posture.
- Magnetic snapping handles pad copper, via copper/centres and the nearest point along an existing track centreline; **Shift** temporarily disables object snapping and **Ctrl** temporarily disables grid snapping.
- Clicking compatible existing copper finishes a connection, **Backspace** removes the previous fixed corner, **Enter** fixes the current preview and ends the route, and **Esc** ends only the already-fixed route (or disarms an armed via first).
- Ratsnest calculation now works from electrical copper islands rather than only pad positions: saved tracks and vias remove completed airwires, incomplete track ends/track middles become valid airwire anchors, and the active route's ratsnest is updated live from its moving head.
- **V** arms a via; its pad diameter and drill diameter are independent, the default drill is **0.000 mm** (a solid conductive cylinder), and the target can be any copper reference layer so a via may span arbitrary layer pairs.
- Saved tracks/vias participate in Delete and Ctrl+Z/Ctrl+Y, survive Tab ↔ Separate window rebuilds, and are rendered in 3D below their reference plane; drilled vias are hollow while zero-drill vias are solid.

## 0.7.13

- Simplifies the viewer-host preferences and File workflow.
- The viewer now always opens when the Elec3D workbench is activated, and FreeCAD side panels are always hidden while the viewer is the active main-window tab, so the obsolete **Open viewer with workbench** and **Hide side panels for viewer tab** checkboxes have been removed from Options.
- The only remaining host preference is **Viewer opens as** (main-window tab or separate window), and it still applies immediately.
- In the viewer File menu, **Import netlist…** is now the first command, ahead of direct footprint import, reflecting the netlist-first board workflow.

## 0.7.12

- Cleans up the File menu chrome in the viewer header.
- The native `QToolButton` popup indicator is no longer used because its appearance varies with FreeCAD/Windows styles and could look like overlapping chevrons; File is now a normal header button with one deterministic down-caret and the popup is positioned manually with a small gap below it.
- The File popup itself now has roomier item padding, margins and separators so its commands are visually distinct from the adjacent 2D/3D/Fit/Grid buttons instead of looking packed against the toolbar.

## 0.7.11

- Fixes four 2D/netlist UI regressions.
- The selected footprint/reference name is again guaranteed visible in the header instead of being allowed to collapse to zero width; the title has a bounded readable width while the existing MDI shrink behaviour is retained.
- `F.Fab` now starts hidden for newly loaded footprints, matching the intended fabrication-layer default while preserving any explicit per-footprint visibility state across rebuilds.
- The File tool button now relies on Qt's native popup indicator and no longer includes a second text glyph, removing the overlapping double-arrow appearance.
- Ratsnest geometry now updates continuously while footprints are dragged, and each unrouted net is redrawn as a minimum spanning tree over its current pad positions rather than a star rooted at the first netlist node, matching the geometry strategy of the reference web viewer for the editor's current no-routing state.
- Undo history is still committed only once on mouse release.

## 0.7.10

- Fixes the header-action regression introduced by the v0.7.9 MDI sizing change.
- The 2D/3D/Fit/Grid and File controls now keep their native readable size hints instead of using a horizontal `QSizePolicy.Ignored`, which allowed the stretch item before them to squeeze the buttons into clipped slivers at the far-right edge.
- The viewer itself, footprint title, stacked pages and MDI host remain shrinkable, so the earlier horizontal-scrollbar fix is retained while the header controls stay legible.

## 0.7.9

- Fixes two FreeCAD MDI-hosting regressions.
- When the viewer is switched from a maximized main-window tab to a separate window, the add-on now remembers the previously active FreeCAD document and hands its maximized state back after removing the KiCad MDI child; this prevents Start/3D document views from reappearing as small floating subwindows.
- The custom viewer, stacked 2D/3D pages and 3D toolbar now explicitly advertise a zero minimum width and use shrinkable size policies, while the long 3D help text switches to a compact form in narrow layouts.
- Native view buttons share the available toolbar width instead of forcing a minimum document width.
- This prevents the maximized KiCad tab from exceeding FreeCAD's central viewport and removes the unwanted horizontal MDI scrollbar/cropped right side on DPI-scaled or dock-constrained layouts.

## 0.7.8

- Changes left-drag navigation in the document-free 3D viewer to the same classic SGI/Gavin Bell virtual-trackball model used by KiCad's `TRACK_BALL::Drag()`.
- Mouse positions are normalised exactly as KiCad does, projected onto the same 0.8-radius sphere/hyperbola, and the resulting screen-space quaternion-equivalent rotation is applied to the complete camera frame around the board target.
- This replaces the previous independent screen-axis yaw/pitch increments, so the slab now feels grabbed and rolled as one object during diagonal/circular drags, including after Top/Bottom/Front/Back/Left/Right/Iso presets and through full 360-degree motion.
- Middle-drag remains the KiCad-style pan gesture; right-drag is retained as an additional compatibility pan gesture.
- The 3D hint now identifies the KiCad trackball interaction.

## 0.7.7

- Makes the document-free 3D board geometry follow the actual **Outline** shape instead of always extruding the Outline bounding rectangle.
- Rectangle, circle and ellipse primitives are now extruded as their real profiles; connected Outline line segments are also chained into closed polygon loops when possible.
- The translucent stack body, every copper reference-plane marker and the top Outline stroke all use the same boundary, so an ellipse/circle seen in 2D remains an ellipse/circle in 3D rather than becoming a rectangular slab with a curved line drawn over it.
- Open line chains still fall back to the historical footprint/bounds rectangle until they form a closed board boundary.

## 0.7.6

- Fixes 3D camera navigation and adds a persistent orientation triad.
- Orbiting is now an unclamped incremental trackball using a stored camera-up vector, so the camera can pass smoothly over either pole, continue underneath the board and complete full 360-degree rotations instead of stopping at ±89°.
- Top/Bottom/Front/Back/Left/Right/Iso presets now define both view direction and screen-up explicitly; the first drag after selecting a preset therefore continues from that exact view instead of jumping when the old camera basis switched between world-Z and world-Y near a pole.
- A small X/Y/Z axis triad is overlaid at the bottom-right of the 3D viewport and follows the camera in both the GPU renderer and software fallback.
- The triad is mouse-transparent and remains available after automatic GPU fallback.

## 0.7.5

- Adds GPU-backed rendering to the 2D editor without changing its editing model.
- When FreeCAD exposes QtOpenGLWidgets, the existing QGraphicsView now paints into a direct Qt `QOpenGLWidget` viewport, so grid, footprint paths, pads, selection overlays and compositing are rasterised by the GPU while the mature CPU-side scene continues to own hit testing, rubber-band selection, outline handles, snapping and undo/redo.
- This deliberately does not use Pivy/Quarter and therefore does not reintroduce the old Quarter native crash path.
- The 2D status bar reports **GPU 2D (Qt OpenGL)** when active; if a GL context cannot be created the canvas automatically replaces the viewport with the normal QWidget/QPainter software path and reports **software fallback**.
- `ELEC3D_DISABLE_GPU_2D=1` can force the software path for diagnostics/remote-desktop setups.
- Pan/zoom coordinates and all existing 2D interactions are unchanged because the scene remains in millimetres and only its paint device changes.

## 0.7.4

- Corrects the physical stackup model so every copper layer is treated as a **reference plane**.
- The editable separation is now the exact distance between adjacent reference planes; changing a copper thickness no longer moves any plane, changes any separation, or changes the stack block height.
- Copper remains etched downward from its own reference plane, so pads (and future routed tracks) occupy `plane Z - copper thickness … plane Z`.
- The bottom margin locates the bottom reference plane above the block bottom, the top margin extends above the top reference plane, and removing a middle layer now merges only its two neighbouring separations instead of incorrectly adding the removed copper thickness.

## 0.7.3

- Fixes STEP material colours in the document-free 3D preview for both common STEP presentation styles.
- The colour reader now expands a `STYLED_ITEM` attached to a whole `MANIFOLD_SOLID_BREP`/shell onto every descendant face, while a direct per-face style still overrides the inherited solid colour.
- This specifically fixes multi-solid KiCad models such as `LAN7800-I_VSX.stp`, where the package body and leads are styled at solid level rather than through `brep_faces[].color`; previously the renderer fell back to the dominant light colour and made the dark package body appear grey.
- Solid topology is retained for future per-solid tessellation paths, and the dominant fallback is now weighted by effective coloured faces.

## 0.7.2

- Fixes the **3D model offset / Manual** sidebar layout on FreeCAD styles and DPI settings where the native spin boxes and three action buttons reported a minimum width wider than the fixed sidebar.
- The scroll-area child is now explicitly allowed to shrink to its viewport instead of silently growing behind a disabled horizontal scrollbar.
- X/Y/Z editors use compact numeric controls with the `mm` unit outside the spin box, and Align Z / Center XY use a two-column row with Reset on its own full-width row, so every control remains visible without widening the sidebar or hiding the 3D canvas.

## 0.7.1

- Moves interactive 3D rendering onto a direct Qt OpenGL path when FreeCAD exposes `QtOpenGL`/`QtOpenGLWidgets`, while retaining the QPainter renderer as a compatibility fallback.
- Unlike the removed Pivy/Quarter experiment, the GPU path is owned entirely by the add-on: board/model geometry is uploaded to vertex buffers once and orbit/pan/zoom changes only the camera matrix, so STEP meshes are no longer re-projected, depth-sorted and rasterised on the CPU on every mouse move.
- The 3D toolbar identifies whether **GPU (Qt OpenGL)** or the software fallback is active.
- Colour matching is corrected in three places: the web viewer's lights are transformed correctly into this viewer's Z-up/Y-flipped coordinates, lighting is applied in linear colour space before sRGB output, and the translucent stack material is rendered before opaque models/pads just like the reference viewer's `renderOrder=-1/depthWrite=false` block, so dark package bodies are no longer washed pale. 3D picking now ray-tests the actual footprint/model triangles (with a line-only fallback), shares selection with the 2D editor/sidebar and shows the same blue 3D selection box.
- A new **3D model offset** section follows the reference app's Auto/Manual workflow and model coordinate transform: Auto measures the placed model, centres it on the rotated pad field and seats non-through-hole models on the pad plane; Manual exposes X/Y/Z offsets plus Align Z, Center XY and Reset.
- Model-offset state survives host switching and participates in undo/redo.
- The **Create FreeCAD document** UI and its conversion-specific Options controls are temporarily removed while the future 3D-to-FreeCAD/STEP workflow is being redesigned.

## 0.7.0

- Brings the 3D preview closer to the reference web viewer and reorganises the header.
- **The board no longer washes blue.** Every design layer used to be drawn as a full-board translucent slab in its own colour. Stacked, those sheets tinted the substrate and every component sitting on it. Each plane is now marked by a thin outline at its height, exactly as the reference viewer does, and the block is bare board material again.
- **Proper lighting.** The renderer used a single light applied with `abs(dot(n, l))`, which lit front and back faces identically and left everything flat. It now runs the reference's rig — a sky/ground hemisphere, a key light from above, a cool side fill, and an upward fill so the underside of the board is lit rather than black when you orbit beneath it. Shading is per-channel, so the tinted fills colour surfaces instead of only brightening them.
- **Models keep their STEP colours.** KiCad's 3D models carry colour on individual faces, so tessellating a model as one mesh could only ever produce one flat grey body. Models are now tessellated face by face with each face's own colour, which is what makes an SOIC read as a black package with plated leads rather than a grey block.
- **Opaque surfaces are drawn before translucent ones.** Without a depth buffer, a single global depth sort put the translucent board block on top of a component whenever the block's centroid happened to be nearer.
- **3D models work for plain footprint imports.** Setting the models folder was reachable only from the netlist wizard, so importing a `.kicad_mod` directly could never show a package body. The **File** menu now offers *3D models folder…* for the whole board, and *Load 3D model for selection…* for a footprint that names no model at all or names one that cannot be found.
- **One File menu instead of five buttons.** Import, Netlist, Reload, Options and Create FreeCAD have moved into a single `File` menu, leaving the header to the view controls that get used constantly.
- Routing (tracks and vias) from the reference viewer is still deliberately not included.
- WRL models are still not previewed: parsing them would reintroduce the Coin dependency the software renderer exists to avoid.

## 0.6.3

- Fixes the software 3D renderer on FreeCAD 1.1.3's PySide compatibility layer.
- Layer colours are represented internally as RGB float tuples; passing those tuples through `QColor(...)` selected FreeCAD's QVariant overload and raised `QVariant must be holding a QColor` while painting footprint graphics.
- The renderer now converts strings, QColor objects and RGB tuples explicitly to channel values, constructs `QPen` through setters instead of an overloaded constructor, and always ends an active `QPainter` in a `finally` block.
- This also removes the secondary `QBackingStore::endPaint() called with active painter` warnings.

## 0.6.2

- Replaces the embedded Pivy/Quarter `QOpenGLWidget` 3D preview with a document-free **software 3D renderer** drawn through Qt `QPainter`.
- On FreeCAD 1.1.3/Windows, Quarter could initialize and display the board but then raise native `GUIApplication::notify` access violations; those faults happen below Python and cannot be caught safely.
- The new renderer never creates an OpenGL widget, while keeping the same 2/3 view switching, orbit/pan/zoom, Fit/Top/Bottom/Front/Back/Left/Right/Iso controls, stackup/copper/pad/outline rendering, and STEP model loading through `Part.read(...).tessellate(...)` without creating an `App::Document`.

## 0.6.1

- Fixes the document-free 3D preview on FreeCAD 1.1 Qt6 builds where Pivy's compatibility package does not re-export `QOpenGLWidget`.
- The add-on now bridges FreeCAD's own `PySide.QtOpenGLWidgets.QOpenGLWidget` (falling back to the bundled PySide6 module) into `pivy.qt.QtWidgets` before importing `pivy.quarter`.
- This keeps the preview on FreeCAD's bundled Qt/Pivy stack, does not bundle a second PySide copy, and preserves the no-`App::Document` 3D architecture.

## 0.6.0

- Adds a native four-step **KiCad netlist import** workflow and a document-free **3D board view**.
- The wizard follows the reference web app's sequence: choose the `.net` file, resolve referenced `.kicad_mod` geometry (with placeholders for anything still missing), optionally choose the KiCad 3D-model library folder, then review and finish in 3D.
- Net connectivity is drawn as a ratsnest and schematic references replace `REF**` in the 2D scene.
- The 2D and 3D views share the same in-memory footprints, design layers, stackup and Outline; **2** switches to 2D and **3** switches to 3D.
- The 3D preview uses FreeCAD's bundled Coin3D/Pivy Quarter viewer directly, so it does not create an `App::Document`; copper planes, stack material, pads, footprint graphics, Outline and referenced STEP/WRL models are rendered from the existing editor state.

## 0.5.7

- Adds a dynamic physical **Stackup** manager below Design Layers.
- It follows the requested top-to-bottom structure instead of using one global separation/copper control: one copper layer shows `top margin → copper → bottom margin`; two layers show `top margin → copper → separation → copper → bottom margin`; every additional copper layer inserts another `separation → copper` pair.
- Defaults are 1 mm top margin, 0.035 mm copper, 1.6 mm separation and 1 mm bottom margin.
- Each copper plane and each inter-layer gap keeps its own editable thickness, existing values are preserved when a new layer is added, the section updates automatically on add/remove, and the total stack height is shown at the bottom.
- Stackup data is part of the document-free layer snapshot, so it survives live Tab ↔ Separate window switching.

## 0.5.6

- Removes Outline-editor flicker by keeping the geometry property controls alive and synchronising their values in place instead of rebuilding the sidebar after every edit.
- On-canvas move/resize gestures now update the numeric readout live, while Design Layers is left untouched unless layer membership or counts actually change.
- Numeric edits still commit as one undoable action, avoiding noisy per-keystroke history.

## 0.5.3

- Adds the permanent board **Outline** design layer and editable outline drawing.
- **Outline** is present in Design layers from startup, can be hidden/activated, and cannot be removed. Footprints can only be assigned to copper layers; importing while Outline is active uses the top copper plane.
- Activating **Outline** opens a right-side drawing panel. It provides Select, Rectangle, Circle, Ellipse and Line tools. Outline shapes are selectable/drawable only while the Outline layer is active.
- A selected outline shape exposes editable geometry on the right: rectangle/ellipse center, width and height; circle center, radius and diameter; line start/end coordinates. Drawing, deleting and geometry edits participate in Ctrl+Z/Ctrl+Y history.
- Footprint **rotation** is now always shown in **Basic Properties** as an editable degree value (positive = counter-clockwise), including 0°. Multi-selections show a mixed value when their rotations differ.
- Outline shapes, active layer, selection and edit history survive live Tab ↔ Separate window rebuilds.

## 0.5.2

- Adds keyboard editing with document-free undo/redo.
- **Ctrl+Z** undoes and **Ctrl+Y** redoes footprint moves, rotations and deletes. Ctrl+Shift+Z is also accepted as redo.
- **R** rotates every selected footprint **90° counter-clockwise** around its own footprint origin; **Shift+R** rotates **90° clockwise**.
- **Delete** removes every selected footprint in one operation, so a multi- selection made by Ctrl/Shift-click, rubber band or Ctrl+A can be deleted and restored with one Ctrl+Z.
- Edit history uses stable in-memory footprint ids, so it survives the live **Tab ↔ Separate window** viewer rebuild. Rotation is also preserved by host switching and carried into **Create FreeCAD** conversion.

## 0.5.1

- Reworks how things get selected.
- **Selection is pad-first.** Clicking a pad selects and highlights the *pad* rather than the footprint it sits on; clicking anywhere else inside a footprint — body, silkscreen, reference or value text — selects the *footprint*. Dragging always moves the footprint, even when the drag starts on a pad: a pad is a finer thing to point at, not a thing you can move on its own.
- **Only pads and footprints are selectable.** Graphics and texts are drawn for reference; clicking one now selects the footprint that owns it instead of showing properties for the shape itself.
- **Every selected footprint is highlighted** in the Footprints list, not just the one driving the graphics and pads sections.
- **Deselecting works.** Clicking empty canvas or pressing Escape returns Properties to *No selection* and clears the Footprints list highlight, instead of quietly falling back to the last active footprint.
- Ctrl-clicking a pad extends the footprint selection rather than collapsing the readout to that single pad. Picking a pad from the Pads list narrows the selection to the footprint that owns it.

## 0.5.0

- Brought the sidebar in line with the reference web viewer.
- **Properties** is split into *Basic Properties*, holding the one control you can act on — the design layer the footprint sits on — and a collapsible *Fields* group holding the read-only detail (reference, value, description, tags, pad count, extent, position, 3D model, source file). The Fields group remembers whether you left it open. Selecting a pad forces it open, because there is nothing else to show for one.
- **Design layers** is new: the board's copper stack, listed top plane first. Each row toggles visibility, shows its colour, its footprint count and a remove button, and clicking a row makes that plane active. *Add layer* stacks a plane on top and relabels the stack the way KiCad does (`B.Cu`, `In1.Cu`, …, `F.Cu`). New footprints land on the active layer. Footprints take their plane's colour for their copper and are dimmed while another plane is active, so the canvas shows at a glance which layer each part belongs to. Removing a plane that still has footprints on it asks first, then moves them to the nearest remaining plane.
- **Multi-selection** works, via ctrl/shift-click, rubber band or Ctrl+A. The Properties panel then shows the *intersection* of the selected footprints: a row appears only when every selected footprint owns that property, and when they own it but disagree the row stays and reads "— mixed —" rather than vanishing. Choosing a layer while several are selected moves all of them.
- Clicking a pad on a footprint that was not already selected now shows the pad's properties instead of being overwritten by the footprint's.
- Section headers count in the `PADS (9)` form.
- Routing (tracks and vias) from the reference viewer is deliberately not included at this stage.

## 0.4.10

- Changed live Tab ↔ Separate window switching to rebuild the Qt host/view widget instead of reparenting a widget that has already become a native top-level window.
- Parsed footprint models, positions, layer visibility, selection, grid settings, zoom and view centre are restored into the fresh viewer, avoiding the Windows/Qt event-routing state that could leave a visible tab unclickable after repeated switches.
- The visible grid also keeps the selected spacing down to 2 px, so a 0.1 mm snap grid at normal zoom no longer renders as a coarser 0.5 mm grid; grid lines are generated from integer indices to stay aligned with snapped cursor coordinates during long pans.
- Opening or importing a `.kicad_mod` file now parses it directly into the custom Qt viewer.
- It does **not** call `App.newDocument()`, does not create `DocumentObject`s, and therefore does not create a second FreeCAD document tab.
