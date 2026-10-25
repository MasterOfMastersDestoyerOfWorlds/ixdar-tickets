# Crawfish ring-scene load times (2026-10-25)

Method: `ixdar-cli run-scene --scene ring-tool --property ixdar.model=<dsl> --keep-alive` from each build's
own CLI, one JVM at a time. Every build loads the same pinned DSL copies (master 53df83c8's
IMG_4111.dsl and IMG_4112.dsl with the path written as `/home/acw/crawfish/...`). The IMG_4111 DSL has
had 54 spline_ring + 28 spline_cut statements since fa22085d (82 rings; only 2 lines changed).
IMG_4112.dsl has only load_mesh + repair_mesh (no rings yet).

- **wall** = launch to the CLI seeing sceneReady. It includes the JVM and GL start-up. The CLI
  polls health every 1.0 s (`run_scene.py`, `time.sleep(1.0)`), so walls only move in about 1 s steps.
  Run "a" of each export also includes uv building the CLI venv (or a Maven compile in a worktree),
  so that run is left out of the steady numbers.
- **graph / per-node** = `mesh-dsl-timing` (ms), averaged over all reps (4 reps for the IMG_4111 week-ago/MESH-60/master builds).
- **adopt** = the ring tool's "gave 82 graph ring(s) anchors in N ms" (`ui-state`). This runs in the first
  frame *after* sceneReady (`RingTool.perFrame -> adoptGraphRings`).
- **regions** = the `[regions] ... built and drawn in N ms` log line (only TOOL-49 builds regions at load). It
  also runs after sceneReady. **regions_at** = launch to that log line.
- Every scene was stopped with `ixdar-cli shutdown`, which confirmed `exited: true`. No ixdar JVM of mine was left running.

## IMG_4111 (82 rings)

| build | wall to ready, s (steady reps) | graph total | load_mesh | repair_mesh | surface_metric | surface_creases | 54 spline_ring | 28 spline_cut | adopt (after ready) | regions (after ready) | launch to regions, s |
|---|---|---|---|---|---|---|---|---|---|---|---|
| fa22085d CRAW-49 (10-17, a week ago) | 7.39, 7.56, 7.39 | 5920 | 276 | 1241 | 188 | 1678 | 2237 | 252 | 1864 | - | - |
| 1cf88f3e (MESH-60 parent, 10-18) | 8.51 | 6096 | 283 | 1308 | 177 | 1686 | 2241 | 352 | - | - | - |
| 2cb47521 MESH-60 Retire ArrayMesh | 8.41, 8.53, 8.42 | 6666 | 306 | 1294 | 204 | 1859 | 2534 | 420 | 1830 | - | - |
| 53df83c8 master (when task given) | 9.54, 8.42, 8.43 | 6812 | 322 | 1480 | 185 | 2024 | 2505 | 246 | 1915 | - | - |
| tool-49 (= TOOL-49, landed as master 305af304/b1ce1bbf at 15:07 during this run) | 8.42, 8.38, 8.41 (+prof 8.38) | 6819 | 317 | 1497 | 185 | 2013 | 2488 | 271 | 1998 | 1914 (1280-2479) | 12.57, 11.22, 12.05 (prof 10.92) |
| craw-81 (repair orientation, two rays per face) | 14.46 (run a 25.2 incl. compile) | 12004 (a: 15816) | 319 | **6534** (a: 9411) | 213 | 1822 | 2662 | 404 | - | - | - |

## IMG_4112 (load + repair only)

| build | wall, s | graph | load_mesh | repair_mesh | regions (after ready) |
|---|---|---|---|---|---|
| fa22085d | 4.24 (a), 3.20 | 1962 | 300 | 1662 | - |
| 1cf88f3e | 3.20, 3.21 | 1990 | 268 | 1722 | - |
| 2cb47521 | 3.19, 3.20, 3.21, 3.19 | 1986-2022 | 279-289 | 1706-1731 | - |
| a1c5eb28 (MESH-61 parent) | 3.19, 3.19 | 1976 | 295 | 1680 | - |
| eea66998 MESH-61 | 4.18, 4.19 | 2253 | 299 | **1954** | - |
| 7bc04bad / 3b23503b MESH-65 | 4.18-4.19 | 2174 / 2207 | 294 | 1880 / 1914 | - |
| 53df83c8 master | 4.18, 4.18, 4.17 | 2183-2229 | 282-300 | 1900-1928 | - |
| tool-49 (new master) | 4.18, 4.20 | 2276 | 320 | 1955 | 961 (regions at 6.65 s) |
| craw-81 | 8.24, 8.19 (a 13.46) | 6336-6583 (a 10545) | 291-349 | **6044-6232** (a 9964) | - |

