# MESH Epic Context -- Read This First

This document gives a fresh agent everything needed to work on a MESH ticket without extensive codebase search.

## What We Are Building

A **hierarchical procedural modeling workbench** -- think "Spore's creature editor on steroids." Every 3D mesh is a parametric recipe of composable nodes (the DSL). Nodes are organized in Blender-style node groups that map to semantic parts (e.g., Hand > Thumb > Fingernail). Users can zoom into any level of the hierarchy, tweak parameters via sliders, and see live updates. LLMs can generate and modify node trees programmatically via JSON.

The system replicates Blender's Geometry Nodes in Java within the Ixdar engine, using the existing annotation processing, scene, and automation infrastructure.

## Architecture Decisions

- **Half-edge mesh** as the core data structure (O(1) adjacency queries)
- **MeshNode interface** with typed input/output ports and `evaluate(NodeContext)`
- **Node groups** for composition -- subgraphs collapse into named nodes with exposed parameters
- **Push-based evaluation** -- topological sort, evaluate all nodes in order
- **JSON serialization** of node graphs (the DSL that LLMs generate)

## Key Codebase Patterns You Must Follow

### Annotation-Driven Registration

All mesh nodes use `@MeshNodeAnnotation(id = "...")` for automatic discovery. The annotation processor (`MeshNodeRegistry`) generates a `MeshNodeRegistry_MeshNodes` class with a `Map<String, Supplier<? extends MeshNode>>` at compile time.

**Pattern**: See how `@GeometryAnnotation`, `@CommandAnnotation`, and `@SceneAnnotation` work -- they all extend `RegistryProcessor` and follow the same structure.

**Key files**:
- `annotations/src/main/java/ixdar/annotations/meshnode/MeshNodeAnnotation.java` -- the annotation
- `annotations/src/main/java/ixdar/annotations/meshnode/MeshNodeRegistry.java` -- the processor
- `annotations/src/main/java/ixdar/annotations/meshnode/MeshNode.java` -- the interface (needs redesign per MESH-2)
- `annotations/src/main/java/ixdar/annotations/RegistryProcessor.java` -- the base processor class

### Scene Lifecycle

Scenes extend `Canvas3D` (or `Scene` which extends `Canvas3D`). Lifecycle: `initGL()` → `drawScene()` per frame → `shutdown()`.

**To create a new scene**: Use the CLI scaffolder:
```bash
uv run ixdar-cli new-scene \
  --name MeshNodeViewerScene --id mesh-viewer \
  --subfolder mesh --display-name "Mesh Node Viewer" \
  --base Scene --camera 3d --maven-profile mesh-viewer
```

This generates the scene class, VS Code launch config, and Maven profile.

**Key files**:
- `ixdar-app/src/main/java/ixdar/scenes/ui/IcosphereSavePointScene.java` -- 3D scene example
- `ixdar-app/src/main/java/ixdar/scenes/anatomy/ModelLoadScene.java` -- model loading scene
- `annotations/src/main/java/ixdar/annotations/scene/SceneDrawable.java` -- base class
- `.cursor/commands/scenes-new-scene.md` -- scaffolder docs

### Rendering Pipeline

Vertex layout: **8 floats per vertex** (3 position + 3 normal + 2 UV). Uses `MeshShader` with uniforms: `model` (mat4), `view` (mat4), `projection` (mat4), `solidColor` (vec4), `lightDir` (vec3), `useTexture` (bool), `emissiveColor` (vec3), `emissiveStrength` (float), `rimStrength` (float).

**Key files**:
- `ixdar-app/src/main/java/ixdar/graphics/render/shaders/MeshShader.java`
- `ixdar-app/src/main/java/ixdar/graphics/render/shaders/VertexArrayObject.java`
- `ixdar-app/src/main/java/ixdar/graphics/render/shaders/VertexBufferObject.java`
- `ixdar-app/src/main/java/ixdar/graphics/render/model/AssimpModelRuntime.java` -- good reference for GPU upload pattern

