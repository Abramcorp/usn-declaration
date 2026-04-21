/**
 * Налоговая декларация ИП на УСН 6%
 * Wizard-based single-page application
 */

// ============================================================================
// STATE
// ============================================================================
const state = {
    currentStep: 1,
    totalSteps: 7,
    projectId: null,

    // Step 1 – bank statement upload
    bankStatementUploaded: false,

    // Step 2 – OFD
    ofdSkipped: false,
    ofdUploaded: false,

    // Step 3 – operations
    operations: [],
    operationsPage: 1,
    operationsPerPage: 20,
    operationsFilter: '',
    operationsSearch: '',
    selectedOperationIds: new Set(),

    // Step 4 – settings
    selectedYear: null,  // налоговый период (год)
    ownerInn: null,      // ИНН ИП из выписки
    ownerName: null,     // ФИО/наименование ИП из выписки

    // Step 5 – calculation
    calculationDone: false,
    calculations: [],

    // Step 6 – declaration
    declarationData: null,
};

// ============================================================================
// HELPERS
// ============================================================================
const API = '';

function fmt(n) {
    const num = parseFloat(n) || 0;
    return num.toLocaleString('ru-RU', { minimumFractionDigits: 2, maximumFractionDigits: 2 }) + ' \u20BD';
}

function showLoading() {
    document.getElementById('loading-overlay').style.display = 'flex';
}

function hideLoading() {
    document.getElementById('loading-overlay').style.display = 'none';
}

function showToast(message, type) {
    type = type || 'success';
    const container = document.getElementById('toast-container');
    const toast = document.createElement('div');
    toast.className = 'toast toast-' + type;
    toast.textContent = message;
    container.appendChild(toast);
    setTimeout(function() { toast.remove(); }, 4000);
}

async function apiCall(url, options) {
    options = options || {};
    try {
        const resp = await fetch(API + url, options);
        if (!resp.ok) {
            let err;
            try { err = await resp.json(); } catch(_) { err = { detail: resp.statusText }; }
            throw new Error(err.detail || 'Ошибка сервера');
        }
        if (resp.status === 204) return null;
        return await resp.json();
    } catch (e) {
        showToast(e.message, 'error');
        throw e;
    }
}

// ============================================================================
// WIZARD NAVIGATION
// ============================================================================
function goToStep(step) {
    if (step < 1 || step > state.totalSteps) return;

    // Hide current step
    var current = document.getElementById('step-' + state.currentStep);
    if (current) {
        current.classList.remove('active');
        current.style.display = 'none';
    }

    // Show target step
    var target = document.getElementById('step-' + step);
    if (target) {
        target.style.display = 'block';
        target.classList.add('active');
    }

    state.currentStep = step;
    updateProgressBar();
    updateNavigationButtons();

    // Load data when entering specific steps
    // 1) Выписка — загрузка (step 1)
    // 2) ОФД — загрузка (step 2)
    // 3) Операции — проверка классификации
    if (step === 3 && state.projectId) {
        state.selectedYear = getSelectedYear();
        loadOperations();
    }
    // 4) Параметры ИП — автозаполнение из выписки
    if (step === 4 && state.projectId) {
        var innField = document.getElementById('step-4-inn');
        var fioField = document.getElementById('step-4-fio');
        if (state.ownerInn && (!innField.value || innField.value === '0000000000')) {
            innField.value = state.ownerInn;
        }
        if (state.ownerName && (!fioField.value || fioField.value === 'Новый проект')) {
            fioField.value = state.ownerName;
        }
    }
    // 5) Расчёт — расчёт взносов + налога по выручке
    if (step === 5 && state.projectId) {
        document.getElementById('step-5-pre-calc').style.display = 'block';
        document.getElementById('step-5-calc-results').style.display = 'none';
        loadStep5RevenueAndContributions();
    }
    // 6) Декларация — формирование документа
    if (step === 6 && state.projectId) loadDeclaration();
    // 7) Сверка ЕНС — сравнение расчёта с фактом уплаты
    if (step === 7 && state.projectId) loadStep4Data();
}

function updateProgressBar() {
    var steps = document.querySelectorAll('.progress-step');
    for (var i = 0; i < steps.length; i++) {
        var el = steps[i];
        var s = parseInt(el.getAttribute('data-step'));
        el.classList.remove('active', 'completed');
        if (s === state.currentStep) {
            el.classList.add('active');
        } else if (s < state.currentStep) {
            el.classList.add('completed');
        }
    }
}

function updateNavigationButtons() {
    var btnPrev = document.getElementById('btn-prev');
    var btnNext = document.getElementById('btn-next');

    btnPrev.style.display = state.currentStep > 1 ? 'inline-flex' : 'none';

    if (state.currentStep === state.totalSteps) {
        btnNext.style.display = 'none';
    } else {
        btnNext.style.display = 'inline-flex';
        btnNext.textContent = 'Далее \u2192';
    }
}

function nextStep() {
    if (state.currentStep === 1 && !state.bankStatementUploaded) {
        showToast('Сначала загрузите банковскую выписку', 'warning');
        return;
    }
    // На шаге 4 (Параметры ИП) сохраняем настройки перед переходом к Расчёту
    if (state.currentStep === 4) {
        saveSettings();
    }
    goToStep(state.currentStep + 1);
}

function previousStep() {
    goToStep(state.currentStep - 1);
}

