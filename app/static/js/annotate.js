// Annotate workbench controller.
//
// Responsibilities:
//   - Server queue cursor through unannotated images.
//   - Annotation lifecycle: open draft, autosave Layer-B form fields, submit/discard.
//   - Konva stage for the image + region layers (Layer C).
//   - Tools: pan/select, bbox, polygon, mask (brush paint -> png_b64 geometry).
//   - Undo/redo command stack (limit 50).
//   - Region list <-> canvas selection mirror, per-region attribute editor.
//   - Keyboard shortcuts.
//
// State convention: `state.regions` is a Map(region_id -> server snapshot). The Konva nodes
// are kept in `state.nodes` keyed by region_id. The list/editor render from `state.regions`.

(() => {
    const DX_KEYS = ['NORMAL', 'CIN1', 'CIN2', 'CIN3', 'AIS', 'INVASIVE_CANCER',
        'INFLAMMATION', 'INFECTION', 'EROSION'];
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

    const state = {
        queue: [],
        queueCursor: null,
        queueIndex: -1,
        patient: '',          // '' = all patients
        image: null,
        annotation: null,
        dirty: false,
        savePending: null,
        saveTimer: null,

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

    // ---------- Konva stage ----------
    function initStage() {
        if (state.stage) state.stage.destroy();
        state.stage = new Konva.Stage({
            container: 'stage',
            width: stageEl.clientWidth,
            height: stageEl.clientHeight,
            draggable: false,
        });
        state.imageLayer = new Konva.Layer({listening: false});
        state.regionLayer = new Konva.Layer();
        state.toolLayer = new Konva.Layer();
        state.stage.add(state.imageLayer);
        state.stage.add(state.regionLayer);
        state.stage.add(state.toolLayer);

        state.transformer = new Konva.Transformer({
            rotateEnabled: false,
            anchorSize: 8,
            borderStroke: '#4f8cff',
            anchorStroke: '#4f8cff',
            anchorFill: '#fff',
        });
        state.regionLayer.add(state.transformer);

        attachStageEvents();
        window.addEventListener('resize', resizeStage);
    }

    function resizeStage() {
        if (!state.stage) return;
        state.stage.width(stageEl.clientWidth);
        state.stage.height(stageEl.clientHeight);
        fitImage();
    }

    function fitImage() {
        const img = state.imageLayer.findOne('Image');
        if (!img) return;
        const sw = state.stage.width(), sh = state.stage.height();
        const iw = img.width(), ih = img.height();
        const scale = Math.min(sw / iw, sh / ih);
        state.scale = scale;
        state.offset = {x: (sw - iw * scale) / 2, y: (sh - ih * scale) / 2};
        state.stage.scale({x: scale, y: scale});
        state.stage.position(state.offset);
        state.stage.batchDraw();
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
            let newScale = direction > 0 ? oldScale * factor : oldScale / factor;
            newScale = Math.max(0.1, Math.min(8, newScale));
            state.scale = newScale;
            state.stage.scale({x: newScale, y: newScale});
            state.stage.position({
                x: pointer.x - mousePointTo.x * newScale,
                y: pointer.y - mousePointTo.y * newScale,
            });
            state.stage.batchDraw();
        });

        // Space+drag pan.
        let panning = false;
        let lastPointer = null;
        window.addEventListener('keydown', (e) => {
            if (e.code === 'Space' && !e.target.matches('input, textarea, select')) {
                panning = true;
                stageWrap.style.cursor = 'grab';
                e.preventDefault();
            }
        });
        window.addEventListener('keyup', (e) => {
            if (e.code === 'Space') {
                panning = false;
                stageWrap.style.cursor = '';
                lastPointer = null;
            }
        });
        state.stage.on('mousedown', (e) => {
            if (panning) {
                lastPointer = state.stage.getPointerPosition();
                stageWrap.style.cursor = 'grabbing';
            }
        });
        state.stage.on('mousemove', () => {
            if (!panning || !lastPointer) return;
            const cur = state.stage.getPointerPosition();
            state.stage.position({
                x: state.stage.x() + (cur.x - lastPointer.x),
                y: state.stage.y() + (cur.y - lastPointer.y),
            });
            lastPointer = cur;
            state.stage.batchDraw();
        });
        state.stage.on('mouseup', () => {
            if (panning) stageWrap.style.cursor = 'grab';
            lastPointer = null;
        });

        // Click empty stage in pan tool -> deselect.
        state.stage.on('click tap', (e) => {
            if (state.tool === 'pan' && e.target === state.stage) {
                selectRegion(null);
            }
        });

        // Tool entry points
        let bboxStart = null;
        let bboxRect = null;
        state.stage.on('mousedown.bboxtool', (e) => {
            if (state.tool !== 'bbox' || panning) return;
            const p = imagePointer();
            if (!p) return;
            bboxStart = p;
            bboxRect = new Konva.Rect({
                x: p.x, y: p.y, width: 1, height: 1,
                stroke: '#4f8cff', strokeWidth: 2,
                dash: [4, 4],
                listening: false,
            });
            state.toolLayer.add(bboxRect);
        });
        state.stage.on('mousemove.bboxtool', () => {
            if (!bboxRect || !bboxStart) return;
            const p = imagePointer();
            if (!p) return;
            bboxRect.x(Math.min(p.x, bboxStart.x));
            bboxRect.y(Math.min(p.y, bboxStart.y));
            bboxRect.width(Math.abs(p.x - bboxStart.x));
            bboxRect.height(Math.abs(p.y - bboxStart.y));
            state.toolLayer.batchDraw();
        });
        state.stage.on('mouseup.bboxtool', async () => {
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

        // Polygon tool: click to add vertex, double-click last to close.
        state.stage.on('click.polygontool', (e) => {
            if (state.tool !== 'polygon' || panning) return;
            const p = imagePointer();
            if (!p) return;
            if (!state.polygonDraft) {
                state.polygonDraft = {
                    points: [[p.x, p.y]],
                    line: new Konva.Line({
                        points: [p.x, p.y, p.x, p.y],
                        stroke: '#4f8cff',
                        strokeWidth: 2,
                        closed: false,
                        listening: false,
                    }),
                };
                state.toolLayer.add(state.polygonDraft.line);
            } else {
                state.polygonDraft.points.push([p.x, p.y]);
                const flat = state.polygonDraft.points.flat();
                state.polygonDraft.line.points([...flat, p.x, p.y]);
            }
            state.toolLayer.batchDraw();
        });
        state.stage.on('mousemove.polygontool', () => {
            if (state.tool !== 'polygon' || !state.polygonDraft) return;
            const p = imagePointer();
            if (!p) return;
            const flat = state.polygonDraft.points.flat();
            state.polygonDraft.line.points([...flat, p.x, p.y]);
            state.toolLayer.batchDraw();
        });
        state.stage.on('dblclick.polygontool', async () => {
            if (state.tool !== 'polygon' || !state.polygonDraft) return;
            const pts = state.polygonDraft.points;
            state.polygonDraft.line.destroy();
            state.polygonDraft = null;
            state.toolLayer.batchDraw();
            if (pts.length < 3) return;
            const geom = {points: pts.map(([x, y]) => [Math.round(x), Math.round(y)])};
            await createRegion('polygon', geom);
        });

        // Mask tool: brush-paint into an offscreen canvas at native resolution.
        let masking = false;
        let lastMaskPt = null;
        state.stage.on('mousedown.masktool', (e) => {
            if (state.tool !== 'mask' || panning) return;
            const p = imagePointer();
            if (!p) return;
            if (state.annotation && state.annotation.status && state.annotation.status !== 'draft') return;
            ensureActiveMask();
            if (!state.activeMask) return;
            masking = true;
            lastMaskPt = p;
            paintDab(state.activeMask.canvas, p.x, p.y);
            updateActiveMaskDisplay();
        });
        state.stage.on('mousemove.masktool', () => {
            if (!masking || !state.activeMask) return;
            const p = imagePointer();
            if (!p) return;
            paintStroke(state.activeMask.canvas, lastMaskPt.x, lastMaskPt.y, p.x, p.y);
            lastMaskPt = p;
            updateActiveMaskDisplay();
        });
        const finishStroke = () => {
            if (!masking) return;
            masking = false;
            lastMaskPt = null;
            scheduleMaskSave();
        };
        state.stage.on('mouseup.masktool', finishStroke);
        state.stage.on('mouseleave.masktool', finishStroke);

        // Crop tool: drag one rectangle that becomes the annotation's crop region.
        let cropStart = null;
        let cropDraft = null;
        state.stage.on('mousedown.croptool', (e) => {
            if (state.tool !== 'crop' || panning) return;
            if (state.annotation && state.annotation.status && state.annotation.status !== 'draft') return;
            const p = imagePointer();
            if (!p) return;
            cropStart = p;
            cropDraft = new Konva.Rect({
                x: p.x, y: p.y, width: 1, height: 1,
                stroke: '#ffd166', strokeWidth: 2, dash: [6, 4], listening: false,
            });
            state.toolLayer.add(cropDraft);
        });
        state.stage.on('mousemove.croptool', () => {
            if (!cropDraft || !cropStart) return;
            const p = imagePointer();
            if (!p) return;
            cropDraft.x(Math.min(p.x, cropStart.x));
            cropDraft.y(Math.min(p.y, cropStart.y));
            cropDraft.width(Math.abs(p.x - cropStart.x));
            cropDraft.height(Math.abs(p.y - cropStart.y));
            state.toolLayer.batchDraw();
        });
        state.stage.on('mouseup.croptool', () => {
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
    }

    function clearCrop() {
        if (state.annotation && state.annotation.status && state.annotation.status !== 'draft') return;
        if (state.cropNode) { state.cropNode.destroy(); state.cropNode = null; }
        state.regionLayer.batchDraw();
        updateCropInfo(null);
        if (state.annotation && state.annotation.crop_box) {
            // Zero-area box tells the server to clear the crop.
            queueAutosave({crop_box: {x: 0, y: 0, w: 0, h: 0}});
        }
        if (state.annotation) state.annotation.crop_box = null;
    }

    function renderCropFromState() {
        const box = state.annotation?.crop_box;
        if (box && box.w && box.h) drawCropBox(box);
        else updateCropInfo(null);
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
        document.querySelectorAll('#toolDock button[data-tool]').forEach(b => {
            b.setAttribute('aria-pressed', b.dataset.tool === name ? 'true' : 'false');
        });
        // Drop any in-flight polygon when switching away.
        if (name !== 'polygon' && state.polygonDraft) {
            state.polygonDraft.line.destroy();
            state.polygonDraft = null;
            state.toolLayer.batchDraw();
        }
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
        node.on('dragend', async (e) => {
            if (region.region_type === 'mask') return;  // masks are full-frame, not draggable
            const geom = readGeometry(node, region.region_type);
            await patchRegion(region.id, {geometry: geom});
        });
        node.on('transformend', async (e) => {
            if (region.region_type !== 'bbox') return;
            const g = {
                x: Math.round(node.x()),
                y: Math.round(node.y()),
                w: Math.round(node.width() * node.scaleX()),
                h: Math.round(node.height() * node.scaleY()),
            };
            node.scale({x: 1, y: 1});
            node.width(g.w); node.height(g.h);
            await patchRegion(region.id, {geometry: g});
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
            if (node && region && region.region_type === 'bbox' && state.tool === 'pan') {
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
                        <div>#${i} ${region.region_type} - ${label}</div>
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
    }

    function renderRegionEditor() {
        const editor = document.getElementById('regionEditor');
        const region = state.regions.get(state.selectedRegionId);
        if (!region) {
            editor.style.display = 'none';
            return;
        }
        editor.style.display = '';
        editor.querySelectorAll('[data-rfield]').forEach(el => {
            const key = el.dataset.rfield;
            const val = region[key];
            if (el.type === 'checkbox') el.checked = !!val;
            else el.value = val == null ? '' : String(val);
        });
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
        // Clear previous image + regions.
        state.imageLayer.destroyChildren();
        state.regionLayer.destroyChildren();
        state.regionLayer.add(state.transformer = new Konva.Transformer({
            rotateEnabled: false, anchorSize: 8,
            borderStroke: '#4f8cff', anchorStroke: '#4f8cff', anchorFill: '#fff',
        }));
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
    }

    // ---------- Queue navigation (unchanged logic from Phase 2) ----------
    function queueUrl(cursor) {
        let url = '/api/v1/images?status=unannotated&limit=100';
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
                setPill('Queue empty', '');
                viewerEmpty.textContent = 'No more unannotated images in the queue.';
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
        `;
    }

    // ---------- Form (Layer B) ----------
    function renderForm() {
        const ann = state.annotation;
        document.querySelectorAll('.dx-btn').forEach(btn => {
            const picked = ann?.diagnosis?.colposcopic_impression === btn.dataset.dx;
            btn.setAttribute('aria-pressed', picked ? 'true' : 'false');
        });
        const confVal = ann?.diagnosis?.confidence ?? 3;
        document.getElementById('confidence').value = confVal;
        document.getElementById('confidenceLabel').textContent = `${confVal} / 5`;
        document.querySelectorAll('[data-field]').forEach(el => {
            const path = el.dataset.field;
            if (path === 'diagnosis.confidence') return;
            const val = getNested(ann, path);
            if (el.type === 'checkbox') el.checked = !!val;
            else el.value = val == null ? '' : String(val);
        });
        updateScoreTotals();
    }

    // ---------- Reid / Swede scoring totals ----------
    function interpretReid(t) {
        if (t <= 2) return 'Likely CIN 1 (low-grade)';
        if (t <= 4) return 'Overlapping CIN 1–2';
        return 'Likely CIN 2–3 (high-grade)';
    }
    function interpretSwede(t) {
        if (t <= 4) return 'Likely low-grade / benign';
        if (t <= 7) return 'Intermediate';
        return 'Likely high-grade (consider biopsy/treatment)';
    }
    function updateScoreTotals() {
        const get = f => {
            const el = document.querySelector(`[data-field="scoring.${f}"]`);
            return (el && el.value !== '') ? Number(el.value) : null;
        };
        const sets = [
            {parts: ['reid_margin', 'reid_color', 'reid_vessels', 'reid_iodine'],
             max: 8, tot: 'reidTotal', intp: 'reidInterp', fn: interpretReid},
            {parts: ['swede_aceto', 'swede_margin', 'swede_vessels', 'swede_size', 'swede_iodine'],
             max: 10, tot: 'swedeTotal', intp: 'swedeInterp', fn: interpretSwede},
        ];
        for (const s of sets) {
            const totEl = document.getElementById(s.tot);
            const intpEl = document.getElementById(s.intp);
            if (!totEl) continue;
            const vals = s.parts.map(get);
            const done = vals.every(v => v !== null);
            const sum = vals.reduce((a, b) => a + (b || 0), 0);
            totEl.textContent = done ? `${sum} / ${s.max}` : `– / ${s.max}`;
            if (intpEl) intpEl.textContent = done ? s.fn(sum) : 'Score all criteria for a total.';
        }
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
        if (state.annotation && state.annotation.status !== 'draft') {
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
        if (!state.savePending) return;
        const body = state.savePending;
        state.savePending = null;
        setPill('Saving...', 'saving');
        try {
            // Lazily create the draft now that we have something to persist.
            if (!state.annotation?.id) {
                const created = await api('/api/v1/annotations', {
                    method: 'POST',
                    body: JSON.stringify({image_id: state.image.id}),
                });
                // Preserve any local edits the user already made while we were id-less.
                const local = state.annotation || {};
                state.annotation = deepMerge(created, local);
            }
            await api(`/api/v1/annotations/${state.annotation.id}`, {
                method: 'PATCH',
                body: JSON.stringify(body),
            });
            state.dirty = false;
            setPill('Saved', 'saved');
        } catch (err) {
            state.savePending = deepMerge(body, state.savePending || {});
            setPill('Error - retry', 'error');
            console.error('autosave failed', err);
        }
    }

    // Diagnosis buttons. Also flush the slider's current confidence so a fresh draft
    // doesn't fail submit just because the user never touched the slider.
    document.querySelectorAll('.dx-btn').forEach(btn => {
        btn.addEventListener('click', () => {
            const dx = btn.dataset.dx;
            document.querySelectorAll('.dx-btn').forEach(b => b.setAttribute('aria-pressed', 'false'));
            btn.setAttribute('aria-pressed', 'true');
            const confidence = Number(document.getElementById('confidence').value);
            queueAutosave({diagnosis: {colposcopic_impression: dx, confidence}});
        });
    });
    document.querySelectorAll('[data-field]').forEach(el => {
        const evt = (el.tagName === 'SELECT' || el.type === 'checkbox') ? 'change' : 'input';
        el.addEventListener(evt, () => {
            if (el.id === 'confidence') {
                document.getElementById('confidenceLabel').textContent = `${el.value} / 5`;
            }
            if (el.dataset.field.startsWith('scoring.')) updateScoreTotals();
            queueAutosave(collectPatchFromField(el));
        });
    });

    // ---------- Brightness / contrast ----------
    function updateFilter() {
        const b = document.getElementById('brightness').value;
        const c = document.getElementById('contrast').value;
        stageWrap.style.setProperty('--img-filter', `brightness(${b}%) contrast(${c}%)`);
    }
    document.getElementById('brightness').addEventListener('input', updateFilter);
    document.getElementById('contrast').addEventListener('input', updateFilter);
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
    document.getElementById('prevBtn').addEventListener('click', () => loadIndex(state.queueIndex - 1));
    document.getElementById('nextBtn').addEventListener('click', () => loadIndex(state.queueIndex + 1));
    document.getElementById('skipBtn').addEventListener('click', () => loadIndex(state.queueIndex + 1));
    document.getElementById('saveBtn').addEventListener('click', () => {
        if (state.saveTimer) { clearTimeout(state.saveTimer); state.saveTimer = null; }
        flushSave();
    });
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
    document.getElementById('submitBtn').addEventListener('click', submit);
    async function submit() {
        if (state.saveTimer) { clearTimeout(state.saveTimer); state.saveTimer = null; }
        await flushSave();
        try {
            await ensureAnnotation();
            setPill('Submitting...', 'saving');
            // Send a final snapshot of the diagnosis block so any UI defaults the user
            // didn't explicitly touch (e.g. confidence slider) are validated against.
            const conf = Number(document.getElementById('confidence').value);
            const dxBtn = document.querySelector('.dx-btn[aria-pressed="true"]');
            const finalSnap = {diagnosis: {confidence: conf}};
            if (dxBtn) finalSnap.diagnosis.colposcopic_impression = dxBtn.dataset.dx;
            await api(`/api/v1/annotations/${state.annotation.id}/submit`, {
                method: 'POST', body: JSON.stringify(finalSnap),
            });
            state.queue.splice(state.queueIndex, 1);
            await loadIndex(state.queueIndex);
        } catch (err) {
            setPill('Error - retry', 'error');
            const detail = err.body?.error?.details?.[0]?.msg;
            alert('Submit failed: ' + (detail || err.message));
        }
    }

    // ---------- Tool dock ----------
    document.querySelectorAll('#toolDock button[data-tool]').forEach(btn => {
        btn.addEventListener('click', () => {
            if (btn.disabled) return;
            setTool(btn.dataset.tool);
        });
    });

    // ---------- Shortcut overlay ----------
    const overlay = document.getElementById('shortcutOverlay');
    document.getElementById('shortcutsBtn').addEventListener('click', () => {
        overlay.dataset.open = overlay.dataset.open === 'true' ? 'false' : 'true';
    });

    // ---------- Keyboard ----------
    document.addEventListener('keydown', (e) => {
        if (e.target.matches('input, textarea, select')) return;
        if (e.key === '?') { overlay.dataset.open = overlay.dataset.open === 'true' ? 'false' : 'true'; return; }
        if (e.key === 'Escape') {
            if (state.polygonDraft) {
                state.polygonDraft.line.destroy();
                state.polygonDraft = null;
                state.toolLayer.batchDraw();
                return;
            }
            overlay.dataset.open = 'false';
            return;
        }
        if (e.key === '[') { loadIndex(state.queueIndex - 1); return; }
        if (e.key === ']') { loadIndex(state.queueIndex + 1); return; }
        if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 's') {
            e.preventDefault();
            if (state.saveTimer) { clearTimeout(state.saveTimer); state.saveTimer = null; }
            flushSave();
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
        if (e.key === 'Enter') { e.preventDefault(); submit(); return; }
        const lower = e.key.toLowerCase();
        if (lower === 'v') { setTool('pan'); return; }
        if (lower === 'b') { setTool('bbox'); return; }
        if (lower === 'p') { setTool('polygon'); return; }
        if (lower === 'm') { setTool('mask'); return; }
        if (lower === 'c') { setTool('crop'); return; }
        if (lower === 'e' && state.tool === 'mask') { toggleErase(); return; }
        if (lower === 'd') { document.getElementById('discardBtn').click(); return; }
        const idx = Number(e.key) - 1;
        if (idx >= 0 && idx < DX_KEYS.length) {
            const btn = document.querySelector(`.dx-btn[data-dx="${DX_KEYS[idx]}"]`);
            if (btn) btn.click();
        }
    });

    window.addEventListener('beforeunload', (e) => {
        if (state.dirty) {
            e.preventDefault();
            e.returnValue = '';
        }
    });

    // ---------- Patient selector ----------
    async function loadPatientList() {
        const sel = document.getElementById('patientSelect');
        if (!sel) return;
        try {
            const d = await api('/api/v1/images/patients');
            sel.innerHTML = '<option value="">All patients</option>';
            for (const p of d.items) {
                const o = document.createElement('option');
                o.value = p.patient_code;
                o.textContent = `${p.patient_code} — ${p.remaining} left / ${p.total}`;
                sel.appendChild(o);
            }
            sel.value = state.patient;
        } catch (e) { /* non-fatal: keep "All patients" */ }
    }

    async function selectPatient(code) {
        state.patient = code || '';
        state.queue = [];
        state.queueCursor = null;
        state.queueIndex = -1;
        await fetchQueue();
        if (state.queue.length) {
            await loadIndex(0);
        } else {
            setPill('Queue empty', '');
            viewerEmpty.textContent = state.patient
                ? `No unannotated images left for ${state.patient}.`
                : 'You have no unannotated images.';
            viewerEmpty.dataset.show = 'true';
            progress.textContent = '0 / 0';
        }
    }

    document.getElementById('patientSelect')?.addEventListener('change', (e) => {
        selectPatient(e.target.value).catch(err => {
            console.error(err);
            setPill('Error', 'error');
        });
    });

    // ---------- Boot ----------
    async function boot() {
        setPill('Loading...', 'saving');
        initStage();
        bindRegionEditor();
        // Optional ?patient=PAT-001 deep link from the admin patients table.
        state.patient = new URLSearchParams(location.search).get('patient') || '';
        try {
            await fetchQueue();
            loadPatientList();   // populate dropdown in the background
            const init = window.ANNOTATE_INIT?.initialImageId;
            if (init) {
                state.queueIndex = state.queue.findIndex(i => i.id === init);
                if (state.queueIndex === -1) {
                    await loadByImageId(init);
                    progress.textContent = '(deep link)';
                } else {
                    await loadIndex(state.queueIndex);
                }
            } else if (state.queue.length) {
                await loadIndex(0);
            } else {
                setPill('Queue empty', '');
                viewerEmpty.textContent = state.patient
                    ? `No unannotated images left for ${state.patient}.`
                    : 'You have no unannotated images.';
            }
        } catch (err) {
            console.error(err);
            setPill('Error', 'error');
            viewerEmpty.textContent = 'Failed to load: ' + err.message;
        }
    }
    boot();
})();