## Attribution

Master compared with a week ago (fa22085d to 53df83c8): the graph is about +0.9 s and sceneReady
about +1 s (one poll step).
- **MESH-60 2cb47521 (Retire ArrayMesh): +0.57 s graph.** spline_ring +0.29, spline_cut +0.07,
  surface_creases +0.17, metric/load +0.05. The parent 1cf88f3e matches fa22085d (+0.18 s, noise). Every
  downstream node now reads a HalfEdgeMesh built by the loader, through its id tables
  (`IntIdTable.get`, `HalfEdgeMesh.vertexPosition`, `faceIdAt` show in the profile), where it used to
  read flat ArrayMesh arrays.
- **MESH-61 eea66998 (one attribute and provenance mechanism): +0.25 s repair_mesh**, bisected on
  IMG_4112: a1c5eb28 is 1680 ms, eea66998 is 1954 ms, and every later commit is the same. On IMG_4112 it
  pushes ready across a poll step (3.2 s to 4.2 s). repair now records per-vertex source provenance on the
  output HalfEdgeMesh (`recordSources`, an attribute layer) and carries slots through the new
  `SlotCarry` path, instead of rebuilding the tag masks.
- Nothing else in fa22085d..53df83c8 moves a phase by more than run-to-run noise (±0.2 s).

What actually makes the scan take 8-12 s, measured on the review branches and on the new master:
- **craw-81 (repair orientation with two rays per face): +4.6 to 5 s in repair_mesh**, steady state
  1.5 s to 6.0-6.6 s (9.4-10 s on the first run after a compile). It costs the same on every scan, rings
  or not: IMG_4112 goes from 3.2-4.2 s to 8.2 s, IMG_4111 to 14.5 s to ready. Profile (IMG_4112):
  `MeshRepair.orient` is 78% of repair (469/599 samples) and `TriangleBvh.facing` is 73% of repair (436). The
  self-time hot spots are `TriangleBvh.positiveAxisRayCrossings` 13%, `TriangleGeometry.outsideSpan` 11% and
  `TriangleGeometry.positiveAxisRayCrosses` 10% of all samples. orient() now builds a BVH over
  all ~985k triangles and casts a parity ray both ways from **every** face's centre (`facing(face)` in a loop
  over all faces). The BVH build is only 2%; the per-face ray casts are the cost. Casting rays only for one
  or a few faces per consistently wound patch (it already union-finds the patches) would remove nearly all of it.
- **TOOL-49 (regions built at load, now on master): +1.3 to 2.5 s after sceneReady** on IMG_4111
  (216 regions), and about 1 s even with no rings (IMG_4112, 2 regions). Launch to regions is now 11-12.6 s.
  It is the first frame's `RegionLayer.rebuild`: a fresh `RingRegions` (`update` runs a whole-mesh slot table and flood),
  then `measureFlatness` (whole-mesh passes), colouring, label resolution and the explosion measure.
- **Ring adoption, after sceneReady, unchanged since CRAW-35 (2026-10-03): 1.8-2.0 s** in every build.
  `RingTool.adoptGraphRings` traces every ring a second time (`AuthoredSplineRing.trace` for each spline_ring,
  `bisector.traced` for each spline_cut) after the graph has already traced it. It is not a regression, but it
  is ~2 s of what the user waits for after "ready".

