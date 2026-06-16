// Review workbench. Loads the pending review queue, renders the selected
// annotation in read-only mode, and lets the reviewer approve/reject.

(() => {
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
    };

    const state = {
        queue: [],
        index: -1,
        imageDims: null,  // {w, h, naturalW, naturalH}
    };

    const queueList = document.getElementById('queueList');
    const queueCount = document.getElementById('queueCount');
    const viewerEmpty = document.getElementById('viewerEmpty');
    const reviewImg = document.getElementById('reviewImg');
    const overlay = document.getElementById('regionOverlay');
    const sidePanel = document.getElementById('side');
    const annotatorLine = document.getElementById('annotatorLine');
    const approveBtn = document.getElementById('approveBtn');
    const rejectBtn = document.getElementById('rejectBtn');
    const commentBox = document.getElementById('reviewComment');

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

    async function fetchQueue() {
        const data = await api('/api/v1/review/queue?limit=100');
        state.queue = data.items;
        renderQueue();
    }

    function renderQueue() {
        queueCount.textContent = `${state.queue.length} pending`;
        if (!state.queue.length) {
            queueList.innerHTML = '<div class="empty-q">No annotations pending review.</div>';
            return;
        }
        queueList.innerHTML = state.queue.map((a, i) => {
            const dx = a.diagnosis?.colposcopic_impression || '(no dx)';
            const color = LABEL_COLOR[dx] || '#7aa3ff';
            return `
                <div class="queue-item" data-idx="${i}" aria-selected="${i === state.index ? 'true' : 'false'}">
                    <img src="/api/v1/images/${a.image_id}/file" alt="" loading="lazy" onerror="this.style.background='#000'">
                    <div>
                        <div class="label" style="color:${color}">${dx}</div>
                        <div class="meta">v${a.version} - ${a.submitted_at ? new Date(a.submitted_at).toLocaleString() : '-'}</div>
                    </div>
                </div>`;
        }).join('');
        queueList.querySelectorAll('.queue-item').forEach(el => {
            el.addEventListener('click', () => selectIndex(Number(el.dataset.idx)));
        });
    }

    async function selectIndex(idx) {
        if (idx < 0 || idx >= state.queue.length) return;
        state.index = idx;
        const ann = state.queue[idx];
        renderQueue();  // refresh aria-selected highlight
        renderAnnotation(ann);
        await loadImage(ann);
        // When a baked annotated image exists (bbox + polygon + mask already drawn
        // and stored in the bucket), show it as-is; otherwise fall back to the
        // original image with a client-side region overlay (bbox/polygon only).
        if (ann.has_crop_image) {
            overlay.innerHTML = '';
        } else {
            renderRegions(ann);
        }
        approveBtn.disabled = false;
        rejectBtn.disabled = false;
        commentBox.value = '';
    }

    function dxBadge(label) {
        if (!label) return '<span class="muted">(none)</span>';
        const color = LABEL_COLOR[label] || '#7aa3ff';
        return `<span class="dx-badge" style="background:${color}">${label}</span>`;
    }

    function kv(rows) {
        return `<div class="kv">${rows.map(([k, v]) => `<div class="k">${k}</div><div class="v">${v ?? '<span class="muted">-</span>'}</div>`).join('')}</div>`;
    }

    function reidInterp(t) {
        if (t == null) return '';
        if (t <= 2) return ' — Likely CIN 1';
        if (t <= 4) return ' — Overlapping CIN 1–2';
        return ' — Likely CIN 2–3';
    }
    function swedeInterp(t) {
        if (t == null) return '';
        if (t <= 4) return ' — Likely low-grade';
        if (t <= 7) return ' — Intermediate';
        return ' — Likely high-grade';
    }

    function renderAnnotation(ann) {
        const q = ann.quality || {};
        const a = ann.anatomy || {};
        const f = ann.features || {};
        const d = ann.diagnosis || {};
        const sc = ann.scoring || {};
        const regionCount = (ann.regions || []).length;

        sidePanel.innerHTML = `
            <div class="group">
                <h4>Diagnosis</h4>
                ${kv([
                    ['Impression', dxBadge(d.colposcopic_impression)],
                    ['Histopath', d.histopathology_result || '<span class="muted">-</span>'],
                    ['Confidence', d.confidence != null ? `${d.confidence}/5` : '-'],
                    ['Notes', d.notes ? escapeHtml(d.notes) : '<span class="muted">-</span>'],
                ])}
            </div>
            <div class="group">
                <h4>Regions (${regionCount})</h4>
                ${regionCount ? (ann.regions.map(r => `
                    <div class="kv" style="margin-bottom:8px;">
                        <div class="k">Type</div><div class="v">${r.region_type}</div>
                        <div class="k">Label</div><div class="v">${dxBadge(r.lesion_label)}</div>
                        ${r.lesion_location_clock != null ? `<div class="k">Clock</div><div class="v">${r.lesion_location_clock}</div>` : ''}
                        ${r.lesion_quadrant ? `<div class="k">Quadrant</div><div class="v">${r.lesion_quadrant}</div>` : ''}
                    </div>
                `).join('<hr style="border:0;border-top:1px solid var(--border);margin:6px 0;">')) : '<div class="muted" style="font-size:12px;">No regions drawn.</div>'}
            </div>
            <div class="group">
                <h4>Quality</h4>
                ${kv([
                    ['Image quality', q.image_quality],
                    ['Blur', q.blur_present === true ? 'Yes' : q.blur_present === false ? 'No' : null],
                    ['Blood', q.blood_present === true ? 'Yes' : q.blood_present === false ? 'No' : null],
                    ['Lighting', q.lighting_issue],
                    ['Training-usable', q.usable_for_training === true ? 'Yes' : q.usable_for_training === false ? 'No' : null],
                ])}
            </div>
            <div class="group">
                <h4>Anatomy</h4>
                ${kv([
                    ['SCJ', a.scj_visibility],
                    ['TZ type', a.transformation_zone_type],
                    ['TZ visibility', a.tz_visibility],
                ])}
            </div>
            <div class="group">
                <h4>Global features</h4>
                ${kv([
                    ['Acetowhitening', f.acetowhitening_severity],
                    ['Iodine', f.iodine_pattern],
                    ['Vascular', f.vascular_pattern],
                    ['Color', f.color_tone],
                    ['Surface', f.surface_contour],
                    ['Atypical vessels', f.atypical_vessels_present === true ? 'Yes' : f.atypical_vessels_present === false ? 'No' : null],
                ])}
            </div>
            <div class="group">
                <h4>Colposcopic scoring</h4>
                ${kv([
                    ['Reid index', sc.reid_total != null
                        ? `<strong>${sc.reid_total}/8</strong>${reidInterp(sc.reid_total)}`
                        : '<span class="muted">incomplete</span>'],
                    ['· Margin', sc.reid_margin],
                    ['· Colour', sc.reid_color],
                    ['· Vessels', sc.reid_vessels],
                    ['· Iodine', sc.reid_iodine],
                    ['Swede score', sc.swede_total != null
                        ? `<strong>${sc.swede_total}/10</strong>${swedeInterp(sc.swede_total)}`
                        : '<span class="muted">incomplete</span>'],
                    ['· Aceto', sc.swede_aceto],
                    ['· Margin', sc.swede_margin],
                    ['· Vessels', sc.swede_vessels],
                    ['· Size', sc.swede_size],
                    ['· Iodine', sc.swede_iodine],
                ])}
            </div>
        `;
        annotatorLine.textContent = `Annotator: ${ann.annotator_id?.slice(0, 8) || '?'} - submitted ${ann.submitted_at ? new Date(ann.submitted_at).toLocaleString() : '-'}`;
    }

    function escapeHtml(s) {
        return s.replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
    }

    async function loadImage(ann) {
        viewerEmpty.dataset.show = 'false';
        reviewImg.style.display = '';
        // Prefer the stored final annotated image (annotated/PAT-NNN/...) which has
        // every region — including masks — baked in; fall back to the original.
        const src = ann.has_crop_image
            ? `/api/v1/annotations/${ann.id}/crop`
            : `/api/v1/images/${ann.image_id}/file`;
        await new Promise(resolve => {
            reviewImg.onload = () => {
                state.imageDims = {
                    naturalW: reviewImg.naturalWidth,
                    naturalH: reviewImg.naturalHeight,
                };
                resolve();
            };
            reviewImg.onerror = resolve;  // don't hang the panel on a missing blob
            reviewImg.src = src;
        });
    }

    function renderRegions(ann) {
        overlay.innerHTML = '';
        const regions = ann.regions || [];
        if (!regions.length || !state.imageDims) return;

        const {naturalW: W, naturalH: H} = state.imageDims;
        // SVG viewBox = native image dims; overlay stretches over the image element.
        overlay.setAttribute('viewBox', `0 0 ${W} ${H}`);
        // Match the image's contained-fit by positioning SVG over its visual bounds.
        positionOverlay();

        for (const r of regions) {
            const color = LABEL_COLOR[r.lesion_label] || '#7aa3ff';
            if (r.region_type === 'bbox') {
                const g = r.geometry;
                overlay.insertAdjacentHTML('beforeend', `
                    <rect x="${g.x}" y="${g.y}" width="${g.w}" height="${g.h}"
                          fill="${color}33" stroke="${color}" stroke-width="3"/>
                `);
            } else if (r.region_type === 'polygon') {
                const pts = r.geometry.points.map(([x, y]) => `${x},${y}`).join(' ');
                overlay.insertAdjacentHTML('beforeend', `
                    <polygon points="${pts}" fill="${color}33" stroke="${color}" stroke-width="3"/>
                `);
            }
        }
    }

    function positionOverlay() {
        // Compute the rect <img> occupies inside its container (object-fit: contain).
        const wrap = document.getElementById('viewer');
        const wR = wrap.getBoundingClientRect();
        const W = state.imageDims.naturalW, H = state.imageDims.naturalH;
        const scale = Math.min(wR.width / W, wR.height / H);
        const dw = W * scale, dh = H * scale;
        const left = (wR.width - dw) / 2;
        const top = (wR.height - dh) / 2;
        overlay.style.left = `${left}px`;
        overlay.style.top = `${top}px`;
        overlay.style.width = `${dw}px`;
        overlay.style.height = `${dh}px`;
        overlay.style.inset = 'auto';
    }
    window.addEventListener('resize', () => state.imageDims && positionOverlay());

    async function recordAction(action) {
        if (state.index < 0) return;
        const ann = state.queue[state.index];
        const comment = commentBox.value.trim() || null;
        approveBtn.disabled = true;
        rejectBtn.disabled = true;
        try {
            await api(`/api/v1/review/${ann.id}/${action}`, {
                method: 'POST',
                body: JSON.stringify({comment}),
            });
            state.queue.splice(state.index, 1);
            if (state.index >= state.queue.length) state.index = state.queue.length - 1;
            renderQueue();
            if (state.index >= 0) selectIndex(state.index);
            else {
                viewerEmpty.dataset.show = 'true';
                reviewImg.style.display = 'none';
                overlay.innerHTML = '';
                sidePanel.innerHTML = '<div class="empty-q">Queue empty.</div>';
                annotatorLine.textContent = '';
            }
        } catch (err) {
            alert(`${action} failed: ` + err.message);
            approveBtn.disabled = false;
            rejectBtn.disabled = false;
        }
    }
    approveBtn.addEventListener('click', () => recordAction('approve'));
    rejectBtn.addEventListener('click', () => recordAction('reject'));

    document.addEventListener('keydown', (e) => {
        if (e.target.matches('input, textarea, select')) return;
        if (e.key === 'a' || e.key === 'A') { approveBtn.click(); return; }
        if (e.key === 'r' || e.key === 'R') { rejectBtn.click(); return; }
        if (e.key === 'ArrowDown' || e.key === 'j') { selectIndex(state.index + 1); return; }
        if (e.key === 'ArrowUp' || e.key === 'k') { selectIndex(state.index - 1); return; }
    });

    async function boot() {
        try {
            await fetchQueue();
            if (state.queue.length) await selectIndex(0);
        } catch (err) {
            queueList.innerHTML = `<div class="empty-q">Failed to load queue: ${err.message}</div>`;
        }
    }
    boot();
})();
