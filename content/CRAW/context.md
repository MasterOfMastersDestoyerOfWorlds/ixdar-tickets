# CRAW Epic Context -- Read This First

Everything a fresh agent needs to pick up a CRAW ticket without a long codebase search.

## What We Are Building

One crawfish model -- bones and weights, photographic textures, semantic patch labels -- expressed
entirely in the mesh DSL, assembled from the ten Trellis2 scans in `/home/acw/crawfish/`
(`IMG_4109.glb` .. `IMG_4118.glb`). Each scan is one mesh (~623k vertices, ~936k triangles,
`POSITION`/`NORMAL`/`TEXCOORD_0`), one PBR material, two embedded PNGs, no skin; the sidecar
`IMG_41xx.settings.json` records the generator settings (backend trellis2, resolution 1024,
12 steps, seed 1234, decimation_target 1000000, texture_size 2048) and is identical for all ten.

Source of intent, in this order:

1. `/home/acw/obsidian/Notes/Plan for Crawfish.md` -- the 13 capability bullets these tickets implement.
2. `/home/acw/obsidian/Notes/Labeling Mesh Patches.md` -- label store = tag set per patch + separate
   ontology hierarchy; VLM highlight-and-name harness (SAMPart3D / Set-of-Mark pattern).
3. `/home/acw/obsidian/Notes/Mesh Booleans.md` and `/home/acw/Code/Ixdar/quad-layout-booleans.md`
   -- Manifold backend, dirty-patch marking, QuadMixer reconstruction.
4. `/home/acw/obsidian/Notes/Quad Layouts.md` -- open items per pipeline stage (feature constraints,
   layout persistence, loading arbitrary layouts).
5. `/home/acw/obsidian/Notes/Coons Patch Representation.md` -- patch representation background.

Papers are PDFs under `/home/acw/QuadLayoutsPapersCompleted/` (LCK21a, LCKB19, KCP*13, NHE*19 QuadMixer,
ZGZ*16, TPH14, PPM*16). Never run `pdftotext` (denied); use `pdfinfo` and the Read tool with page ranges.

## Standing Rulings That Bind This Epic

From `/home/acw/Code/Ixdar/CLAUDE.md`, `ARCHITECTURE.md` and `REFACTOR-PLAN-2.md`:

- **No decimation of the scans.** The quad layout is the compression step. If a pipeline stage cannot
  handle a full-resolution scan, the stage is improved (CRAW-2). A decimation node is deliberately
  deferred; do not add one.
- **One language.** Everything a graph needs is expressed in `.dsl`. Saved layouts, labels, collections
  and manifests are `.dsl` files (CRAW-4, CRAW-6, CRAW-12). No sibling file formats.
- **Elements are selected geometrically** -- by position, nearest vertex, shortest edge path with
  waypoints, or a surface point on a patch -- never by literal vertex/face/patch id.
- **Data separate from algorithm.** Stages produce plain value classes with `public final` fields.
  No nested classes, no records, no enums (a two-state switch is a `public boolean`).
- **Slot names are owned by one constant** on the value type or producing node. Attributes ride
  `GeometryBundle` slots; prefer `IntField` / `BoolField` / `Vector3Field` over raw arrays (C6).
- **No `System.getProperty` knobs** for behaviour; public fields set by the caller.
- **Desktop-only where native**: Assimp, Manifold, solvers stay behind `desktopOnly = true` and the
  `Class.forName` firewall; the TeaVM build must stay green (`./tools/teavm-build.sh`).
- **Unit tests never load mesh files**; mesh-backed checks go in `ixdar-app/test/benchmark/`.
- **Checkstyle gates the build**; fix every violation you see. Javadoc `@param` for every parameter.
- The user commits. `git add/commit/checkout/...` are denied for agents.

## Build and Verify

```
mvn -q clean compile -pl annotations,ixdar-app                      # always clean; plain compile prunes registries
mvn clean test -pl annotations,ixdar-app -Dtest=A,B -Dsurefire.failIfNoSpecifiedTests=false
uv run ixdar-cli run-scene --scene <id> --timeout 90 --skip-build [--property ixdar.model=<path>] [--profile] [--keep-alive]
uv run ixdar-cli mesh-dsl-validate --dsl <file.dsl> [--export]
uv run ixdar-cli gen-docs                                            # after adding routes or CLI commands
```

