// Patient diagnose page controller.
//
// Shows every image for a patient plus one FINAL diagnosis form for the current
// annotator. Draft autosaves (debounced) like the per-image annotate workbench;
// submit is one-shot and the form becomes read-only afterwards.

(() => {
    const AUTOSAVE_MS = 800;
    const patientCode = window.PATIENT_CODE;
    const STATUS_LABEL = {submitted: 'submitted', reviewed: 'reviewed', consensus: 'consensus', draft: 'draft'};

    const state = {
        diagnosis: null,      // {id, status, colposcopic_impression, ...} or null
        savePending: null,
        saveTimer: null,
        dirty: false,
    };

    const statusPill = document.getElementById('statusPill');
    const dxForm = document.getElementById('dxForm');
    const imageGrid = document.getElementById('imageGrid');
    const imageCount = document.getElementById('imageCount');
    const confidence = document.getElementById('confidence');
    const confidenceLabel = document.getElementById('confidenceLabel');
    const histopathology = document.getElementById('histopathology');
    const notes = document.getElementById('notes');
    const consensusBox = document.getElementById('consensusBox');
    const consensusContent = document.getElementById('consensusContent');
    const REID_FIELDS = ['reid_margin', 'reid_color', 'reid_vessels', 'reid_iodine'];
    const SWEDE_FIELDS = ['swede_aceto', 'swede_margin', 'swede_vessels', 'swede_size', 'swede_iodine'];
    const SCORE_LABEL = {
        reid_margin: 'Reid \u00b7 Margin', reid_color: 'Reid \u00b7 Colour', reid_vessels: 'Reid \u00b7 Vessels',
        reid_iodine: 'Reid \u00b7 Iodine staining', swede_aceto: 'Swede \u00b7 Acetowhite uptake',
        swede_margin: 'Swede \u00b7 Margins & surface', swede_vessels: 'Swede \u00b7 Vessels',
        swede_size: 'Swede \u00b7 Lesion size', swede_iodine: 'Swede \u00b7 Iodine staining',
    };
    // Screening-context selects: single-value enums, saved on change like histopathology.
    const CONTEXT_SELECTS = ['cytology_result', 'hpv_status', 'management_recommendation'];
    const IMAGE_TYPE_LABEL = {
        native: 'Baseline', via: 'VIA (acetic acid)', vili: 'VILI (Lugol\'s)',
        green_filter: 'Green filter', unknown: 'Unknown',
    };

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

    function setPill(text, cls) {
        statusPill.textContent = text;
        statusPill.className = 'pill' + (cls ? ` ${cls}` : '');
    }

    function statusChip(status) {
        if (!status) return '<span class="chip none">new</span>';
        return `<span class="chip ${status}">${STATUS_LABEL[status] || status}</span>`;
    }

    function esc(s) {
        return String(s ?? '').replace(/[&<>"']/g, c => ({
            '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
        }[c]));
    }

    function renderImages(images) {
        imageCount.textContent = `(${images.length})`;
        if (!images.length) {
            imageGrid.innerHTML = '<div class="muted">No images for this patient.</div>';
            return;
        }
        renderReviewerBanner(images);
        imageGrid.innerHTML = images.map(img => {
            const flagged = !!img.needs_redo;
            // Compulsory fields the draft still lacks (server-computed; null = no draft).
            const missing = Array.isArray(img.missing_fields) ? img.missing_fields.length : 0;
            let tip = img.dataset_source;
            if (flagged) tip = 'Reviewer asked you to redo this' + (img.reviewer_rejection ? ': ' + img.reviewer_rejection : '');
            else if (missing) tip = `${missing} compulsory field${missing === 1 ? '' : 's'} still empty \u2014 open to finish`;
            const cls = 'img-card' + (flagged ? ' rejected' : '') + (missing ? ' incomplete' : '');
            return `
            <a class="${cls}" href="/annotate/${img.id}" title="${esc(tip)}">
                <img src="/api/v1/images/${img.id}/file" loading="lazy" alt="">
                ${statusChip(img.my_annotation_status)}
                ${missing ? `<span class="incomplete-flag">${missing} left</span>` : ''}
                ${img.image_phase ? `<span class="type-chip">${IMAGE_TYPE_LABEL[img.image_phase] || img.image_phase}</span>` : ''}
                ${flagged ? '<span class="reject-flag" title="Reviewer rejected — redo needed">↩ redo</span>' : ''}
            </a>`;
        }).join('');
    }

    function renderReviewerBanner(images) {
        const flagged = images.filter(i => i.needs_redo);
        let banner = document.getElementById('reviewerBanner');
        if (!flagged.length) { if (banner) banner.remove(); return; }
        if (!banner) {
            banner = document.createElement('div');
            banner.id = 'reviewerBanner';
            banner.className = 'alert error';
            imageGrid.parentElement.insertBefore(banner, imageGrid);
        }
        banner.innerHTML = `<strong>A reviewer returned this patient.</strong> Fix the ${flagged.length} flagged image(s) below (marked &ldquo;&#8617; redo&rdquo;), then re-submit the diagnosis.`
            + flagged.filter(f => f.reviewer_rejection).map(f => `<div style="margin-top:6px;">&bull; ${esc(f.reviewer_rejection)}</div>`).join('');
    }

    function renderConsensus(consensus) {
        if (!consensus) {
            consensusBox.style.display = 'none';
            return;
        }
        consensusBox.style.display = 'block';
        const pct = consensus.agreement_score != null ? `${Math.round(consensus.agreement_score * 100)}%` : '-';
        consensusContent.innerHTML = `
            <div><strong>Label:</strong> ${consensus.label.join(', ') || '-'}</div>
            <div><strong>Agreement:</strong> ${pct}</div>
            <div><strong>Computed:</strong> ${consensus.computed_at ? new Date(consensus.computed_at).toLocaleString() : '-'}</div>
        `;
    }

    function hydrateForm(diagnosis) {
        const impression = diagnosis?.colposcopic_impression || [];
        document.querySelectorAll('.dx-btn').forEach(btn => {
            btn.setAttribute('aria-pressed', impression.includes(btn.dataset.dx) ? 'true' : 'false');
        });
        confidence.value = diagnosis?.confidence || 3;
        confidenceLabel.textContent = diagnosis?.confidence ? `${diagnosis.confidence} / 5` : '-';
        histopathology.value = diagnosis?.histopathology_result || '';
        notes.value = diagnosis?.notes || '';
        for (const field of CONTEXT_SELECTS) {
            const el = document.getElementById(field);
            if (el) el.value = diagnosis?.[field] || '';
        }
        document.getElementById('biopsy_taken').checked = !!diagnosis?.biopsy_taken;
        for (const field of [...REID_FIELDS, ...SWEDE_FIELDS]) {
            const el = document.getElementById(field);
            if (el) el.value = diagnosis?.[field] ?? '';
        }
        updateScoreTotals();

        const isSubmitted = diagnosis?.status && diagnosis.status !== 'draft';
        dxForm.disabled = !!isSubmitted;
        document.getElementById('submitBtn').style.display = isSubmitted ? 'none' : '';
        document.getElementById('saveBtn').style.display = isSubmitted ? 'none' : '';
        setPill(isSubmitted ? `Submitted (${diagnosis.status})` : (diagnosis ? 'Draft' : 'Not started'),
            isSubmitted ? 'saved' : '');
    }

    function collectForm() {
        const selectedDx = Array.from(document.querySelectorAll('.dx-btn[aria-pressed="true"]')).map(b => b.dataset.dx);
        const body = {
            colposcopic_impression: selectedDx,
            confidence: Number(confidence.value),
            histopathology_result: histopathology.value || null,
            notes: notes.value || null,
            biopsy_taken: document.getElementById('biopsy_taken').checked,
        };
        for (const field of CONTEXT_SELECTS) {
            const el = document.getElementById(field);
            body[field] = el && el.value !== '' ? el.value : null;
        }
        for (const field of [...REID_FIELDS, ...SWEDE_FIELDS]) {
            const el = document.getElementById(field);
            body[field] = el && el.value !== '' ? Number(el.value) : null;
        }
        return body;
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
    function missingScoreFields() {
        return [...REID_FIELDS, ...SWEDE_FIELDS].filter(f => {
            const el = document.getElementById(f);
            return el && el.value === '';
        });
    }

    function updateScoreTotals() {
        const get = f => {
            const el = document.getElementById(f);
            return (el && el.value !== '') ? Number(el.value) : null;
        };
        const missing = new Set(missingScoreFields());
        for (const f of [...REID_FIELDS, ...SWEDE_FIELDS]) {
            document.getElementById(f)?.classList.toggle('is-missing', missing.has(f));
        }
        const sets = [
            {parts: REID_FIELDS, max: 8, tot: 'reidTotal', intp: 'reidInterp', fn: interpretReid},
            {parts: SWEDE_FIELDS, max: 10, tot: 'swedeTotal', intp: 'swedeInterp', fn: interpretSwede},
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

    async function ensureDiagnosis() {
        if (state.diagnosis?.id) return state.diagnosis;
        state.diagnosis = await api(`/api/v1/patients/${encodeURIComponent(patientCode)}/diagnosis`, {method: 'POST'});
        return state.diagnosis;
    }

    function queueAutosave(patch) {
        if (state.diagnosis && state.diagnosis.status !== 'draft') return;
        state.savePending = {...(state.savePending || {}), ...patch};
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
            await ensureDiagnosis();
            await api(`/api/v1/patients/${encodeURIComponent(patientCode)}/diagnosis`, {
                method: 'PATCH', body: JSON.stringify(body),
            });
            state.dirty = false;
            setPill('Saved', 'saved');
        } catch (err) {
            state.savePending = {...body, ...(state.savePending || {})};
            setPill('Error - retry', 'error');
            console.error('autosave failed', err);
        }
    }

    document.querySelectorAll('.dx-btn').forEach(btn => {
        btn.addEventListener('click', () => {
            const isPressed = btn.getAttribute('aria-pressed') === 'true';
            btn.setAttribute('aria-pressed', !isPressed ? 'true' : 'false');
            queueAutosave(collectForm());
        });
    });
    confidence.addEventListener('input', () => {
        confidenceLabel.textContent = `${confidence.value} / 5`;
        queueAutosave(collectForm());
    });
    histopathology.addEventListener('change', () => queueAutosave(collectForm()));
    notes.addEventListener('input', () => queueAutosave(collectForm()));
    for (const field of CONTEXT_SELECTS) {
        document.getElementById(field)?.addEventListener('change', () => queueAutosave(collectForm()));
    }
    document.getElementById('biopsy_taken').addEventListener('change', () => queueAutosave(collectForm()));
    for (const field of [...REID_FIELDS, ...SWEDE_FIELDS]) {
        const el = document.getElementById(field);
        if (!el) continue;
        el.addEventListener('change', () => {
            updateScoreTotals();
            queueAutosave(collectForm());
        });
    }

    document.getElementById('saveBtn').addEventListener('click', () => {
        if (state.saveTimer) { clearTimeout(state.saveTimer); state.saveTimer = null; }
        flushSave();
    });

    function clearSubmitError() {
        document.getElementById('submitError')?.remove();
    }

    function showSubmitError(html) {
        clearSubmitError();
        const box = document.createElement('div');
        box.id = 'submitError';
        box.className = 'alert error';
        box.style.marginTop = '12px';
        box.innerHTML = html;
        dxForm.after(box);
        box.scrollIntoView({behavior: 'smooth', block: 'nearest'});
    }

    // Everything the server will refuse without, checked locally first so the
    // annotator gets pointed at the exact control instead of a round-trip.
    function localSubmitProblems() {
        const problems = [];
        if (!document.querySelector('.dx-btn[aria-pressed="true"]')) problems.push('Colposcopic impression');
        for (const f of missingScoreFields()) problems.push(SCORE_LABEL[f] || f);
        return problems;
    }

    document.getElementById('submitBtn').addEventListener('click', async () => {
        if (state.saveTimer) { clearTimeout(state.saveTimer); state.saveTimer = null; }
        await flushSave();
        clearSubmitError();

        const problems = localSubmitProblems();
        if (problems.length) {
            showSubmitError('<strong>Not submitted \u2014 compulsory fields are empty:</strong> ' + esc(problems.join(', ')) + '.');
            const first = missingScoreFields()[0];
            if (first) document.getElementById(first)?.focus();
            return;
        }
        try {
            await ensureDiagnosis();
            setPill('Submitting...', 'saving');
            const result = await api(`/api/v1/patients/${encodeURIComponent(patientCode)}/diagnosis/submit`, {
                method: 'POST', body: JSON.stringify(collectForm()),
            });
            state.diagnosis = result.diagnosis;
            hydrateForm(state.diagnosis);
            renderConsensus(result.consensus);
            if (result.images_finalized) {
                const note = document.createElement('div');
                note.className = 'muted';
                note.style.cssText = 'font-size:12px; margin-top:8px;';
                note.textContent = `${result.images_finalized} image annotation(s) finalized.`;
                dxForm.after(note);
            }
            // Image status chips (draft -> submitted) just changed server-side.
            const refreshed = await api(`/api/v1/patients/${encodeURIComponent(patientCode)}`);
            renderImages(refreshed.images);
        } catch (err) {
            setPill('Error - retry', 'error');
            if (err.body?.error?.code === 'incomplete_annotations') {
                // One line per image so the annotator can jump straight to it.
                const items = err.body.error.details || [];
                const rows = items.map(d =>
                    `<li><a href="/annotate/${esc(d.image_id)}">Open image</a> \u2014 missing: ${esc(d.msg)}</li>`
                ).join('');
                showSubmitError(`<strong>${esc(err.body.error.message)}</strong><ul style="margin:8px 0 0 18px; padding:0;">${rows}</ul>`);
                // Re-render the grid so the "N left" flags match the server.
                try {
                    const refreshed = await api(`/api/v1/patients/${encodeURIComponent(patientCode)}`);
                    renderImages(refreshed.images);
                } catch (_) {}
                return;
            }
            const detail = err.body?.error?.details?.[0]?.msg || err.body?.error?.message;
            showSubmitError('<strong>Submit failed:</strong> ' + esc(detail || err.message));
        }
    });

    window.addEventListener('beforeunload', (e) => {
        if (state.dirty) {
            e.preventDefault();
            e.returnValue = '';
        }
    });

    async function boot() {
        try {
            const data = await api(`/api/v1/patients/${encodeURIComponent(patientCode)}`);
            renderImages(data.images);
            state.diagnosis = data.my_diagnosis;
            hydrateForm(state.diagnosis);
            renderConsensus(data.consensus);
        } catch (err) {
            setPill('Error', 'error');
            imageGrid.innerHTML = `<div class="muted">Failed to load: ${err.message}</div>`;
        }
    }
    boot();
})();