// ============================================================================
// STEP 1: BANK STATEMENT UPLOAD
// ============================================================================
async function ensureProject() {
    if (state.projectId) return state.projectId;

    // First check if any projects already exist
    try {
        var projects = await apiCall('/api/projects/');
        if (projects && projects.length > 0) {
            state.projectId = projects[projects.length - 1].id;
            return state.projectId;
        }
    } catch (_) {}

    // No existing projects — create one
    var year = new Date().getFullYear();
    var data = await apiCall('/api/projects/', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
            inn: '0000000000',
            fio: 'Новый проект',
            tax_period_year: year,
        }),
    });
    state.projectId = data.id;
    return data.id;
}

async function handleBankStatementUpload(event) {
    var file = event.target.files[0];
    if (!file) return;
    await uploadBankStatement(file);
}

async function uploadBankStatement(file) {
    showLoading();
    try {
        var projectId = await ensureProject();
        var formData = new FormData();
        formData.append('file', file);

        var result = await apiCall('/api/import/bank-statement/' + projectId, {
            method: 'POST',
            body: formData,
        });

        state.bankStatementUploaded = true;

        // Save owner info from parser
        if (result.owner_inn) state.ownerInn = result.owner_inn;
        if (result.owner_name) state.ownerName = result.owner_name;

        // Show summary
        var summary = document.getElementById('step-1-summary');
        summary.style.display = 'block';
        document.getElementById('summary-total-ops').textContent = result.total_saved || 0;
        document.getElementById('summary-filename').textContent = file.name;

        // Account, owner, and period info
        if (result.account_number) {
            document.getElementById('summary-account').textContent = result.account_number;
        }
        if (result.owner_name) {
            document.getElementById('summary-owner-name').textContent = result.owner_name;
        }
        if (result.owner_inn) {
            document.getElementById('summary-owner-inn').textContent = result.owner_inn;
        }
        if (result.period_start && result.period_end) {
            document.getElementById('summary-period').textContent = result.period_start + ' — ' + result.period_end;
        }

        // Raw direction totals (from bank statement)
        document.getElementById('summary-raw-income-amount').textContent = fmt(result.raw_income_amount || 0);
        document.getElementById('summary-raw-income-count').textContent = (result.raw_income_count || 0) + ' операций';
        document.getElementById('summary-raw-expense-amount').textContent = fmt(result.raw_expense_amount || 0);
        document.getElementById('summary-raw-expense-count').textContent = (result.raw_expense_count || 0) + ' операций';

        // Classification-based amounts (income operations only)
        document.getElementById('summary-income-amount').textContent = fmt(result.income_amount || 0);
        document.getElementById('summary-income-count').textContent = (result.income_count || 0) + ' операций';
        document.getElementById('summary-excluded-amount').textContent = fmt(result.not_income_amount || 0);
        document.getElementById('summary-excluded-count').textContent = (result.not_income_count || 0) + ' операций';
        document.getElementById('summary-disputed-amount').textContent = fmt(result.disputed_amount || 0);
        document.getElementById('summary-disputed-count').textContent = (result.disputed_count || 0) + ' операций';

        // Hide drop zone
        document.getElementById('drop-zone-1').style.display = 'none';

        showToast('Загружено ' + (result.total_saved || 0) + ' операций');
    } catch (e) {
        console.error('Upload error:', e);
    } finally {
        hideLoading();
    }
}

// Drag & drop
function setupDropZone(zoneId, handler) {
    var zone = document.getElementById(zoneId);
    if (!zone) return;

    zone.addEventListener('dragover', function(e) {
        e.preventDefault();
        zone.classList.add('drag-over');
    });
    zone.addEventListener('dragleave', function() {
        zone.classList.remove('drag-over');
    });
    zone.addEventListener('drop', function(e) {
        e.preventDefault();
        zone.classList.remove('drag-over');
        var file = e.dataTransfer.files[0];
        if (file) handler(file);
    });
}

// ============================================================================
// STEP 2: OFD RECEIPTS (OPTIONAL)
// ============================================================================
async function handleOFDReceiptsUpload(event) {
    var file = event.target.files[0];
    if (!file) return;
    await uploadOFDReceipts(file);
}

async function uploadOFDReceipts(file) {
    showLoading();
    try {
        var formData = new FormData();
        formData.append('file', file);
        var result = await apiCall('/api/import/ofd/' + state.projectId, {
            method: 'POST',
            body: formData,
        });

        state.ofdUploaded = true;

        var summary = document.getElementById('step-2-summary');
        summary.style.display = 'block';
        document.getElementById('summary-receipts-count').textContent = result.total || 0;
        document.getElementById('summary-receipts-amount').textContent = fmt(result.amount || 0);

        document.getElementById('drop-zone-2').style.display = 'none';
        document.getElementById('step-2-actions').style.display = 'none';

        showToast('Чеки ОФД загружены');
    } catch (e) {
        console.error('OFD upload error:', e);
    } finally {
        hideLoading();
    }
}

function skipOFDReceipts() {
    state.ofdSkipped = true;
    showToast('Чеки ОФД пропущены');
    nextStep();
}

// ============================================================================
// STEP 3: OPERATIONS REVIEW
// ============================================================================
async function loadOperations() {
    showLoading();
    try {
        var url = '/api/operations/' + state.projectId + '?limit=10000';
        if (state.selectedYear) url += '&year=' + state.selectedYear;
        if (state.operationsFilter) url += '&classification=' + state.operationsFilter;
        if (state.operationsSearch) url += '&search=' + encodeURIComponent(state.operationsSearch);

        state.operations = await apiCall(url);
        state.operationsPage = 1;
        state.selectedOperationIds.clear();
        renderOperationsTable();
        renderOperationsStats();
    } catch (e) {
        console.error('Load operations error:', e);
    } finally {
        hideLoading();
    }
}