Known baseline: 3 pre-existing failing quad-layout test classes (REFACTOR-PLAN-2 A7) plus the
`@Disabled` provenance half of `MeshBooleanProvenanceTest` (CRAW-13 fixes it). Fertility layout
baseline: 13853 quads, 284 patches, singularities 44, euler -6 -- must not move unless a ticket says so.

## Key Code (all under `ixdar-app/src/main/java/ixdar/`)

| Concern | Where |
|---|---|
| Mesh file loading (.obj/.ply/.off -> ArrayMesh) | `geometry/mesh/data/load/MeshLoader.java`, `*MeshParser.java`; node `nodes/data/LoadMeshNode.java` (`load_mesh`) |
| Assimp (GLB-capable, render-only today) | `graphics/render/model/AssimpModelImporter.java`, `AssimpModelRuntime.java` |
| Bundle + slots | `geometry/mesh/data/GeometryBundle.java`; tags `nodes/data/TagGeometryNode.java` (`_tags`, per-vertex `Map<String,boolean[]>`); edge marks `data/EdgeMarks.java` + `nodes/modifier/MarkEdgesNode.java` |
| Quad layout driver | `geometry/mesh/quadlayout/QuadLayoutEngine.java` (node `quad_layout`); stages in `crossfield/`, `seamless/`, `motorcycle/`, `quantization/`, `embedding/`, `gridmap/`, `extraction/` |
| Layout result | `quadlayout/embedding/ArcNetwork.java` (nodes/arcs/patches, id == index); `extraction/PatchSurfaceGeometry.java` (`patch_id` per-face IntField) |
| Authored layouts (round-trippable DSL) | `nodes/network/{ArcNetworkNode,NetworkNode,NetworkArc,NetworkPatch}.java`; `src/main/resources/dsl/fixtures/*.dsl` |
| Saved layouts (CRAW-4) | `nodes/network/QuadLayoutDslWriter.java`, `gui/terminal/commands/SaveLayoutCommand.java`, `data/load/OffMeshWriter.java`; `src/main/resources/dsl/layouts/*.dsl` |
| Cross-field constraints | `quadlayout/crossfield/constraint/{ConstraintSource,CurvatureConstraints}.java` |
| Booleans | `geometry/mesh/csg/ManifoldMeshBooleanBackend.java` (FFM), `MeshBooleanResult.java`; node `nodes/geometry/MeshBooleanNode.java`; scene `scenes/mesh/MeshBooleanScene.java` |
| Mirror / join / ops | `nodes/modifier/MirrorGeometryNode.java`, `nodes/geometry/JoinGeometryNode.java`, `geometry/mesh/data/ops/*` |
| Bones | `nodes/modifier/SetBoneWeightNode.java`, `ApplyBoneNode.java`; example `src/main/resources/dsl/hand.dsl` |
| Model scenes and menu | `scenes/model/{ModelScene,ModelCatalog,ModelChoice,GraphChoice}.java`; right panel `gui/ui/menu/SceneModelMenu.java` (HyperString rows, clickable words) |
| Layout scenes | `scenes/QuadLayoutScene.java` (`quad-layout`), `scenes/EmbeddedTMeshScene.java` (`embedded-tmesh`), `scenes/CrossFieldExaminationScene.java` |
| Rendering | `graphics/render/model/HalfEdgeMeshRuntime.java` (`setTags`/`setTagColor`, FLAT/SCALAR), `QuadLayoutRuntime.java` (patch overlays) |
| Automation | `platform/automation/AutomationEndpoint.java`, `endpoints/**` (`@AutomationRouteAnnotation`, example `endpoints/ui/Screenshot.java`), `endpoints/AutomationRuntime.java` (`runOnMainThread`, `uiState`) |
| CLI | `ixdar_automation_cli/` (`ixdar_cli.py`, `cli_commands/`, `automation_client.py`, `automation_routes.json`, `mesh_catalog.py`) |

There is no ray picking, no UV/material on meshes, no VLM client in Ixdar, and
`MeshBooleanResult.faceOrigin` is empty -- those gaps are exactly what CRAW-7, CRAW-3,
CRAW-10 and CRAW-13 fill. `LayoutFixture` mentioned in CLAUDE.md no longer exists; fixtures are the
`dsl/fixtures/*.dsl` graphs.

## Saved Layout Format (CRAW-4)