### Camera System

`Camera3D` owns `position`, `target`, `view` matrix, `yaw`, `pitch`, `fov`. Has `updateViewFirstPerson()` for look-at rendering and `drag(dx, dy)` / `mouseMove(...)` for interactive control. For orbit camera (needed for mesh viewer), compute camera position as `target + spherical(azimuth, elevation, distance)`.

**Key files**:
- `ixdar-app/src/main/java/ixdar/graphics/cameras/Camera3D.java`
- `ixdar-app/src/main/java/ixdar/platform/input/MouseTrap.java` -- mouse handling

### Automation CLI (Validation)

The automation system has a Java HTTP server (`AutomationApiServer`) running inside the game and a Python CLI (`ixdar_cli.py`) outside. The server exposes endpoints for health, UI state, screenshots, input injection, and shutdown. The CLI calls these endpoints.

**For mesh node validation**, use the same pattern as the Blender CLI in `/home/acw/Code/Blender-Procedural-Human/tools/`:
1. Launch the scene: `mvn -P mesh-viewer`
2. Wait for health: `uv run ixdar-cli health`
3. Load a node graph: `uv run ixdar-cli mesh-load-graph --file graph.json`
4. Validate geometry: `uv run ixdar-cli mesh-validate`
5. Take a screenshot: `uv run ixdar-cli screenshot`

**Key files**:
- `ixdar-app/src/main/java/ixdar/platform/automation/AutomationApiServer.java` -- HTTP routes
- `ixdar-app/src/main/java/ixdar/platform/automation/AutomationRuntime.java` -- runtime state + uiState()
- `tools/ixdar-automation-cli/ixdar_cli.py` -- CLI entry point
- `tools/ixdar-automation-cli/automation_client.py` -- Python HTTP client

### Platform Abstraction

All GL calls go through `Platforms.gl()` which returns a `GL` interface. Implementations: `LwjglGL` (desktop), `WebGL` (web), `HeadlessGL` (tests). Never call LWJGL directly.

**Key files**:
- `ixdar-app/src/main/java/ixdar/platform/Platforms.java`
- `ixdar-app/src/main/java/ixdar/platform/gl/GL.java`

## Things That Will Surprise You

1. **The current `MeshNode` interface is wrong.** It mirrors `TerminalOption` (with `usage()`, `desc()`, `fullName()`, etc.) because it was copied from the command system. MESH-2 redesigns it to have input/output ports. Until MESH-2 is done, don't implement the current interface -- it will be replaced.

2. **`Icosphere` is annotated with `@MeshNodeAnnotation` but doesn't implement `MeshNode`.** The annotation processor would generate a broken registry entry. This needs to be fixed in MESH-2 or MESH-6.

3. **`FaceState` constructor parameter order is misleading.** The constructor is `(index, normal, rotation, position)` but the `Icosphere` class passes `(index, center, rotation, normal)` -- the arguments are swapped. This is a latent bug but doesn't cause visible issues because `IcosphereRuntime.createFaceHandle` uses `Face` directly.

4. **Never allocate `ByteBuffer.allocateDirect()` per frame.** This is a critical performance rule. Cache reusable buffer fields and `clear()` + reuse them. See the learning at `.cursor/docs/ai-learnings/bytebuffer-allocatedirect-per-frame.md`.

5. **The `ModelRuntime` interface requires a file path** (`loadFromAssetRepo`). Our mesh runtime won't load from files -- it takes a `HalfEdgeMesh` directly. Don't implement `ModelRuntime`; create a new `HalfEdgeMeshRuntime` class.

6. **Scenes own their mouse/key handlers.** To customize input (e.g., orbit camera), create a `MouseTrap` subclass and set it on the canvas. See `IcosphereSavePointScene.IcosphereMouseTrap` for the pattern.