So the "8-12 s" is: ~8.4 s to sceneReady (fa22085d: 7.4 s) + ~2 s adoption + ~1.3-2.5 s regions (TOOL-49) = 11-12.6 s
to a settled scene on today's master. With craw-81 on top, ready alone is 14.5 s. A week ago IMG_4111 was
already 7.4 s to ready + 1.9 s adoption. The 4-5 s figure matches IMG_4112-like scans with few or no rings
(3.2 s a week ago, 4.2 s now).

## Per ring

Truncated copies of IMG_4111 (the first N ring statements; the cuts all come after ring 54, so r10/r20/r40 are
spline_rings only). Graph ms:

| rings | build | spline_ring total | per-ring mean | graph total | wall to ready | regions build | launch to regions |
|---|---|---|---|---|---|---|---|
| 10 | 53df83c8 | 1410 | 141 | 5566 | 7.35 | - | - |
| 10 | tool-49 | 930 | 93 | 5052 | 6.50 | 982 (11 regions) | 7.84 |
| 20 | 53df83c8 | 1557 | 78 | 5495 | 7.28 | - | - |
| 20 | tool-49 | 1403 | 70 | 5517 | 7.29 | 512 (18 regions) | 8.02 |
| 40 | 53df83c8 | 2082 | 52 | 6090 | 8.35 | - | - |
| 40 | tool-49 | 2095 | 52 | 6036 | 7.31 | 1512 (73 regions) | 10.25 |
| 82 (54 ring + 28 cut) | 53df83c8 (4 reps) | 2505 + 246 | 34 | 6812 | 8.4-9.5 | - | - |
| 82 | tool-49 (4 reps) | 2488 + 271 | 34 | 6819 | 8.4 | 1280-2479 (216 regions) | 10.9-12.6 |
| 10 / 20 / 40 | craw-81 | 1120 / 1404 / 2106 | | 9939 / 10287 / 11019 | 12.3 each | - | - |

**Not linear in ring count.** The cost is front-loaded and set by each ring's complexity:
- The first ten rings cost 0.9-1.4 s: JIT warm-up, plus the heavy crease-mode rings. ring_00 is 150 ms;
  ring_01 is 250 ms; ring_04 (11 anchors, crease mode) is 290-490 ms; ring_06 is 75 ms.
- From ring 11 on, the median spline_ring is 8-10 ms and the median spline_cut 6-11 ms. The mean is
  ~31 ms because a few multi-anchor crease rings (ring_15, ring_22, ring_30, 5 anchors each) cost 100-300 ms.
- Marginal cost: ~30 ms/ring for rings 11-40, ~16 ms/ring for rings 41-82. Graph total: 10 rings 5.1-5.6 s,
  40 rings 6.0 s, 82 rings 6.8 s. So each ring costs about 20-30 ms in the graph, the same again in adoption, and a little in regions.
- Adoption (82 rings) is 1.8-2.0 s, about the same as the graph's own ring nodes (~2.75 s cold, warm by then).
  That is about 23 ms/ring of pure duplicate work.
- The regions build does not scale with rings either: 0.5-1.5 s for 10-40 rings, 1.3-2.5 s for 82. It is a whole-mesh
  fixed cost (987k faces) with GC noise.

Profile of the 82-ring load on new master (tool-49 worktree, `--profile`, cpu, 2423 samples; 1032 of them on
the main thread, the rest mostly JIT/GC threads in libjvm.so at 54% of all samples):

| frame (inclusive) | samples | share of all | share of main thread |
|---|---|---|---|
| NodeGraphRuntime.evaluate (the graph) | 642 | 26.5% | 62% |
| AuthoredSplineRing.trace (graph rings + adoption re-trace) | 385 | 15.9% | 37% |
| SplineRingNode.evaluate | 227 | 9.4% | 22% |
| SurfaceCreases.of (surface_creases; 131 of it SemanticPatchDecomposer.computeEdgeDihedrals) | 197 | 8.1% | 19% |
| RingTool.perFrame -> adoptGraphRings (after ready) | 183-184 | 7.6% | 18% |
| MeshRepair.build | 146 | 6.0% | 14% |
| RegionLayer.rebuild (after ready) | 124 | 5.1% | 12% |
| RingRegions.update (self 92) | 99 | 4.1% | 10% |
| FlipGeodesics.shorten | 72 | 3.0% | 7% |
| Dijkstra.settle | 64 | 2.6% | 6% |
| SplineCutNode.evaluate | 23 | 0.9% | 2% |
| SurfaceSpline.of | 18 | 0.7% | 2% |
| RingRegions.measureFlatness | 13 | 0.5% | 1% |
| EdgeMarks.fingerprint + EdgeMarks.<init> | 13 + 7 | 0.8% | 2% |

