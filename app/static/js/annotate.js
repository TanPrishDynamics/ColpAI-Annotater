// Annotate workbench controller.
//
// Responsibilities:
//   - Server queue cursor through unannotated images.
//   - Annotation lifecycle: open draft, autosave Layer-B form fields, submit/discard.
//   - Compulsory-field tracking: every [data-required] control, the image type,
//     and every drawn region's attributes must be filled before Next will leave
//     the image (and before the patient can be submitted -- the server refuses
//     otherwise, see app/services/completeness.py). The header pill, tab badges
//     and region-row badges show what's still empty on the current image.
//   - Konva stage for the image + region layers (Layer C).
//   - Tools: pan/select, bbox, polygon, mask (brush paint -> png_b64 geometry).
//   - Undo/redo command stack (limit 50).
//   - Region list <-> canvas selection mirror, per-region attribute editor.
//   - Keyboard shortcuts.
//
// State convention: `state.regions` is a Map(region_id -> server snapshot). The Konva nodes
// are kept in `state.nodes` keyed by region_id. The list/editor render from `state.regions`.

(() => {
    const AUTOSAVE_MS = 800;
    const LABEL_COLOR = {
        NORMAL: '#2e9a5a',
        CIN1: '#c9a531',
        CIN2: '#cf7a31',
        CIN3: '#c84a3a',
        AIS: '#8a4fbf',
        INVASIVE_CANCER: '#7a1f1f',
        INFLAMMATION: '#d65db1',
        INFECTION: '#00a3a3',
        EROSION: '#b5651d',
        null: '#7aa3ff',
    };
    const UNDO_LIMIT = 50;
    // Fingers need much bigger resize handles than a mouse cursor does.
    const COARSE_POINTER = window.matchMedia && window.matchMedia('(pointer: coarse)').matches;
    const ANCHOR_SIZE = COARSE_POINTER ? 20 : 8;
    // Tap-to-close radius for the polygon tool's first vertex, in screen px.
    const POLY_CLOSE_PX = COARSE_POINTER ? 24 : 12;

    const state = {
        queue: [],
        queueCursor: null,
        queueIndex: -1,
        patient: '',          // the annotate workbench is patient-first; always set except on a codeless deep link
        image: null,
        annotation: null,
        dirty: false,
        savePending: null,
        saveTimer: null,
        saveInFlight: null,   // promise of the PATCH currently on the wire, if any

        // Konva
        stage: null,
        imageLayer: null,
        regionLayer: null,
        toolLayer: null,
        transformer: null,
        scale: 1,
        offset: {x: 0, y: 0},

        // Regions
        regions: new Map(),         // region_id -> server dict
        nodes: new Map(),           // region_id -> Konva node
        selectedRegionId: null,
        tool: 'pan',

        // Crop region (per-annotation). Konva node lives on the region layer.
        cropNode: null,

        // In-flight polygon draft (no server id yet)
        polygonDraft: null,

        // Mask brush
        brush: {size: 28, erase: false},
        activeMask: null,           // {regionId|null, canvas} currently being painted
        maskCanvases: new Map(),    // region_id -> data canvas (white-on-transparent)
        maskSaveTimer: null,

        // Undo/redo
        undoStack: [],
        redoStack: [],
    };

    const pill = document.getElementById('statusPill');
    const progress = document.getElementById('progress');
    const meta = document.getElementById('meta');
    const viewerEmpty = document.getElementById('viewerEmpty');
    const stageWrap = document.getElementById('stageWrap');
    const stageEl = document.getElementById('stage');

    // ---------- utils ----------
    function setPill(text, kind) {
        pill.textContent = text;
        pill.className = 'pill ' + (kind || '');
    }
    async function api(path, opts = {}) {
        const res = await fetch(path, {
            credentials: 'same-origin',
            headers: {'Content-Type': 'application/json', ...(opts.headers || {})},
            ...opts,
        });
        if (!res.ok) {
            let body = null;
            try { body = await res.json(); } catch (_) {}
            const err = new Error(body?.error?.message || `HTTP ${res.status}`);
            err.status = res.status;
            err.body = body;
            throw err;
        }
        if (res.status === 204) return null;
        const text = await res.text();
        return text ? JSON.parse(text) : null;
    }
    function getNested(obj, path) {
        const parts = path.split('.');
        let cur = obj;
        for (const p of parts) {
            if (cur == null) return null;
            cur = cur[p];
        }
        return cur;
    }
    function setNested(obj, path, value) {
        const parts = path.split('.');
        let cur = obj;
        for (let i = 0; i < parts.length - 1; i++) {
            if (cur[parts[i]] == null) cur[parts[i]] = {};
            cur = cur[parts[i]];
        }
        cur[parts[parts.length - 1]] = value;
    }
    function deepMerge(base, extra) {
        for (const key of Object.keys(extra)) {
            if (extra[key] && typeof extra[key] === 'object' && !Array.isArray(extra[key])) {
                if (!base[key] || typeof base[key] !== 'object') base[key] = {};
                deepMerge(base[key], extra[key]);
            } else {
                base[key] = extra[key];
            }
        }
        return base;
    }

    function showHUD(text) {
        const hud = document.getElementById('hudOverlay');
        if (!hud) return;
        hud.textContent = text;
        hud.style.opacity = '1';
        hud.style.transform = 'translate(-50%, -50%) scale(1.1)';
        setTimeout(() => {
            hud.style.opacity = '0';
            hud.style.transform = 'translate(-50%, -50%) scale(1)';
        }, 1000);
    }

    // ---------- Konva stage ----------
    const clampScale = (s) => Math.max(0.1, Math.min(8, s));

    // One definition for both the initial stage and the per-image rebuild, so the
    // touch-sized anchors can't drift apart between the two.
    function makeTransformer() {
        return new Konva.Transformer({
            rotateEnabled: false,
            flipEnabled: false,          // keeps w/h positive, so no geometry normalising
            ignoreStroke: true,
            anchorSize: ANCHOR_SIZE,
            anchorCornerRadius: ANCHOR_SIZE / 2,
            anchorStrokeWidth: COARSE_POINTER ? 2 : 1,
            padding: COARSE_POINTER ? 6 : 0,
            borderStroke: '#4f8cff',
            anchorStroke: '#4f8cff',
            anchorFill: '#fff',
            boundBoxFunc: (oldBox, newBox) =>
                (newBox.width < 5 || newBox.height < 5) ? oldBox : newBox,
        });
    }

    function initStage() {
        if (state.stage) state.stage.destroy();
        state.stage = new Konva.Stage({
            container: 'stage',
            width: stageEl.clientWidth,
            height: stageEl.clientHeight,
            draggable: state.tool === 'pan',
        });
        state.imageLayer = new Konva.Layer({listening: false});
        state.regionLayer = new Konva.Layer();
        state.toolLayer = new Konva.Layer();
        state.stage.add(state.imageLayer);
        state.stage.add(state.regionLayer);
        state.stage.add(state.toolLayer);

        state.transformer = makeTransformer();
        state.regionLayer.add(state.transformer);

        attachStageEvents();
        window.addEventListener('resize', resizeStage);
    }

    function resizeStage() {
        if (!state.stage) return;
        state.stage.width(stageEl.clientWidth);
        state.stage.height(stageEl.clientHeight);
        fitImage();
        repositionRegionEditor();
    }

    function fitImage(animate = false) {
        const img = state.imageLayer.findOne('Image');
        if (!img) return;
        const sw = state.stage.width(), sh = state.stage.height();
        const iw = img.width(), ih = img.height();
        const scale = Math.min(sw / iw, sh / ih);
        state.scale = scale;
        state.offset = {x: (sw - iw * scale) / 2, y: (sh - ih * scale) / 2};
        
        if (animate && Konva.Tween) {
            new Konva.Tween({
                node: state.stage,
                duration: 0.3,
                scaleX: scale,
                scaleY: scale,
                x: state.offset.x,
                y: state.offset.y,
                easing: Konva.Easings.EaseInOut,
                onFinish: () => state.stage.batchDraw()
            }).play();
        } else {
            state.stage.scale({x: scale, y: scale});
            state.stage.position(state.offset);
            state.stage.batchDraw();
        }
    }

    function fitToCrop(geom, animate = false) {
        if (!geom || !geom.w || !geom.h) return;
        const sw = state.stage.width(), sh = state.stage.height();
        // Add a small padding (5%) so the crop box isn't flush against the edges
        const padding = 0.05;
        const availW = sw * (1 - padding * 2);
        const availH = sh * (1 - padding * 2);
        
        let scale = Math.min(availW / geom.w, availH / geom.h);
        scale = Math.max(0.1, Math.min(8, scale));
        
        state.scale = scale;
        state.offset = {
            x: (sw - geom.w * scale) / 2 - geom.x * scale,
            y: (sh - geom.h * scale) / 2 - geom.y * scale
        };
        
        if (animate && Konva.Tween) {
            new Konva.Tween({
                node: state.stage,
                duration: 0.3,
                scaleX: scale,
                scaleY: scale,
                x: state.offset.x,
                y: state.offset.y,
                easing: Konva.Easings.EaseInOut,
                onFinish: () => state.stage.batchDraw()
            }).play();
        } else {
            state.stage.scale({x: scale, y: scale});
            state.stage.position(state.offset);
            state.stage.batchDraw();
        }
    }

    function attachStageEvents() {
        // Wheel zoom around cursor.
        state.stage.on('wheel', (e) => {
            e.evt.preventDefault();
            const oldScale = state.stage.scaleX();
            const pointer = state.stage.getPointerPosition();
            if (!pointer) return;
            const mousePointTo = {
                x: (pointer.x - state.stage.x()) / oldScale,
                y: (pointer.y - state.stage.y()) / oldScale,
            };
            const direction = e.evt.deltaY > 0 ? -1 : 1;
            const factor = 1.1;
            const newScale = clampScale(direction > 0 ? oldScale * factor : oldScale / factor);
            state.scale = newScale;
            state.stage.scale({x: newScale, y: newScale});
            state.stage.position({
                x: pointer.x - mousePointTo.x * newScale,
                y: pointer.y - mousePointTo.y * newScale,
            });
            state.stage.batchDraw();
            syncPolygonDots();
            repositionRegionEditor();
        });

        // Space+drag pan (and native pan tool)
        let spacePressed = false;
        window.addEventListener('keydown', (e) => {
            if (e.code === 'Space' && !e.target.matches('input, textarea, select') && !spacePressed) {
                spacePressed = true;
                state.stage.draggable(true);
                stageWrap.style.cursor = 'grab';
                e.preventDefault();
            }
        });
        window.addEventListener('keyup', (e) => {
            if (e.code === 'Space') {
                spacePressed = false;
                state.stage.draggable(state.tool === 'pan');
                stageWrap.style.cursor = state.tool === 'pan' ? '' : 'crosshair';
            }
        });
        
        state.stage.on('dragstart', (e) => {
            if (e.target === state.stage) stageWrap.style.cursor = 'grabbing';
        });
        state.stage.on('dragend', (e) => {
            if (e.target === state.stage) {
                stageWrap.style.cursor = spacePressed ? 'grab' : (state.tool === 'pan' ? '' : 'crosshair');
                repositionRegionEditor();
            }
        });

        // Click empty stage in pan tool -> deselect.
        state.stage.on('click tap', (e) => {
            const isBg = e.target === state.stage || (state.imageLayer && e.target === state.imageLayer.findOne('Image'));
            if (state.tool === 'pan' && isBg) {
                selectRegion(null);
            }
        });

        // ---------- Pointer plumbing ----------
        // Konva 9 emits `pointer*` events for mouse, finger and stylus alike, so a
        // single set of handlers covers all three. Touch brings two wrinkles a mouse
        // doesn't have: extra fingers (a pinch must never draw) and no wheel (so the
        // pinch has to drive the zoom instead).
        let bboxStart = null, bboxRect = null;
        let cropStart = null, cropDraft = null;
        let masking = false, lastMaskPt = null;
        let drawPointerId = null;   // the pointer that owns the in-flight draft
        let touchCount = 0;         // live finger count, read straight off the TouchEvent

        const pid = (e) => (e.evt && e.evt.pointerId != null) ? e.evt.pointerId : 'mouse';
        // A draw gesture belongs to one pointer only, and never runs during a pinch.
        const canDraw = (e) => touchCount < 2 && (drawPointerId === null || pid(e) === drawPointerId);

        const finishStroke = () => {
            if (!masking) return;
            masking = false;
            lastMaskPt = null;
            scheduleMaskSave();
        };

        // Drop anything half-drawn -- a second finger landed, or a pinch began.
        const cancelDrafts = () => {
            if (bboxRect) { bboxRect.destroy(); bboxRect = null; bboxStart = null; }
            if (cropDraft) { cropDraft.destroy(); cropDraft = null; cropStart = null; }
            finishStroke();
            drawPointerId = null;
            state.toolLayer.batchDraw();
        };

        // ---------- Pinch zoom / two-finger pan ----------
        // Bound natively rather than through Konva because we need the whole touch
        // list. `touch-action: none` on #stage stops Safari claiming the gesture.
        const container = state.stage.container();
        let pinchDist = 0;
        let pinchCenter = null;

        const touchPoint = (t) => {
            const r = container.getBoundingClientRect();
            return {x: t.clientX - r.left, y: t.clientY - r.top};
        };

        container.addEventListener('touchstart', (e) => {
            touchCount = e.touches.length;
            if (touchCount >= 2) {
                if (state.stage.isDragging()) state.stage.stopDrag();
                cancelDrafts();
                pinchDist = 0;
                pinchCenter = null;
            }
        }, {passive: false});

        container.addEventListener('touchmove', (e) => {
            touchCount = e.touches.length;
            if (touchCount < 2) return;
            e.preventDefault();
            const a = touchPoint(e.touches[0]);
            const b = touchPoint(e.touches[1]);
            const dist = Math.hypot(b.x - a.x, b.y - a.y);
            const center = {x: (a.x + b.x) / 2, y: (a.y + b.y) / 2};
            if (!pinchDist) { pinchDist = dist; pinchCenter = center; return; }

            // Anchor the zoom on the previous midpoint, then move the stage by however
            // far the midpoint travelled -- that is pinch-zoom and two-finger pan in one.
            const oldScale = state.stage.scaleX();
            const newScale = clampScale(oldScale * (dist / pinchDist));
            const anchor = {
                x: (pinchCenter.x - state.stage.x()) / oldScale,
                y: (pinchCenter.y - state.stage.y()) / oldScale,
            };
            state.scale = newScale;
            state.stage.scale({x: newScale, y: newScale});
            state.stage.position({
                x: center.x - anchor.x * newScale,
                y: center.y - anchor.y * newScale,
            });
            state.stage.batchDraw();
            syncPolygonDots();
            pinchDist = dist;
            pinchCenter = center;
        }, {passive: false});

        const endTouch = (e) => {
            touchCount = e.touches.length;
            if (touchCount < 2) { pinchDist = 0; pinchCenter = null; repositionRegionEditor(); }
        };
        container.addEventListener('touchend', endTouch, {passive: false});
        container.addEventListener('touchcancel', endTouch, {passive: false});

        // Tool entry points
        state.stage.on('pointerdown.bboxtool', (e) => {
            if (state.tool !== 'bbox' || spacePressed || !canDraw(e)) return;
            const p = imagePointer();
            if (!p) return;
            drawPointerId = pid(e);
            bboxStart = p;
            bboxRect = new Konva.Rect({
                x: p.x, y: p.y, width: 1, height: 1,
                stroke: '#4f8cff', strokeWidth: 2,
                dash: [4, 4],
                listening: false,
            });
            state.toolLayer.add(bboxRect);
        });
        state.stage.on('pointermove.bboxtool', (e) => {
            if (!bboxRect || !bboxStart || !canDraw(e)) return;
            const p = imagePointer();
            if (!p) return;
            bboxRect.x(Math.min(p.x, bboxStart.x));
            bboxRect.y(Math.min(p.y, bboxStart.y));
            bboxRect.width(Math.abs(p.x - bboxStart.x));
            bboxRect.height(Math.abs(p.y - bboxStart.y));
            state.toolLayer.batchDraw();
        });
        state.stage.on('pointerup.bboxtool', async () => {
            if (!bboxRect || !bboxStart) return;
            const geom = {
                x: Math.round(bboxRect.x()),
                y: Math.round(bboxRect.y()),
                w: Math.round(bboxRect.width()),
                h: Math.round(bboxRect.height()),
            };
            bboxRect.destroy();
            bboxRect = null;
            bboxStart = null;
            state.toolLayer.batchDraw();
            if (geom.w < 4 || geom.h < 4) return;
            await createRegion('bbox', geom);
            // After creating one bbox, stay in bbox tool for serial drawing.
        });

        // Polygon tool: tap/click to add a vertex; close by tapping the first vertex,
        // double-tapping, hitting Enter, or pressing Finish in the polygon dock.
        state.stage.on('pointerclick.polygontool', (e) => {
            if (state.tool !== 'polygon' || spacePressed || touchCount >= 2) return;
            const p = imagePointer();
            if (!p) return;
            if (!state.polygonDraft) {
                startPolygon(p);
            } else {
                // Measure the close test in screen px so it stays a finger-sized
                // target no matter how far the image is zoomed in or out.
                const [fx, fy] = state.polygonDraft.points[0];
                const screenDist = Math.hypot(p.x - fx, p.y - fy) * (state.stage.scaleX() || 1);
                if (state.polygonDraft.points.length >= 3 && screenDist <= POLY_CLOSE_PX) {
                    finishPolygon();
                    return;
                }
                state.polygonDraft.points.push([p.x, p.y]);
                addPolygonDot(p);
                state.polygonDraft.line.points([...state.polygonDraft.points.flat(), p.x, p.y]);
            }
            updatePolygonDock();
            state.toolLayer.batchDraw();
        });
        state.stage.on('pointermove.polygontool', () => {
            if (state.tool !== 'polygon' || !state.polygonDraft || touchCount >= 2) return;
            const p = imagePointer();
            if (!p) return;
            const flat = state.polygonDraft.points.flat();
            state.polygonDraft.line.points([...flat, p.x, p.y]);
            state.toolLayer.batchDraw();
        });
        state.stage.on('pointerdblclick.polygontool', () => {
            if (state.tool !== 'polygon') return;
            finishPolygon();
        });

        // Mask tool: brush-paint into an offscreen canvas at native resolution.
        state.stage.on('pointerdown.masktool', (e) => {
            if (state.tool !== 'mask' || spacePressed || !canDraw(e)) return;
            const p = imagePointer();
            if (!p) return;
            if (state.annotation && state.annotation.status && state.annotation.status !== 'draft') return;
            ensureActiveMask();
            if (!state.activeMask) return;
            drawPointerId = pid(e);
            masking = true;
            lastMaskPt = p;
            paintDab(state.activeMask.canvas, p.x, p.y);
            updateActiveMaskDisplay();
        });
        state.stage.on('pointermove.masktool', (e) => {
            if (!masking || !state.activeMask || !canDraw(e)) return;
            const p = imagePointer();
            if (!p) return;
            paintStroke(state.activeMask.canvas, lastMaskPt.x, lastMaskPt.y, p.x, p.y);
            lastMaskPt = p;
            updateActiveMaskDisplay();
        });
        state.stage.on('pointerup.masktool', finishStroke);
        state.stage.on('pointerleave.masktool', finishStroke);
        state.stage.on('pointercancel.masktool', finishStroke);

        // Crop tool: drag one rectangle that becomes the annotation's crop region.
        state.stage.on('pointerdown.croptool', (e) => {
            if (state.tool !== 'crop' || spacePressed || !canDraw(e)) return;
            if (state.annotation && state.annotation.status && state.annotation.status !== 'draft') return;
            const p = imagePointer();
            if (!p) return;
            drawPointerId = pid(e);
            cropStart = p;
            cropDraft = new Konva.Rect({
                x: p.x, y: p.y, width: 1, height: 1,
                stroke: '#ffd166', strokeWidth: 2, dash: [6, 4], listening: false,
            });
            state.toolLayer.add(cropDraft);
        });
        state.stage.on('pointermove.croptool', (e) => {
            if (!cropDraft || !cropStart || !canDraw(e)) return;
            const p = imagePointer();
            if (!p) return;
            cropDraft.x(Math.min(p.x, cropStart.x));
            cropDraft.y(Math.min(p.y, cropStart.y));
            cropDraft.width(Math.abs(p.x - cropStart.x));
            cropDraft.height(Math.abs(p.y - cropStart.y));
            state.toolLayer.batchDraw();
        });
        state.stage.on('pointerup.croptool', () => {
            if (!cropDraft || !cropStart) return;
            const geom = {
                x: Math.round(cropDraft.x()),
                y: Math.round(cropDraft.y()),
                w: Math.round(cropDraft.width()),
                h: Math.round(cropDraft.height()),
            };
            cropDraft.destroy();
            cropDraft = null;
            cropStart = null;
            state.toolLayer.batchDraw();
            if (geom.w < 4 || geom.h < 4) return;
            setCropBox(geom);
        });

        // Registered last so the tool handlers above still see the owning pointer
        // when they commit the draft.
        state.stage.on('pointerup.draw pointercancel.draw', (e) => {
            if (pid(e) === drawPointerId) drawPointerId = null;
        });
        state.stage.on('pointerleave.draw', () => { drawPointerId = null; });
    }

    // ---------- Polygon draft ----------
    // Vertices live in image space, so their radii are divided by the stage scale to
    // keep a constant on-screen size -- big enough to aim a finger at.
    function polyDotRadius(index) {
        const s = (state.stage && state.stage.scaleX()) || 1;
        const px = index === 0 ? (COARSE_POINTER ? 9 : 6) : (COARSE_POINTER ? 6 : 4);
        return px / s;
    }

    function addPolygonDot(p) {
        const draft = state.polygonDraft;
        if (!draft) return;
        const index = draft.dots.getChildren().length;
        draft.dots.add(new Konva.Circle({
            x: p.x, y: p.y,
            radius: polyDotRadius(index),
            fill: index === 0 ? '#ffd166' : '#4f8cff',   // first vertex = the close target
            stroke: '#fff',
            strokeWidth: 1 / ((state.stage && state.stage.scaleX()) || 1),
            listening: false,
        }));
    }

    function syncPolygonDots() {
        const draft = state.polygonDraft;
        if (!draft) return;
        const s = (state.stage && state.stage.scaleX()) || 1;
        draft.dots.getChildren().forEach((c, i) => {
            c.radius(polyDotRadius(i));
            c.strokeWidth(1 / s);
        });
        state.toolLayer.batchDraw();
    }

    function startPolygon(p) {
        state.polygonDraft = {
            points: [[p.x, p.y]],
            line: new Konva.Line({
                points: [p.x, p.y, p.x, p.y],
                stroke: '#4f8cff',
                strokeWidth: 2,
                closed: false,
                listening: false,
            }),
            dots: new Konva.Group({listening: false}),
        };
        state.toolLayer.add(state.polygonDraft.line);
        state.toolLayer.add(state.polygonDraft.dots);
        addPolygonDot(p);
    }

    function cancelPolygon() {
        if (!state.polygonDraft) return;
        state.polygonDraft.line.destroy();
        state.polygonDraft.dots.destroy();
        state.polygonDraft = null;
        if (state.toolLayer) state.toolLayer.batchDraw();
        updatePolygonDock();
    }

    async function finishPolygon() {
        if (!state.polygonDraft) return;
        const pts = state.polygonDraft.points;
        cancelPolygon();
        if (pts.length < 3) return;
        await createRegion('polygon', {points: pts.map(([x, y]) => [Math.round(x), Math.round(y)])});
    }

    function updatePolygonDock() {
        const dock = document.getElementById('polygonDock');
        if (!dock) return;
        dock.style.display = state.tool === 'polygon' ? 'flex' : 'none';
        const n = state.polygonDraft ? state.polygonDraft.points.length : 0;
        const finish = document.getElementById('polygonFinish');
        if (finish) finish.disabled = n < 3;
        const hint = document.getElementById('polygonHint');
        if (hint) {
            hint.textContent = n === 0
                ? 'Tap to add points, then tap the first point to close.'
                : `${n} point${n === 1 ? '' : 's'} - tap the first point to close.`;
        }
    }

    // ---------- Crop region ----------
    function drawCropBox(box) {
        if (state.cropNode) { state.cropNode.destroy(); state.cropNode = null; }
        if (!box || !box.w || !box.h) return;
        const node = new Konva.Rect({
            x: box.x, y: box.y, width: box.w, height: box.h,
            stroke: '#ffd166', strokeWidth: 2, dash: [6, 4],
            fill: '#ffd16618', listening: false,
        });
        state.cropNode = node;
        state.regionLayer.add(node);
        state.regionLayer.batchDraw();
        updateCropInfo(box);
    }

    function updateCropInfo(box) {
        const info = document.getElementById('cropInfo');
        if (info) info.textContent = box && box.w ? `${box.w}×${box.h}px` : '';
    }

    function setCropBox(geom) {
        if (state.annotation && state.annotation.status && state.annotation.status !== 'draft') {
            setPill('Read-only', '');
            return;
        }
        drawCropBox(geom);
        // Autosave merges this into state.annotation.crop_box and PATCHes it.
        queueAutosave({crop_box: geom});
        state.imageLayer.clip({ x: geom.x, y: geom.y, width: geom.w, height: geom.h });
        state.imageLayer.batchDraw();
        fitToCrop(geom, true);
    }

    function clearCrop() {
        if (state.annotation && state.annotation.status && state.annotation.status !== 'draft') return;
        if (state.cropNode) { state.cropNode.destroy(); state.cropNode = null; }
        state.regionLayer.batchDraw();
        state.imageLayer.clip(null);
        state.imageLayer.batchDraw();
        updateCropInfo(null);
        if (state.annotation && state.annotation.crop_box) {
            // Zero-area box tells the server to clear the crop.
            queueAutosave({crop_box: {x: 0, y: 0, w: 0, h: 0}});
        }
        if (state.annotation) state.annotation.crop_box = null;
        fitImage(true);
    }

    function renderCropFromState() {
        const box = state.annotation?.crop_box;
        if (box && box.w && box.h) {
            drawCropBox(box);
            state.imageLayer.clip({ x: box.x, y: box.y, width: box.w, height: box.h });
            state.imageLayer.batchDraw();
            fitToCrop(box, false);
        } else {
            state.imageLayer.clip(null);
            state.imageLayer.batchDraw();
            updateCropInfo(null);
        }
    }

    // ---------- Mask brush helpers ----------
    function imageDims() {
        const img = state.imageLayer && state.imageLayer.findOne('Image');
        if (img) return {w: img.width(), h: img.height()};
        if (state.image) return {w: state.image.width_px, h: state.image.height_px};
        return null;
    }

    function newCanvas(w, h) {
        const c = document.createElement('canvas');
        c.width = w; c.height = h;
        return c;
    }

    function paintDab(canvas, x, y) {
        const ctx = canvas.getContext('2d');
        ctx.globalCompositeOperation = state.brush.erase ? 'destination-out' : 'source-over';
        ctx.fillStyle = '#fff';
        ctx.beginPath();
        ctx.arc(x, y, state.brush.size / 2, 0, Math.PI * 2);
        ctx.fill();
    }

    function paintStroke(canvas, x0, y0, x1, y1) {
        const ctx = canvas.getContext('2d');
        ctx.globalCompositeOperation = state.brush.erase ? 'destination-out' : 'source-over';
        ctx.strokeStyle = '#fff';
        ctx.lineWidth = state.brush.size;
        ctx.lineCap = 'round';
        ctx.lineJoin = 'round';
        ctx.beginPath();
        ctx.moveTo(x0, y0);
        ctx.lineTo(x1, y1);
        ctx.stroke();
    }

    // Recolour a white-on-transparent mask canvas to the label colour for display.
    function tintedMaskCanvas(src, color) {
        const out = newCanvas(src.width, src.height);
        const ctx = out.getContext('2d');
        ctx.drawImage(src, 0, 0);
        ctx.globalCompositeOperation = 'source-in';
        ctx.fillStyle = color;
        ctx.fillRect(0, 0, src.width, src.height);
        return out;
    }

    function canvasHasInk(canvas) {
        const ctx = canvas.getContext('2d', {willReadFrequently: true});
        const {data} = ctx.getImageData(0, 0, canvas.width, canvas.height);
        for (let i = 3; i < data.length; i += 4) {
            if (data[i] !== 0) return true;
        }
        return false;
    }

    // Stored png masks are grayscale (white = lesion). Convert to white-on-transparent.
    async function maskCanvasFromGeometry(geometry, w, h) {
        const c = newCanvas(w, h);
        if (!geometry || geometry.format !== 'png_b64' || !geometry.data) return c;
        const img = new window.Image();
        const src = geometry.data.startsWith('data:')
            ? geometry.data : 'data:image/png;base64,' + geometry.data;
        await new Promise((res) => { img.onload = res; img.onerror = res; img.src = src; });
        const tmp = newCanvas(w, h);
        const tctx = tmp.getContext('2d', {willReadFrequently: true});
        tctx.drawImage(img, 0, 0, w, h);
        const id = tctx.getImageData(0, 0, w, h);
        const d = id.data;
        for (let i = 0; i < d.length; i += 4) {
            const on = d[i] > 127 ? 255 : 0;  // luminance threshold (r==g==b)
            d[i] = 255; d[i + 1] = 255; d[i + 2] = 255; d[i + 3] = on;
        }
        c.getContext('2d').putImageData(id, 0, 0);
        return c;
    }

    // Serialise a white-on-transparent canvas to a base64 grayscale PNG (white = lesion).
    function serializeMask(canvas) {
        const out = newCanvas(canvas.width, canvas.height);
        const ctx = out.getContext('2d');
        ctx.fillStyle = '#000';
        ctx.fillRect(0, 0, out.width, out.height);
        ctx.drawImage(canvas, 0, 0);  // white painted pixels over black background
        return out.toDataURL('image/png').split(',')[1];
    }

    function ensureActiveMask() {
        if (state.activeMask) return;
        const dims = imageDims();
        if (!dims) return;
        const sel = state.regions.get(state.selectedRegionId);
        if (sel && sel.region_type === 'mask') {
            const existing = state.maskCanvases.get(sel.id);
            const canvas = existing || newCanvas(dims.w, dims.h);
            state.maskCanvases.set(sel.id, canvas);
            state.activeMask = {regionId: sel.id, canvas};
        } else {
            state.activeMask = {regionId: null, canvas: newCanvas(dims.w, dims.h)};
        }
    }

    function updateActiveMaskDisplay() {
        if (!state.activeMask) return;
        const key = state.activeMask.regionId || '__active_mask__';
        const old = state.nodes.get(key);
        if (old) { old.destroy(); state.nodes.delete(key); }
        const color = state.activeMask.regionId
            ? colorFor(state.regions.get(state.activeMask.regionId) || {})
            : LABEL_COLOR.null;
        const node = new Konva.Image({
            image: tintedMaskCanvas(state.activeMask.canvas, color),
            x: 0, y: 0,
            width: state.activeMask.canvas.width,
            height: state.activeMask.canvas.height,
            opacity: 0.5,
            listening: false,
        });
        state.nodes.set(key, node);
        state.regionLayer.add(node);
        state.regionLayer.batchDraw();
    }

    function scheduleMaskSave() {
        if (state.maskSaveTimer) clearTimeout(state.maskSaveTimer);
        state.maskSaveTimer = setTimeout(flushMaskSave, 600);
    }

    async function flushMaskSave() {
        if (state.maskSaveTimer) { clearTimeout(state.maskSaveTimer); state.maskSaveTimer = null; }
        const active = state.activeMask;
        if (!active) return;
        const dims = imageDims();
        if (!dims) return;

        // Empty canvas: drop a saved region, or just abandon a never-saved one.
        if (!canvasHasInk(active.canvas)) {
            if (active.regionId) await deleteRegion(active.regionId);
            const node = state.nodes.get('__active_mask__');
            if (node) { node.destroy(); state.nodes.delete('__active_mask__'); }
            state.activeMask = null;
            state.regionLayer.batchDraw();
            return;
        }

        const geometry = {format: 'png_b64', size: [dims.h, dims.w], data: serializeMask(active.canvas)};
        setPill('Saving mask...', 'saving');
        try {
            if (!active.regionId) {
                await ensureAnnotation();
                const created = await api(`/api/v1/annotations/${state.annotation.id}/regions`, {
                    method: 'POST',
                    body: JSON.stringify({region_type: 'mask', geometry}),
                });
                state.regions.set(created.id, created);
                state.maskCanvases.set(created.id, active.canvas);
                // Re-key the live display node to the real region id.
                const tmp = state.nodes.get('__active_mask__');
                if (tmp) { tmp.destroy(); state.nodes.delete('__active_mask__'); }
                active.regionId = created.id;
                drawRegion(created);
                renderRegionList();
                selectRegion(created.id);
                pushUndo({type: 'create', region: created});
            } else {
                const updated = await api(`/api/v1/regions/${active.regionId}`, {
                    method: 'PATCH',
                    body: JSON.stringify({geometry}),
                });
                state.regions.set(updated.id, updated);
                state.maskCanvases.set(updated.id, active.canvas);
                drawRegion(updated);
                renderRegionList();
            }
            setPill('Saved', 'saved');
        } catch (err) {
            setPill('Error', 'error');
            console.error(err);
            alert('Mask save failed: ' + err.message);
        }
    }

    function clearActiveMask() {
        if (state.tool !== 'mask') return;
        ensureActiveMask();
        if (!state.activeMask) return;
        const c = state.activeMask.canvas;
        c.getContext('2d').clearRect(0, 0, c.width, c.height);
        updateActiveMaskDisplay();
        flushMaskSave();
    }

    // Commit any in-progress mask and forget the active handle (on tool/image switch).
    function commitActiveMask() {
        if (state.activeMask) flushMaskSave();
        state.activeMask = null;
    }

    function imagePointer() {
        const pos = state.stage.getPointerPosition();
        if (!pos) return null;
        const transform = state.stage.getAbsoluteTransform().copy().invert();
        const local = transform.point(pos);
        // Clamp inside image.
        const img = state.imageLayer.findOne('Image');
        if (!img) return null;
        return {
            x: Math.max(0, Math.min(img.width(), local.x)),
            y: Math.max(0, Math.min(img.height(), local.y)),
        };
    }

    // ---------- Tool selection ----------
    function setTool(name) {
        state.tool = name;
        state.stage.draggable(name === 'pan');
        document.querySelectorAll('#toolDock button[data-tool]').forEach(b => {
            b.setAttribute('aria-pressed', b.dataset.tool === name ? 'true' : 'false');
        });
        // Drop any in-flight polygon when switching away.
        if (name !== 'polygon') cancelPolygon();
        updatePolygonDock();
        // Commit any in-progress mask paint when leaving the mask tool.
        if (name !== 'mask') commitActiveMask();
        // Region drag/transform only when in pan tool (masks never drag).
        state.regions.forEach((region, id) => {
            const node = state.nodes.get(id);
            if (!node) return;
            node.draggable(name === 'pan' && region.region_type !== 'mask');
        });
        if (name !== 'pan') selectRegion(null);
        // Show brush controls only for the mask tool.
        const brushDock = document.getElementById('brushDock');
        if (brushDock) brushDock.style.display = name === 'mask' ? 'flex' : 'none';
        // Show crop controls only for the crop tool.
        const cropDock = document.getElementById('cropDock');
        if (cropDock) cropDock.style.display = name === 'crop' ? 'flex' : 'none';
        stageWrap.style.cursor = name === 'pan' ? '' : 'crosshair';
    }

    // ---------- Region rendering ----------
    function colorFor(region) {
        return LABEL_COLOR[region.lesion_label] || LABEL_COLOR.null;
    }

    function drawRegion(region) {
        const existing = state.nodes.get(region.id);
        if (existing) { existing.destroy(); state.nodes.delete(region.id); }

        let node = null;
        const color = colorFor(region);
        if (region.region_type === 'bbox') {
            const g = region.geometry;
            node = new Konva.Rect({
                x: g.x, y: g.y, width: g.w, height: g.h,
                stroke: color,
                strokeWidth: 2,
                fill: color + '22',
                draggable: state.tool === 'pan',
            });
        } else if (region.region_type === 'polygon') {
            const pts = region.geometry.points.flat();
            node = new Konva.Line({
                points: pts,
                stroke: color,
                strokeWidth: 2,
                fill: color + '22',
                closed: true,
                draggable: state.tool === 'pan',
            });
        } else if (region.region_type === 'mask') {
            const dims = imageDims();
            const cached = state.maskCanvases.get(region.id);
            if (!cached) {
                // Decode the stored png once, cache it, then redraw.
                if (dims) {
                    maskCanvasFromGeometry(region.geometry, dims.w, dims.h).then(c => {
                        state.maskCanvases.set(region.id, c);
                        if (state.regions.has(region.id)) {
                            drawRegion(state.regions.get(region.id));
                            state.regionLayer.batchDraw();
                        }
                    });
                }
                return;  // node added on the async redraw
            }
            node = new Konva.Image({
                image: tintedMaskCanvas(cached, color),
                x: 0, y: 0,
                width: cached.width, height: cached.height,
                opacity: 0.5,
            });
        }
        if (!node) return;

        node.regionId = region.id;
        node.on('click tap', (e) => {
            e.cancelBubble = true;
            if (state.tool !== 'pan') return;
            selectRegion(region.id);
        });
        node.on('dragstart transformstart', () => setEditorGhosted(true));
        node.on('dragend transformend', () => setEditorGhosted(false));
        node.on('dragend', async (e) => {
            if (region.region_type === 'mask') return;  // masks are full-frame, not draggable
            const geom = readGeometry(node, region.region_type);
            await patchRegion(region.id, {geometry: geom});
        });
        // Resize handles write the transformer's scale back into real geometry --
        // Konva scales the node, the server only ever stores pixel coordinates.
        node.on('transformend', async (e) => {
            if (region.region_type === 'bbox') {
                const g = {
                    x: Math.round(node.x()),
                    y: Math.round(node.y()),
                    w: Math.round(node.width() * node.scaleX()),
                    h: Math.round(node.height() * node.scaleY()),
                };
                node.scale({x: 1, y: 1});
                node.width(g.w); node.height(g.h);
                await patchRegion(region.id, {geometry: g});
            } else if (region.region_type === 'polygon') {
                const sx = node.scaleX(), sy = node.scaleY();
                const ox = node.x(), oy = node.y();
                const flat = node.points();
                const points = [];
                for (let i = 0; i < flat.length; i += 2) {
                    points.push([Math.round(ox + flat[i] * sx), Math.round(oy + flat[i + 1] * sy)]);
                }
                node.scale({x: 1, y: 1});
                await patchRegion(region.id, {geometry: {points}});
            }
        });

        // Hover Tooltip
        node.on('mouseenter', (e) => {
            if (state.tool !== 'pan') return;
            document.body.style.cursor = 'pointer';
            const label = region.lesion_label || 'Unlabeled';
            const size = region.lesion_size_percent ? ` - ${region.lesion_size_percent}%` : '';
            const tooltip = document.getElementById('hoverTooltip');
            if (tooltip) {
                tooltip.textContent = `${label}${size}`;
                tooltip.style.display = 'block';
                const pos = state.stage.getPointerPosition();
                if (pos) {
                    tooltip.style.left = pos.x + 'px';
                    tooltip.style.top = pos.y + 'px';
                }
            }
        });
        node.on('mousemove', (e) => {
            if (state.tool !== 'pan') return;
            const tooltip = document.getElementById('hoverTooltip');
            if (tooltip && tooltip.style.display === 'block') {
                const pos = state.stage.getPointerPosition();
                if (pos) {
                    tooltip.style.left = pos.x + 'px';
                    tooltip.style.top = pos.y + 'px';
                }
            }
        });
        node.on('mouseleave', () => {
            document.body.style.cursor = 'default';
            const tooltip = document.getElementById('hoverTooltip');
            if (tooltip) tooltip.style.display = 'none';
        });

        // Context menu
        node.on('contextmenu', (e) => {
            e.evt.preventDefault();
            if (state.tool !== 'pan') return;
            selectRegion(region.id);
            const menu = document.getElementById('contextMenu');
            if (menu) {
                menu.style.display = 'block';
                const pos = state.stage.getPointerPosition();
                if (pos) {
                    menu.style.left = pos.x + 'px';
                    menu.style.top = pos.y + 'px';
                }
                menu.dataset.targetId = region.id;
            }
        });

        state.regionLayer.add(node);
        state.nodes.set(region.id, node);
    }

    function readGeometry(node, region_type) {
        if (region_type === 'bbox') {
            return {
                x: Math.round(node.x()),
                y: Math.round(node.y()),
                w: Math.round(node.width()),
                h: Math.round(node.height()),
            };
        }
        if (region_type === 'polygon') {
            const flat = node.points();
            const offX = node.x(), offY = node.y();
            const points = [];
            for (let i = 0; i < flat.length; i += 2) {
                points.push([Math.round(flat[i] + offX), Math.round(flat[i+1] + offY)]);
            }
            return {points};
        }
        return null;
    }

    function selectRegion(id) {
        state.selectedRegionId = id;
        state.transformer.nodes([]);
        document.querySelectorAll('.region-row').forEach(r => r.setAttribute('aria-selected', 'false'));
        if (id) {
            const row = document.querySelector(`.region-row[data-id="${id}"]`);
            if (row) row.setAttribute('aria-selected', 'true');
            const node = state.nodes.get(id);
            const region = state.regions.get(id);
            const resizable = region && (region.region_type === 'bbox' || region.region_type === 'polygon');
            if (node && resizable && state.tool === 'pan') {
                state.transformer.nodes([node]);
            }
        }
        state.regionLayer.batchDraw();
        renderRegionEditor();
    }

    function renderRegionList() {
        const list = document.getElementById('regionList');
        document.getElementById('regionCount').textContent = `(${state.regions.size})`;
        if (state.regions.size === 0) {
            list.innerHTML = '<div class="empty-list">No regions yet. Pick the Bbox or Polygon tool to draw.</div>';
            renderCompleteness();
            return;
        }
        const html = [];
        let i = 1;
        for (const region of state.regions.values()) {
            const color = colorFor(region);
            const label = region.lesion_label || '(unlabeled)';
            const detail = region.region_type === 'bbox'
                ? `${region.geometry.w}x${region.geometry.h}`
                : region.region_type === 'polygon'
                    ? `${region.geometry.points.length} pts`
                    : 'mask';
            html.push(`
                <div class="region-row" data-id="${region.id}" aria-selected="${region.id === state.selectedRegionId ? 'true' : 'false'}">
                    <div class="swatch" style="background:${color}"></div>
                    <div>
                        <div class="region-title">#${i} ${region.region_type} - ${label}</div>
                        <div class="meta">${detail}</div>
                    </div>
                    <button class="del-region" data-id="${region.id}" title="Delete">&times;</button>
                </div>`);
            i++;
        }
        list.innerHTML = html.join('');
        list.querySelectorAll('.region-row').forEach(row => {
            row.addEventListener('click', (e) => {
                if (e.target.classList.contains('del-region')) return;
                selectRegion(row.dataset.id);
            });
        });
        list.querySelectorAll('.del-region').forEach(btn => {
            btn.addEventListener('click', (e) => {
                e.stopPropagation();
                deleteRegion(btn.dataset.id);
            });
        });
        renderCompleteness();
    }

    function renderRegionEditor() {
        const editor = document.getElementById('regionEditor');
        if (!editor) return;
        const region = state.regions.get(state.selectedRegionId);
        if (!region) {
            editor.style.display = 'none';
            return;
        }
        editor.style.display = 'block';
        positionRegionEditor(editor, state.nodes.get(state.selectedRegionId));

        editor.querySelectorAll('[data-rfield]').forEach(el => {
            const key = el.dataset.rfield;
            const val = region[key];
            if (el.type === 'checkbox') el.checked = !!val;
            else el.value = val == null ? '' : String(val);
        });
        renderRegionEditorMissing();
    }

    // Park the editor beside the selected region rather than on top of it -- it used
    // to sit dead-centre, which covered the region and its resize handles. Tries
    // right, left, below, above; if the panel fits nowhere it goes to the corner
    // with the most clearance.
    function positionRegionEditor(editor, node) {
        const GAP = 12;
        const MIN_H = 160;   // below this the panel is too short to be worth scrolling
        const clamp = (v, lo, hi) => Math.max(lo, Math.min(Math.max(lo, hi), v));
        const wrapW = stageWrap.clientWidth;
        const wrapH = stageWrap.clientHeight;

        // Drop any height cap left over from the previous selection before measuring,
        // falling back to the max-height in the stylesheet.
        editor.style.maxHeight = '';
        const ew = editor.offsetWidth;
        let eh = editor.offsetHeight;

        // Keep clear of the tool dock, which is pinned to the top-left of the stage.
        const dock = document.getElementById('toolDock');
        const minX = dock ? dock.offsetLeft + dock.offsetWidth + GAP : GAP;
        const maxX = wrapW - ew - GAP;

        const place = (x, y) => {
            editor.style.left = clamp(x, minX, maxX) + 'px';
            editor.style.top = clamp(y, GAP, wrapH - eh - GAP) + 'px';
        };

        if (!node) { place(maxX, GAP); return; }

        // Region box in stage-container px, grown to cover the transformer handles.
        const r = node.getClientRect();
        const pad = ANCHOR_SIZE;
        const box = {left: r.x - pad, top: r.y - pad, right: r.x + r.width + pad, bottom: r.y + r.height + pad};

        const centredY = clamp((box.top + box.bottom) / 2 - eh / 2, GAP, wrapH - eh - GAP);
        const centredX = clamp((box.left + box.right) / 2 - ew / 2, minX, maxX);
        const candidates = [
            {x: box.right + GAP, y: centredY},        // right of the region
            {x: box.left - GAP - ew, y: centredY},    // left of it
            {x: centredX, y: box.bottom + GAP},       // below it
            {x: centredX, y: box.top - GAP - eh},     // above it
        ];
        const fits = (c) => c.x >= minX && c.y >= GAP && c.x + ew <= wrapW - GAP && c.y + eh <= wrapH - GAP;

        const spot = candidates.find(fits);
        if (spot) { place(spot.x, spot.y); return; }

        // Nothing fits at full height -- e.g. a wide band across the middle. Hand the
        // panel the taller free strip above or below the region and let it scroll.
        const above = box.top - GAP * 2;
        const below = wrapH - box.bottom - GAP * 2;
        if (Math.max(above, below) >= MIN_H) {
            editor.style.maxHeight = Math.max(above, below) + 'px';
            eh = editor.offsetHeight;   // re-measure now that it's capped
            place(centredX, below >= above ? box.bottom + GAP : box.top - GAP - eh);
            return;
        }

        // The region covers nearly the whole stage; take the corner with most room.
        place((wrapW - box.right) >= (box.left - minX) ? maxX : minX,
              (wrapH - box.bottom) >= box.top ? wrapH - eh - GAP : GAP);
    }

    // Zooming/panning moves the region under the panel, so re-park it afterwards.
    // Position only -- never re-read the field values, or it would clobber typing.
    function repositionRegionEditor() {
        const editor = document.getElementById('regionEditor');
        if (!editor || editor.style.display !== 'block') return;
        positionRegionEditor(editor, state.nodes.get(state.selectedRegionId));
    }

    // Fade the editor out while a region is actually being dragged or resized, so it
    // can never sit under the finger mid-gesture.
    function setEditorGhosted(on) {
        const editor = document.getElementById('regionEditor');
        if (!editor) return;
        editor.style.opacity = on ? '0' : '1';
        editor.style.pointerEvents = on ? 'none' : 'auto';
    }

    function bindRegionEditor() {
        document.querySelectorAll('#regionEditor [data-rfield]').forEach(el => {
            const evt = (el.tagName === 'SELECT' || el.type === 'checkbox') ? 'change' : 'input';
            el.addEventListener(evt, () => {
                if (!state.selectedRegionId) return;
                let value;
                if (el.type === 'checkbox') value = el.checked;
                else if (el.type === 'number') value = el.value === '' ? null : Number(el.value);
                else value = el.value === '' ? null : el.value;
                patchRegion(state.selectedRegionId, {[el.dataset.rfield]: value});
            });
        });
    }

    // ---------- Region API calls + undo/redo ----------
    async function createRegion(region_type, geometry) {
        if (state.annotation && state.annotation.status && state.annotation.status !== 'draft') return;
        setPill('Saving region...', 'saving');
        try {
            await ensureAnnotation();
            const created = await api(`/api/v1/annotations/${state.annotation.id}/regions`, {
                method: 'POST',
                body: JSON.stringify({region_type, geometry}),
            });
            state.regions.set(created.id, created);
            drawRegion(created);
            renderRegionList();
            selectRegion(created.id);
            pushUndo({type: 'create', region: created});
            setPill('Saved', 'saved');
        } catch (err) {
            setPill('Error', 'error');
            console.error(err);
            alert('Region create failed: ' + err.message);
        }
    }

    async function patchRegion(id, patch, opts = {silent: false}) {
        const before = state.regions.get(id);
        if (!before) return;
        setPill('Saving...', 'saving');
        try {
            const updated = await api(`/api/v1/regions/${id}`, {
                method: 'PATCH',
                body: JSON.stringify(patch),
            });
            state.regions.set(id, updated);
            drawRegion(updated);
            renderRegionList();
            if (id === state.selectedRegionId) selectRegion(id);
            if (!opts.silent) {
                // Only attribute changes go on the undo stack with a useful diff.
                const attrPatch = {...patch};
                delete attrPatch.geometry;
                if (Object.keys(attrPatch).length || patch.geometry) {
                    pushUndo({
                        type: 'patch',
                        id,
                        prev: snapshotAttrs(before, patch),
                        next: snapshotAttrs(updated, patch),
                    });
                }
            }
            setPill('Saved', 'saved');
        } catch (err) {
            setPill('Error', 'error');
            console.error(err);
            alert('Region patch failed: ' + err.message);
        }
    }

    function snapshotAttrs(region, patch) {
        const snap = {};
        for (const k of Object.keys(patch)) snap[k] = region[k];
        return snap;
    }

    async function deleteRegion(id, opts = {silent: false}) {
        const before = state.regions.get(id);
        if (!before) return;
        setPill('Deleting...', 'saving');
        try {
            await api(`/api/v1/regions/${id}`, {method: 'DELETE'});
            const node = state.nodes.get(id);
            if (node) node.destroy();
            state.nodes.delete(id);
            state.regions.delete(id);
            if (state.selectedRegionId === id) selectRegion(null);
            renderRegionList();
            state.regionLayer.batchDraw();
            if (!opts.silent) pushUndo({type: 'delete', region: before});
            setPill('Saved', 'saved');
        } catch (err) {
            setPill('Error', 'error');
            console.error(err);
            alert('Region delete failed: ' + err.message);
        }
    }

    // Recreate a deleted region. Server assigns a new id since we don't restore the original.
    async function recreateRegion(snapshot) {
        const created = await api(`/api/v1/annotations/${state.annotation.id}/regions`, {
            method: 'POST',
            body: JSON.stringify({
                region_type: snapshot.region_type,
                geometry: snapshot.geometry,
                lesion_label: snapshot.lesion_label,
                lesion_location_clock: snapshot.lesion_location_clock,
                lesion_quadrant: snapshot.lesion_quadrant,
                lesion_size_percent: snapshot.lesion_size_percent,
                lesion_margins: snapshot.lesion_margins,
                punctation_present: snapshot.punctation_present,
                punctation_severity: snapshot.punctation_severity,
                mosaic_present: snapshot.mosaic_present,
                mosaic_severity: snapshot.mosaic_severity,
                region_notes: snapshot.region_notes,
            }),
        });
        state.regions.set(created.id, created);
        drawRegion(created);
        renderRegionList();
        return created;
    }

    // ---------- Undo / redo ----------
    function pushUndo(op) {
        state.undoStack.push(op);
        if (state.undoStack.length > UNDO_LIMIT) state.undoStack.shift();
        state.redoStack.length = 0;
    }

    async function undo() {
        const op = state.undoStack.pop();
        if (!op) return;
        if (op.type === 'create') {
            await deleteRegion(op.region.id, {silent: true});
            state.redoStack.push({type: 'recreate', snapshot: op.region});
        } else if (op.type === 'delete') {
            const created = await recreateRegion(op.region);
            state.redoStack.push({type: 'create', region: created});
        } else if (op.type === 'patch') {
            await patchRegion(op.id, op.prev, {silent: true});
            state.redoStack.push({type: 'patch', id: op.id, prev: op.next, next: op.prev});
        } else if (op.type === 'recreate') {
            const created = await recreateRegion(op.snapshot);
            state.redoStack.push({type: 'delete', region: created});
        }
    }

    async function redo() {
        const op = state.redoStack.pop();
        if (!op) return;
        if (op.type === 'create') {
            await deleteRegion(op.region.id, {silent: true});
            state.undoStack.push({type: 'recreate', snapshot: op.region});
        } else if (op.type === 'delete') {
            const created = await recreateRegion(op.region);
            state.undoStack.push({type: 'create', region: created});
        } else if (op.type === 'patch') {
            await patchRegion(op.id, op.prev, {silent: true});
            state.undoStack.push({type: 'patch', id: op.id, prev: op.next, next: op.prev});
        } else if (op.type === 'recreate') {
            const created = await recreateRegion(op.snapshot);
            state.undoStack.push({type: 'delete', region: created});
        }
    }

    document.getElementById('undoBtn').addEventListener('click', undo);
    document.getElementById('redoBtn').addEventListener('click', redo);

    // ---------- Image load ----------
    async function loadImageOnStage(image) {
        // Clear previous image + regions. The tool layer holds any in-flight draft,
        // which must not survive into the next image.
        cancelPolygon();
        state.toolLayer.destroyChildren();
        state.imageLayer.destroyChildren();
        state.regionLayer.destroyChildren();
        state.regionLayer.add(state.transformer = makeTransformer());
        state.nodes.clear();
        state.regions.clear();
        state.maskCanvases.clear();
        state.cropNode = null;  // destroyed with regionLayer children above
        state.activeMask = null;
        if (state.maskSaveTimer) { clearTimeout(state.maskSaveTimer); state.maskSaveTimer = null; }
        state.undoStack.length = 0;
        state.redoStack.length = 0;
        selectRegion(null);

        const imgEl = new window.Image();
        imgEl.crossOrigin = 'anonymous';
        await new Promise((resolve, reject) => {
            imgEl.onload = resolve;
            imgEl.onerror = reject;
            imgEl.src = `/api/v1/images/${image.id}/file`;
        });
        const kImg = new Konva.Image({
            image: imgEl,
            width: image.width_px || imgEl.naturalWidth,
            height: image.height_px || imgEl.naturalHeight,
        });
        state.imageLayer.add(kImg);
        viewerEmpty.dataset.show = 'false';
        fitImage();
    }

    function loadRegionsForCurrentAnnotation() {
        const annRegions = state.annotation?.regions || [];
        for (const r of annRegions) {
            state.regions.set(r.id, r);
            drawRegion(r);
        }
        renderRegionList();
        state.regionLayer.batchDraw();
        renderCompleteness();
    }

    // ---------- Queue navigation (unchanged logic from Phase 2) ----------
    // When the patient's image queue is exhausted, jump straight to that patient's
    // diagnosis page -- there's nothing left to annotate at the image level.
    function goToPatientDiagnose() {
        window.location.href = '/patients/' + encodeURIComponent(state.patient) + '/diagnose';
    }

    function queueUrl(cursor) {
        // No status filter: nothing gets submitted per-image anymore, so the
        // "queue" is simply every image for the patient, in a fixed order. Reaching
        // past the last one means the patient's images are all done.
        let url = '/api/v1/images?limit=100';
        if (state.patient) url += `&patient_code=${encodeURIComponent(state.patient)}`;
        if (cursor) url += `&cursor=${encodeURIComponent(cursor)}`;
        return url;
    }

    async function fetchQueue() {
        const data = await api(queueUrl());
        state.queue = data.items;
        state.queueCursor = data.next_cursor;
    }

    async function loadByImageId(image_id) {
        const img = await api(`/api/v1/images/${image_id}`);
        // GET existing draft (if any). Returns 204 with null body when the user
        // hasn't started this image yet -- we don't POST a draft on mere navigation.
        const existing = await api(`/api/v1/annotations/mine?image_id=${encodeURIComponent(image_id)}`);
        state.image = img;
        state.annotation = existing;  // may be null
        renderMeta();
        renderForm();
        await loadImageOnStage(img);
        if (existing) loadRegionsForCurrentAnnotation();
        renderCropFromState();
        setPill(existing ? 'Saved' : 'Idle', existing ? 'saved' : '');
    }

    async function ensureAnnotation() {
        if (state.annotation) return state.annotation;
        if (!state.image) throw new Error('No image loaded.');
        state.annotation = await api('/api/v1/annotations', {
            method: 'POST',
            body: JSON.stringify({image_id: state.image.id}),
        });
        return state.annotation;
    }

    async function loadIndex(idx) {
        if (idx < 0) return;
        if (idx >= state.queue.length) {
            if (state.queueCursor) {
                const data = await api(queueUrl(state.queueCursor));
                state.queue = state.queue.concat(data.items);
                state.queueCursor = data.next_cursor;
            }
            if (idx >= state.queue.length) {
                if (state.patient) { goToPatientDiagnose(); return; }
                setPill('Queue empty', '');
                viewerEmpty.textContent = 'You have no unannotated images.';
                viewerEmpty.dataset.show = 'true';
                return;
            }
        }
        state.queueIndex = idx;
        await loadByImageId(state.queue[idx].id);
        progress.textContent = `${idx + 1} / ${state.queue.length}${state.queueCursor ? '+' : ''}`;
    }

    function renderMeta() {
        const img = state.image;
        if (!img) { meta.textContent = ''; return; }
        meta.innerHTML = `
            <div><strong>ID:</strong> <code>${img.id.slice(0,8)}</code></div>
            <div><strong>Dataset:</strong> ${img.dataset_source}</div>
            <div><strong>Phase:</strong> ${img.image_phase || '-'}</div>
            <div><strong>Resolution:</strong> ${img.image_resolution || '-'}</div>
            <div><strong>Device:</strong> ${img.capture_device || '-'}</div>
            ${img.patient_code ? `
            <div style="margin-top: 8px;"><strong>Patient:</strong> ${img.patient_code}</div>
            <div><a href="/patients/${encodeURIComponent(img.patient_code)}/diagnose">Diagnose patient &rarr;</a></div>
            ` : ''}
        `;
    }

    // ---------- Form (Layer B) ----------
    function renderForm() {
        const ann = state.annotation;
        document.querySelectorAll('[data-field]').forEach(el => {
            const path = el.dataset.field;
            const val = getNested(ann, path);
            if (el.type === 'checkbox') el.checked = !!val;
            else el.value = val == null ? '' : String(val);
        });
        const typeSel = document.getElementById('imageTypeSelect');
        if (typeSel) typeSel.value = state.image?.image_phase || '';
        // A fresh image: the blocked-Next notice belonged to the previous one.
        if (nextBlocked) nextBlocked.hidden = true;
        renderCompleteness();
    }

    // ---------- Compulsory fields ----------
    // Mirrors app/services/completeness.py: every [data-required] control in the
    // side panel, plus the shared image type. Checkboxes are never "missing" --
    // unchecked simply means absent and is saved as false.
    const requiredPill = document.getElementById('requiredPill');
    const nextBlocked = document.getElementById('nextBlocked');
    const imageTypeSelectEl = document.getElementById('imageTypeSelect');

    // Mirrors REQUIRED_REGION_FIELDS / REGION_CONDITIONAL_FIELDS on the server.
    const REGION_REQUIRED = ['lesion_label', 'lesion_location_clock', 'lesion_size_percent', 'lesion_quadrant', 'lesion_margins'];
    const REGION_REQUIRED_IF = {punctation_severity: 'punctation_present', mosaic_severity: 'mosaic_present'};

    function missingRegionFields(region) {
        const missing = REGION_REQUIRED.filter(f => region[f] == null);
        for (const [field, gate] of Object.entries(REGION_REQUIRED_IF)) {
            if (region[gate] && region[field] == null) missing.push(field);
        }
        return missing;
    }

    // Image-level gaps are DOM controls; region gaps come from the server
    // snapshots in state.regions (the editor only shows one region at a time).
    function computeMissing() {
        const controls = [];
        if (imageTypeSelectEl && !imageTypeSelectEl.value) controls.push(imageTypeSelectEl);
        document.querySelectorAll('.side [data-required]').forEach(el => {
            if (el.value === '' || el.value == null) controls.push(el);
        });
        const regions = [];
        for (const region of state.regions.values()) {
            const fields = missingRegionFields(region);
            if (fields.length) regions.push({id: region.id, fields});
        }
        const regionCount = regions.reduce((n, r) => n + r.fields.length, 0);
        return {controls, regions, total: controls.length + regionCount};
    }

    function renderCompleteness() {
        const missing = computeMissing();
        const missingSet = new Set(missing.controls);
        if (imageTypeSelectEl) imageTypeSelectEl.classList.toggle('is-missing', missingSet.has(imageTypeSelectEl));
        document.querySelectorAll('.side [data-required]').forEach(el => {
            el.classList.toggle('is-missing', missingSet.has(el));
        });

        // Region rows: "N left" badge per region still missing attributes.
        const perRegion = new Map(missing.regions.map(r => [r.id, r.fields.length]));
        document.querySelectorAll('.region-row').forEach(row => {
            let badge = row.querySelector('.left-badge');
            const n = perRegion.get(row.dataset.id) || 0;
            if (n && !badge) {
                badge = document.createElement('span');
                badge.className = 'left-badge';
                row.querySelector('.region-title')?.appendChild(badge);
            }
            if (badge) {
                if (n) badge.textContent = `${n} left`;
                else badge.remove();
            }
        });
        renderRegionEditorMissing();

        // Per-tab counts so the annotator can see where the gaps are without
        // clicking through every tab.
        const perTab = {};
        for (const el of missing.controls) {
            const tab = el.closest('.tab-content');
            if (tab) perTab[tab.id] = (perTab[tab.id] || 0) + 1;
        }
        const regionGaps = missing.total - missing.controls.length;
        if (regionGaps) perTab['tab-regions'] = regionGaps;
        document.querySelectorAll('[data-tab-badge]').forEach(badge => {
            const n = perTab[badge.dataset.tabBadge] || 0;
            badge.textContent = n ? String(n) : '';
        });

        if (!missing.total && nextBlocked) nextBlocked.hidden = true;

        if (!requiredPill) return;
        if (!state.image) { requiredPill.hidden = true; return; }
        requiredPill.hidden = false;
        if (missing.total) {
            requiredPill.textContent = `${missing.total} required left`;
            requiredPill.className = 'pill incomplete';
        } else {
            requiredPill.textContent = 'All required filled';
            requiredPill.className = 'pill complete';
        }
    }

    // Highlight the empty compulsory controls of the region currently open in
    // the editor (severity inputs only count while their checkbox is ticked).
    function renderRegionEditorMissing() {
        const editor = document.getElementById('regionEditor');
        const region = state.regions.get(state.selectedRegionId);
        if (!editor) return;
        const fields = new Set(region ? missingRegionFields(region) : []);
        editor.querySelectorAll('[data-rfield]').forEach(el => {
            el.classList.toggle('is-missing', fields.has(el.dataset.rfield));
        });
    }

    function describeMissing(missing) {
        const n = missing.total;
        const parts = [];
        if (missing.controls.length) parts.push(`${missing.controls.length} form field${missing.controls.length === 1 ? '' : 's'}`);
        if (missing.regions.length) parts.push(`${missing.regions.length} region${missing.regions.length === 1 ? '' : 's'} not fully described`);
        return `${n} compulsory field${n === 1 ? '' : 's'} still empty (${parts.join(', ')}). Fill them in before moving on.`;
    }

    // Take the annotator to the first gap: the tab holding the control, or the
    // region whose editor still has empty fields.
    function focusFirstMissing(missing) {
        const first = missing.controls[0];
        if (first) {
            const tab = first.closest('.tab-content');
            if (tab && typeof switchTab === 'function') switchTab(tab.id);
            first.closest('details')?.setAttribute('open', '');
            first.focus({preventScroll: false});
            first.scrollIntoView({block: 'center', behavior: 'smooth'});
            return;
        }
        const region = missing.regions[0];
        if (region) {
            if (typeof switchTab === 'function') switchTab('tab-regions');
            selectRegion(region.id);
            const el = document.querySelector(`#regionEditor [data-rfield="${region.fields[0]}"]`);
            el?.focus();
        }
    }

    // Next is a hard stop while anything compulsory is empty. Read-only rows
    // (already submitted) are exempt: their fields can't be edited here.
    function nextGateBlocks() {
        const editable = !state.annotation?.id || state.annotation.status === 'draft';
        if (!editable) return false;
        const missing = computeMissing();
        if (!missing.total) return false;
        renderCompleteness();
        if (nextBlocked) {
            nextBlocked.textContent = describeMissing(missing);
            nextBlocked.hidden = false;
        }
        showHUD(`${missing.total} required field${missing.total === 1 ? '' : 's'} left`);
        focusFirstMissing(missing);
        return true;
    }

    // Explicit false for every checkbox on the form, so a draft never carries a
    // NULL boolean just because the annotator didn't touch the box.
    function checkboxSnapshot() {
        const snap = {};
        document.querySelectorAll('.side [data-field][type="checkbox"]').forEach(el => {
            setNested(snap, el.dataset.field, el.checked);
        });
        return snap;
    }

    function collectPatchFromField(el) {
        const path = el.dataset.field;
        let value;
        if (el.type === 'checkbox') value = el.checked;
        else if (el.type === 'number' || el.type === 'range') value = el.value === '' ? null : Number(el.value);
        else value = el.value === '' ? null : el.value;
        const patch = {};
        setNested(patch, path, value);
        return patch;
    }

    function queueAutosave(patch) {
        // Only a row the server has handed back carries a status; a local,
        // not-yet-created draft ({} or partial) must stay editable, otherwise
        // every edit after the first one on a fresh image is dropped until the
        // debounced create round-trips.
        if (state.annotation?.id && state.annotation.status !== 'draft') {
            setPill('Read-only', '');
            return;
        }
        // Locally mirror the change even when there's no annotation row yet -- the
        // form keeps the value, and flushSave will create the draft on first save.
        if (!state.annotation) state.annotation = {};
        deepMerge(state.annotation, patch);
        state.savePending = deepMerge(state.savePending || {}, patch);
        state.dirty = true;
        setPill('Unsaved', 'unsaved');
        if (state.saveTimer) clearTimeout(state.saveTimer);
        state.saveTimer = setTimeout(flushSave, AUTOSAVE_MS);
    }

    async function flushSave() {
        // Serialize: the debounce timer and an explicit flush (Save / Next) can
        // both land here, and two concurrent first-saves would each try to
        // create the draft.
        if (state.saveInFlight) await state.saveInFlight;
        if (!state.savePending) return;
        const run = flushSaveNow();
        state.saveInFlight = run;
        try { await run; } finally { state.saveInFlight = null; }
    }

    async function flushSaveNow() {
        const body = state.savePending;
        state.savePending = null;
        setPill('Saving...', 'saving');
        try {
            // Lazily create the draft now that we have something to persist.
            let patchBody = body;
            if (!state.annotation?.id) {
                const created = await api('/api/v1/annotations', {
                    method: 'POST',
                    body: JSON.stringify({image_id: state.image.id}),
                });
                // Preserve any local edits the user already made while we were id-less.
                const local = state.annotation || {};
                state.annotation = deepMerge(created, local);
                // First save of a fresh draft: pin every checkbox to an explicit
                // boolean (the user's own edits win over the snapshot).
                if (created.status === 'draft') {
                    patchBody = deepMerge(checkboxSnapshot(), body);
                    deepMerge(state.annotation, patchBody);
                }
            }
            await api(`/api/v1/annotations/${state.annotation.id}`, {
                method: 'PATCH',
                body: JSON.stringify(patchBody),
            });
            state.dirty = false;
            setPill('Saved', 'saved');
            if (Object.keys(body).length) {
                showHUD('Saved ✓');
            }
        } catch (err) {
            state.savePending = deepMerge(body, state.savePending || {});
            setPill('Error - retry', 'error');
            console.error('autosave failed', err);
        }
    }

    document.querySelectorAll('[data-field]').forEach(el => {
        const evt = (el.tagName === 'SELECT' || el.type === 'checkbox') ? 'change' : 'input';
        el.addEventListener(evt, () => {
            queueAutosave(collectPatchFromField(el));
            renderCompleteness();
        });
    });

    // Image type is a property of the shared Image row, not this annotation --
    // saved immediately via its own endpoint, not the autosave debounce.
    const imageTypeSelect = document.getElementById('imageTypeSelect');
    if (imageTypeSelect) {
        imageTypeSelect.addEventListener('change', async () => {
            if (!state.image || !imageTypeSelect.value) return;
            try {
                const updated = await api(`/api/v1/images/${state.image.id}`, {
                    method: 'PATCH',
                    body: JSON.stringify({image_phase: imageTypeSelect.value}),
                });
                state.image.image_phase = updated.image_phase;
                renderMeta();
                renderCompleteness();
                showHUD('Image type saved');
            } catch (err) {
                alert('Failed to save image type: ' + err.message);
                imageTypeSelect.value = state.image.image_phase || '';
            }
        });
    }

    // ---------- Brightness / contrast ----------
    function updateFilter() {
        const b = document.getElementById('brightness');
        const c = document.getElementById('contrast');
        if (b && c) {
            stageWrap.style.setProperty('--img-filter', `brightness(${b.value}%) contrast(${c.value}%)`);
        }
    }
    const bInput = document.getElementById('brightness');
    const cInput = document.getElementById('contrast');
    if (bInput) bInput.addEventListener('input', updateFilter);
    if (cInput) cInput.addEventListener('input', updateFilter);
    document.getElementById('resetView').addEventListener('click', fitImage);

    // ---------- Mask brush controls ----------
    const brushSize = document.getElementById('brushSize');
    const brushSizeLabel = document.getElementById('brushSizeLabel');
    const brushErase = document.getElementById('brushErase');
    if (brushSize) {
        brushSize.addEventListener('input', () => {
            state.brush.size = Number(brushSize.value);
            brushSizeLabel.textContent = `${brushSize.value}px`;
        });
    }
    function toggleErase() {
        state.brush.erase = !state.brush.erase;
        if (brushErase) brushErase.setAttribute('aria-pressed', state.brush.erase ? 'true' : 'false');
        if (brushErase) brushErase.classList.toggle('active', state.brush.erase);
    }
    if (brushErase) brushErase.addEventListener('click', toggleErase);
    const brushClear = document.getElementById('brushClear');
    if (brushClear) brushClear.addEventListener('click', clearActiveMask);

    // ---------- Polygon dock buttons ----------
    // Touch has no reliable double-click, so the dock is the primary way to close a
    // polygon on a tablet; the keyboard and double-tap paths still work too.
    document.getElementById('polygonFinish')?.addEventListener('click', finishPolygon);
    document.getElementById('polygonCancel')?.addEventListener('click', cancelPolygon);

    // ---------- Crop dock buttons ----------
    const cropClearBtn = document.getElementById('cropClear');
    if (cropClearBtn) cropClearBtn.addEventListener('click', clearCrop);
    const cropDownloadBtn = document.getElementById('cropDownload');
    if (cropDownloadBtn) cropDownloadBtn.addEventListener('click', async () => {
        if (!state.annotation?.crop_box) { alert('Draw a crop region first.'); return; }
        // Persist any pending crop edit so the server can render the current box.
        if (state.saveTimer) { clearTimeout(state.saveTimer); state.saveTimer = null; }
        await flushSave();
        if (!state.annotation?.id) return;
        window.open(`/api/v1/annotations/${state.annotation.id}/crop`, '_blank');
    });

    // ---------- Footer buttons ----------
    // Nothing gets submitted per-image; Prev/Next just flush the pending autosave
    // (so a fast click never drops an edit) and move to the adjacent image. The
    // patient's images are only finalized together when the diagnosis is submitted.
    async function flushPendingSave() {
        if (state.saveTimer) { clearTimeout(state.saveTimer); state.saveTimer = null; }
        await flushSave();
    }
    document.getElementById('prevBtn').addEventListener('click', async () => {
        await flushPendingSave();
        loadIndex(state.queueIndex - 1);
    });
    async function goNext() {
        await flushPendingSave();
        // A failed autosave leaves the edits pending; don't walk away from them.
        if (state.savePending) {
            setPill('Error - retry', 'error');
            showHUD('Save failed — try again before moving on');
            return;
        }
        if (nextGateBlocks()) return;
        loadIndex(state.queueIndex + 1);
    }
    document.getElementById('nextBtn').addEventListener('click', goNext);
    document.getElementById('saveBtn').addEventListener('click', flushPendingSave);
    document.getElementById('discardBtn').addEventListener('click', async () => {
        const reason = prompt('Reason for discarding this image:');
        if (!reason) return;
        try {
            await ensureAnnotation();
            await api(`/api/v1/annotations/${state.annotation.id}/discard`, {
                method: 'POST', body: JSON.stringify({reason}),
            });
            state.queue.splice(state.queueIndex, 1);
            loadIndex(state.queueIndex);
        } catch (err) {
            alert('Discard failed: ' + err.message);
        }
    });

    // ---------- Tool dock ----------
    document.querySelectorAll('#toolDock button[data-tool]').forEach(btn => {
        btn.addEventListener('click', () => {
            if (btn.disabled) return;
            setTool(btn.dataset.tool);
        });
    });

    // ---------- Shortcut overlay ----------
    const overlay = document.getElementById('shortcutOverlay');
    if (overlay) {
        document.getElementById('shortcutsBtn').addEventListener('click', () => {
            overlay.dataset.open = 'true';
        });
    }

    // Hide context menu when clicking outside
    if (state.stage) {
        state.stage.on('click', (e) => {
            const menu = document.getElementById('contextMenu');
            if (menu && e.target === state.imageLayer.findOne('Image')) {
                menu.style.display = 'none';
            }
        });
    }
    // Bind context menu actions
    document.querySelectorAll('.ctx-btn').forEach(btn => {
        btn.addEventListener('click', async (e) => {
            const menu = document.getElementById('contextMenu');
            if (!menu) return;
            menu.style.display = 'none';
            const rid = menu.dataset.targetId;
            if (!rid) return;
            const action = btn.dataset.action;
            if (action === 'delete') {
                await deleteRegion(rid);
            } else if (action === 'label') {
                const val = btn.dataset.val;
                await patchRegion(rid, {lesion_label: val});
            }
        });
    });

    // Close menus on general canvas click/drag (pointerdown so a tap counts too)
    stageEl.addEventListener('pointerdown', () => {
        const menu = document.getElementById('contextMenu');
        if (menu) menu.style.display = 'none';
    });

    // Close region editor when clicking/tapping outside of it
    document.addEventListener('pointerdown', (e) => {
        const editor = document.getElementById('regionEditor');
        if (editor && editor.style.display === 'block') {
            if (editor.contains(e.target)) return;
            const menu = document.getElementById('contextMenu');
            if (menu && menu.contains(e.target)) return;
            if (e.target.closest('.region-row')) return;
            if (stageEl.contains(e.target)) return;
            
            selectRegion(null);
        }
    });

    // ---------- Keyboard ----------
    document.addEventListener('keydown', (e) => {
        if (e.target.matches('input, textarea, select')) return;
        if (e.key === '?') { overlay.dataset.open = overlay.dataset.open === 'true' ? 'false' : 'true'; return; }
        if (e.key === 'Escape') {
            if (state.polygonDraft) { cancelPolygon(); return; }
            overlay.dataset.open = 'false';
            return;
        }
        if (e.key === '[') { loadIndex(state.queueIndex - 1); return; }
        if (e.key === ']') { goNext(); return; }
        if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 's') {
            e.preventDefault();
            if (state.saveTimer) { clearTimeout(state.saveTimer); state.saveTimer = null; }
            flushSave();
            showHUD('Saved ✓');
            return;
        }
        if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'z') {
            e.preventDefault();
            if (e.shiftKey) redo();
            else undo();
            return;
        }
        if (e.key === 'Delete' || e.key === 'Backspace') {
            if (state.selectedRegionId) {
                e.preventDefault();
                deleteRegion(state.selectedRegionId);
            } else if (state.tool === 'crop' && state.cropNode) {
                e.preventDefault();
                clearCrop();
            }
            return;
        }
        // Enter closes an in-flight polygon before it means "next image".
        if (e.key === 'Enter' && state.polygonDraft) { e.preventDefault(); finishPolygon(); return; }
        if (e.key === 'Enter') { e.preventDefault(); document.getElementById('nextBtn').click(); return; }
        const lower = e.key.toLowerCase();
        if (lower === 'v') { setTool('pan'); return; }
        if (lower === 'b') { setTool('bbox'); return; }
        if (lower === 'p') { setTool('polygon'); return; }
        if (lower === 'm') { setTool('mask'); return; }
        if (lower === 'c') { setTool('crop'); return; }
        if (lower === 'e' && state.tool === 'mask') { toggleErase(); return; }
        if (lower === 'd') { document.getElementById('discardBtn').click(); return; }
    });

    window.addEventListener('beforeunload', (e) => {
        if (state.dirty) {
            e.preventDefault();
            e.returnValue = '';
        }
    });

    // ---------- Patient selector ----------
    // The workbench is patient-first (the /annotate route redirects to the /patients
    // picker when no patient is chosen), so switching patients is a real navigation,
    // not an in-page queue swap.
    async function loadPatientList() {
        const sel = document.getElementById('patientSelect');
        if (!sel) return;
        try {
            const [queueData, dxData] = await Promise.all([
                api('/api/v1/images/patients'),
                api('/api/v1/patients').catch(() => ({items: []})),
            ]);
            const dxStatus = new Map(dxData.items.map(p => [p.patient_code, p.my_diagnosis_status]));
            sel.innerHTML = '';
            for (const p of queueData.items) {
                const o = document.createElement('option');
                o.value = p.patient_code;
                const dxMark = dxStatus.get(p.patient_code) === 'submitted' ? ' · dx ✓' : '';
                o.textContent = `${p.patient_code} — ${p.remaining} left / ${p.total}${dxMark}`;
                sel.appendChild(o);
            }
            sel.value = state.patient;
        } catch (e) { /* non-fatal: dropdown stays empty */ }
    }

    document.getElementById('patientSelect')?.addEventListener('change', (e) => {
        if (e.target.value) window.location.href = '/annotate?patient=' + encodeURIComponent(e.target.value);
    });

    // ---------- Boot ----------
    async function boot() {
        setPill('Loading...', 'saving');
        initStage();
        bindRegionEditor();
        state.patient = new URLSearchParams(location.search).get('patient') || '';
        try {
            const init = window.ANNOTATE_INIT?.initialImageId;
            if (init) {
                // Deep link to a single image (e.g. from the patient diagnose page's
                // image grid). Load it directly, then derive its patient for queueing.
                await loadByImageId(init);
                if (!state.patient) state.patient = state.image?.patient_code || '';
                if (state.patient) {
                    await fetchQueue();
                    loadPatientList();
                    state.queueIndex = state.queue.findIndex(i => i.id === init);
                    progress.textContent = state.queueIndex === -1
                        ? '(deep link)'
                        : `${state.queueIndex + 1} / ${state.queue.length}${state.queueCursor ? '+' : ''}`;
                } else {
                    progress.textContent = '(deep link)';
                }
            } else if (state.patient) {
                await fetchQueue();
                loadPatientList();
                if (state.queue.length) {
                    await loadIndex(0);
                } else {
                    goToPatientDiagnose();
                }
            } else {
                // Shouldn't happen -- the server redirects a bare /annotate to /patients.
                window.location.href = '/patients';
            }
        } catch (err) {
            console.error(err);
            setPill('Error', 'error');
            viewerEmpty.textContent = 'Failed to load: ' + err.message;
        }
    }
    boot();
})();