function renderOperationsStats() {
    var ops = state.operations;
    var incomeOps = ops.filter(function(o) { return o.classification === 'income'; });
    var excludedOps = ops.filter(function(o) { return o.classification === 'not_income'; });
    var disputedOps = ops.filter(function(o) { return o.classification === 'disputed'; });

    var totalIncome = incomeOps.reduce(function(s, o) { return s + parseFloat(o.amount || 0); }, 0);

    document.getElementById('step-3-total-income').textContent = fmt(totalIncome);
    document.getElementById('step-3-excluded-count').textContent = excludedOps.length;
    document.getElementById('step-3-disputed-summary').textContent = disputedOps.length;

    var alert = document.getElementById('step-3-alert');
    if (disputedOps.length > 0) {
        alert.style.display = 'block';
        document.getElementById('disputed-count').textContent = disputedOps.length;
    } else {
        alert.style.display = 'none';
    }
}

function renderOperationsTable() {
    var tbody = document.getElementById('step-3-operations-tbody');
    var start = (state.operationsPage - 1) * state.operationsPerPage;
    var page = state.operations.slice(start, start + state.operationsPerPage);

    var html = '';
    for (var i = 0; i < page.length; i++) {
        var op = page[i];
        var checked = state.selectedOperationIds.has(op.id) ? 'checked' : '';
        var badgeClass, badgeText;
        if (op.classification === 'income') { badgeClass = 'badge-income'; badgeText = 'Доход'; }
        else if (op.classification === 'not_income') { badgeClass = 'badge-not-income'; badgeText = 'Не доход'; }
        else { badgeClass = 'badge-disputed'; badgeText = 'Спорная'; }

        var purpose = op.purpose || '\u2014';
        var truncPurpose = purpose.length > 60 ? purpose.substring(0, 60) + '\u2026' : purpose;
        var escapedPurpose = purpose.replace(/"/g, '&quot;');

        html += '<tr>'
            + '<td><input type="checkbox" ' + checked + ' onchange="toggleOperationSelection(' + op.id + ', this.checked)"></td>'
            + '<td>' + (op.operation_date || '\u2014') + '</td>'
            + '<td style="text-align:right;white-space:nowrap;">' + fmt(op.amount) + '</td>'
            + '<td>' + (op.counterparty || '\u2014') + '</td>'
            + '<td title="' + escapedPurpose + '">' + truncPurpose + '</td>'
            + '<td><span class="badge ' + badgeClass + '">' + badgeText + '</span></td>'
            + '<td><button class="btn btn-secondary" style="padding:4px 8px;font-size:12px;" onclick="openOperationDetailModal(' + op.id + ')">&#9998;</button></td>'
            + '</tr>';
    }
    tbody.innerHTML = html;

    // Pagination
    var totalPages = Math.ceil(state.operations.length / state.operationsPerPage) || 1;
    document.getElementById('step-3-pagination-info').textContent =
        'Страница ' + state.operationsPage + ' из ' + totalPages + ' (всего: ' + state.operations.length + ')';
    document.getElementById('step-3-prev-page').style.display = state.operationsPage > 1 ? 'inline-flex' : 'none';
    document.getElementById('step-3-next-page').style.display = state.operationsPage < totalPages ? 'inline-flex' : 'none';

    updateBatchActions();
}

function prevPageOperations() {
    if (state.operationsPage > 1) {
        state.operationsPage--;
        renderOperationsTable();
    }
}

function nextPageOperations() {
    var totalPages = Math.ceil(state.operations.length / state.operationsPerPage);
    if (state.operationsPage < totalPages) {
        state.operationsPage++;
        renderOperationsTable();
    }
}

function filterOperationsStep3() {
    state.operationsFilter = document.getElementById('step-3-classification-filter').value;
    state.operationsSearch = document.getElementById('step-3-search').value;
    loadOperations();
}

function toggleOperationSelection(id, checked) {
    if (checked) state.selectedOperationIds.add(id);
    else state.selectedOperationIds.delete(id);
    updateBatchActions();
}

function toggleSelectAllOperations() {
    var checked = document.getElementById('step-3-select-all').checked;
    var start = (state.operationsPage - 1) * state.operationsPerPage;
    var page = state.operations.slice(start, start + state.operationsPerPage);
    for (var i = 0; i < page.length; i++) {
        if (checked) state.selectedOperationIds.add(page[i].id);
        else state.selectedOperationIds.delete(page[i].id);
    }
    renderOperationsTable();
}

function updateBatchActions() {
    var bar = document.getElementById('step-3-batch-actions');
    var count = state.selectedOperationIds.size;
    if (count > 0) {
        bar.style.display = 'flex';
        document.getElementById('step-3-selected-count').textContent = count;
    } else {
        bar.style.display = 'none';
    }
}

async function batchMarkAsIncome() {
    if (state.selectedOperationIds.size === 0) return;
    showLoading();
    try {
        await apiCall('/api/operations/batch-classify', {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                operation_ids: Array.from(state.selectedOperationIds),
                classification: 'income',
                comment: 'Пакетная классификация: доход',
            }),
        });
        showToast(state.selectedOperationIds.size + ' операций \u2192 Доход');
        state.selectedOperationIds.clear();
        await loadOperations();
    } catch (e) {
        console.error(e);
    } finally {
        hideLoading();
    }
}