Top self-time frames: AuthoredSplineRing.trace 4.7%, RingRegions.update 3.8%, computeEdgeDihedrals 1.8%,
Dijkstra.settle 1.5%, Double.compare 1.3%, HalfEdgeMesh.vertexPosition 1.1%, PrincipalDirectionField.oneRings 0.9%,
SurfaceGeodesics.seedWalk 0.8%. Flame graph: `/tmp/claude-1000/-home-acw-Code-ai-workspace/9efaa0b3-c829-4d74-bd31-fc243b81c6fe/scratchpad/perf/logs/profile-tool-49-4111.html`; the craw-81 repair profile is
`/tmp/claude-1000/-home-acw-Code-ai-workspace/9efaa0b3-c829-4d74-bd31-fc243b81c6fe/scratchpad/perf/logs/profile-craw-81-4112.html`. All run logs, timings and ui-state captures are in that `logs/` directory.

### What each ring does at load that touches the whole mesh

- **Its trace is already local.** `SurfaceGeodesics.seedWalk` is an A* Dijkstra with a search budget toward the
  next anchor. `FlipGeodesics.shorten` flips only along the path, and `IntrinsicTriangulation` keeps sparse
  overlay flips that are discarded per geodesic. What is whole-mesh per ring is **allocation and scans**, not search:
  - `SurfaceGeodesics.over(metric)` makes a new engine per ring, and its `Dijkstra.start` allocates
    distance/via/visit arrays of `vertexIdBound` (~494k) the first time each engine runs. That is 82 times at load,
    and 82 more times in adoption.
  - `SurfaceSpline.of` allocates `boolean[edgeIdCeiling]` (~1.48M) for marks, plus a second "snapped" array of
    the same size.
  - `EdgeMarks(boolean[], gaps)` scans the whole mask twice to count it and list it. `SplineRingNode` allocates
    `boolean[edgeCount]` for the SELECTION output. `EdgeMarks.fingerprint` scans masks again.
  - Each costs ~1-3 ms. Over 82 rings × (graph + adoption) that is roughly 0.3-0.5 s, plus GC pressure
    (part of the 54% in libjvm). They could be limited to the ring's own edges: store ring marks as
    the edge-id list (`markedEdgeIds`) with a lazily built mask, and share one Dijkstra across rings.
- **The edge-mark pass in RingRegions.update** scans every new ring's full edge-id mask twice
  (`for edgeId < scanned`), which is O(E) per ring. It could read the ring's edge-id list directly.
- **The region flood.** At load the build is fresh, so one whole-mesh pass is needed (slot table, face areas, flood
  of 987k faces). It is done once, not per ring. Per ring after that (an edit), `update` already re-floods only the
  components beside edges that changed their wall, which is "only the region the ring divides". Two parts
  are still O(F) per edit, though: clearing a component loops over all faces, and the seed loop of the
  flood scans all faces. `measureFlatness` also does `Arrays.fill` over all faces and two more all-face
  loops on every call, even when it re-measures only the changed regions. These could be limited to the split region
  if each region kept its face list. The flood inside a region is already bounded by the region.
- **Adoption** (`RingTool.adoptGraphRings`): re-tracing every ring the graph already traced is the largest
  per-ring cost outside the graph, ~23 ms/ring and 1.9 s at 82 rings. It could take the `SurfaceSpline` the graph
  stored under the ring's label (the `SurfaceSpline.SLOT` slot) together with the snapped anchors, and re-trace a ring only when it is edited.
- **Not per ring:** surface_creases (2.0 s, whole-mesh dihedrals and the principal-direction field) and repair_mesh
  (1.5 s; 6+ s with craw-81) are fixed costs per scan, the same with 10 rings or 82.