A saved quad layout is an ordinary authored .dsl graph -- there is no second format and no sidecar.
`QuadLayoutDslWriter` emits, in this order:

```
# Saved quad layout of <source model>
carrier = load_mesh(path="<carrier .off>")
network = arc_network(mesh=carrier.geometry)
n0 = network_node(net=network.net, point=<x, y, z>[, critical=true][, border=true])
a0 = network_arc(net=network.net, from=n0.id, to=n7.id[, length=N][, feature=true]
                 [, via1=<x, y, z>, ...])
p0 = network_patch(net=network.net, a=n0.id, b=n7.id, c=n9.id, d=n3.id,
                   first_side=1, second_side=2, third_side=1, fourth_side=2)
surfaces = quad_layout_surfaces(net=network.net)
```

- **The carrier is the pipeline's own output, not its input.** `GridMapAssembly` needs the seamless
  parametrization, so a saved network cannot regenerate the quad mesh without re-running the solver.
  The saved form therefore persists results: `QuadCarrierLayout` restates the finished layout on the
  **extracted quad mesh** (fertility: 13847 vertices, 13853 quads), which `OffMeshWriter` writes as
  `<stem>_carrier.off` with quad faces and `OffMeshParser` reads back as quads. Layout arcs are
  chains of quad-mesh edges there, so re-tracing reproduces them: on fertility all 568 arcs need
  **2 via waypoints in total** (worst arc 1), against 1449 (worst 30) on the triangle working copy.
- **Geometric anchors only.** A node is the position of its carrier vertex, resolved on reload by
  `NearestVertex`. An arc is the concatenation of unique-shortest-edge-path legs between the fewest
  via waypoints that reproduce the stored path exactly. A patch is its four corner nodes plus the
  four side arc counts. No literal vertex, face or element id is ever written.