async function batchMarkAsNotIncome() {
    if (state.selectedOperationIds.size === 0) return;
    showLoading();
    try {
        await apiCall('/api/operations/batch-classify', {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                operation_ids: Array.from(state.selectedOperationIds),
                classification: 'not_income',
                comment: 'Пакетная классификация: не доход',
            }),
        });
        showToast(state.selectedOperationIds.size + ' операций \u2192 Не доход');
        state.selectedOperationIds.clear();
        await loadOperations();
    } catch (e) {
        console.error(e);
    } finally {
        hideLoading();
    }
}

// Operation detail modal
var editingOperationId = null;

function openOperationDetailModal(id) {
    editingOperationId = id;
    var op = null;
    for (var i = 0; i < state.operations.length; i++) {
        if (state.operations[i].id === id) { op = state.operations[i]; break; }
    }
    if (!op) return;

    var info = document.getElementById('operation-detail-info');
    info.innerHTML = '<div class="summary-grid" style="margin-bottom:16px;">'
        + '<div class="summary-item"><span class="summary-label">Дата:</span><span class="summary-value">' + (op.operation_date || '\u2014') + '</span></div>'
        + '<div class="summary-item"><span class="summary-label">Сумма:</span><span class="summary-value">' + fmt(op.amount) + '</span></div>'
        + '<div class="summary-item"><span class="summary-label">Контрагент:</span><span class="summary-value">' + (op.counterparty || '\u2014') + '</span></div>'
        + '</div>'
        + '<p style="font-size:13px;color:var(--gray-text);margin-bottom:8px;">Назначение:</p>'
        + '<p style="font-size:14px;margin-bottom:16px;">' + (op.purpose || '\u2014') + '</p>';

    document.getElementById('operation-detail-classification').value = op.classification || 'disputed';
    document.getElementById('operation-detail-modal').style.display = 'flex';
}

function closeOperationDetailModal() {
    document.getElementById('operation-detail-modal').style.display = 'none';
    editingOperationId = null;
}

async function saveOperationClassification() {
    if (!editingOperationId) return;
    var classification = document.getElementById('operation-detail-classification').value;
    showLoading();
    try {
        await apiCall('/api/operations/classify/' + editingOperationId, {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ classification: classification, comment: 'Ручная классификация' }),
        });
        closeOperationDetailModal();
        showToast('Классификация обновлена');
        await loadOperations();
    } catch (e) {
        console.error(e);
    } finally {
        hideLoading();
    }
}

// ============================================================================
// STEP 4: SETTINGS (with auto-detection of ENS payments)
// ============================================================================

// ENS payments detected from statement
state.ensPayments = [];
state.autoContribInfo = null;

function toggleEmployeesStep4() {
    var has = document.getElementById('step-4-has-employees').checked;
    document.getElementById('step-4-employee-quarters').style.display = has ? 'block' : 'none';
}

function getSelectedYear() {
    var el = document.getElementById('step-4-period');
    return el ? parseInt(el.value) : null;
}

function onTaxPeriodChange() {
    state.selectedYear = getSelectedYear();
    // Re-load Step 4 data with new year
    loadStep4Data();
}

async function loadStep5RevenueAndContributions() {
    if (!state.projectId) return;

    state.selectedYear = getSelectedYear();
    var yearParam = state.selectedYear ? '?year=' + state.selectedYear : '';

    showLoading();
    try {
        // 1) Расчёт взносов по выручке (источник — выписка, классифицированные income)
        var contribData = await apiCall('/api/tax/auto-contributions/' + state.projectId + yearParam);
        state.autoContribInfo = contribData;

        document.getElementById('step-4-calc-fixed').textContent = fmt(contribData.fixed_contributions);
        document.getElementById('step-4-calc-1pct').textContent = fmt(contribData.one_percent);
        document.getElementById('step-4-calc-total').textContent = fmt(contribData.total_contributions);

        var noteEl = document.getElementById('step-4-contrib-note');
        if (parseFloat(contribData.one_percent) > 0) {
            noteEl.style.display = 'block';
            document.getElementById('step-4-contrib-note-text').textContent =
                '\u2139\uFE0F Доход ' + fmt(contribData.total_income) +
                ' превышает ' + fmt(contribData.income_threshold) +
                '. 1% = ' + fmt(contribData.one_percent) +
                ' (макс. ' + fmt(contribData.max_1pct_cap) + ')';
        } else {
            noteEl.style.display = 'none';
        }

        // 2) Выручка по двум источникам
        var bankRevenue = parseFloat(contribData.total_income) || 0;
        document.getElementById('step-5-revenue-bank').textContent = fmt(bankRevenue);

        // ОФД — попытаемся загрузить, если есть
        var ofdRevenue = 0;
        try {
            var ofdData = await apiCall('/api/tax/ofd-revenue/' + state.projectId + yearParam);
            ofdRevenue = parseFloat(ofdData.total_revenue) || 0;
            document.getElementById('step-5-revenue-ofd').textContent = fmt(ofdRevenue);
        } catch (e) {
            document.getElementById('step-5-revenue-ofd').textContent = 'Нет данных';
        }

        var diff = Math.abs(bankRevenue - ofdRevenue);
        document.getElementById('step-5-revenue-diff').textContent = ofdRevenue > 0 ? fmt(diff) : '—';

        // Дневная сверка ОФД ↔ эквайринг банка (если есть ОФД-данные)
        var reconCard = document.getElementById('step-5-recon-card');
        if (ofdRevenue > 0) {
            try {
                var recon = await apiCall('/api/tax/revenue-reconciliation/' + state.projectId + yearParam);
                var s = recon.summary || {};
                document.getElementById('step-5-recon-cash').textContent = fmt(s.ofd_cash_sale);
                document.getElementById('step-5-recon-ofd-card').textContent = fmt(s.ofd_card_sale);
                document.getElementById('step-5-recon-bank-acq').textContent = fmt(s.bank_acquiring_total);
                document.getElementById('step-5-recon-acq-diff').textContent = fmt(s.acquiring_diff_total);
                document.getElementById('step-5-recon-cash-rev').textContent = fmt(s.cash_revenue_total);
                document.getElementById('step-5-recon-suggested').textContent = fmt(s.suggested_tax_base);

                var tbody = document.getElementById('step-5-recon-daily-tbody');
                tbody.innerHTML = '';
                var daily = (recon.daily || []).slice(0, 30);
                for (var i = 0; i < daily.length; i++) {
                    var d = daily[i];
                    var tr = document.createElement('tr');
                    tr.innerHTML =
                        '<td>' + d.date + '</td>' +
                        '<td style="text-align:right;">' + fmt(d.ofd_cash) + '</td>' +
                        '<td style="text-align:right;">' + fmt(d.ofd_card) + '</td>' +
                        '<td style="text-align:right;">' + fmt(d.bank_acquiring) + '</td>' +
                        '<td style="text-align:right;">' + fmt(d.acquiring_diff) + '</td>';
                    tbody.appendChild(tr);
                }
                reconCard.style.display = 'block';
                state.suggestedTaxBase = parseFloat(s.suggested_tax_base) || 0;
            } catch (e) {
                reconCard.style.display = 'none';
            }
        } else {
            reconCard.style.display = 'none';
        }
    } catch (e) {
        console.error('Load step 5 data error:', e);
    } finally {
        hideLoading();
    }
}

