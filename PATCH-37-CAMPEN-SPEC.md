# PATCH-37: Campen 2014 thesis — algorithmic spec for Java port

This is the *authoritative spec* every PATCH-38..45 sub-ticket implements against. Source: `/tmp/quad-thesis.txt` (Marcel Campen, RWTH Aachen 2014, advisor Kobbelt). Where metriko's C++ deviates from the thesis, we follow the thesis.

The thesis bundles three published papers: stage 2 = [CBK12] *Dual Loops Meshing*, stage 3 = [CK14b], anisotropic geodesics = [CHK13], meshless = [CK11].

---

## Stage 1 — Cross field (Chapter 4 → PATCH-39)

**Goal.** Pick irregular node count + valences via singularities of a smooth cross field, principal-direction aligned where reliable.

**Formulation (Eq. 4.6 — angle-based, the implementation choice):**
```
min  Σ_{e_ij ∈ E}  ( θ_i − θ_j + κ_ij + m_ij · π/2 )^2
```
- `θ_i` ∈ ℝ per face: tangent angle in local reference frame.
- `κ_ij` constant: angular offset aligning frames of face i, j.
- `m_ij` ∈ ℤ on a face-cycle basis (one per spanning-tree edge per [BZK09]).
- Solver: greedy MI of [BZK12].

**Singularity index → node valence:**
```
1 − val(n_i)/4 = index_C(s_i),   index ∈ {±1/4, ±1/2, ...}
```

**Principal-direction alignment (Sec 4.2.2):** detect anisotropic regions, hard-fix `θ` to the principal direction in those triangles. Thesis explicitly rejects integrable / parameterization-level alignment as too expensive.

**Why angle-based, not vector-based [KCPS13, DVPSH14]:** vector forms restrict valences to {3,5} or admit zero-vector degeneracies. Angle-based always produces valid crosses with arbitrary valence.

**Failure modes (4.4):**
- Index ≥ 1 from concentrated Gaussian curvature → split into lower-index singularities per [BZK09, PZKW11].
- (val-3, val-5, genus-1) is the one configuration that satisfies (4.3) but admits no quad layout — merge them ([JT73, MPZ14]).
- Regular nodes are *ignored at this stage* — they emerge from separatrix crossings later. Node positions here are preliminary; stage 3 repositions.

---

## Stage 2 — Connectivity (Chapter 5 → PATCH-40)

**Idea.** Don't build separatrices (primal); build a dual graph of crossing loops on branched coverings, then dualize.

**Required dual properties (5.1.1):** Hard: D1 transversal intersections, D2 no triple points, D3 region valence ≥ 1, D4 disc topology. Soft: D5 near-orthogonal crossings, D6 principal alignment, D7 region valence reflects total curvature, D8 short and few.

**Branched coverings (5.1.3, [KNP07]):** M2 (2-sheet) and M4 (4-sheet) with branch points at singularities. Lift cross field to a single-valued direction field `F_D` on M4 (and line field `F_L` on M2). Loops constructed on M4, projected.

**Loop cost (Eq. 5.1):**
```
c_α(l) = ∫_0^b  cos²θ(s) + α² sin²θ(s)  ds
```
**`α = 30` is the working value.** α=∞ → exact field-following spirals; α=1 → field-oblivious geodesics; α<10 → unaligned bad loops; α>70 → discretization limits.

**Admissibility:** `θ ∈ [−π/4, π/4)` per loop. M2-simple = no two loops intersect on M2.

**Two structural theorems:**
- **5.1.1 Region valence-index:** For an M2-simple admissible arrangement, region R bounded by k loop intersections has `index_C(R) = 1 − k/4`.
- **5.1.2 Singularity separation:** Any region with `|index| > 1/4` containing multiple singularities can be split by adding admissible loops without violating M2-simplicity. (Construction: shadow-loops along existing loops, then cut+reconnect inside R.)

**Greedy loop selection (5.1.5) — MAXIMUM-first:**
```
A_0 = ∅
A_{i+1} = A_i ∪ { argmax_{l ∈ L_min(A_i)}  c_α(l) }
```
Securing long structurally-important loops first, before later loops constrain them.

**Discretization (5.2):**
- Anisotropic front propagation on M4 — uses **STVD with k=4** (see PATCH-38 prerequisites).
- Precompute meta-graph P of edge chains up to length k with weights; run plain Dijkstra on P.
- Discard graph edges violating `θ ∈ [−π/4, π/4)`.
- Whisker bookkeeping: each loop carries left/right half-edge sets; front vertices store one flag per existing loop indicating side; flag flips on whisker crossing — reject propagation across.
- Self-intersecting discrete loops: cut at intersection, alternately reverse-and-reconnect to a grazing (non-crossing) loop.
- **Separation Indicators (SIs):** discrete paths between singularity pairs, one per homotopy class. For genus g surfaces use `2g+1` SIs per pair via Erickson-Whittlesey [EW05] adapted to use TWO distance fields (one per endpoint). Plus Dijkstra shortest path for the trivial class when a≠b.
- **Heuristic I (loop cuts SI):** count crossings parity. Even crossings ambiguous → check pre-images on M4: >1 sheet ⇒ cut.
- **Heuristic II:** restrict to `2g+1` SIs per pair.
- **Lazy candidate evaluation:** in step 1 compute one candidate per SI; the largest is added; later only re-compute candidates for SIs whose previous candidate is no longer M2-simple.
- **Post-validation flood-fill:** within regions of A; if two singularities still connect, add the connecting path as a new SI.