- **`quad_layout_surfaces` closes the graph.** It floods the carrier's faces from the arc walls
  (`LayoutPatchPartition`; interior left of walk, seeded from each arc's left/right flank) and emits
  them regrouped one component per patch with the `patch_id` per-face `IntField`
  (`PatchSurfaceGeometry.partitionBundle`) -- the same shape a pipeline run's `engine.patchSurfaces`
  has, so `QuadLayoutRuntime.setLayoutPatchSurfaces` renders patch fill (P), boundaries (B) and the
  quad grid (Q) from it directly. The node also passes `net` through, so `lastOutput("net")` still
  reaches the network.
- **Byte-stable.** Nodes are sorted by position (x, then y, then z), arcs by end-node index then via
  geometry, patches by corner indices; statement ids are the emission order, so one layout always
  renders to the same bytes, across JVMs included. Patch *numbering* is therefore the writer's
  geometric order, not the pipeline's internal ids -- the face partition is identical, the palette
  hash that colours it is not.
- **A carrier file is written whenever the network is not on the loaded mesh's own vertices**: the
  layout's `topology.sourceMesh` differs from the scene's mesh (the quad-layout case) or the network
  uses minted vertices (the embedded-tmesh case, where 229 of 278 nodes and 556 of 568 arcs sit on
  refinement-minted vertices). A layout on the source mesh's own vertices names the source directly.
- **Where they live.** `ixdar-app/src/main/resources/dsl/layouts/*.dsl`; `ModelCatalog` lists each as
  `ModelChoice.Kind.DSL` named `<stem> (layout)`, so the model menu, the `model` command and
  `-Dixdar.model=<path>.dsl` all reach it. Write one with the `save-layout <path.dsl>` terminal
  command, or `--property quadLayout.save=<path>` for a scene whose layout is final at load
  (`quad-layout` is, since `loadModel` runs the pipeline).
- **Known ceiling.** `NetworkArc.VIA_COUNT` is 32 fixed via ports. On the quad carrier that is
  ample; on a triangle working copy fertility's worst arc already needs 30 -- see CRAW-4's unknowns
  before saving a scan-scale layout that way.
- **`EmbeddedMeshTopology` accepts a uniform non-triangle carrier**, copying it as it stands and
  registering no barycentrics, so the authored network works on quads but local remeshing does not.

## Neighbouring Epics (check before starting)

- **VIEW** (mesh viewer): VIEW-3 tag tree panel, VIEW-4 click-to-zoom, VIEW-5 tagged rendering,
  VIEW-6 staging dir, VIEW-7/8 sidebar -- CRAW-7, 8, 11, 12 extend these to patches and labels.
- **PATCH** (semantic patch decomposition and the quad-layout build-out): PATCH-89 is the
  feature-arc ticket (blocked by CRAW-5; there is deliberately no CRAW duplicate); PATCH-57 Coons DSL
  emission; PATCH-10 chunked Claude labeling; PATCH-12/46/55/103 solver scaling relevant to CRAW-2.
- **MESH**: MESH-38 quad-preserving boolean research (blocks CRAW-17), MESH-50 unwelded boolean
  output, MESH-49 ArrayMesh canonicalization.
- **VOYAGE / Daud** (`/home/acw/Code/Daud`): existing VLM harness code (VOYAGE-4, 19, 51) to
  reuse in CRAW-10.

## Dependency Order

```
CRAW-1 GLB load ─┬─ CRAW-2 pipeline at scan scale ───────┬─ CRAW-18 symmetrize ─┐
                 ├─ CRAW-3 UV/material ─┬─ CRAW-14 textures across boolean        │
                 │                      └─ CRAW-20 glTF export ───────────────────┤
                 └─ CRAW-12 collection ── CRAW-16 union of members ───────────────┼─ CRAW-19 crawfish.dsl
CRAW-13 boolean provenance ── CRAW-14, CRAW-15, CRAW-16                          │
CRAW-4 save layout ── CRAW-6 labels ─┬─ CRAW-7 label scene ─┬─ CRAW-8 label tree (after VIEW-3)
                                     │                      └─ CRAW-9 API ── CRAW-10 VLM
                                     ├─ CRAW-11 label materials ──────────────────┘
                                     ├─ CRAW-15 attributes across boolean
                                     └─ CRAW-20 export
CRAW-5 feature edges ── PATCH-89 feature arcs in the motorcycle graph (PATCH epic)
CRAW-17 research (after MESH-38; may spawn follow-on tickets)
```

Start immediately, in parallel: CRAW-1, CRAW-4, CRAW-5, CRAW-13.

## Ticket Lifecycle

Mark work with the CLI, never by hand-editing JSON:

```
python generate_board.py update CRAW-N --status IN_PROGRESS
python generate_board.py update CRAW-N --add-changes "..." --add-related-file path --add-blocked-by X --add-blocks Y
python generate_board.py mark done CRAW-N
```

`create` and `update` accept `--blocked-by` / `--blocks` (mirrored onto the other ticket),
`--unknown`, `--related-file` and repeatable `--subsystem`. If a change cannot be expressed,
extend `generate_board.py` first, then use it.

## Ring workflow (CRAW-21..25): define cuts once, inherit everywhere

The ten scans are the same crawfish, so part structure is shared. Rings (neck loops) and part
labels are authored once on a reference scan in surface coordinates and re-evaluated on every
aligned member. The geometric-selection ruling is what makes this portable: a ring is a list of
surface waypoints, never vertex ids.

1. Align the collection (CRAW-12 members, CRAW-16 `align_to_mesh`).
2. Propose rings on the reference scan from the TEASAR skeleton: branch regions -> boundary seed
   loops -> FlipOut tightening -> ranked by neckness (CRAW-22, on top of CRAW-21).
3. Review: accept/reject numbered candidates; add missing rings by three points -> Dijkstra ->
   FlipOut (CRAW-23). Name the regions between rings. Save `crawfish_parts.dsl`.
4. Transfer the same DSL to every member; waypoints re-snap, rings re-tighten; parts that fail to
   snap are reported ABSENT (CRAW-24). That is the bad back side, detected without vision.
5. Part gallery: per part, one contact sheet of every member's version; human or vision model
   picks the best (CRAW-25), written as `parts_choice.dsl`.
6. Assemble chosen parts (CRAW-16 consumes the choice file), symmetrize, quad layout with rings
   as forced arcs (PATCH-89), label from regions, rig with joints at ring centroids.

Agents never click: rings come from `/mesh/rings/list` rows and `ring_at_branch(branch, t)`;
adding a ring is `loop_through_points` fed with skeleton or centroid coordinates; only the
gallery step needs an image, and it gets a numbered Set-of-Mark sheet per decision.

FlipOut (Sharp & Crane 2020, CRAW-21) is the tightening primitive shared by steps 2, 3 and 4.