async function loadStep4Data() {
    if (!state.projectId) return;

    // Sync selected year from dropdown
    state.selectedYear = getSelectedYear();
    var yearParam = state.selectedYear ? '?year=' + state.selectedYear : '';

    showLoading();
    try {
        // Load auto-calculated contributions and ENS payments in parallel
        var contribPromise = apiCall('/api/tax/auto-contributions/' + state.projectId + yearParam);
        var ensPromise = apiCall('/api/tax/ens-payments/' + state.projectId + yearParam);

        var contribData = await contribPromise;
        var ensData = await ensPromise;

        // Show auto-calculated contributions
        state.autoContribInfo = contribData;
        document.getElementById('step-4-calc-fixed').textContent = fmt(contribData.fixed_contributions);
        document.getElementById('step-4-calc-1pct').textContent = fmt(contribData.one_percent);
        document.getElementById('step-4-calc-total').textContent = fmt(contribData.total_contributions);

        var noteEl = document.getElementById('step-4-contrib-note');
        if (parseFloat(contribData.one_percent) > 0) {
            noteEl.style.display = 'block';
            document.getElementById('step-4-contrib-note-text').textContent =
                '\u2139\uFE0F Доход ' + fmt(contribData.total_income) +
                ' превышает ' + fmt(contribData.income_threshold) +
                '. 1% = ' + fmt(contribData.one_percent) +
                ' (макс. ' + fmt(contribData.max_1pct_cap) + ')';
        } else {
            noteEl.style.display = 'none';
        }

        // Show detected ENS payments
        state.ensPayments = ensData.payments || [];
        renderENSPayments();

    } catch (e) {
        console.error('Load step 4 data error:', e);
    } finally {
        hideLoading();
    }
}

