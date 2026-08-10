// Diagnosis review workbench. Shows patient-level diagnoses for verification/approval.

(() => {
    const LABEL_COLOR = {
        NORMAL: '#059669',
        CIN1: '#D97706',
        CIN2: '#EA580C',
        CIN3: '#DC2626',
        AIS: '#7C3AED',
        INVASIVE_CANCER: '#881337',
        INFLAMMATION: '#DB2777',
        INFECTION: '#0891B2',
        EROSION: '#B45309',
    };

    const state = {
        queue: [],
        index: -1,
    };

    const queueList = document.getElementById('queueList');
    const diagnosisCard = document.getElementById('diagnosisCard');
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
        const data = await api('/api/v1/review/diagnosis-queue?limit=100');
        state.queue = data.items;
        renderQueue();
    }

    function renderQueue() {
        if (!state.queue.length) {
            queueList.innerHTML = '<div class="empty-q">No diagnoses pending review.</div>';
            return;
        }
        queueList.innerHTML = state.queue.map((d, i) => {
            const dx = (d.colposcopic_impression || []).join(', ') || '(pending)';
            return `
                <div class="queue-item" data-idx="${i}" aria-selected="${i === state.index ? 'true' : 'false'}">
                    <div class="label">${d.patient_code || '(no code)'}</div>
                    <div class="meta">${dx}</div>
                    <div class="meta">${d.submitted_at ? new Date(d.submitted_at).toLocaleString() : '-'}</div>
                </div>`;
        }).join('');
        queueList.querySelectorAll('.queue-item').forEach(el => {
            el.addEventListener('click', () => selectIndex(Number(el.dataset.idx)));
        });
    }

    async function selectIndex(idx) {
        if (idx < 0 || idx >= state.queue.length) return;
        state.index = idx;
        const diagnosis = state.queue[idx];
        renderQueue();
        renderDiagnosis(diagnosis);
        approveBtn.disabled = false;
        rejectBtn.disabled = false;
        commentBox.value = '';
    }

    function dxBadge(label) {
        if (!label) return '';
        const color = LABEL_COLOR[label] || '#7aa3ff';
        return `<span class="dx-badge" style="background:${color}">${label}</span>`;
    }

    function kv(rows) {
        return `<div class="kv">${rows.map(([k, v]) => `<div class="k">${k}</div><div class="v">${v ?? '<span class="muted">-</span>'}</div>`).join('')}</div>`;
    }

    function renderDiagnosis(dx) {
        const impressions = dx.colposcopic_impression || [];

        let html = `
            <h2>Patient ${dx.patient_code || '(no code)'}</h2>

            <div class="section">
                <div class="section-title">Colposcopic Impression</div>
                ${impressions.length ? `
                    <div class="dx-badges">
                        ${impressions.map(l => dxBadge(l)).join('')}
                    </div>
                ` : '<div class="muted">No findings recorded.</div>'}
            </div>

            <div class="section">
                <div class="section-title">Clinical Data</div>
                ${kv([
                    ['Histopathology', dx.histopathology_result],
                    ['Cytology (Pap)', dx.cytology_result],
                    ['HPV status', dx.hpv_status],
                    ['Confidence', dx.confidence ? `${dx.confidence}/5` : null],
                ])}
            </div>
        `;

        if (dx.reid_margin != null || dx.reid_color != null) {
            html += `
                <div class="section">
                    <div class="section-title">Reid Colposcopic Index</div>
                    <table class="score-table">
                        <tr>
                            <th>Criterion</th><th>Score</th>
                        </tr>
                        <tr>
                            <td>Margin</td><td>${dx.reid_margin ?? '-'}</td>
                        </tr>
                        <tr>
                            <td>Colour</td><td>${dx.reid_color ?? '-'}</td>
                        </tr>
                        <tr>
                            <td>Vessels</td><td>${dx.reid_vessels ?? '-'}</td>
                        </tr>
                        <tr>
                            <td>Iodine</td><td>${dx.reid_iodine ?? '-'}</td>
                        </tr>
                        <tr>
                            <td><strong>Total</strong></td><td><span class="score-total">${dx.reid_total ?? '-'} / 8</span></td>
                        </tr>
                    </table>
                </div>
            `;
        }

        if (dx.swede_aceto != null || dx.swede_margin != null) {
            html += `
                <div class="section">
                    <div class="section-title">Swede Score</div>
                    <table class="score-table">
                        <tr>
                            <th>Criterion</th><th>Score</th>
                        </tr>
                        <tr>
                            <td>Acetowhite uptake</td><td>${dx.swede_aceto ?? '-'}</td>
                        </tr>
                        <tr>
                            <td>Margins & surface</td><td>${dx.swede_margin ?? '-'}</td>
                        </tr>
                        <tr>
                            <td>Vessels</td><td>${dx.swede_vessels ?? '-'}</td>
                        </tr>
                        <tr>
                            <td>Lesion size</td><td>${dx.swede_size ?? '-'}</td>
                        </tr>
                        <tr>
                            <td>Iodine staining</td><td>${dx.swede_iodine ?? '-'}</td>
                        </tr>
                        <tr>
                            <td><strong>Total</strong></td><td><span class="score-total">${dx.swede_total ?? '-'} / 10</span></td>
                        </tr>
                    </table>
                </div>
            `;
        }

        if (dx.management_recommendation || dx.biopsy_taken) {
            html += `
                <div class="section">
                    <div class="section-title">Management</div>
                    ${kv([
                        ['Recommendation', dx.management_recommendation],
                        ['Biopsy taken', dx.biopsy_taken === true ? 'Yes' : dx.biopsy_taken === false ? 'No' : null],
                    ])}
                </div>
            `;
        }

        if (dx.notes) {
            html += `
                <div class="section">
                    <div class="section-title">Notes</div>
                    <div style="font-size: 13px; color: var(--fg);">${escapeHtml(dx.notes)}</div>
                </div>
            `;
        }

        diagnosisCard.innerHTML = html;
        annotatorLine.textContent = `Annotator: ${dx.annotator_id?.slice(0, 8) || '?'} - Submitted: ${dx.submitted_at ? new Date(dx.submitted_at).toLocaleString() : '-'}`;
    }

    function escapeHtml(s) {
        return s.replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
    }

    function toast(msg) {
        let el = document.getElementById('dxReviewToast');
        if (!el) {
            el = document.createElement('div');
            el.id = 'dxReviewToast';
            el.style.cssText = 'position:fixed; bottom:20px; left:50%; transform:translateX(-50%); '
                + 'background:#14232E; color:#fff; padding:11px 18px; border-radius:10px; font-size:13px; '
                + 'z-index:9999; max-width:90vw; box-shadow:0 8px 24px rgba(10,25,38,0.35); '
                + 'transition:opacity 0.25s ease;';
            document.body.appendChild(el);
        }
        el.textContent = msg;
        el.style.opacity = '1';
        clearTimeout(el._t);
        el._t = setTimeout(() => { el.style.opacity = '0'; }, 4000);
    }

    async function recordAction(action) {
        if (state.index < 0) return;
        const diagnosis = state.queue[state.index];
        const comment = commentBox.value.trim() || null;
        approveBtn.disabled = true;
        rejectBtn.disabled = true;
        try {
            await api(`/api/v1/review/diagnosis/${encodeURIComponent(diagnosis.patient_code)}/${action}`, {
                method: 'POST',
                body: JSON.stringify({comment}),
            });
            toast(`${action === 'approve' ? 'Approved' : 'Rejected'} diagnosis for ${diagnosis.patient_code}.`);
            state.queue.splice(state.index, 1);
            if (state.index >= state.queue.length) state.index = state.queue.length - 1;
            renderQueue();
            if (state.index >= 0) selectIndex(state.index);
            else {
                diagnosisCard.innerHTML = '<div class="empty-q">Queue empty.</div>';
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