**Boundaries (5.3):** propagate two opposing fronts on different M4 sheets; whichever happens first — they meet (loop) or both reach boundary (boundary-to-boundary curve).

**Feature curves (5.3):** add as directional constraints in cross field [BZK09]. Lift each feature curve onto the M2 sheet aligned with its tangent.

**Layout primalization (5.2.3):** pick representative vertex `ν(h)` per dual region. For each adjacent region pair compute face-chain `γ(a)` via BFS on dual mesh graph (restricted to two adjacent regions, allowed to cross the separating loop exactly once). `(ν, γ)` is the layout output passed to stage 3.

---

## Stage 3 — Embedding (Chapter 6 → PATCH-41 + ...)

Input: layout graph G with `(ν, γ)` from stage 2 (or manual sketch). Three substages.

### 3a. Guiding-field topology (6.3.1)
Cross field C topologically compatible with L has `2g + b + s − 1` DoFs. Discretely: prescribe per-face-cycle turning numbers.
- For singularity at node h: face-cycle clockwise around `ν(h)` gets `t = −val(h)/4`.
- For each homology generator cycle `c = a_1...a_n`, build face cycle `γ(c)` by concatenating `γ(a_i)` and inserting clockwise face-fans at nodes; `t = (n − n_a)/4` where `n_a` = #arcs the face-cycle crosses emanating from involved nodes.
- Boundary cycles: choose orientation so boundary lies right of c.

Run the Ray et al. **zipping algorithm [RVLL08]** (or [CDS10]) to convert turning numbers → period jumps.

### 3b. Guiding-field smoothness (6.3.2)
Given period jumps fixed, find smoothest C interpolating sparse direction constraints. Linear system [RVLL08] with **soft constraints, penalty 100**. If which-of-4 directions is not pre-assigned, add integer vars + run MI solver once to assign.

### 3c. Aligned parametrization (6.4)
Per-corner piecewise-linear (u,v); per-triangle gradients. Energy (Eq. 6.1):
```
E = Σ_{t ∈ T} ( ||∇_t u − u_t||² + ||∇_t v − v_t||² ) · A_t   → min
```
where `(u_t, v_t)` are the cross-field directions in triangle t.

**Transitions across edges (Eq. 6.2):** `(u,v)_t = R(r_st)(u,v)_s + (j_st, k_st)`. `r_st` fixed a priori from period jumps; `(j_st, k_st)` are real (NO integer rounding here, unlike MIQ). Eliminated as linear constraints. **Single linear solve.**

### 3d. Node connection constraints (6.4.1)
For arc a from face `γ(a)⊢` to `γ(a)⊣` with composite transition `τ(a)`:
```
(τ(a)(u,v)_s)_{λ(a)} = ((u,v)_t)_{λ(a)}    (Eq. 6.3)
```
with `λ(a) ∈ {u, v}`.

**Consistent labeling (combinatorial first, geometric second).** Given rotation system (σ, θ) of L:
```
λ̄(σ(a))   = τ_σ(a) · λ̄(a)               (6.4)
λ̄(θ(a))   = Rot(π/2) · τ_θ(a) · λ̄(a)    (6.5)
```
Then ONE global geometric flip: pick {ū,v̄} ↔ {u,v} to maximize alignment of ū-arcs to u-direction.

### 3e. Embedding extraction (6.4.2)
Trace iso-parametric curves from each node = arc embeddings. Per-patch maps `f_p: [0,w_p]×[0,h_p] → patch`: BFS over triangles propagating transitions to a common chart (FIFO queue, accumulating `f ∘ f_st`).

### 3f. Node optimization (6.5) — alternating outer loop
A) Solve for (u,v) with nodes fixed (linear).
B) Move nodes via gradient descent with (u,v) fixed.

**Gradient (Eq. 6.6):** `d(h) = −(∂E/∂x, ∂E/∂y)` in a local 2D chart (geodesic polar map [WW94] flattening 1-ring with uniform angle scaling so inner angles sum to 2π).

**Efficient estimator d̃ (6.6.1).** Differentiate only direct dependence — sum over 1-ring only. Empirically: avg 4° angular deviation, magnitude ~1.5× too big → use **`(2/3) d̃`** in practice.

