# PATCH-37: Java port of metriko quad-layout pipeline — file plan

Mirror metriko's module structure under `ixdar.geometry.mesh.qgp.*`. Each metriko C++ module becomes a Java sub-package; per-module LOC estimate based on metriko's count + Java's higher line-density.

## Java package tree

```
ixdar-app/src/main/java/ixdar/geometry/mesh/qgp/
├── common/
│   ├── ComplexF.java           # 4×Complex helpers if not on classpath
│   ├── QgpTypes.java           # shared records (Cell, FaceLocal, etc.)
│   ├── Predicates.java         # robust geometry predicates (orient2d etc.)
│   └── Utilities.java          # math helpers, simplex of simplicity
│
├── solver/                     # sparse linear algebra + ILP wrappers
│   ├── SparseMatrix.java       # CSR or COO + Java-native or ojAlgo-backed
│   ├── SparseCholesky.java
│   ├── SparseLu.java           # for IGM iterative resolves
│   ├── GeneralizedEigen.java   # for KCPS smoothest-field eigensolve
│   ├── ComplexSparseMatrix.java # KCPS uses C-Hermitian — lift to 2×2 real if libs lack
│   └── IlpSolver.java          # facade over ojAlgo
│
├── hmesh/                      # half-edge mesh wrapper around ixdar.geometry.mesh.data.HalfEdgeMesh
│   ├── HMesh.java              # adapter — re-uses Ixdar HalfEdgeMesh
│   ├── HFace.java
│   ├── HEdge.java
│   └── HVert.java
│
├── vectorfield/                # KCPS cross field (Knöppel-Crane-Pinkall-Schröder 2013)
│   ├── BaseField.java
│   ├── FaceRosyField.java      # 4-RoSy field per face, smoothest-energy minimization
│   ├── SingularityFinder.java  # locate index ±1/4 cones
│   ├── CombedField.java        # combed (no rotational ambiguity) + matchings + seam
│   └── PrincipalAlignment.java # optional alignment to principal curvature directions
│
├── igm/                        # Integer-Grid Maps (Bommes 2013)
│   ├── SeamlessParameterization.java   # initial floating UV per face
│   ├── IterativeRounding.java          # round non-grid-snap UVs to nearest integer at singularities, re-solve
│   ├── InjectivityBarrier.java         # log-barrier penalty for fold-overs
│   ├── IterRoundingInit.java
│   ├── IterRoundingLoop.java
│   └── IterRoundingCommon.java
│
├── tmesh/                      # Motorcycle-graph T-mesh (Eppstein 2008 / Campen 2015 / Lyon 2021 modified)
│   ├── Motorcycle.java         # one trace
│   ├── MotorcycleGraph.java    # all traces + intersections
│   ├── TMesh.java              # cell decomposition: nodes + arcs + patches
│   ├── TNode.java
│   ├── TArc.java
│   └── TQuad.java
│
├── quantization/               # Campen 2015 + Lyon 2021
│   ├── Quantization.java       # main driver
│   ├── QuantizationConstraint.java
│   ├── QuantizationValidation.java
│   ├── QuantizationBasisLoop.java
│   ├── QuantizationWeight.java
│   ├── QuantizationEvaluation.java
│   └── QuantizationRelinearization.java # Lyon 2021 reparameterization (metriko's WIP file is the start)
│
├── qex/                        # QEx (Ebke 2013) quad mesh extraction
│   ├── QuadMeshExtractor.java
│   ├── QVert.java
│   ├── QEdge.java
│   ├── QFace.java
│   └── Sanitization.java
│
├── boundary/                   # PATCH-37h (skull has openings; metriko doesn't yet support boundaries)
│   ├── BoundaryCapper.java     # close holes with a fan triangulation pre-pass
│   ├── BoundaryUncapper.java   # restore holes post-quad-extraction
│   └── (alternative) FreeBoundaryQuantization.java — Lyon 2019 "Parametrization quantization with free boundaries"
│
└── QgpDecomposer.java          # public entry: ArrayMesh → quad mesh
```

## CLI verifiers (`ixdar-app/src/main/java/ixdar/cli/`)