7. **The automation API `uiState()` is scene-specific.** It currently has special handling for `TradeScene`, `MainScene`, and `IrregularGridScene`. The mesh viewer scene needs its own section in `uiState()` exposing mesh stats (vertex count, face count, bounding box, etc.) for CLI validation.

8. **Import rules are strict.** Imports always go at the top of the file. Never use fully-qualified class names inline to work around import conflicts. The only exception is two classes with the same name used in the same file.

9. **The `annotations` module is a separate Maven module** from `ixdar-app`. Interfaces and annotations that nodes implement must live in the `annotations` module. Node implementations live in `ixdar-app`.

## Where Things Go

| What | Where |
|------|-------|
| MeshNode interface, ports, annotation | `annotations/src/main/java/ixdar/annotations/meshnode/` |
| Half-edge mesh data structure | `ixdar-app/src/main/java/ixdar/geometry/mesh/` |
| Curve data structure | `ixdar-app/src/main/java/ixdar/geometry/curve/` |
| Node implementations | `ixdar-app/src/main/java/ixdar/geometry/mesh/nodes/{primitives,math,operations,control,input,topology,attributes,instances,sampling,sdf,conversion,utility}/` |
| Node graph evaluation engine | `ixdar-app/src/main/java/ixdar/geometry/mesh/eval/` |
| GPU mesh runtime | `ixdar-app/src/main/java/ixdar/graphics/render/model/HalfEdgeMeshRuntime.java` |
| Viewer scene | `ixdar-app/src/main/java/ixdar/scenes/mesh/MeshNodeViewerScene.java` |
| Automation API extensions | `ixdar-app/src/main/java/ixdar/platform/automation/AutomationRuntime.java` |
| CLI validation commands | `tools/ixdar-automation-cli/mesh_validation.py` |
| CLI entry point updates | `tools/ixdar-automation-cli/ixdar_cli.py` |
| UI widgets (dropdown, sliders, tree) | `ixdar-app/src/main/java/ixdar/gui/ui/` |
| Docker environment | `docker/` at repo root |

## How to Validate Your Work

Every ticket should be validated through the automation CLI, not just by compiling:

1. **Compile**: `mvn -DskipTests compile` from `ixdar-app/`
2. **Launch**: `mvn -P mesh-viewer` (starts the mesh viewer scene)
3. **Health check**: `uv run ixdar-cli health`
4. **Mesh state**: `uv run ixdar-cli mesh-state` (returns vertex/face counts, bounding box)
5. **Screenshot**: `uv run ixdar-cli screenshot --out tmp/test.png`
6. **Shutdown**: `uv run ixdar-cli shutdown`

For node-specific validation:
- **Load a graph**: `uv run ixdar-cli mesh-load-graph --file path/to/graph.json`
- **Validate geometry**: `uv run ixdar-cli mesh-validate` (checks watertight, not degenerate, correct normals)

## Dependency Order (What to Build First)

```
MESH-5 (Scene + orbit camera) ─── can start immediately, hardcoded test cube
MESH-31 (Docker environment) ─── can start immediately, parallel infra
MESH-1 (Half-edge mesh) ──────── can start immediately, core data structure
MESH-30 (Automation CLI) ─────── after MESH-5, adds mesh endpoints + CLI commands
MESH-4 (GPU runtime) ─────────── after MESH-1, integrates into scene
MESH-2 (MeshNode interface) ──── after MESH-1
MESH-3 (Eval engine) ─────────── after MESH-2
Then all node tickets in parallel (MESH-6 through MESH-29)
```

## Coding Standards Quick Reference

- **No inline comments** unless non-obvious math, bit shifting, or algorithm references
- **Javadoc** says what the method does, parameters, and return
- **Public fields** for data classes, private only when encapsulation matters
- **One class per file**
- **Domain-organized packages** (geometry/mesh/, not model/data/)
- **Static factory methods** for parsing
- **Almost never catch exceptions** -- let them propagate
- See `.cursor/rules/ixdar-coding-standards.mdc` for full details
