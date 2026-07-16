// Patient picker: lists patients with remaining-image counts, per-patient
// progress and diagnosis status, so the annotator can start (or resume) one
// patient at a time. Supports client-side filtering by patient code.

(() => {
    async function api(path) {
        const res = await fetch(path, {credentials: 'same-origin'});
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        return res.json();
    }

    function esc(s) {
        return String(s ?? '').replace(/[&<>"']/g, c => ({
            '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
        }[c]));
    }

    let allItems = [];
    let dxStatus = new Map();

    function render() {
        const el = document.getElementById('patientList');
        const countEl = document.getElementById('patCount');
        const q = (document.getElementById('patSearch').value || '').trim().toLowerCase();
        const items = q
            ? allItems.filter(p => p.patient_code.toLowerCase().includes(q))
            : allItems;

        countEl.textContent = q
            ? `${items.length} of ${allItems.length} patients`
            : `${allItems.length} patient${allItems.length === 1 ? '' : 's'}`;

        if (!items.length) {
            el.innerHTML = `<div class="card empty-state">
                ${q ? `No patients match "<strong>${esc(q)}</strong>".` : 'No patients yet — ask an admin to upload images.'}
            </div>`;
            return;
        }

        el.innerHTML = items.map(p => {
            const status = dxStatus.get(p.patient_code);
            const dxPill = status
                ? `<span class="chip dx-pill ${status}">diagnosis ${status}</span>`
                : '';
            const done = p.remaining === 0;
            const doneCount = p.total - p.remaining;
            const pct = p.total ? Math.round((doneCount / p.total) * 100) : 0;
            const href = done
                ? `/patients/${encodeURIComponent(p.patient_code)}/diagnose`
                : `/annotate?patient=${encodeURIComponent(p.patient_code)}`;
            return `
                <div class="pat-card">
                    <div class="info">
                        <div class="code">${esc(p.patient_code)}${dxPill}</div>
                        <div class="meta">${doneCount} of ${p.total} images annotated${done ? '' : ` — ${p.remaining} left`}</div>
                        <div class="progress-track" role="progressbar" aria-valuenow="${pct}" aria-valuemin="0" aria-valuemax="100" aria-label="Annotation progress for ${esc(p.patient_code)}">
                            <div class="progress-fill${done ? ' done' : ''}" style="width:${pct}%"></div>
                        </div>
                    </div>
                    <a class="btn ${done ? 'secondary' : ''}" href="${href}">
                        ${done ? 'Enter diagnosis' : (doneCount > 0 ? 'Continue' : 'Start annotating')}
                        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><line x1="5" y1="12" x2="19" y2="12"/><polyline points="12 5 19 12 12 19"/></svg>
                    </a>
                </div>`;
        }).join('');
    }

    async function boot() {
        const el = document.getElementById('patientList');
        try {
            const [queueData, dxData] = await Promise.all([
                api('/api/v1/images/patients'),
                api('/api/v1/patients').catch(() => ({items: []})),
            ]);
            dxStatus = new Map(dxData.items.map(p => [p.patient_code, p.my_diagnosis_status]));

            // Patients with work remaining first, then fully-done ones.
            allItems = [...queueData.items].sort((a, b) => {
                if ((a.remaining > 0) !== (b.remaining > 0)) return a.remaining > 0 ? -1 : 1;
                return a.patient_code.localeCompare(b.patient_code);
            });

            document.getElementById('patSearch').addEventListener('input', render);
            render();
        } catch (err) {
            el.innerHTML = `<div class="alert error">Failed to load patients: ${esc(err.message)}</div>`;
        }
    }
    boot();
})();