function renderENSPayments() {
    var payments = state.ensPayments;
    var tbody = document.getElementById('step-4-ens-tbody');
    var tableContainer = document.getElementById('step-4-ens-table-container');
    var emptyMsg = document.getElementById('step-4-ens-empty');

    if (!payments || payments.length === 0) {
        tableContainer.style.display = 'none';
        emptyMsg.style.display = 'block';
        updateENSSummary();
        return;
    }

    tableContainer.style.display = 'block';
    emptyMsg.style.display = 'none';

    var categoryOptions = [
        { value: 'fixed_contributions', label: '\u0424\u0438\u043a\u0441. \u0432\u0437\u043d\u043e\u0441\u044b \u0418\u041f' },
        { value: 'one_percent', label: '1% \u0441\u0432\u044b\u0448\u0435 300\u0442' },
        { value: 'employee_contributions', label: '\u0412\u0437\u043d\u043e\u0441\u044b \u0437\u0430 \u0441\u043e\u0442\u0440.' },
        { value: 'tax_advance', label: '\u0410\u0432\u0430\u043d\u0441 \u0423\u0421\u041d' },
        { value: 'ens_mixed', label: '\u0415\u041d\u0421 (\u043e\u0431\u0449\u0438\u0439)' },
        { value: 'other', label: '\u0414\u0440\u0443\u0433\u043e\u0435' },
    ];

    var html = '';
    for (var i = 0; i < payments.length; i++) {
        var p = payments[i];
        var purpose = (p.purpose || '').length > 50 ? p.purpose.substring(0, 50) + '\u2026' : (p.purpose || '\u2014');
        var escapedPurpose = (p.purpose || '').replace(/"/g, '&quot;');

        html += '<tr>';
        html += '<td>' + (p.date || '\u2014') + '</td>';
        html += '<td style="text-align:right;white-space:nowrap;">' + fmt(p.amount) + '</td>';
        html += '<td title="' + escapedPurpose + '">' + purpose + '</td>';
        html += '<td><select class="form-input" onchange="onENSCategoryChange(' + i + ', this.value)" style="font-size:13px;">';
        for (var j = 0; j < categoryOptions.length; j++) {
            var opt = categoryOptions[j];
            var selected = opt.value === p.detected_category ? ' selected' : '';
            html += '<option value="' + opt.value + '"' + selected + '>' + opt.label + '</option>';
        }
        html += '</select></td>';
        html += '</tr>';
    }
    tbody.innerHTML = html;
    updateENSSummary();
}

function onENSCategoryChange(index, value) {
    if (state.ensPayments[index]) {
        state.ensPayments[index].detected_category = value;
    }
    updateENSSummary();
}

function updateENSSummary() {
    var contribTotal = 0;
    var advanceTotal = 0;
    var payments = state.ensPayments || [];

    for (var i = 0; i < payments.length; i++) {
        var amt = parseFloat(payments[i].amount) || 0;
        var cat = payments[i].detected_category;
        if (cat === 'fixed_contributions' || cat === 'one_percent' || cat === 'employee_contributions' || cat === 'ens_mixed') {
            contribTotal += amt;
        } else if (cat === 'tax_advance') {
            advanceTotal += amt;
        }
    }

    document.getElementById('step-4-ens-paid-contrib').textContent = fmt(contribTotal);
    document.getElementById('step-4-ens-paid-advance').textContent = fmt(advanceTotal);
}

async function runAutoCalcStep4() {
    if (!state.projectId) return;
    showLoading();
    try {
        var hasEmployees = document.getElementById('step-4-has-employees').checked;

        // Build categorized payments list
        var ensPaymentsList = [];
        for (var i = 0; i < state.ensPayments.length; i++) {
            var p = state.ensPayments[i];
            ensPaymentsList.push({
                operation_id: p.operation_id,
                category: p.detected_category,
                amount: parseFloat(p.amount) || 0,
                date: p.date || '',
            });
        }

        var result = await apiCall('/api/tax/auto-calculate/' + state.projectId, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                ens_payments: ensPaymentsList,
                has_employees: hasEmployees,
                year: state.selectedYear || getSelectedYear(),
            }),
        });

        // Show advance comparison table
        var advCard = document.getElementById('step-4-advance-card');
        advCard.style.display = 'block';

        var advTbody = document.getElementById('step-4-advance-tbody');
        var advCalcs = result.advance_calculations || [];
        var advComps = result.advance_comparison || [];
        var html = '';

        for (var i = 0; i < advCalcs.length; i++) {
            var calc = advCalcs[i];
            var comp = advComps[i] || {};
            var diff = parseFloat(comp.difference) || 0;
            var diffClass = diff >= 0 ? 'color:var(--success-color)' : 'color:var(--danger-color)';
            var diffSign = diff >= 0 ? '+' : '';

            html += '<tr>';
            html += '<td>' + (calc.period_name || calc.period) + '</td>';
            html += '<td class="amount">' + fmt(calc.income_cumulative) + '</td>';
            html += '<td class="amount">' + fmt(calc.tax_due) + '</td>';
            html += '<td class="amount">' + fmt(comp.actual_paid || 0) + '</td>';
            html += '<td class="amount" style="font-weight:bold;' + diffClass + '">' + diffSign + fmt(Math.abs(diff)) + '</td>';
            html += '</tr>';
        }
        advTbody.innerHTML = html;

        // Update contributions required vs paid
        var reqTotal = result.contributions_required || {};
        document.getElementById('step-4-calc-fixed').textContent = fmt(reqTotal.fixed || 0);
        document.getElementById('step-4-calc-1pct').textContent = fmt(reqTotal.one_percent || 0);
        document.getElementById('step-4-calc-total').textContent = fmt(reqTotal.total || 0);

        showToast('Авторасчёт выполнен');
    } catch (e) {
        console.error('Auto-calc error:', e);
    } finally {
        hideLoading();
    }
}

async function saveSettings() {
    if (!state.projectId) return;

    var inn = document.getElementById('step-4-inn').value || '0000000000';
    var fio = document.getElementById('step-4-fio').value || '\u0418\u041f';
    var year = parseInt(document.getElementById('step-4-period').value) || new Date().getFullYear();
    var oktmo = document.getElementById('step-4-oktmo').value || '';
    var ifns = document.getElementById('step-4-ifns').value || '';
    var hasEmployees = document.getElementById('step-4-has-employees').checked;

    var employeeStartQuarter = null;
    if (hasEmployees) {
        var checked = document.querySelector('input[name="employee-quarter"]:checked');
        var qMap = { q1: 1, q2: 2, q3: 3, q4: 4 };
        employeeStartQuarter = checked ? qMap[checked.value] : 1;
    }

    try {
        // Update project settings
        await apiCall('/api/projects/' + state.projectId, {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                inn: inn,
                fio: fio,
                tax_period_year: year,
                oktmo: oktmo,
                ifns_code: ifns,
                has_employees: hasEmployees,
                employee_start_quarter: employeeStartQuarter,
                uses_ens: true,
            }),
        });

        // Save contributions from ENS payments (contribution categories)
        var contributions = [];
        for (var i = 0; i < state.ensPayments.length; i++) {
            var p = state.ensPayments[i];
            var cat = p.detected_category;
            if (cat === 'fixed_contributions' || cat === 'one_percent' || cat === 'employee_contributions' || cat === 'ens_mixed') {
                contributions.push({
                    contribution_type: cat === 'ens_mixed' ? 'total' : cat === 'fixed_contributions' ? 'fixed_ip' : cat,
                    amount: parseFloat(p.amount) || 0,
                    payment_date: p.date || null,
                });
            }
        }

        if (contributions.length > 0) {
            await apiCall('/api/tax/contributions/' + state.projectId, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(contributions),
            });
        }
    } catch (e) {
        console.error('Save settings error:', e);
    }
}