```
VerifyCrossField.java       # stage 1 — cross field on a flat plane (expect zero singularity), torus (expect ±1/4 cones at handle), sphere (expect 8 ±1/4 cones)
VerifyIgm.java              # stage 2 — IGM injectivity on the cube + sphere; UV consistency at seams
VerifyMotorcycleGraph.java  # stage 3 — cell count on the cube (expect 6), torus (expect specific count from theory)
VerifyQuantization.java     # stage 4 — ILP feasibility + minimum-coarseness solution on synthetic torus
VerifyQex.java              # stage 5 — quad mesh count = quantization count on the torus
VerifyQgp.java              # end-to-end — Hand.obj should match metriko_quad's baseline output (within tolerance)
```

## Java tests (`ixdar-app/src/test/java/ixdar/geometry/mesh/qgp/`)

Tests live alongside implementations. Each stage gets unit tests that compare against:
- Synthetic cases with known closed-form answers (cube, sphere, torus)
- The metriko C++ baseline output on Hand.obj (saved in `tests/qgp/baseline-hand-*.txt`)

## Ground-truth baseline (regression target)

```
ixdar-app/src/test/resources/qgp/baseline-hand/
├── Hand.obj                # input
├── crossfield.bin          # face_count × 2 complex direction-field values
├── singular.txt            # list of singularity vertex/face indices + types
├── matching.bin            # per-edge index turn (0..3)
├── seam.bin                # cut-graph edges
├── parametrization.bin     # 3-corner UV per face
├── tmesh.json              # nodes, arcs, patches in JSON for diff-friendly format
├── quantization.txt        # per-arc integer length
├── quad-mesh.obj           # final output
└── timing.txt              # per-stage timing, useful for our own perf budget
```

Sub-agent #1 is generating this baseline now from `/tmp/metriko` running on Hand.obj.

## Maven deps (added to `ixdar-app/pom.xml`)

Hybrid pure-Java stack confirmed by survey:

```xml
<dependency>
    <groupId>org.ojalgo</groupId>
    <artifactId>ojalgo</artifactId>
    <version>54.x</version>
    <!-- backbone: sparse LU, ILP (IntegerSolver branch-and-bound + Gomory cuts),
         complex sparse storage, dense generalized EvD as fallback -->
</dependency>
<dependency>
    <groupId>com.googlecode.matrix-toolkits-java</groupId>
    <artifactId>mtj</artifactId>
    <version>1.0.x</version>
    <!-- specifically for ArpackSym: shift-invert Lanczos for the KCPS
         generalized eigenproblem A x = λ B x, sparse, pure Java.
         Calls into ojAlgo SparseQDLDL as the inner linear solver. -->
</dependency>
```

Both are pure Java, no native deps, no JNI. Compatible classpath.

### Why this combo
- ojAlgo lacks sparse Cholesky and sparse generalized EvD; MTJ has ArpackSym (Lanczos) which only needs a sparse SPD-system solver as a callback — ojAlgo's `SparseQDLDL` on a regularized Laplacian (`L + εI`) fills that role.
- Complex Hermitian KCPS systems get lifted to real 2N×2N block form (both libraries' sparse solvers are primitive-double only — same restriction for MTJ as ojAlgo, so this is unavoidable, not a library choice issue).
- ILP scale (T-mesh quantization, hundreds to a few thousand integer variables) sits comfortably inside ojAlgo's `IntegerSolver` capability.

## Sub-ticket sequencing (PATCH-38 through PATCH-45)

```
PATCH-38  Linear-algebra foundation (ojAlgo + MTJ ArpackSym, sparse + complex + ILP wrappers)   [BLOCKER]
PATCH-39  Cross field (KCPS)                  depends on 38
PATCH-40  Integer-Grid Maps                   depends on 38, 39
PATCH-41  Motorcycle-graph T-mesh             depends on 40
PATCH-42  Quantization ILP                    depends on 38, 41
PATCH-43  QEx quad mesh extraction            depends on 40, 42
PATCH-44  Lyon 2021 reparameterization        depends on 42 (refinement, not blocker)
PATCH-45  Boundary support                    can run in parallel with 39–44
```

Hand.obj and the metriko baseline get used as the **integration test for every sub-ticket** — each stage's output should match metriko's at floating-point tolerance, with documented exceptions where the thesis dictates a different algorithm (sub-agent #3 is producing that spec now).

Estimated total: **10–15 k LOC Java** + Maven deps. Multi-month effort. PATCH-37a (linear algebra) is the prerequisite for everything else.