**Step length (6.6.2):** `l(h) = α · ||d(h)||` with **`α = 0.75`**. Voronoi clamp: clamp movement to current cell of a Dijkstra-based node Voronoi.

**Node movement (6.5.1).** Trace straightest geodesic [PS98] of length l(h) from current vertex. New point p' generally not on a vertex → relocate ONE incident vertex of the face containing p' to p' (avoids inserting new vertices/changing connectivity). Pick the relocate-vertex to minimize geometric alteration; exclude vertices holding other nodes or on sharp features. If singularity moved, update period jumps along edge chain `c` from old to new vertex; update all `τ(a)` and `λ(a)` for incident arcs.

**Outer-loop termination (6.5.1):** stop when next step increases residual; output prior step. Then halve step size — **max 5 halvings or 25 total steps**.

**Anti-fold step (last iteration):** linear tri-sector orientation constraints from [BCE*13]. Stiffening trades distortion for fold removal.

**Selective optimization (6.5.2):** to fix arc `a`: add constraints not just at endpoints but at every edge crossing `(u,v)` parameter equal across the arc.

### 3g. Optional extensions (6.4.3)
- Anisotropic E with ratio 10 [BZK09].
- Sizing field reducing curl [RLL*06] — used in all thesis examples.
- Quad-mesh integers (6.7): first relax, solve nodes; then solve integer (j,k) and node (u,v) as multiples of q [BZK09]; then re-relax-optimize nodes with fixed integers.

---

## Anisotropic geodesics — Short-Term Vector Dijkstra (Chapter 9 → PATCH-38)

**Where used.** Stage 2 minimal-loop computation; separation indicators; node Voronoi cells in stage 3.

**STVD update (9.6):**
```
update_dist(v, w):
    tmp = w.pred
    w.pred = v
    dist = min over i ∈ {1..k} of:
        w.pred^i.dist  +  ℓ_g( Σ_{j=1..i} (w.pred^{j-1}, w.pred^j) )
    w.pred = tmp
    return dist
```
where `w.pred^{i+1} = w.pred^i.pred`, `w.pred^0 = w`.

**Vector summation:** unfold k-edge chain into a common 2D plane preserving edge lengths and 1-ring angles. Sum `E = Σ ê_j`. Apportion E among edges via signed orthogonal projection.

**Key params:**
- **k = 4** for stage 2 (sufficient for visually smooth elastica).
- **k = 10** for high anisotropy (γ = 20+, including the α=30 stage-2 metric).
- k = 1 ⇒ classical Dijkstra (overestimates); k → ∞ ⇒ vector-valued (underestimates, ignores holes).

**Efficiency optimization for stage 2:** precompute meta-graph P with all edge chains up to length k and their weights; then run plain Dijkstra on P.

**STVD does NOT need iDT and does NOT need triangle-inequality fixing** — main practical advantage.

---

## Meshless geodesics (Chapter 10) — out of scope for v1

Skip until needed. Used only for defective input meshes (gaps, polygon soup, NURBS soup). The skull and hand are clean.

---

## Practical robustness (collected)

- Stage 1: index ≥ 1 → split. Single (val-3, val-5, genus-1) edge case → merge.
- Stage 1: noise-free input assumption — use scale-aware fields [RVAL09, ECBK14] if needed.
- Stage 2: heuristic-I parity ambiguous (even count) → check M4 pre-images; >1 sheet ⇒ cut.
- Stage 2: post-validation flood-fill catches heuristic failures.
- Stage 2: assume "no two singularities adjacent on a mesh edge" — enforce by edge-splitting.
- Stage 2: discrete loops can self-cross → cut-reverse-reconnect to grazing.
- Stage 2: α=30 sweet spot.
- Stage 3: soft direction constraints with penalty 100.
- Stage 3: node-movement step length factor 0.75; halve step size 5× max for fine-tuning.
- Stage 3: at most 25 total relocation steps.
- Stage 3: Voronoi clamp on each node prevents pathological jumps.
- Stage 3: linear tri-sector anti-fold [BCE*13] applied only on LAST iteration.
- Stage 3: when nodes drift together (parametric distance < ε), poly-chord collapse.
- Stage 9: STVD avoids iDT.

The thesis does NOT use Simulation-of-Simplicity. Tie-breaking comes from construction-level guarantees.

---

## Architectural decision flagged by the thesis

Sec 11 outlook: thesis itself recommends "an automatic algorithm based on elastica loops [Chapter 8] instead of cross-field-guided loops [Chapter 5]" as a future direction — i.e., the elastica-based stage 2 from Chapter 8 may be a cleaner foundation than the M4-loops stage 2 from Chapter 5. **For our v1 we follow the M4-loops version (Ch. 5)** since metriko has matching scaffolding; revisit elastica in a future ticket once the M4 path is working.