// ============================================================================
// STEP 5: TAX CALCULATION
// ============================================================================
async function calculateTax() {
    if (!state.projectId) return;
    showLoading();
    document.getElementById('step-5-loading').style.display = 'flex';
    try {
        var sel = document.querySelector('input[name="revenue-source"]:checked');
        var source = sel ? sel.value : 'bank';
        var url = '/api/tax/calculate/' + state.projectId + '?revenue_source=' + encodeURIComponent(source);
        var result = await apiCall(url, { method: 'POST' });
        if (result.revenue_source_note) {
            showToast(result.revenue_source_note);
        }

        state.calculations = result.calculations || [];
        state.calculationDone = true;

        renderCalculationResults();
        document.getElementById('step-5-pre-calc').style.display = 'none';
        document.getElementById('step-5-calc-results').style.display = 'block';

        showToast('Расчёт выполнен');
    } catch (e) {
        console.error('Calculation error:', e);
    } finally {
        hideLoading();
        document.getElementById('step-5-loading').style.display = 'none';
    }
}

function renderCalculationResults() {
    var calcs = state.calculations;
    if (!calcs.length) return;

    var yearCalc = null;
    for (var i = 0; i < calcs.length; i++) {
        if (calcs[i].period === 'year') { yearCalc = calcs[i]; break; }
    }
    if (!yearCalc) yearCalc = calcs[calcs.length - 1];

    document.getElementById('step-5-total-income').textContent = fmt(yearCalc.income_cumulative);
    document.getElementById('step-5-tax-rate').textContent = fmt(yearCalc.tax_calculated);
    document.getElementById('step-5-contributions-applied').textContent = fmt(yearCalc.contributions_applied);
    document.getElementById('step-5-tax-due').textContent = fmt(yearCalc.tax_due);

    var periodNames = { q1: '1 квартал', half_year: 'Полугодие', nine_months: '9 месяцев', year: 'Год' };
    var tbody = document.getElementById('step-5-calc-tbody');
    var html = '';
    for (var i = 0; i < calcs.length; i++) {
        var c = calcs[i];
        html += '<tr>'
            + '<td>' + (periodNames[c.period] || c.period) + '</td>'
            + '<td class="amount">' + fmt(c.income_cumulative) + '</td>'
            + '<td class="amount">' + fmt(c.tax_calculated) + '</td>'
            + '<td class="amount">' + fmt(c.contributions_applied) + '</td>'
            + '<td class="amount">' + fmt(c.contribution_limit) + '</td>'
            + '<td class="amount">' + fmt(c.tax_after_reduction) + '</td>'
            + '<td class="amount">' + fmt(c.advance_paid) + '</td>'
            + '<td class="amount" style="font-weight:bold;">' + fmt(c.tax_due) + '</td>'
            + '</tr>';
    }
    tbody.innerHTML = html;

    // Balance alert
    var alertEl = document.getElementById('step-5-balance-alert');
    var due = parseFloat(yearCalc.tax_due) || 0;
    if (due > 0) {
        alertEl.className = 'alert alert-warning';
        alertEl.innerHTML = '<strong>К доплате за год:</strong> ' + fmt(due);
        alertEl.style.display = 'block';
    } else if (due < 0) {
        alertEl.className = 'alert alert-success';
        alertEl.innerHTML = '<strong>Переплата:</strong> ' + fmt(Math.abs(due));
        alertEl.style.display = 'block';
    } else {
        alertEl.className = 'alert alert-success';
        alertEl.innerHTML = '<strong>Налог полностью уплачен.</strong>';
        alertEl.style.display = 'block';
    }
}

// ============================================================================
// STEP 6: DECLARATION
// ============================================================================
async function loadDeclaration() {
    if (!state.projectId) return;
    showLoading();
    try {
        var data = await apiCall('/api/tax/declaration/' + state.projectId);
        state.declarationData = data;
        renderDeclaration(data);
    } catch (e) {
        console.error('Load declaration error:', e);
    } finally {
        hideLoading();
    }
}

function renderDeclaration(data) {
    var section211 = document.getElementById('step-6-section-211');
    var calcs = data.calculations || [];

    var periodLabels = { q1: '1 кв.', half_year: 'Полуг.', nine_months: '9 мес.', year: 'Год' };
    var lines211 = [];

    // Lines 110-113: cumulative income
    var lineNames110 = { q1: '110', half_year: '111', nine_months: '112', year: '113' };
    for (var i = 0; i < calcs.length; i++) {
        var c = calcs[i];
        lines211.push({ label: 'Стр. ' + (lineNames110[c.period] || '\u2014') + ' Доход (' + periodLabels[c.period] + ')', value: fmt(c.income_cumulative) });
    }

    // Lines 120-123: tax rate
    var lineNames120 = { q1: '120', half_year: '121', nine_months: '122', year: '123' };
    for (var i = 0; i < calcs.length; i++) {
        lines211.push({ label: 'Стр. ' + (lineNames120[calcs[i].period] || '\u2014') + ' Ставка', value: (data.tax_rate || 6) + '%' });
    }

    // Lines 130-133: calculated tax
    var lineNames130 = { q1: '130', half_year: '131', nine_months: '132', year: '133' };
    for (var i = 0; i < calcs.length; i++) {
        lines211.push({ label: 'Стр. ' + (lineNames130[calcs[i].period] || '\u2014') + ' Налог (' + periodLabels[calcs[i].period] + ')', value: fmt(calcs[i].tax_calculated) });
    }

    // Lines 140-143: contributions applied
    var lineNames140 = { q1: '140', half_year: '141', nine_months: '142', year: '143' };
    for (var i = 0; i < calcs.length; i++) {
        lines211.push({ label: 'Стр. ' + (lineNames140[calcs[i].period] || '\u2014') + ' Взносы (' + periodLabels[calcs[i].period] + ')', value: fmt(calcs[i].contributions_applied) });
    }

    var html211 = '';
    for (var i = 0; i < lines211.length; i++) {
        html211 += '<div class="declaration-line">'
            + '<span class="declaration-line-label">' + lines211[i].label + '</span>'
            + '<span class="declaration-line-value">' + lines211[i].value + '</span>'
            + '</div>';
    }
    section211.innerHTML = html211;

    // Section 1.1
    var section11 = document.getElementById('step-6-section-11');
    var lines11 = [];

    // OKTMO lines
    var oktmoLines = ['010', '030', '060', '090'];
    for (var i = 0; i < oktmoLines.length; i++) {
        lines11.push({ label: 'Стр. ' + oktmoLines[i] + ' ОКТМО', value: data.oktmo || '\u2014' });
    }

    // Advance/tax lines
    for (var i = 0; i < calcs.length; i++) {
        var c = calcs[i];
        var due = parseFloat(c.tax_due) || 0;
        if (c.period === 'q1') {
            lines11.push({ label: 'Стр. 020 Аванс к уплате (1 кв.)', value: fmt(Math.max(0, due)) });
        } else if (c.period === 'half_year') {
            if (due >= 0) lines11.push({ label: 'Стр. 040 Аванс к уплате (полуг.)', value: fmt(due) });
            else lines11.push({ label: 'Стр. 050 Аванс к уменьшению (полуг.)', value: fmt(Math.abs(due)) });
        } else if (c.period === 'nine_months') {
            if (due >= 0) lines11.push({ label: 'Стр. 070 Аванс к уплате (9 мес.)', value: fmt(due) });
            else lines11.push({ label: 'Стр. 080 Аванс к уменьшению (9 мес.)', value: fmt(Math.abs(due)) });
        } else if (c.period === 'year') {
            if (due >= 0) lines11.push({ label: 'Стр. 100 Налог к уплате (год)', value: fmt(due) });
            else lines11.push({ label: 'Стр. 110 Налог к уменьшению (год)', value: fmt(Math.abs(due)) });
        }
    }

    var html11 = '';
    for (var i = 0; i < lines11.length; i++) {
        html11 += '<div class="declaration-line">'
            + '<span class="declaration-line-label">' + lines11[i].label + '</span>'
            + '<span class="declaration-line-value">' + lines11[i].value + '</span>'
            + '</div>';
    }
    section11.innerHTML = html11;
}

// ============================================================================
// EXPORT
// ============================================================================
function exportDeclaration(format) {
    if (!state.projectId) return;
    var url = '/api/export/' + format + '/' + state.projectId;
    window.open(url, '_blank');
}

// ============================================================================
// INIT
// ============================================================================
document.addEventListener('DOMContentLoaded', function() {
    setupDropZone('drop-zone-1', uploadBankStatement);
    setupDropZone('drop-zone-2', uploadOFDReceipts);
    updateProgressBar();
    updateNavigationButtons();

    // Allow clicking progress bar steps (only completed or current)
    var progressSteps = document.querySelectorAll('.progress-step');
    for (var i = 0; i < progressSteps.length; i++) {
        (function(el) {
            el.addEventListener('click', function() {
                var s = parseInt(el.getAttribute('data-step'));
                if (s <= state.currentStep) goToStep(s);
            });
        })(progressSteps[i]);
    }

    // Check if there's already a project (page reload scenario)
    apiCall('/api/projects/').then(function(projects) {
        if (projects && projects.length > 0) {
            var last = projects[projects.length - 1];
            state.projectId = last.id;

            if (last.inn && last.inn !== '0000000000') {
                document.getElementById('step-4-inn').value = last.inn;
            }
            if (last.fio && last.fio !== 'Новый проект') {
                document.getElementById('step-4-fio').value = last.fio;
            }
            if (last.tax_period_year) {
                document.getElementById('step-4-period').value = last.tax_period_year;
            }
            if (last.oktmo) document.getElementById('step-4-oktmo').value = last.oktmo;
            if (last.ifns_code) document.getElementById('step-4-ifns').value = last.ifns_code;
            if (last.has_employees) {
                document.getElementById('step-4-has-employees').checked = true;
                toggleEmployeesStep4();
            }
            if (last.uses_ens) {
                document.getElementById('step-4-has-ens').checked = true;
                toggleENSStep4();
            }
        }
    }).catch(function() {});
});
