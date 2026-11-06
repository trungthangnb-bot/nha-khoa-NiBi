/**
 * Nha Khoa NiBi - BS. Thắng
 * Client Application Logic
 */

// Application State
const state = {
    token: localStorage.getItem('nibi_token') || '',
    user: null,
    meta: { branches: [], categories: [], methods: [], statuses: [], dentists: [] },
    activeTab: 'dashboard',
    branchFilter: '',
    searchDebounce: null,
    patients: { items: [], total: 0, limit: 50, offset: 0 },
    debts: { items: [], total: 0, debtTotal: 0 },
    currentPatient: null,
    pinPromptCallback: null,
    adminPwCallback: null,
};

// ==================== UTILITY FUNCTIONS ====================
function formatVND(val) {
    const num = Number(val) || 0;
    const inK = num / 1000;
    if (inK === 0) return '0K';
    if (inK % 1 === 0) {
        return new Intl.NumberFormat('vi-VN').format(inK) + 'K';
    }
    return new Intl.NumberFormat('vi-VN', { maximumFractionDigits: 1 }).format(inK) + 'K';
}

function parseK(str) {
    if (!str) return 0;
    let s = String(str).trim().toLowerCase().replace(/k/g, '');
    let num = parseInt(s.replace(/[^\d]/g, ''), 10) || 0;
    // Auto-normalize if user typed full VND with 3 zeros out of habit (e.g. 500.000 -> 500)
    if (num >= 100000 && num % 1000 === 0) {
        num = Math.round(num / 1000);
    }
    return num;
}

function parseVND(str) {
    // All inputs in app are in K, multiply by 1000 to get exact VND
    return parseK(str) * 1000;
}

function formatCurrencyInput(input) {
    const raw = parseK(input.value);
    input.value = raw > 0 ? new Intl.NumberFormat('vi-VN').format(raw) : '0';
}

function clearZeroOnFocus(input) {
    if (input.value === '0') input.value = '';
}

function escapeHtml(str) {
    if (!str) return '';
    return String(str)
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;');
}

function showToast(message, type = 'info') {
    const container = document.getElementById('toast-container');
    if (!container) return;
    const toast = document.createElement('div');
    const colors = {
        success: 'bg-emerald-600 text-white',
        error: 'bg-rose-600 text-white',
        warning: 'bg-amber-500 text-white',
        info: 'bg-slate-800 text-white'
    };
    const icons = {
        success: 'fa-circle-check',
        error: 'fa-circle-exclamation',
        warning: 'fa-triangle-exclamation',
        info: 'fa-circle-info'
    };
    toast.className = `${colors[type] || colors.info} px-4 py-3 rounded-xl shadow-xl flex items-center gap-2.5 text-xs font-semibold pointer-events-auto transform transition-all duration-300 translate-y-2 opacity-0`;
    toast.innerHTML = `<i class="fa-solid ${icons[type] || icons.info} text-sm"></i><span>${escapeHtml(message)}</span>`;
    container.appendChild(toast);
    requestAnimationFrame(() => {
        toast.classList.remove('translate-y-2', 'opacity-0');
    });
    setTimeout(() => {
        toast.classList.add('opacity-0', 'translate-y-2');
        setTimeout(() => toast.remove(), 300);
    }, 3500);
}

// ==================== API CLIENT ====================
async function api(path, options = {}) {
    const headers = options.headers || {};
    if (state.token) {
        headers['Authorization'] = `Bearer ${state.token}`;
    }
    if (options.body && typeof options.body === 'object' && !(options.body instanceof FormData)) {
        headers['Content-Type'] = 'application/json';
        options.body = JSON.stringify(options.body);
    }
    options.headers = headers;

    try {
        const resp = await fetch(path, options);
        if (resp.status === 401) {
            state.token = '';
            localStorage.removeItem('nibi_token');
            showLoginModal();
            throw new Error('Phiên đăng nhập đã hết hạn. Vui lòng đăng nhập lại.');
        }
        const data = await resp.json().catch(() => ({}));
        if (!resp.ok) {
            const err = data.detail || data.message || `Lỗi hệ thống (${resp.status})`;
            throw new Error(typeof err === 'string' ? err : JSON.stringify(err));
        }
        return data;
    } catch (e) {
        throw e;
    }
}

// ==================== INITIALIZATION & AUTH ====================
document.addEventListener('DOMContentLoaded', async () => {
    // Enable smart keyboard navigation (Enter, Arrow Up/Down/Left/Right) in intake and dialogs
    setupFormKeyboardNavigation();

    // Setup global branch selector sync
    const bTop = document.getElementById('branch-select-top');
    const bSide = document.getElementById('branch-select');
    if (bTop && bSide) {
        bTop.addEventListener('change', (e) => { bSide.value = e.target.value; });
        bSide.addEventListener('change', (e) => { bTop.value = e.target.value; });
    }

    // Check token and initialize
    if (state.token) {
        try {
            state.user = await api('/api/auth/me');
            hideLoginModal();
            await onAuthenticated();
        } catch (err) {
            console.warn('Invalid saved token:', err);
            showLoginModal();
        }
    } else {
        showLoginModal();
    }

    // Login Form Submit Handler
    const loginForm = document.getElementById('login-form');
    if (loginForm) {
        loginForm.addEventListener('submit', async (e) => {
            e.preventDefault();
            const u = document.getElementById('login-username').value.trim();
            const p = document.getElementById('login-password').value;
            const remember = document.getElementById('login-remember').checked;
            const errBox = document.getElementById('login-error');
            const errMsg = document.getElementById('login-error-msg');
            const submitBtn = document.getElementById('btn-login-submit');

            errBox.classList.add('hidden');
            submitBtn.disabled = true;
            submitBtn.innerHTML = '<i class="fa-solid fa-spinner fa-spin"></i> Đang mở khóa...';

            try {
                const res = await api('/api/auth/login', {
                    method: 'POST',
                    body: { username: u, password: p, remember: remember }
                });
                state.token = res.token;
                state.user = res.user;
                if (remember) {
                    localStorage.setItem('nibi_token', res.token);
                } else {
                    sessionStorage.setItem('nibi_token', res.token);
                }
                hideLoginModal();
                await onAuthenticated();
                showToast(`Chào mừng ${res.user.full_name} (${res.user.role === 'admin' ? 'Bác sĩ quản trị' : 'Nhân viên'})`, 'success');
            } catch (err) {
                errMsg.textContent = err.message || 'Sai thông tin đăng nhập';
                errBox.classList.remove('hidden');
            } finally {
                submitBtn.disabled = false;
                submitBtn.innerHTML = '<i class="fa-solid fa-right-to-bracket"></i> Mở khóa hệ thống';
            }
        });
    }
});

// Form 2D Navigation Grids for instant Enter / Arrow navigation
const FORM_NAV_GRIDS = {
    'patient-modal': [
        ['pm-name', 'pm-phone'],
        ['pm-yob', 'pm-age', 'pm-branch'],
        ['pm-address'],
        ['pm-history', 'pm-notes'],
        ['pm-t-date', 'pm-t-cat', 'pm-t-dentist'],
        ['pm-t-proc', 'pm-t-tooth'],
        ['pm-t-cost', 'pm-t-paid', 'pm-t-method'],
        ['pm-t-next'],
        ['btn-save-patient']
    ],
    'treatment-modal': [
        ['tm-date', 'tm-branch'],
        ['tm-category', 'tm-dentist'],
        ['tm-procedure', 'tm-tooth'],
        ['tm-cost', 'tm-paid'],
        ['tm-method', 'tm-next-date'],
        ['tm-notes'],
        ['btn-save-treatment']
    ],
    'appointment-modal': [
        ['apptm-patient-search'],
        ['apptm-kind', 'apptm-branch'],
        ['apptm-date', 'apptm-time'],
        ['apptm-reason'],
        ['btn-save-appointment']
    ],
    'collect-modal': [
        ['col-amount', 'col-method'],
        ['col-date', 'col-note'],
        ['btn-save-collect']
    ]
};

function setupFormKeyboardNavigation() {
    document.addEventListener('keydown', (e) => {
        const target = e.target;
        if (!target) return;

        // Check if inside one of our modal forms
        let activeModalId = null;
        let grid = null;
        for (const [modalId, mGrid] of Object.entries(FORM_NAV_GRIDS)) {
            const modalEl = document.getElementById(modalId);
            if (modalEl && !modalEl.classList.contains('hidden') && modalEl.contains(target)) {
                activeModalId = modalId;
                grid = mGrid;
                break;
            }
        }

        // Standard Enter prevention for ordinary non-modal forms
        if (!grid) {
            if (e.key === 'Enter' && target.tagName !== 'TEXTAREA' && target.tagName !== 'BUTTON') {
                const form = target.closest('form');
                if (form && form.id !== 'login-form') {
                    e.preventDefault();
                    return false;
                }
            }
            return;
        }

        const key = e.key;
        if (!['Enter', 'ArrowDown', 'ArrowUp', 'ArrowRight', 'ArrowLeft'].includes(key)) {
            return;
        }

        // If target is a button and user hits Enter, let button perform click naturally
        if (target.tagName === 'BUTTON' && key === 'Enter') {
            return;
        }

        // Find position in grid
        const targetId = target.id;
        let curRow = -1;
        let curCol = -1;
        for (let r = 0; r < grid.length; r++) {
            const c = grid[r].indexOf(targetId);
            if (c !== -1) {
                curRow = r;
                curCol = c;
                break;
            }
        }

        if (curRow === -1) return;

        // Helper: check if element is visible and interactable
        const isInteractable = (elId) => {
            const el = document.getElementById(elId);
            if (!el || el.disabled) return false;
            if (el.offsetParent === null) return false;
            if (el.closest('.hidden')) return false;
            return true;
        };

        const focusAndSelect = (elId) => {
            const el = document.getElementById(elId);
            if (!el) return false;
            el.focus();
            if (typeof el.select === 'function' && el.tagName === 'INPUT' && !['date', 'checkbox', 'radio'].includes(el.type)) {
                el.select();
            }
            return true;
        };

        // Flatten visible fields in order
        const flatVisible = [];
        for (let r = 0; r < grid.length; r++) {
            for (let c = 0; c < grid[r].length; c++) {
                const id = grid[r][c];
                if (isInteractable(id)) {
                    flatVisible.push({ id, r, c });
                }
            }
        }

        const flatIdx = flatVisible.findIndex(item => item.id === targetId);

        // 1. ENTER: jump to next visible field
        if (key === 'Enter') {
            e.preventDefault();
            if (flatIdx !== -1 && flatIdx < flatVisible.length - 1) {
                focusAndSelect(flatVisible[flatIdx + 1].id);
            } else if (flatIdx === flatVisible.length - 1) {
                const btn = document.getElementById(flatVisible[flatIdx].id);
                if (btn) btn.click();
            }
            return;
        }

        // 2. ARROW DOWN: jump to row below
        if (key === 'ArrowDown') {
            // For selects: let user cycle options unless Shift/Alt, or navigate down if simple
            if (target.tagName === 'SELECT') {
                // If it's a closed select, Enter or Down can be used, let standard select change work
                return;
            }
            e.preventDefault();
            for (let r = curRow + 1; r < grid.length; r++) {
                const rowItems = grid[r].filter(id => isInteractable(id));
                if (rowItems.length > 0) {
                    const targetItem = rowItems[Math.min(curCol, rowItems.length - 1)];
                    focusAndSelect(targetItem);
                    return;
                }
            }
            return;
        }

        // 3. ARROW UP: jump to row above
        if (key === 'ArrowUp') {
            if (target.tagName === 'SELECT') {
                return;
            }
            e.preventDefault();
            for (let r = curRow - 1; r >= 0; r--) {
                const rowItems = grid[r].filter(id => isInteractable(id));
                if (rowItems.length > 0) {
                    const targetItem = rowItems[Math.min(curCol, rowItems.length - 1)];
                    focusAndSelect(targetItem);
                    return;
                }
            }
            return;
        }

        // 4. ARROW RIGHT: move to next column or next field
        if (key === 'ArrowRight') {
            // If typing in input, only navigate if cursor is at the very end or text is all selected
            if (target.tagName === 'INPUT' && (target.type === 'text' || target.type === 'tel')) {
                const isAtEnd = target.selectionStart === target.value.length;
                const isAllSelected = target.selectionStart === 0 && target.selectionEnd === target.value.length;
                if (!isAtEnd && !isAllSelected) {
                    return; // let cursor move inside text
                }
            }
            e.preventDefault();
            if (flatIdx !== -1 && flatIdx < flatVisible.length - 1) {
                focusAndSelect(flatVisible[flatIdx + 1].id);
            }
            return;
        }

        // 5. ARROW LEFT: move to previous column or previous field
        if (key === 'ArrowLeft') {
            // If typing in input, only navigate if cursor is at the very beginning or text is all selected
            if (target.tagName === 'INPUT' && (target.type === 'text' || target.type === 'tel')) {
                const isAtStart = target.selectionStart === 0 && target.selectionEnd === 0;
                const isAllSelected = target.selectionStart === 0 && target.selectionEnd === target.value.length;
                if (!isAtStart && !isAllSelected) {
                    return; // let cursor move inside text
                }
            }
            e.preventDefault();
            if (flatIdx > 0) {
                focusAndSelect(flatVisible[flatIdx - 1].id);
            }
            return;
        }
    });
}

function showLoginModal() {
    const m = document.getElementById('login-modal');
    if (m) m.classList.remove('hidden');
}

function hideLoginModal() {
    const m = document.getElementById('login-modal');
    if (m) m.classList.add('hidden');
}

function togglePasswordVisibility(fieldId, btn) {
    const input = document.getElementById(fieldId);
    if (!input) return;
    if (input.type === 'password') {
        input.type = 'text';
        btn.innerHTML = '<i class="fa-solid fa-eye-slash text-sm"></i>';
    } else {
        input.type = 'password';
        btn.innerHTML = '<i class="fa-solid fa-eye text-sm"></i>';
    }
}

async function doLogout() {
    if (!confirm('Bạn có chắc chắn muốn đăng xuất khỏi hệ thống?')) return;
    try {
        await api('/api/auth/logout', { method: 'POST' });
    } catch (_) {}
    state.token = '';
    state.user = null;
    localStorage.removeItem('nibi_token');
    sessionStorage.removeItem('nibi_token');
    showLoginModal();
}

async function onAuthenticated() {
    // Update user display (desktop & mobile drawer)
    const nameEl = document.getElementById('user-display-name');
    const roleEl = document.getElementById('user-display-role');
    const nameDrawerEl = document.getElementById('user-display-name-drawer');
    const roleDrawerEl = document.getElementById('user-display-role-drawer');

    const roleText = state.user.role === 'admin' ? 'BS. Thắng (Admin)' : 'Nhân viên';
    if (nameEl) nameEl.textContent = state.user.full_name;
    if (roleEl) roleEl.textContent = roleText;
    if (nameDrawerEl) nameDrawerEl.textContent = state.user.full_name;
    if (roleDrawerEl) roleDrawerEl.textContent = roleText;

    // Show/hide admin-only elements
    const isAdmin = state.user.role === 'admin';
    document.querySelectorAll('.admin-only').forEach(el => {
        if (isAdmin) {
            el.classList.remove('hidden');
        } else {
            el.classList.add('hidden');
        }
    });

    // Load Meta
    try {
        state.meta = await api('/api/meta');
    } catch (e) {
        console.error('Failed to load meta', e);
    }

    // Populate dentist & branch selection lists in intake & treatment forms
    populateDentistSelects();
    populateBranchSelects();

    // Render address autocomplete suggestions & quick chips from history
    renderAddressSuggestions();

    // Check silent alerts for admin
    if (isAdmin) {
        checkAdminAlerts();
        if (!window._adminAlertInterval) {
            window._adminAlertInterval = setInterval(checkAdminAlerts, 20000);
        }
    }

    // Set today date on treatment default
    const today = new Date().toISOString().split('T')[0];
    const tDateInput = document.getElementById('pm-t-date');
    if (tDateInput) tDateInput.value = today;

    // Load initial tab: Staff NEVER sees dashboard or admin hub, always lands on patients
    const initialTab = isAdmin ? (state.activeTab || 'dashboard') : 'patients';
    await switchTab(initialTab);
}

// ==================== TAB NAVIGATION ====================
async function switchTab(tab) {
    // Automatically close mobile sidebar drawer on navigation
    closeMobileSidebar();

    if ((tab === 'admin-hub' || tab === 'dashboard') && (!state.user || state.user.role !== 'admin')) {
        showToast('Khu vực này chỉ dành riêng cho Admin (BS. Thắng)', 'error');
        await switchTab('patients');
        return;
    }
    state.activeTab = tab;

    // Update Desktop Nav & Drawer styles
    document.querySelectorAll('[data-tab-btn]').forEach(btn => {
        const isCurrent = btn.getAttribute('data-tab-btn') === tab;
        const isMobileBottom = btn.classList.contains('mobile-nav-btn');
        if (isMobileBottom) {
            if (isCurrent) {
                btn.classList.add('text-teal-600', 'font-bold');
                btn.classList.remove('text-slate-400');
            } else {
                btn.classList.remove('text-teal-600', 'font-bold');
                btn.classList.add('text-slate-400');
            }
            return;
        }

        // Sidebar / Drawer items
        if (isCurrent) {
            if (tab === 'admin-hub') {
                btn.className = 'nav-item w-full flex items-center gap-3 px-3 py-2.5 rounded-xl transition text-left text-amber-300 bg-amber-950/70 border border-amber-500 font-bold shadow-md';
            } else {
                btn.className = btn.className.replace(/text-slate-400 hover:text-white hover:bg-slate-800\/60/g, '') + ' text-teal-400 bg-slate-800/80 font-semibold shadow-sm';
            }
        } else {
            if (btn.getAttribute('data-tab-btn') === 'admin-hub') {
                btn.className = 'nav-item w-full flex items-center gap-3 px-3 py-2.5 rounded-xl transition text-left text-amber-300 hover:text-white hover:bg-amber-950/40 border border-amber-500/30 font-semibold shadow-sm mt-1';
            } else {
                btn.className = btn.className.replace(/text-teal-400 bg-slate-800\/80 font-semibold shadow-sm/g, '') + ' text-slate-400 hover:text-white hover:bg-slate-800/60';
            }
        }
    });

    // Hide all sections, show active
    ['dashboard', 'patients', 'debts', 'appointments', 'admin-hub'].forEach(t => {
        const sec = document.getElementById(`tab-${t}`);
        if (sec) {
            if (t === tab) sec.classList.remove('hidden');
            else sec.classList.add('hidden');
        }
    });

    // Update Header Titles
    const titles = {
        dashboard: ['Tổng quan phòng khám', 'Dữ liệu đồng bộ trực tiếp • Hệ thống Nha Khoa NiBi'],
        patients: ['Hồ sơ bệnh nhân & Bệnh án', 'Tìm kiếm không dấu, xem đợt điều trị, tiền sử bệnh'],
        debts: ['Sổ quản lý công nợ', 'Thu hồi công nợ theo đợt điều trị, tự động cấn trừ FIFO'],
        appointments: ['Lịch hẹn & Tái khám', 'Quản lý lịch hẹn khám mới và nhắc tái khám'],
        'admin-hub': ['Trung Tâm Quản Trị Hệ Thống', 'Doanh thu • Cấp quyền tài khoản • Chỉnh sửa ứng dụng • Sao lưu an toàn']
    };
    if (titles[tab]) {
        document.getElementById('page-title').textContent = titles[tab][0];
        document.getElementById('page-subtitle').textContent = titles[tab][1];
    }

    // Scroll to top
    const scroller = document.getElementById('content-scroll');
    if (scroller) scroller.scrollTop = 0;

    // Load data for active tab
    if (tab === 'dashboard') await loadDashboard();
    else if (tab === 'patients') await loadPatients(0);
    else if (tab === 'debts') await loadDebts();
    else if (tab === 'appointments') await filterAppointments(currentApptFilter || 'month');
    else if (tab === 'admin-hub') await loadAdminHub();
}

function onBranchChange(val) {
    state.branchFilter = val;
    const bTop = document.getElementById('branch-select-top');
    const bSide = document.getElementById('branch-select');
    const bMob = document.getElementById('branch-select-mobile');
    if (bTop) bTop.value = val;
    if (bSide) bSide.value = val;
    if (bMob) bMob.value = val;

    // Reload active tab
    switchTab(state.activeTab);
}

// ==================== DASHBOARD TAB ====================
async function loadDashboard() {
    try {
        const data = await api(`/api/dashboard?branch=${encodeURIComponent(state.branchFilter)}`);

        document.getElementById('stat-patients').textContent = data.patients || 0;
        document.getElementById('stat-visits-today').textContent = data.visits_today || 0;

        if (data.revenue) {
            document.getElementById('stat-revenue-month').textContent = formatVND(data.revenue.month);
            document.getElementById('stat-billed-month').textContent = `Phát sinh: ${formatVND(data.revenue.billed_month)}`;
            renderDashboardChart(data.revenue.series);
        }

        const totalDebt = data.debt ? data.debt.total : 0;
        const debtCount = data.debt ? data.debt.patients : 0;
        document.getElementById('stat-total-debt').textContent = formatVND(totalDebt);
        document.getElementById('stat-debt-patients').textContent = `${debtCount} người đang nợ`;

        // Update badges
        const badgeDebt = document.getElementById('badge-debt-count');
        const mobileBadgeDebt = document.getElementById('mobile-badge-debt');
        const drawerBadgeDebt = document.getElementById('badge-debt-count-drawer');
        if (debtCount > 0) {
            if (badgeDebt) { badgeDebt.textContent = debtCount; badgeDebt.classList.remove('hidden'); }
            if (mobileBadgeDebt) mobileBadgeDebt.classList.remove('hidden');
            if (drawerBadgeDebt) { drawerBadgeDebt.textContent = debtCount; drawerBadgeDebt.classList.remove('hidden'); }
        } else {
            if (badgeDebt) badgeDebt.classList.add('hidden');
            if (mobileBadgeDebt) mobileBadgeDebt.classList.add('hidden');
            if (drawerBadgeDebt) drawerBadgeDebt.classList.add('hidden');
        }

        // Top Debtors List
        const debtorsContainer = document.getElementById('top-debtors-list');
        if (debtorsContainer) {
            const list = (data.debt && data.debt.top) || [];
            if (list.length === 0) {
                debtorsContainer.innerHTML = '<p class="text-xs text-slate-400 py-3 text-center">Không có bệnh nhân nào còn nợ tiền 🎉</p>';
            } else {
                debtorsContainer.innerHTML = list.map(d => `
                    <div class="py-2.5 flex items-center justify-between text-xs hover:bg-slate-50 rounded-xl px-2 transition">
                        <div class="min-w-0 pr-2">
                            <p class="font-bold text-slate-800 truncate cursor-pointer hover:text-teal-600" onclick="openPatientDetail(${d.patient_id})">${escapeHtml(d.name)}</p>
                            <p class="text-[11px] text-slate-400">
                                ${d.phone ? `<a href="tel:${d.phone}" class="text-teal-600 hover:underline font-semibold"><i class="fa-solid fa-phone text-[10px] mr-0.5"></i>${d.phone}</a>` : 'Không có SĐT'}
                                ${d.oldest ? ` • Nợ từ: ${d.oldest}` : ''}
                            </p>
                        </div>
                        <div class="flex items-center gap-2 flex-shrink-0">
                            <span class="font-extrabold text-rose-600">${formatVND(d.debt)}</span>
                            <button onclick="openCollectModalFor(${d.patient_id}, '${escapeHtml(d.name)}', ${d.debt})" class="px-2.5 py-1 bg-rose-50 text-rose-700 hover:bg-rose-100 font-bold rounded-lg transition text-[11px]">
                                Thu nợ
                            </button>
                        </div>
                    </div>
                `).join('');
            }
        }

        // Upcoming Appts List
        const apptsContainer = document.getElementById('upcoming-appts-list');
        if (apptsContainer) {
            const list = data.upcoming || [];
            if (list.length === 0) {
                apptsContainer.innerHTML = '<p class="text-xs text-slate-400 py-3 text-center">Không có lịch hẹn nào trong 7 ngày tới</p>';
            } else {
                apptsContainer.innerHTML = list.map(a => `
                    <div class="py-2.5 flex items-center justify-between text-xs hover:bg-slate-50 rounded-xl px-2 transition">
                        <div class="min-w-0 pr-2">
                            <p class="font-bold text-slate-800 truncate cursor-pointer hover:text-teal-600" onclick="openPatientDetail(${a.patient_id})">
                                ${escapeHtml(a.patient_name)}
                                <span class="ml-1.5 px-2 py-0.5 rounded-full text-[10px] font-bold ${a.kind === 'tai_kham' ? 'bg-teal-50 text-teal-700' : 'bg-blue-50 text-blue-700'}">${a.kind === 'tai_kham' ? 'Tái khám' : 'Hẹn khám'}</span>
                            </p>
                            <p class="text-[11px] text-slate-500 mt-0.5">
                                📅 <b>${a.appt_date}</b> ${a.appt_time ? `(${a.appt_time})` : ''} • ${escapeHtml(a.reason || 'Khám điều trị')}
                            </p>
                        </div>
                        <div class="flex items-center gap-1 flex-shrink-0">
                            ${a.patient_phone ? `<a href="tel:${a.patient_phone}" class="p-1.5 text-teal-600 hover:bg-teal-50 rounded-lg"><i class="fa-solid fa-phone"></i></a>` : ''}
                            <button onclick="quickUpdateApptStatus(${a.id}, 'Đã đến')" title="Đánh dấu đã đến" class="p-1.5 text-emerald-600 hover:bg-emerald-50 rounded-lg"><i class="fa-solid fa-check"></i></button>
                        </div>
                    </div>
                `).join('');
            }
        }
    } catch (e) {
        showToast(e.message, 'error');
    }
}

function renderDashboardChart(series) {
    const container = document.getElementById('dashboard-chart-container');
    if (!container || !series || series.length === 0) return;

    const maxVal = Math.max(...series.map(s => Math.max(s.collected, s.billed)), 1000000);
    container.innerHTML = series.map(s => {
        const hCollected = Math.max(Math.round((s.collected / maxVal) * 140), 4);
        const hBilled = Math.max(Math.round((s.billed / maxVal) * 140), 4);
        const monthLabel = s.month.split('-')[1] + '/' + s.month.split('-')[0].slice(2);
        return `
            <div class="flex-1 min-w-[36px] flex flex-col items-center gap-1 group relative">
                <!-- Tooltip -->
                <div class="hidden group-hover:block absolute -top-16 bg-slate-900 text-white text-[10px] py-1 px-2 rounded shadow-lg whitespace-nowrap z-30 pointer-events-none">
                    <p class="font-bold text-teal-300">Tháng ${s.month}</p>
                    <p>Thực thu: ${formatVND(s.collected)}</p>
                    <p>Phát sinh: ${formatVND(s.billed)}</p>
                </div>
                <!-- Dual Bars -->
                <div class="w-full flex items-end justify-center gap-1 h-36">
                    <div style="height: ${hBilled}px" class="w-2.5 bg-slate-200 rounded-t-sm group-hover:bg-slate-300 transition-all"></div>
                    <div style="height: ${hCollected}px" class="w-2.5 bg-teal-500 rounded-t-sm group-hover:bg-teal-600 transition-all"></div>
                </div>
                <span class="text-[10px] text-slate-400 font-medium">${monthLabel}</span>
            </div>
        `;
    }).join('');
}

// ==================== PATIENTS TAB ====================
function debounceSearchPatients() {
    clearTimeout(state.searchDebounce);
    const val = document.getElementById('patient-search-input').value;
    const btnClear = document.getElementById('btn-clear-search');
    if (btnClear) {
        if (val) btnClear.classList.remove('hidden');
        else btnClear.classList.add('hidden');
    }
    state.searchDebounce = setTimeout(() => {
        loadPatients(0);
    }, 280);
}

function clearPatientSearch() {
    const input = document.getElementById('patient-search-input');
    if (input) input.value = '';
    const btnClear = document.getElementById('btn-clear-search');
    if (btnClear) btnClear.classList.add('hidden');
    loadPatients(0);
}

async function loadPatients(offset = 0) {
    const q = (document.getElementById('patient-search-input')?.value || '').trim();
    const onlyDebt = document.getElementById('filter-only-debt')?.checked || false;
    const limit = state.patients.limit;

    try {
        const url = `/api/patients?q=${encodeURIComponent(q)}&branch=${encodeURIComponent(state.branchFilter)}&only_debt=${onlyDebt}&limit=${limit}&offset=${offset}`;
        const data = await api(url);

        state.patients.items = data.items;
        state.patients.total = data.total;
        state.patients.offset = offset;

        document.getElementById('patients-count').textContent = data.total;
        const pageNum = Math.floor(offset / limit) + 1;
        const totalPages = Math.ceil(data.total / limit) || 1;
        document.getElementById('patients-page-info').textContent = `Trang ${pageNum} / ${totalPages}`;

        const tbody = document.getElementById('patients-table-body');
        const empty = document.getElementById('patients-empty');

        if (data.items.length === 0) {
            tbody.innerHTML = '';
            empty.classList.remove('hidden');
        } else {
            empty.classList.add('hidden');
            tbody.innerHTML = data.items.map(p => `
                <tr class="hover:bg-slate-50 transition cursor-pointer" onclick="openPatientDetail(${p.id})">
                    <td class="py-3 px-4">
                        <div class="font-bold text-slate-800">${escapeHtml(p.name)}</div>
                        <span class="text-[10px] text-teal-600 font-bold bg-teal-50 px-1.5 py-0.5 rounded">${p.code || 'BN' + String(p.id).padStart(5, '0')}</span>
                    </td>
                    <td class="py-3 px-4">
                        ${p.phone ? `
                            <a href="tel:${p.phone}" onclick="event.stopPropagation()" class="inline-flex items-center gap-1 text-teal-600 hover:text-teal-800 font-semibold text-xs">
                                <i class="fa-solid fa-phone text-[10px]"></i>
                                <span>${escapeHtml(p.phone)}</span>
                            </a>
                        ` : '<span class="text-slate-400 text-xs">--</span>'}
                    </td>
                    <td class="py-3 px-4 hidden md:table-cell text-xs text-slate-600">
                        ${p.yob ? `<b>${p.yob}</b> (${p.age} tuổi)` : '--'}
                    </td>
                    <td class="py-3 px-4 hidden lg:table-cell text-xs text-slate-500 max-w-xs truncate">
                        ${escapeHtml(p.address || '--')}
                    </td>
                    <td class="py-3 px-4 text-xs font-medium">
                        <span class="px-2 py-0.5 rounded-full text-[11px] bg-slate-100 text-slate-700 font-semibold">${escapeHtml(p.branch)}</span>
                    </td>
                    <td class="py-3 px-4 text-right font-medium text-xs text-slate-700">
                        ${formatVND(p.billed)}
                    </td>
                    <td class="py-3 px-4 text-right text-xs">
                        ${p.debt > 0 ? `<span class="font-bold text-rose-600 bg-rose-50 px-2 py-1 rounded-lg">${formatVND(p.debt)}</span>` : '<span class="text-emerald-600 font-semibold">Đã đủ</span>'}
                    </td>
                    <td class="py-3 px-4 text-center" onclick="event.stopPropagation()">
                        <button onclick="openPatientDetail(${p.id})" class="px-3 py-1 bg-teal-50 hover:bg-teal-100 text-teal-700 rounded-lg text-xs font-bold transition">
                            Bệnh án
                        </button>
                    </td>
                </tr>
            `).join('');
        }

        renderPagination(data.total, limit, offset);
    } catch (e) {
        showToast(e.message, 'error');
    }
}

function renderPagination(total, limit, offset) {
    const container = document.getElementById('patients-pagination');
    if (!container) return;
    if (total <= limit) {
        container.innerHTML = '';
        return;
    }
    const current = Math.floor(offset / limit);
    const pages = Math.ceil(total / limit);

    container.innerHTML = `
        <button onclick="loadPatients(${Math.max(0, offset - limit)})" ${offset === 0 ? 'disabled class="opacity-40 px-3 py-1 bg-slate-100 rounded-lg text-xs font-semibold"' : 'class="px-3 py-1 bg-white border border-slate-200 hover:bg-slate-50 rounded-lg text-xs font-semibold shadow-sm"'}>
            <i class="fa-solid fa-chevron-left mr-1"></i>Trước
        </button>
        <span class="text-xs text-slate-500 px-2">Trang <b>${current + 1}</b> / ${pages}</span>
        <button onclick="loadPatients(${offset + limit})" ${offset + limit >= total ? 'disabled class="opacity-40 px-3 py-1 bg-slate-100 rounded-lg text-xs font-semibold"' : 'class="px-3 py-1 bg-white border border-slate-200 hover:bg-slate-50 rounded-lg text-xs font-semibold shadow-sm"'}>
            Sau<i class="fa-solid fa-chevron-right ml-1"></i>
        </button>
    `;
}

// ==================== ADDRESS HISTORY & AUTOCOMPLETE ====================
function renderAddressSuggestions() {
    const metaAddresses = state.meta?.addresses || [];
    let localAddresses = [];
    try {
        localAddresses = JSON.parse(localStorage.getItem('nibi_recent_addresses') || '[]');
    } catch (_) {}

    // Clean, capitalize and deduplicate
    const set = new Set();
    const combined = [];
    [...localAddresses, ...metaAddresses].forEach(addr => {
        if (!addr) return;
        const trimmed = addr.trim();
        if (!trimmed) return;
        const key = trimmed.toLowerCase();
        if (!set.has(key)) {
            set.add(key);
            // Capitalize first letters nicely
            const formatted = trimmed.split(' ').map(w => w.charAt(0).toUpperCase() + w.slice(1).toLowerCase()).join(' ');
            combined.push(formatted);
        }
    });

    // Populate datalist
    const datalist = document.getElementById('address-suggestions');
    if (datalist) {
        datalist.innerHTML = combined.map(a => `<option value="${escapeHtml(a)}"></option>`).join('');
    }

    // Populate quick chips (top 10 most frequent / recent)
    const chipsContainer = document.getElementById('quick-address-chips');
    if (chipsContainer) {
        const topChips = combined.slice(0, 10);
        chipsContainer.innerHTML = `
            <span class="text-[10px] font-bold text-slate-400 uppercase tracking-wider self-center mr-1">Gợi ý nhanh:</span>
            ${topChips.map(a => `
                <button type="button" onclick="selectQuickAddress('${escapeHtml(a).replace(/'/g, "\\'")}')"
                        class="px-2.5 py-1 bg-slate-100 hover:bg-teal-50 hover:text-teal-700 hover:border-teal-300 border border-slate-200 text-slate-700 rounded-lg text-xs font-semibold transition active:scale-95 flex items-center gap-1 shadow-2xs">
                    <i class="fa-solid fa-map-pin text-[10px] text-teal-600"></i>
                    <span>${escapeHtml(a)}</span>
                </button>
            `).join('')}
        `;
    }
}

function selectQuickAddress(addr) {
    const input = document.getElementById('pm-address');
    if (input) {
        input.value = addr;
        input.focus();
    }
}

function saveAddressToHistory(addr) {
    if (!addr || !addr.trim()) return;
    const cleanAddr = addr.trim();
    try {
        let recents = JSON.parse(localStorage.getItem('nibi_recent_addresses') || '[]');
        recents = [cleanAddr, ...recents.filter(x => x.toLowerCase() !== cleanAddr.toLowerCase())].slice(0, 50);
        localStorage.setItem('nibi_recent_addresses', JSON.stringify(recents));
        renderAddressSuggestions();
    } catch (_) {}
}

// ==================== PATIENT INTAKE MODAL (CRITICAL USER REQ) ====================
function openPatientModal(editingPatient = null) {
    const modal = document.getElementById('patient-modal');
    const form = document.getElementById('patient-form');
    form.reset();

    const title = document.getElementById('patient-modal-title');
    const btnSaveText = document.getElementById('btn-save-patient-text');
    const firstTreatContainer = document.getElementById('pm-first-treatment-container');
    const firstTreatCheck = document.getElementById('pm-enable-treatment');

    // Default branch
    document.getElementById('pm-branch').value = state.branchFilter || 'Ninh Vân';

    if (editingPatient) {
        // Edit Mode
        title.textContent = `Chỉnh Sửa Hồ Sơ: ${editingPatient.name}`;
        btnSaveText.textContent = 'Lưu thay đổi hồ sơ';
        document.getElementById('pm-id').value = editingPatient.id;
        document.getElementById('pm-name').value = editingPatient.name;
        document.getElementById('pm-phone').value = editingPatient.phone || '';
        document.getElementById('pm-yob').value = editingPatient.yob || '';
        document.getElementById('pm-age').value = editingPatient.yob ? (new Date().getFullYear() - editingPatient.yob) : '';
        document.getElementById('pm-branch').value = editingPatient.branch;
        document.getElementById('pm-address').value = editingPatient.address || '';
        document.getElementById('pm-history').value = editingPatient.medical_history || '';
        document.getElementById('pm-notes').value = editingPatient.notes || '';

        // Hide first treatment in edit mode
        if (firstTreatContainer) firstTreatContainer.classList.add('hidden');
    } else {
        // Create Mode
        title.textContent = 'Tiếp Đón Bệnh Nhân Mới';
        btnSaveText.textContent = 'Lưu hồ sơ & Điều trị';
        document.getElementById('pm-id').value = '';
        if (firstTreatContainer) firstTreatContainer.classList.remove('hidden');
        if (firstTreatCheck) firstTreatCheck.checked = true;
        toggleFirstTreatmentFields();

        // Default treatment fields
        const today = new Date().toISOString().split('T')[0];
        document.getElementById('pm-t-date').value = today;
        document.getElementById('pm-t-cat').value = 'Tổng quát';
        const pmDent = document.getElementById('pm-t-dentist');
        if (pmDent && pmDent.options.length > 0) pmDent.selectedIndex = 0;
        document.getElementById('pm-t-proc').value = '';
        document.getElementById('pm-t-tooth').value = '';
        document.getElementById('pm-t-cost').value = '0';
        document.getElementById('pm-t-paid').value = '0';
        updateCategoryRecallHint('Tổng quát', 'pm-t-hint');
    }

    renderAddressSuggestions();
    modal.classList.remove('hidden');
    setTimeout(() => {
        const nameInput = document.getElementById('pm-name');
        if (nameInput) {
            nameInput.focus();
            nameInput.select();
        }
    }, 50);
}

function closePatientModal() {
    const modal = document.getElementById('patient-modal');
    if (modal) modal.classList.add('hidden');
}

function toggleFirstTreatmentFields() {
    const chk = document.getElementById('pm-enable-treatment');
    const fields = document.getElementById('pm-treatment-fields');
    const btnSaveText = document.getElementById('btn-save-patient-text');
    if (chk && chk.checked) {
        if (fields) fields.classList.remove('hidden');
        if (btnSaveText) btnSaveText.textContent = 'Lưu hồ sơ & Điều trị';
    } else {
        if (fields) fields.classList.add('hidden');
        if (btnSaveText) btnSaveText.textContent = 'Lưu hồ sơ bệnh nhân';
    }
}

function syncAgeFromYob() {
    const yob = parseInt(document.getElementById('pm-yob').value, 10);
    const thisYear = new Date().getFullYear();
    if (yob >= 1900 && yob <= thisYear) {
        document.getElementById('pm-age').value = thisYear - yob;
    }
}

function syncYobFromAge() {
    const age = parseInt(document.getElementById('pm-age').value, 10);
    const thisYear = new Date().getFullYear();
    if (age >= 0 && age <= 110) {
        document.getElementById('pm-yob').value = thisYear - age;
    }
}

async function submitPatientForm() {
    const pid = document.getElementById('pm-id').value;
    const name = document.getElementById('pm-name').value.trim();
    const phone = document.getElementById('pm-phone').value.trim();
    const yob = parseInt(document.getElementById('pm-yob').value, 10) || null;
    const branch = document.getElementById('pm-branch').value;
    const address = document.getElementById('pm-address').value.trim();
    if (address) saveAddressToHistory(address);
    const history = document.getElementById('pm-history').value.trim();
    const notes = document.getElementById('pm-notes').value.trim();

    if (!name) {
        showToast('Vui lòng nhập họ tên bệnh nhân', 'error');
        document.getElementById('pm-name').focus();
        return;
    }

    const payload = {
        name, phone, yob, branch, address,
        medical_history: history,
        notes: notes
    };

    const isEdit = Boolean(pid);

    if (!isEdit) {
        // Check if first treatment enabled
        const enableTreatment = document.getElementById('pm-enable-treatment')?.checked;
        if (enableTreatment) {
            const proc = document.getElementById('pm-t-proc').value.trim();
            if (!proc) {
                showToast('Vui lòng nhập nội dung thủ thuật điều trị (hoặc bỏ chọn ô nhập đợt 1)', 'error');
                document.getElementById('pm-t-proc').focus();
                return;
            }
            const cost = parseVND(document.getElementById('pm-t-cost').value);
            const paid = parseVND(document.getElementById('pm-t-paid').value);
            if (paid > cost) {
                showToast('Số tiền đã trả không được lớn hơn tổng tiền', 'error');
                return;
            }
            payload.first_treatment = {
                visit_date: document.getElementById('pm-t-date').value || null,
                branch: branch,
                category: document.getElementById('pm-t-cat').value,
                dentist: document.getElementById('pm-t-dentist').value.trim() || 'Thắng',
                procedure: proc,
                tooth: document.getElementById('pm-t-tooth').value.trim(),
                cost: cost,
                paid: paid,
                method: document.getElementById('pm-t-method').value,
                next_date: document.getElementById('pm-t-next').value || null
            };
        }
    }

    const saveBtn = document.getElementById('btn-save-patient');
    saveBtn.disabled = true;
    saveBtn.innerHTML = '<i class="fa-solid fa-spinner fa-spin"></i> Đang lưu...';

    const executeSave = async (pin = null) => {
        try {
            if (isEdit) {
                payload.pin = pin;
                await api(`/api/patients/${pid}`, { method: 'PUT', body: payload });
                showToast('Đã cập nhật thông tin bệnh nhân thành công', 'success');
            } else {
                const res = await api('/api/patients', { method: 'POST', body: payload });
                showToast(`Tiếp đón thành công bệnh nhân ${name} (${res.code})`, 'success');
            }
            closePatientModal();
            if (isEdit && state.currentPatient) {
                await openPatientDetail(pid);
            }
            await loadPatients(0);
        } catch (e) {
            showToast(e.message, 'error');
        } finally {
            saveBtn.disabled = false;
            saveBtn.innerHTML = '<i class="fa-solid fa-floppy-disk"></i> <span id="btn-save-patient-text">Lưu hồ sơ</span>';
        }
    };

    if (isEdit) {
        requestPin(executeSave, 'Nhập PIN nhân viên (0000) để lưu thay đổi hồ sơ:');
    } else {
        await executeSave();
    }
}

// ==================== PATIENT EMR DRAWER / DETAIL ====================
async function openPatientDetail(pid) {
    try {
        const data = await api(`/api/patients/${pid}`);
        state.currentPatient = data;

        const p = data.patient;
        document.getElementById('emr-patient-name').textContent = p.name;
        document.getElementById('emr-patient-code').textContent = p.code || 'BN' + String(p.id).padStart(5, '0');
        document.getElementById('emr-patient-sub').textContent = `Cơ sở: ${p.branch} • ${p.yob ? `Năm sinh: ${p.yob} (${p.age} tuổi)` : 'Chưa có năm sinh'}`;

        // Phone box with clickable dialer
        const phoneBox = document.getElementById('emr-patient-phone-box');
        if (p.phone) {
            phoneBox.innerHTML = `
                <a href="tel:${p.phone}" class="inline-flex items-center gap-1.5 px-3 py-1 bg-teal-50 text-teal-700 hover:bg-teal-100 rounded-lg font-bold text-xs transition">
                    <i class="fa-solid fa-phone"></i>
                    <span>${escapeHtml(p.phone)}</span>
                    <span class="text-[10px] text-teal-600 font-normal">(bấm gọi ngay)</span>
                </a>
            `;
        } else {
            phoneBox.innerHTML = '<span class="text-slate-400">Chưa lưu SĐT</span>';
        }

        document.getElementById('emr-address').textContent = p.address || 'Chưa cập nhật địa chỉ';
        document.getElementById('emr-history').textContent = p.medical_history || 'Không có tiền sử đặc biệt';

        // Totals
        const totals = data.totals || { billed: 0, paid: 0, debt: 0 };
        document.getElementById('emr-total-billed').textContent = formatVND(totals.billed);
        document.getElementById('emr-total-paid').textContent = formatVND(totals.paid);
        document.getElementById('emr-total-debt').textContent = formatVND(totals.debt);

        const btnCollect = document.getElementById('btn-collect-debt-fast');
        if (totals.debt > 0) {
            btnCollect.classList.remove('hidden');
        } else {
            btnCollect.classList.add('hidden');
        }

        // Render Treatments
        renderEmrTreatments(data.treatments);

        // Render Appointments
        renderEmrAppointments(data.appointments);

        // Show Modal
        document.getElementById('patient-detail-modal').classList.remove('hidden');
    } catch (e) {
        showToast(e.message, 'error');
    }
}

function closePatientDetail() {
    const modal = document.getElementById('patient-detail-modal');
    if (modal) modal.classList.add('hidden');
    state.currentPatient = null;
}

function renderEmrTreatments(treatments) {
    const container = document.getElementById('emr-treatments-list');
    document.getElementById('emr-treatments-count').textContent = treatments.length;

    if (!treatments || treatments.length === 0) {
        container.innerHTML = '<p class="text-xs text-slate-400 py-4 text-center">Chưa có lịch sử điều trị nào</p>';
        return;
    }

    container.innerHTML = treatments.map(t => {
        const debt = Math.max(t.cost - t.paid, 0);
        return `
            <div class="bg-white p-4 rounded-xl border border-slate-200 shadow-sm space-y-3">
                <div class="flex items-start justify-between gap-2">
                    <div>
                        <div class="flex items-center gap-2">
                            <span class="font-extrabold text-slate-800 text-sm">${escapeHtml(t.procedure)}</span>
                            <span class="px-2 py-0.5 rounded-full text-[10px] font-bold bg-teal-50 text-teal-700">${escapeHtml(t.category)}</span>
                            ${t.tooth ? `<span class="px-2 py-0.5 rounded-full text-[10px] font-bold bg-slate-100 text-slate-600">Răng: ${escapeHtml(t.tooth)}</span>` : ''}
                        </div>
                        <p class="text-[11px] text-slate-400 mt-1">
                            📅 Ngày khám: <b>${t.visit_date || 'Không ngày'}</b> • Cơ sở: ${escapeHtml(t.branch)} • Bác sĩ: <b>${escapeHtml(t.dentist || 'Thắng')}</b>
                        </p>
                    </div>

                    <div class="flex items-center gap-1">
                        <button onclick="openEditTreatmentModal(${t.id})" title="Sửa đợt điều trị" class="p-1.5 text-slate-400 hover:text-teal-600 hover:bg-slate-100 rounded-lg text-xs">
                            <i class="fa-solid fa-pen-to-square"></i>
                        </button>
                        <button onclick="deleteTreatmentRecord(${t.id})" title="Xóa đợt điều trị" class="p-1.5 text-slate-400 hover:text-rose-600 hover:bg-slate-100 rounded-lg text-xs">
                            <i class="fa-solid fa-trash"></i>
                        </button>
                    </div>
                </div>

                <!-- Financial Progress -->
                <div class="bg-slate-50 p-2.5 rounded-lg flex items-center justify-between text-xs">
                    <div>
                        <span class="text-slate-400 text-[10px] block">Tổng tiền ca này</span>
                        <span class="font-extrabold text-slate-800">${formatVND(t.cost)}</span>
                    </div>
                    <div>
                        <span class="text-slate-400 text-[10px] block">Đã thu</span>
                        <span class="font-extrabold text-emerald-600">${formatVND(t.paid)}</span>
                    </div>
                    <div>
                        <span class="text-slate-400 text-[10px] block">Còn nợ</span>
                        <span class="font-extrabold ${debt > 0 ? 'text-rose-600' : 'text-slate-400'}">${formatVND(debt)}</span>
                    </div>
                    <div>
                        ${debt > 0 ? `
                            <button onclick="openPayTreatmentModal(${t.id}, '${escapeHtml(t.procedure)}', ${debt})" class="px-2.5 py-1 bg-rose-600 hover:bg-rose-700 text-white font-bold rounded-lg text-[11px] transition">
                                Thu thêm
                            </button>
                        ` : '<span class="text-emerald-600 font-bold text-[11px]"><i class="fa-solid fa-check"></i> Đủ tiền</span>'}
                    </div>
                </div>

                <!-- Payments list inside treatment -->
                ${t.payments && t.payments.length > 0 ? `
                    <div class="pt-2 border-t border-slate-100">
                        <p class="text-[10px] font-bold uppercase text-slate-400 mb-1.5">Lịch sử các lần thu tiền:</p>
                        <div class="space-y-1">
                            ${t.payments.map(pay => `
                                <div class="flex items-center justify-between text-[11px] bg-slate-50/60 px-2 py-1 rounded">
                                    <span class="text-slate-600">
                                        Thu <b>${formatVND(pay.amount)}</b> (${pay.method}) vào ngày ${pay.paid_on || '--'}
                                        ${pay.note ? `• ${escapeHtml(pay.note)}` : ''}
                                    </span>
                                    <button onclick="deletePaymentRecord(${pay.id})" title="Xóa khoản thu này" class="text-slate-400 hover:text-rose-600 p-0.5">
                                        <i class="fa-solid fa-trash text-[10px]"></i>
                                    </button>
                                </div>
                            `).join('')}
                        </div>
                    </div>
                ` : ''}

                ${t.notes ? `<p class="text-xs text-slate-500 italic mt-1 bg-amber-50/60 p-2 rounded">Ghi chú: ${escapeHtml(t.notes)}</p>` : ''}
            </div>
        `;
    }).join('');
}

function renderEmrAppointments(appointments) {
    const container = document.getElementById('emr-appointments-list');
    document.getElementById('emr-appointments-count').textContent = appointments.length;

    if (!appointments || appointments.length === 0) {
        container.innerHTML = '<p class="text-xs text-slate-400 py-3 text-center">Chưa có lịch hẹn tái khám</p>';
        return;
    }

    container.innerHTML = appointments.map(a => `
        <div class="bg-white p-3 rounded-xl border border-slate-200 shadow-sm flex items-center justify-between text-xs">
            <div>
                <p class="font-bold text-slate-800">
                    📅 ${a.appt_date} ${a.appt_time ? `lúc ${a.appt_time}` : ''}
                    <span class="ml-1.5 px-2 py-0.5 rounded-full text-[10px] font-bold ${a.status === 'Đã đến' ? 'bg-emerald-100 text-emerald-800' : a.status === 'Chờ đến' ? 'bg-teal-100 text-teal-800' : 'bg-rose-100 text-rose-800'}">${a.status}</span>
                </p>
                <p class="text-slate-500 text-[11px] mt-0.5">${escapeHtml(a.reason || 'Tái khám')} • Cơ sở: ${a.branch}</p>
            </div>
            <div class="flex items-center gap-1.5">
                ${a.status === 'Chờ đến' ? `
                    <button onclick="quickUpdateApptStatus(${a.id}, 'Đã đến', true)" class="px-2 py-1 bg-emerald-50 text-emerald-700 hover:bg-emerald-100 font-bold rounded-lg text-[11px]">Đã đến</button>
                    <button onclick="quickUpdateApptStatus(${a.id}, 'Lỡ hẹn', true)" class="px-2 py-1 bg-slate-100 text-slate-600 hover:bg-slate-200 font-semibold rounded-lg text-[11px]">Lỡ hẹn</button>
                ` : ''}
            </div>
        </div>
    `).join('');
}

function editCurrentPatient() {
    if (!state.currentPatient) return;
    openPatientModal(state.currentPatient.patient);
}

function deleteCurrentPatient() {
    if (!state.currentPatient) return;
    const p = state.currentPatient.patient;
    const totals = state.currentPatient.totals;

    requestAdminPassword(async (password) => {
        try {
            await api(`/api/patients/${p.id}/delete`, {
                method: 'POST',
                body: { password }
            });
            showToast(`Đã xóa hoàn toàn hồ sơ ${p.name}`, 'success');
            closePatientDetail();
            await loadPatients(0);
        } catch (e) {
            showToast(e.message, 'error');
        }
    }, `Bạn đang xóa hồ sơ bệnh nhân "${p.name}" (${p.code}) gồm ${state.currentPatient.treatments.length} đợt điều trị, nợ ${formatVND(totals.debt)}.`);
}

// ==================== TREATMENT CRUD ====================
function openAddTreatmentModal() {
    if (!state.currentPatient) return;
    const modal = document.getElementById('treatment-modal');
    const form = document.getElementById('treatment-form');
    form.reset();

    document.getElementById('treatment-modal-title').textContent = `Thêm Đợt Điều Trị: ${state.currentPatient.patient.name}`;
    document.getElementById('tm-id').value = '';
    document.getElementById('tm-patient-id').value = state.currentPatient.patient.id;
    document.getElementById('tm-date').value = new Date().toISOString().split('T')[0];
    document.getElementById('tm-branch').value = state.currentPatient.patient.branch;
    document.getElementById('tm-category').value = 'Tổng quát';
    const tmDent = document.getElementById('tm-dentist');
    if (tmDent && tmDent.options.length > 0) tmDent.selectedIndex = 0;
    document.getElementById('tm-cost').value = '0';
    document.getElementById('tm-paid').value = '0';
    updateCategoryRecallHint('Tổng quát', 'tm-t-hint');

    document.getElementById('tm-pay-section').classList.remove('hidden');
    document.getElementById('tm-edit-reason-section').classList.add('hidden');

    modal.classList.remove('hidden');
    document.getElementById('tm-procedure').focus();
}

function openEditTreatmentModal(tid) {
    if (!state.currentPatient) return;
    const t = state.currentPatient.treatments.find(x => x.id === tid);
    if (!t) return;

    const modal = document.getElementById('treatment-modal');
    document.getElementById('treatment-modal-title').textContent = `Sửa Đợt Điều Trị: ${t.procedure}`;
    document.getElementById('tm-id').value = t.id;
    document.getElementById('tm-patient-id').value = t.patient_id;
    document.getElementById('tm-date').value = t.visit_date || '';
    document.getElementById('tm-branch').value = t.branch;
    document.getElementById('tm-category').value = t.category;
    const tmDentEl = document.getElementById('tm-dentist');
    if (tmDentEl) {
        if (t.dentist && ![...tmDentEl.options].some(o => o.value === t.dentist)) {
            const opt = document.createElement('option');
            opt.value = t.dentist;
            opt.textContent = t.dentist.startsWith('BS.') ? t.dentist : 'BS. ' + t.dentist;
            tmDentEl.appendChild(opt);
        }
        tmDentEl.value = t.dentist || (tmDentEl.options[0]?.value || 'Thắng');
    }
    document.getElementById('tm-procedure').value = t.procedure;
    document.getElementById('tm-tooth').value = t.tooth || '';
    document.getElementById('tm-cost').value = new Intl.NumberFormat('vi-VN').format(Math.round(t.cost / 1000));
    document.getElementById('tm-notes').value = t.notes || '';

    // In edit mode: hide initial pay section, show edit reason
    document.getElementById('tm-pay-section').classList.add('hidden');
    document.getElementById('tm-edit-reason-section').classList.remove('hidden');
    document.getElementById('tm-reason').value = '';

    modal.classList.remove('hidden');
}

function closeTreatmentModal() {
    const modal = document.getElementById('treatment-modal');
    if (modal) modal.classList.add('hidden');
}

async function submitTreatmentForm() {
    const tid = document.getElementById('tm-id').value;
    const pid = document.getElementById('tm-patient-id').value;
    const proc = document.getElementById('tm-procedure').value.trim();
    if (!proc) {
        showToast('Vui lòng nhập nội dung thủ thuật điều trị', 'error');
        document.getElementById('tm-procedure').focus();
        return;
    }

    const cost = parseVND(document.getElementById('tm-cost').value);
    const isEdit = Boolean(tid);

    if (isEdit) {
        const payload = {
            visit_date: document.getElementById('tm-date').value || null,
            branch: document.getElementById('tm-branch').value,
            category: document.getElementById('tm-category').value,
            dentist: document.getElementById('tm-dentist').value.trim() || 'Thắng',
            procedure: proc,
            tooth: document.getElementById('tm-tooth').value.trim(),
            cost: cost,
            notes: document.getElementById('tm-notes').value.trim(),
            reason: document.getElementById('tm-reason').value.trim()
        };

        requestPin(async (pin) => {
            try {
                payload.pin = pin;
                await api(`/api/treatments/${tid}`, { method: 'PUT', body: payload });
                showToast('Đã sửa đợt điều trị thành công', 'success');
                closeTreatmentModal();
                await openPatientDetail(pid);
            } catch (e) {
                showToast(e.message, 'error');
            }
        }, 'Nhập PIN nhân viên (0000) để lưu thay đổi đợt điều trị:');
    } else {
        const paid = parseVND(document.getElementById('tm-paid').value);
        if (paid > cost) {
            showToast('Số tiền thanh toán không được lớn hơn tổng tiền', 'error');
            return;
        }

        const payload = {
            visit_date: document.getElementById('tm-date').value || null,
            branch: document.getElementById('tm-branch').value,
            category: document.getElementById('tm-category').value,
            dentist: document.getElementById('tm-dentist').value.trim() || 'Thắng',
            procedure: proc,
            tooth: document.getElementById('tm-tooth').value.trim(),
            cost: cost,
            notes: document.getElementById('tm-notes').value.trim(),
            paid: paid,
            method: document.getElementById('tm-method').value,
            next_date: document.getElementById('tm-next-date').value || null
        };

        try {
            await api(`/api/patients/${pid}/treatments`, { method: 'POST', body: payload });
            showToast('Đã thêm đợt điều trị mới', 'success');
            closeTreatmentModal();
            await openPatientDetail(pid);
        } catch (e) {
            showToast(e.message, 'error');
        }
    }
}

function deleteTreatmentRecord(tid) {
    requestPin(async (pin) => {
        try {
            await api(`/api/treatments/${tid}/delete`, {
                method: 'POST',
                body: { pin, reason: 'Người dùng yêu cầu xóa' }
            });
            showToast('Đã xóa đợt điều trị', 'success');
            if (state.currentPatient) {
                await openPatientDetail(state.currentPatient.patient.id);
            }
        } catch (e) {
            showToast(e.message, 'error');
        }
    }, 'Nhập PIN nhân viên (0000) để xóa đợt điều trị này:');
}

function deletePaymentRecord(paymentId) {
    requestPin(async (pin) => {
        try {
            await api(`/api/payments/${paymentId}/delete`, {
                method: 'POST',
                body: { pin, reason: 'Xóa khoản thu' }
            });
            showToast('Đã xóa khoản thu', 'success');
            if (state.currentPatient) {
                await openPatientDetail(state.currentPatient.patient.id);
            }
        } catch (e) {
            showToast(e.message, 'error');
        }
    }, 'Nhập PIN nhân viên (0000) để xóa khoản thu này:');
}

// ==================== DEBT RECOVERY & COLLECT ====================
async function loadDebts() {
    const q = (document.getElementById('debt-search-input')?.value || '').trim();
    const sort = document.getElementById('debt-sort-select')?.value || 'debt';

    try {
        const url = `/api/debts?q=${encodeURIComponent(q)}&branch=${encodeURIComponent(state.branchFilter)}&sort=${sort}&limit=100`;
        const data = await api(url);

        state.debts.items = data.items;
        state.debts.total = data.total;
        state.debts.debtTotal = data.totals.debt;

        document.getElementById('debt-banner-total').textContent = formatVND(data.totals.debt);
        document.getElementById('debt-banner-sub').textContent = `Tổng cộng ${data.totals.patients} bệnh nhân chưa thanh toán hết`;

        // Update badges
        const badgeDebt = document.getElementById('badge-debt-count');
        const mobileBadgeDebt = document.getElementById('mobile-badge-debt');
        const drawerBadgeDebt = document.getElementById('badge-debt-count-drawer');
        const pCount = (data.totals && data.totals.patients) || 0;
        if (pCount > 0) {
            if (badgeDebt) { badgeDebt.textContent = pCount; badgeDebt.classList.remove('hidden'); }
            if (mobileBadgeDebt) mobileBadgeDebt.classList.remove('hidden');
            if (drawerBadgeDebt) { drawerBadgeDebt.textContent = pCount; drawerBadgeDebt.classList.remove('hidden'); }
        } else {
            if (badgeDebt) badgeDebt.classList.add('hidden');
            if (mobileBadgeDebt) mobileBadgeDebt.classList.add('hidden');
            if (drawerBadgeDebt) drawerBadgeDebt.classList.add('hidden');
        }

        const container = document.getElementById('debts-list-container');
        if (data.items.length === 0) {
            container.innerHTML = '<div class="p-8 text-center text-slate-400 bg-white rounded-2xl border border-slate-200">Không có công nợ nào! 🎉</div>';
            return;
        }

        container.innerHTML = data.items.map(g => `
            <div class="bg-white p-4 rounded-2xl border border-slate-200 shadow-sm flex flex-col md:flex-row items-start md:items-center justify-between gap-3 hover:border-teal-300 transition">
                <div class="min-w-0 flex-1">
                    <div class="flex items-center gap-2">
                        <span class="font-extrabold text-slate-800 text-sm hover:text-teal-600 cursor-pointer" onclick="openPatientDetail(${g.patient_id})">${escapeHtml(g.name)}</span>
                        <span class="text-[10px] text-teal-600 font-bold bg-teal-50 px-2 py-0.5 rounded-full">${g.code}</span>
                        <span class="text-[10px] text-slate-500 bg-slate-100 px-2 py-0.5 rounded-full font-semibold">${g.branch}</span>
                    </div>
                    <div class="text-xs text-slate-500 mt-1 flex flex-wrap items-center gap-3">
                        ${g.phone ? `
                            <a href="tel:${g.phone}" class="text-teal-600 font-bold hover:underline flex items-center gap-1">
                                <i class="fa-solid fa-phone text-[10px]"></i>
                                <span>${escapeHtml(g.phone)}</span>
                                ${g.phone_note ? `<span class="text-slate-400 font-normal">(${escapeHtml(g.phone_note)})</span>` : ''}
                            </a>
                        ` : '<span class="text-slate-400">Không có SĐT</span>'}
                        ${g.oldest ? `<span class="text-rose-600 font-medium">📅 Nợ từ ngày: <b>${g.oldest}</b></span>` : ''}
                        <span>${g.treatments.length} ca điều trị còn nợ</span>
                    </div>
                </div>

                <div class="flex items-center justify-between w-full md:w-auto gap-4 pt-2 md:pt-0 border-t md:border-t-0 border-slate-100">
                    <div class="text-right">
                        <span class="text-[10px] uppercase font-bold text-slate-400 block">Số tiền còn nợ</span>
                        <span class="text-base font-extrabold text-rose-600">${formatVND(g.debt)}</span>
                    </div>
                    <button onclick="openCollectModalFor(${g.patient_id}, '${escapeHtml(g.name)}', ${g.debt})" class="px-4 py-2 bg-rose-600 hover:bg-rose-700 text-white font-bold rounded-xl text-xs shadow-md shadow-rose-600/20 transition flex items-center gap-1.5 flex-shrink-0">
                        <i class="fa-solid fa-hand-holding-dollar"></i>
                        <span>Thu nợ</span>
                    </button>
                </div>
            </div>
        `).join('');
    } catch (e) {
        showToast(e.message, 'error');
    }
}

function debounceSearchDebts() {
    clearTimeout(state.searchDebounce);
    state.searchDebounce = setTimeout(() => {
        loadDebts();
    }, 280);
}

function openCollectModal() {
    if (!state.currentPatient) return;
    const p = state.currentPatient.patient;
    const debt = state.currentPatient.totals.debt;
    openCollectModalFor(p.id, p.name, debt);
}

function openCollectModalFor(pid, name, debt) {
    const modal = document.getElementById('collect-modal');
    document.getElementById('col-patient-id').value = pid;
    document.getElementById('col-patient-name').textContent = name;
    document.getElementById('col-patient-debt').textContent = formatVND(debt);
    const debtK = Math.round(debt / 1000);
    document.getElementById('col-amount').value = new Intl.NumberFormat('vi-VN').format(debtK);
    document.getElementById('col-note').value = 'Thu nợ điều trị';
    modal.classList.remove('hidden');
    document.getElementById('col-amount').focus();
}

function closeCollectModal() {
    const modal = document.getElementById('collect-modal');
    if (modal) modal.classList.add('hidden');
}

function setQuickCollect(val) {
    if (val === 'all') {
        const debtK = parseK(document.getElementById('col-patient-debt').textContent);
        document.getElementById('col-amount').value = new Intl.NumberFormat('vi-VN').format(debtK);
    } else {
        const curK = parseK(document.getElementById('col-amount').value);
        document.getElementById('col-amount').value = new Intl.NumberFormat('vi-VN').format(curK + val);
    }
}

async function submitCollectDebt() {
    const pid = document.getElementById('col-patient-id').value;
    const amount = parseVND(document.getElementById('col-amount').value);
    const method = document.getElementById('col-method').value;
    const note = document.getElementById('col-note').value.trim();

    if (amount <= 0) {
        showToast('Vui lòng nhập số tiền hợp lệ lớn hơn 0', 'error');
        return;
    }

    try {
        const res = await api(`/api/patients/${pid}/collect`, {
            method: 'POST',
            body: { amount, method, note }
        });
        showToast(`Đã thu nợ ${formatVND(amount)} thành công (còn nợ: ${formatVND(res.remaining_debt)})`, 'success');
        closeCollectModal();
        if (state.currentPatient && state.currentPatient.patient.id == pid) {
            await openPatientDetail(pid);
        }
        await loadDebts();
        await loadDashboard();
    } catch (e) {
        showToast(e.message, 'error');
    }
}

function openPayTreatmentModal(tid, procName, remainingDebt) {
    const debtK = Math.round(remainingDebt / 1000);
    const amountStr = prompt(`Thu tiền cho thủ thuật: "${procName}"\nSố tiền còn nợ: ${formatVND(remainingDebt)}\n\nNhập số tiền thu (tính theo đơn vị K, ví dụ: ${debtK} = ${formatVND(remainingDebt)}):`, debtK);
    if (!amountStr) return;
    const amount = parseVND(amountStr);
    if (amount <= 0) return;

    api(`/api/treatments/${tid}/payments`, {
        method: 'POST',
        body: { amount, method: 'Tiền mặt', note: 'Thu trực tiếp' }
    }).then(async () => {
        showToast(`Đã thu ${formatVND(amount)}`, 'success');
        if (state.currentPatient) {
            await openPatientDetail(state.currentPatient.patient.id);
        }
    }).catch(e => showToast(e.message, 'error'));
}

// Clinical Hint Helper for Dental Recall Rules
function updateCategoryRecallHint(cat, targetId) {
    const el = document.getElementById(targetId);
    if (!el) return;
    if (cat === 'Răng giả') {
        el.innerHTML = '<i class="fa-solid fa-wand-magic-sparkles text-amber-500 mr-1.5"></i><b>Quy tắc chuyên khoa Răng giả:</b> Hệ thống tự động tạo <b>2 lịch hẹn tái khám</b>: lần 1 sau <b>6 tháng</b> và tái khám định kỳ <b>1 năm/lần</b>.';
        el.classList.remove('hidden');
    } else if (cat === 'Chỉnh nha') {
        el.innerHTML = '<i class="fa-solid fa-wand-magic-sparkles text-teal-600 mr-1.5"></i><b>Quy tắc chuyên khoa Chỉnh nha:</b> Hệ thống tự động tạo lịch hẹn tái khám <b>1 tháng/lần</b> định kỳ liên tục đến khi kết thúc chỉnh nha.';
        el.classList.remove('hidden');
    } else {
        el.classList.add('hidden');
    }
}

// ==================== APPOINTMENTS TAB ====================
let currentApptFilter = 'month';

async function filterAppointments(when) {
    currentApptFilter = when || 'month';
    document.querySelectorAll('.appt-filter-btn').forEach(btn => {
        if (btn.getAttribute('data-appt-filter') === currentApptFilter) {
            btn.className = 'appt-filter-btn px-3 py-1.5 rounded-xl text-xs font-bold bg-teal-600 text-white shadow-sm transition flex items-center gap-1';
        } else {
            btn.className = 'appt-filter-btn px-3 py-1.5 rounded-xl text-xs font-bold text-slate-600 hover:bg-slate-100 transition flex items-center gap-1';
        }
    });

    try {
        const url = `/api/appointments?when=${currentApptFilter}&branch=${encodeURIComponent(state.branchFilter)}&limit=150`;
        const data = await api(url);

        // Update counts
        if (data.counts) {
            const cntMonth = document.getElementById('appt-count-month');
            if (cntMonth) cntMonth.textContent = data.counts.month || 0;
            const cntToday = document.getElementById('appt-count-today');
            if (cntToday) cntToday.textContent = data.counts.today || 0;
            const cntUpcoming = document.getElementById('appt-count-upcoming');
            if (cntUpcoming) cntUpcoming.textContent = data.counts.upcoming || 0;
            const cntOverdue = document.getElementById('appt-count-overdue');
            if (cntOverdue) cntOverdue.textContent = data.counts.overdue || 0;

            const badgeSide = document.getElementById('badge-appt-today');
            const badgeMob = document.getElementById('mobile-badge-appt');
            const badgeDrawer = document.getElementById('badge-appt-today-drawer');
            const totalPending = (data.counts.month !== undefined) ? data.counts.month : (data.counts.today || 0);
            if (totalPending > 0) {
                if (badgeSide) { badgeSide.textContent = totalPending; badgeSide.classList.remove('hidden'); }
                if (badgeMob) badgeMob.classList.remove('hidden');
                if (badgeDrawer) { badgeDrawer.textContent = totalPending; badgeDrawer.classList.remove('hidden'); }
            } else {
                if (badgeSide) badgeSide.classList.add('hidden');
                if (badgeMob) badgeMob.classList.add('hidden');
                if (badgeDrawer) badgeDrawer.classList.add('hidden');
            }
        }

        const container = document.getElementById('appointments-list-container');
        if (data.items.length === 0) {
            container.innerHTML = '<div class="p-8 text-center text-slate-400 bg-white rounded-2xl border border-slate-200">Không có lịch hẹn nào trong mục này</div>';
            return;
        }

        container.innerHTML = data.items.map(a => `
            <div class="bg-white p-4 rounded-2xl border border-slate-200 shadow-sm flex flex-col md:flex-row items-start md:items-center justify-between gap-3">
                <div class="min-w-0 flex-1">
                    <div class="flex items-center gap-2">
                        <span class="font-bold text-slate-800 text-sm hover:text-teal-600 cursor-pointer" onclick="openPatientDetail(${a.patient_id})">${escapeHtml(a.patient_name)}</span>
                        <span class="text-[10px] font-bold px-2 py-0.5 rounded-full ${a.kind === 'tai_kham' ? 'bg-teal-50 text-teal-700' : 'bg-blue-50 text-blue-700'}">${a.kind === 'tai_kham' ? 'Tái khám' : 'Hẹn khám'}</span>
                        <span class="text-[10px] font-semibold bg-slate-100 px-2 py-0.5 rounded-full">${a.branch}</span>
                    </div>
                    <p class="text-xs text-slate-600 mt-1">
                        📅 <b>${a.appt_date}</b> ${a.appt_time ? `• Giờ: <b>${a.appt_time}</b>` : ''} • Nội dung: ${escapeHtml(a.reason || 'Khám điều trị')}
                    </p>
                    <div class="text-[11px] text-slate-400 mt-0.5">
                        ${a.patient_phone ? `<a href="tel:${a.patient_phone}" class="text-teal-600 hover:underline font-semibold"><i class="fa-solid fa-phone text-[10px] mr-0.5"></i>${a.patient_phone}</a>` : 'Không có SĐT'}
                    </div>
                </div>

                <div class="flex items-center gap-1.5 flex-shrink-0">
                    <span class="text-xs font-bold px-2.5 py-1 rounded-xl ${a.status === 'Đã đến' ? 'bg-emerald-50 text-emerald-700' : a.status === 'Chờ đến' ? 'bg-amber-50 text-amber-700' : 'bg-slate-100 text-slate-600'}">
                        ${a.status}
                    </span>
                    ${a.status === 'Chờ đến' ? `
                        <button onclick="quickUpdateApptStatus(${a.id}, 'Đã đến')" title="Đánh dấu đã đến khám" class="p-2 bg-emerald-50 text-emerald-700 hover:bg-emerald-100 rounded-xl text-xs font-bold transition">
                            <i class="fa-solid fa-check"></i>
                        </button>
                        <button onclick="quickUpdateApptStatus(${a.id}, 'Lỡ hẹn')" title="Đánh dấu lỡ hẹn" class="p-2 bg-slate-100 text-slate-600 hover:bg-slate-200 rounded-xl text-xs font-semibold transition">
                            <i class="fa-solid fa-user-xmark"></i>
                        </button>
                    ` : ''}
                    <button onclick="deleteAppointmentRecord(${a.id})" title="Xóa lịch hẹn" class="p-2 text-slate-400 hover:text-rose-600 rounded-xl text-xs transition">
                        <i class="fa-solid fa-trash"></i>
                    </button>
                </div>
            </div>
        `).join('');
    } catch (e) {
        showToast(e.message, 'error');
    }
}

async function quickUpdateApptStatus(aid, status, refreshEmr = false) {
    try {
        await api(`/api/appointments/${aid}`, {
            method: 'PUT',
            body: { status }
        });
        showToast(`Đã chuyển lịch hẹn sang: ${status}`, 'success');
        if (refreshEmr && state.currentPatient) {
            await openPatientDetail(state.currentPatient.patient.id);
        }
        await filterAppointments(currentApptFilter);
    } catch (e) {
        showToast(e.message, 'error');
    }
}

async function deleteAppointmentRecord(aid) {
    if (!confirm('Bạn có chắc muốn xóa lịch hẹn này?')) return;
    try {
        await api(`/api/appointments/${aid}`, { method: 'DELETE' });
        showToast('Đã xóa lịch hẹn', 'success');
        await filterAppointments(currentApptFilter);
    } catch (e) {
        showToast(e.message, 'error');
    }
}

function openNewAppointmentModal() {
    const modal = document.getElementById('appointment-modal');
    document.getElementById('appointment-form').reset();
    document.getElementById('apptm-patient-id').value = '';
    document.getElementById('apptm-patient-chosen').textContent = 'Chưa chọn';
    document.getElementById('apptm-patient-select-box').classList.remove('hidden');
    document.getElementById('apptm-date').value = new Date().toISOString().split('T')[0];
    document.getElementById('apptm-branch').value = state.branchFilter || 'Ninh Vân';
    modal.classList.remove('hidden');
}

function openPatientAppointmentModal() {
    if (!state.currentPatient) return;
    const p = state.currentPatient.patient;
    const modal = document.getElementById('appointment-modal');
    document.getElementById('appointment-form').reset();
    document.getElementById('apptm-patient-id').value = p.id;
    document.getElementById('apptm-patient-chosen').textContent = `${p.name} (${p.code})`;
    document.getElementById('apptm-patient-select-box').classList.add('hidden');
    document.getElementById('apptm-date').value = new Date().toISOString().split('T')[0];
    document.getElementById('apptm-branch').value = p.branch;
    modal.classList.remove('hidden');
}

function closeAppointmentModal() {
    const modal = document.getElementById('appointment-modal');
    if (modal) modal.classList.add('hidden');
}

let apptPatientSearchTimeout = null;
function searchAppointmentPatient(q) {
    clearTimeout(apptPatientSearchTimeout);
    const box = document.getElementById('apptm-patient-results');
    if (!q.trim()) {
        box.classList.add('hidden');
        return;
    }
    apptPatientSearchTimeout = setTimeout(async () => {
        try {
            const data = await api(`/api/patients?q=${encodeURIComponent(q)}&limit=8`);
            if (data.items.length === 0) {
                box.innerHTML = '<p class="p-2 text-slate-400 text-center">Không tìm thấy</p>';
            } else {
                box.innerHTML = data.items.map(p => `
                    <div class="p-2 hover:bg-slate-100 cursor-pointer flex items-center justify-between" onclick="selectApptPatient(${p.id}, '${escapeHtml(p.name)}', '${p.code}')">
                        <span class="font-bold text-slate-800">${escapeHtml(p.name)}</span>
                        <span class="text-teal-600 font-semibold">${p.code} • ${p.phone || ''}</span>
                    </div>
                `).join('');
            }
            box.classList.remove('hidden');
        } catch (_) {}
    }, 250);
}

function selectApptPatient(id, name, code) {
    document.getElementById('apptm-patient-id').value = id;
    document.getElementById('apptm-patient-chosen').textContent = `${name} (${code})`;
    document.getElementById('apptm-patient-results').classList.add('hidden');
    document.getElementById('apptm-patient-search').value = '';
}

async function submitAppointmentForm() {
    const pid = document.getElementById('apptm-patient-id').value;
    const date = document.getElementById('apptm-date').value;
    if (!pid) {
        showToast('Vui lòng chọn bệnh nhân', 'error');
        return;
    }
    if (!date) {
        showToast('Vui lòng chọn ngày hẹn', 'error');
        return;
    }

    const payload = {
        patient_id: parseInt(pid, 10),
        kind: document.getElementById('apptm-kind').value,
        appt_date: date,
        appt_time: document.getElementById('apptm-time').value || '',
        branch: document.getElementById('apptm-branch').value,
        reason: document.getElementById('apptm-reason').value.trim()
    };

    try {
        await api('/api/appointments', { method: 'POST', body: payload });
        showToast('Đã lưu lịch hẹn thành công', 'success');
        closeAppointmentModal();
        if (state.currentPatient && state.currentPatient.patient.id == pid) {
            await openPatientDetail(pid);
        }
        await filterAppointments(currentApptFilter);
    } catch (e) {
        showToast(e.message, 'error');
    }
}

// ==================== REPORTS TAB (ADMIN ONLY) ====================
async function loadReports() {
    if (state.user.role !== 'admin') return;

    const yearSelect = document.getElementById('report-year-select');
    const selectedYear = parseInt(yearSelect.value, 10) || new Date().getFullYear();

    try {
        const url = `/api/admin/reports?year=${selectedYear}&branch=${encodeURIComponent(state.branchFilter)}`;
        const data = await api(url);

        // Populate Year Options if not populated
        if (yearSelect.options.length <= 1 && data.years) {
            yearSelect.innerHTML = data.years.map(y => `<option value="${y}" ${y === selectedYear ? 'selected' : ''}>Năm ${y}</option>`).join('');
        }

        // Summary
        const totalCollected = data.months.reduce((acc, m) => acc + m.collected, 0);
        const totalBilled = data.months.reduce((acc, m) => acc + m.billed, 0);
        const totalVisits = data.months.reduce((acc, m) => acc + m.visits, 0);

        document.getElementById('report-year-collected').textContent = formatVND(totalCollected);
        document.getElementById('report-year-billed').textContent = formatVND(totalBilled);
        document.getElementById('report-year-visits').textContent = totalVisits;

        // Months Table
        const tbody = document.getElementById('report-months-body');
        tbody.innerHTML = data.months.map(m => {
            const diff = m.billed - m.collected;
            return `
                <tr class="hover:bg-slate-50 transition">
                    <td class="py-3 px-4 font-bold text-slate-800">Tháng ${m.month.split('-')[1]}</td>
                    <td class="py-3 px-4 text-right font-medium">${m.visits}</td>
                    <td class="py-3 px-4 text-right font-semibold text-slate-700">${formatVND(m.billed)}</td>
                    <td class="py-3 px-4 text-right font-extrabold text-emerald-600">${formatVND(m.collected)}</td>
                    <td class="py-3 px-4 text-right text-xs ${diff > 0 ? 'text-rose-600 font-bold' : 'text-slate-400'}">
                        ${diff > 0 ? `+${formatVND(diff)}` : '0K'}
                    </td>
                </tr>
            `;
        }).join('');

        // Breakdown by Categories
        const catBox = document.getElementById('report-categories-list');
        const maxCat = Math.max(...data.categories.map(c => c.billed), 1);
        catBox.innerHTML = data.categories.map(c => `
            <div>
                <div class="flex items-center justify-between text-xs font-semibold mb-1">
                    <span class="text-slate-700">${c.name} (${c.visits} ca)</span>
                    <span class="text-teal-700 font-bold">${formatVND(c.billed)}</span>
                </div>
                <div class="w-full bg-slate-100 h-2 rounded-full overflow-hidden">
                    <div style="width: ${Math.round((c.billed / maxCat) * 100)}%" class="bg-teal-500 h-full rounded-full"></div>
                </div>
            </div>
        `).join('');

        // Breakdown by Branches
        const brBox = document.getElementById('report-branches-list');
        const maxBr = Math.max(...data.branches.map(b => b.billed), 1);
        brBox.innerHTML = data.branches.map(b => `
            <div>
                <div class="flex items-center justify-between text-xs font-semibold mb-1">
                    <span class="text-slate-700">${b.name} (${b.visits} ca)</span>
                    <span class="text-teal-700 font-bold">${formatVND(b.billed)}</span>
                </div>
                <div class="w-full bg-slate-100 h-2 rounded-full overflow-hidden">
                    <div style="width: ${Math.round((b.billed / maxBr) * 100)}%" class="bg-teal-600 h-full rounded-full"></div>
                </div>
            </div>
        `).join('');
    } catch (e) {
        showToast(e.message, 'error');
    }
}

// ==================== AUDIT TAB (ADMIN ONLY) ====================
async function loadAuditLogs() {
    if (state.user.role !== 'admin') return;

    try {
        const data = await api('/api/admin/audit?scope=all&limit=60');
        const container = document.getElementById('audit-logs-container');

        if (data.items.length === 0) {
            container.innerHTML = '<div class="p-8 text-center text-slate-400 bg-white rounded-2xl border border-slate-200">Chưa có nhật ký nào</div>';
            return;
        }

        container.innerHTML = data.items.map(l => {
            const isDelete = l.action.includes('delete');
            const isEdit = l.action.includes('edit');
            return `
                <div class="bg-white p-4 rounded-xl border border-slate-200 shadow-sm text-xs space-y-1.5">
                    <div class="flex items-center justify-between">
                        <div class="flex items-center gap-2">
                            <span class="font-bold text-slate-800">${escapeHtml(l.full_name || l.username)}</span>
                            <span class="px-2 py-0.5 rounded-full text-[10px] font-bold ${isDelete ? 'bg-rose-100 text-rose-800' : isEdit ? 'bg-amber-100 text-amber-800' : 'bg-teal-100 text-teal-800'}">
                                ${l.action}
                            </span>
                        </div>
                        <span class="text-[11px] text-slate-400">${l.created_at}</span>
                    </div>
                    <p class="text-slate-700 font-medium">${escapeHtml(l.summary || '')}</p>
                    ${l.changes && l.changes.length > 0 ? `
                        <div class="bg-slate-50 p-2 rounded border border-slate-200 text-[11px] space-y-1">
                            ${l.changes.map(c => `
                                <div>
                                    <span class="font-semibold text-slate-600">${c.label || c.field}:</span>
                                    <span class="line-through text-rose-500 mr-1">${c.money ? formatVND(c.old) : (c.old || '(trống)')}</span>
                                    → <span class="font-bold text-emerald-600">${c.money ? formatVND(c.new) : (c.new || '(trống)')}</span>
                                </div>
                            `).join('')}
                        </div>
                    ` : ''}
                </div>
            `;
        }).join('');
    } catch (e) {
        showToast(e.message, 'error');
    }
}

// ==================== CSV EXPORTS ====================
function exportDebtsCsv() {
    const t = state.token ? `&token=${encodeURIComponent(state.token)}` : '';
    window.location.href = `/api/export/debts.csv?branch=${encodeURIComponent(state.branchFilter)}${t}`;
}

function exportPatientsCsv() {
    const t = state.token ? `?token=${encodeURIComponent(state.token)}` : '';
    window.location.href = `/api/export/patients.csv${t}`;
}

// ==================== PIN & PASSWORD HELPERS ====================
function requestPin(callback, promptText = 'Nhập mã PIN nhân viên (0000):') {
    // If admin is logged in, no PIN check needed
    if (state.user && state.user.role === 'admin') {
        callback(null);
        return;
    }
    const modal = document.getElementById('pin-modal');
    document.getElementById('pin-modal-prompt').textContent = promptText;
    const input = document.getElementById('pin-input');
    input.value = '';
    state.pinPromptCallback = callback;
    modal.classList.remove('hidden');
    input.focus();
}

function confirmPinPrompt() {
    const input = document.getElementById('pin-input');
    const pin = input.value.trim();
    if (!pin) {
        showToast('Vui lòng nhập mã PIN', 'error');
        return;
    }
    const modal = document.getElementById('pin-modal');
    modal.classList.add('hidden');
    if (state.pinPromptCallback) {
        state.pinPromptCallback(pin);
        state.pinPromptCallback = null;
    }
}

function cancelPinPrompt() {
    const modal = document.getElementById('pin-modal');
    modal.classList.add('hidden');
    state.pinPromptCallback = null;
}

function requestAdminPassword(callback, promptText = 'Nhập mật khẩu Admin để tiếp tục:') {
    const modal = document.getElementById('admin-pw-modal');
    document.getElementById('admin-pw-prompt').textContent = promptText;
    const input = document.getElementById('admin-pw-input');
    input.value = '';
    state.adminPwCallback = callback;
    modal.classList.remove('hidden');
    input.focus();
}

function confirmAdminPwPrompt() {
    const input = document.getElementById('admin-pw-input');
    const pw = input.value;
    if (!pw) {
        showToast('Vui lòng nhập mật khẩu admin', 'error');
        return;
    }
    const modal = document.getElementById('admin-pw-modal');
    modal.classList.add('hidden');
    if (state.adminPwCallback) {
        state.adminPwCallback(pw);
        state.adminPwCallback = null;
    }
}

function cancelAdminPwPrompt() {
    const modal = document.getElementById('admin-pw-modal');
    modal.classList.add('hidden');
    state.adminPwCallback = null;
}

// ==================== SETTINGS MODALS ====================
function openChangePasswordModal() {
    document.getElementById('change-pw-modal').classList.remove('hidden');
    document.getElementById('cpw-old').value = '';
    document.getElementById('cpw-new').value = '';
}

function closeChangePasswordModal() {
    document.getElementById('change-pw-modal').classList.add('hidden');
}

async function submitChangePassword() {
    const oldP = document.getElementById('cpw-old').value;
    const newP = document.getElementById('cpw-new').value;
    if (!oldP || !newP) {
        showToast('Vui lòng nhập đủ thông tin', 'error');
        return;
    }
    try {
        await api('/api/auth/change-password', {
            method: 'POST',
            body: { old_password: oldP, new_password: newP }
        });
        showToast('Đổi mật khẩu thành công', 'success');
        closeChangePasswordModal();
    } catch (e) {
        showToast(e.message, 'error');
    }
}

function openPinAdminModal() {
    document.getElementById('admin-pin-modal').classList.remove('hidden');
    document.getElementById('cpin-pw').value = '';
    document.getElementById('cpin-new').value = '';
}

function closePinAdminModal() {
    document.getElementById('admin-pin-modal').classList.add('hidden');
}

async function submitAdminPin() {
    const pw = document.getElementById('cpin-pw').value;
    const pin = document.getElementById('cpin-new').value.trim();
    if (!pw || !/^\d{4}$/.test(pin)) {
        showToast('Vui lòng nhập mật khẩu admin và PIN đúng 4 số', 'error');
        return;
    }
    try {
        await api('/api/admin/pin', {
            method: 'POST',
            body: { password: pw, new_pin: pin }
        });
        showToast(`Đã đổi mã PIN nhân viên thành công thành: ${pin}`, 'success');
        closePinAdminModal();
    } catch (e) {
        showToast(e.message, 'error');
    }
}

// ==================== DEDICATED ADMIN HUB ====================
let currentAdminSubTab = 'reports';

async function loadAdminHub() {
    if (!state.user || state.user.role !== 'admin') return;
    await switchAdminSubTab(currentAdminSubTab);
}

async function switchAdminSubTab(sub) {
    currentAdminSubTab = sub;

    // Update button styles
    document.querySelectorAll('.admin-sub-btn').forEach(btn => {
        if (btn.getAttribute('data-admin-sub') === sub) {
            btn.className = 'admin-sub-btn px-3 py-1.5 rounded-lg bg-amber-500 text-slate-950 font-bold transition shadow';
        } else {
            btn.className = 'admin-sub-btn px-3 py-1.5 rounded-lg text-slate-300 hover:text-white transition';
        }
    });

    // Toggle sub-views
    ['reports', 'users', 'settings', 'backup'].forEach(s => {
        const view = document.getElementById(`admin-sub-view-${s}`);
        if (view) {
            if (s === sub) view.classList.remove('hidden');
            else view.classList.add('hidden');
        }
    });

    if (sub === 'reports') await loadReports();
    else if (sub === 'users') await loadUsers();
    else if (sub === 'settings') await loadClinicSettings();
    else if (sub === 'backup') await loadAuditLogs();
}

// ───────── Admin Sub-Tab: Users Management ─────────
async function loadUsers() {
    try {
        const users = await api('/api/admin/users');
        const container = document.getElementById('admin-users-list');
        if (!container) return;

        if (users.length === 0) {
            container.innerHTML = '<p class="text-xs text-slate-400 p-4">Chưa có người dùng nào</p>';
            return;
        }

        container.innerHTML = users.map(u => {
            const isAdmin = u.role === 'admin';
            const isActive = Boolean(u.active);
            const isSelf = state.user && state.user.id === u.id;
            return `
                <div class="bg-white p-4 rounded-xl border ${isAdmin ? 'border-amber-300 bg-amber-50/20' : 'border-slate-200'} shadow-sm flex items-start justify-between gap-3 text-xs">
                    <div class="min-w-0 flex-1">
                        <div class="flex items-center gap-2 flex-wrap">
                            <span class="font-bold text-slate-800 text-sm">${escapeHtml(u.full_name)}</span>
                            <span class="px-2 py-0.5 rounded-full text-[10px] font-bold ${isAdmin ? 'bg-amber-100 text-amber-800' : 'bg-teal-50 text-teal-700'}">
                                ${isAdmin ? 'Quản trị viên (Admin)' : 'Nhân viên'}
                            </span>
                            <span class="px-2 py-0.5 rounded-full text-[10px] font-bold ${isActive ? 'bg-emerald-50 text-emerald-700' : 'bg-rose-50 text-rose-700'}">
                                ${isActive ? 'Hoạt động' : 'Tạm khóa'}
                            </span>
                        </div>
                        <p class="text-slate-500 text-[11px] mt-1 font-mono">
                            Tài khoản: <b>${escapeHtml(u.username)}</b> • Tạo ngày: ${u.created_at || '--'}
                        </p>
                    </div>

                    <div class="flex items-center gap-1.5 flex-shrink-0">
                        <button onclick="openEditUserModal(${u.id}, '${escapeHtml(u.username)}', '${escapeHtml(u.full_name)}', '${u.role}', ${isActive})" title="Sửa tài khoản / Đổi mật khẩu" class="p-1.5 bg-slate-100 hover:bg-slate-200 text-slate-700 rounded-lg text-xs font-semibold">
                            <i class="fa-solid fa-pen-to-square"></i>
                        </button>
                        ${!isSelf ? `
                            <button onclick="toggleUserLock(${u.id}, ${isActive})" title="${isActive ? 'Khóa tài khoản' : 'Mở khóa tài khoản'}" class="p-1.5 ${isActive ? 'text-amber-600 hover:bg-amber-50' : 'text-emerald-600 hover:bg-emerald-50'} rounded-lg text-xs font-semibold">
                                <i class="fa-solid ${isActive ? 'fa-lock' : 'fa-lock-open'}"></i>
                            </button>
                            <button onclick="deleteUserAccount(${u.id}, '${escapeHtml(u.username)}')" title="Xóa tài khoản" class="p-1.5 text-rose-500 hover:bg-rose-50 rounded-lg text-xs font-semibold">
                                <i class="fa-solid fa-trash"></i>
                            </button>
                        ` : ''}
                    </div>
                </div>
            `;
        }).join('');
    } catch (e) {
        showToast(e.message, 'error');
    }
}

function openCreateUserModal() {
    const modal = document.getElementById('user-modal');
    document.getElementById('user-form').reset();
    document.getElementById('user-modal-title').textContent = 'Thêm Tài Khoản Nhân Viên Mới';
    document.getElementById('um-id').value = '';
    document.getElementById('um-username').readOnly = false;
    document.getElementById('um-password').required = true;
    document.getElementById('um-pw-required').classList.remove('hidden');
    document.getElementById('um-pw-hint').classList.add('hidden');
    document.getElementById('um-active-box').classList.add('hidden');
    modal.classList.remove('hidden');
    document.getElementById('um-username').focus();
}

function openEditUserModal(id, username, fullname, role, active) {
    const modal = document.getElementById('user-modal');
    document.getElementById('user-modal-title').textContent = `Chỉnh Sửa Tài Khoản: ${username}`;
    document.getElementById('um-id').value = id;
    document.getElementById('um-username').value = username;
    document.getElementById('um-username').readOnly = true;
    document.getElementById('um-fullname').value = fullname;
    document.getElementById('um-password').value = '';
    document.getElementById('um-password').required = false;
    document.getElementById('um-pw-required').classList.add('hidden');
    document.getElementById('um-pw-hint').classList.remove('hidden');
    document.getElementById('um-role').value = role;
    document.getElementById('um-active').checked = active;
    document.getElementById('um-active-box').classList.remove('hidden');
    modal.classList.remove('hidden');
}

function closeUserModal() {
    const modal = document.getElementById('user-modal');
    if (modal) modal.classList.add('hidden');
}

async function submitUserForm() {
    const uid = document.getElementById('um-id').value;
    const isEdit = Boolean(uid);
    const username = document.getElementById('um-username').value.trim();
    const fullname = document.getElementById('um-fullname').value.trim();
    const password = document.getElementById('um-password').value;
    const role = document.getElementById('um-role').value;
    const active = document.getElementById('um-active').checked;

    if (!fullname) {
        showToast('Vui lòng nhập họ tên nhân viên', 'error');
        return;
    }

    try {
        if (isEdit) {
            const body = { full_name: fullname, role: role, active: active };
            if (password) body.password = password;
            await api(`/api/admin/users/${uid}`, { method: 'PUT', body });
            showToast('Đã cập nhật thông tin tài khoản thành công', 'success');
        } else {
            if (!username || !password) {
                showToast('Vui lòng nhập tên đăng nhập và mật khẩu', 'error');
                return;
            }
            await api('/api/admin/users', {
                method: 'POST',
                body: { username, password, full_name: fullname, role }
            });
            showToast(`Đã tạo tài khoản ${username} (${role}) thành công`, 'success');
        }
        closeUserModal();
        await loadUsers();
    } catch (e) {
        showToast(e.message, 'error');
    }
}

async function toggleUserLock(uid, currentActive) {
    const action = currentActive ? 'khóa' : 'mở khóa';
    if (!confirm(`Bạn có chắc muốn ${action} tài khoản này?`)) return;
    try {
        await api(`/api/admin/users/${uid}`, {
            method: 'PUT',
            body: { active: !currentActive }
        });
        showToast(`Đã ${action} tài khoản thành công`, 'success');
        await loadUsers();
    } catch (e) {
        showToast(e.message, 'error');
    }
}

async function deleteUserAccount(uid, username) {
    if (!confirm(`Bạn có chắc muốn xóa vĩnh viễn tài khoản "${username}"?`)) return;
    try {
        await api(`/api/admin/users/${uid}`, { method: 'DELETE' });
        showToast(`Đã xóa tài khoản ${username}`, 'success');
        await loadUsers();
    } catch (e) {
        showToast(e.message, 'error');
    }
}

// ───────── Admin Sub-Tab: Clinic Settings ─────────
async function loadClinicSettings() {
    try {
        const s = await api('/api/admin/settings');
        document.getElementById('cfg-clinic-name').value = s.clinic_name || '';
        document.getElementById('cfg-clinic-hotline').value = s.clinic_hotline || '';
        document.getElementById('cfg-clinic-address').value = s.clinic_address || '';
        document.getElementById('cfg-branches').value = (s.branches || []).join(', ');
        document.getElementById('cfg-categories').value = (s.categories || []).join(', ');
        document.getElementById('cfg-dentists').value = (s.dentists || []).join(', ');
        const gemKeyEl = document.getElementById('cfg-gemini-key');
        if (gemKeyEl) gemKeyEl.value = s.gemini_api_key || '';
    } catch (e) {
        showToast(e.message, 'error');
    }
}

async function saveClinicSettings() {
    const name = document.getElementById('cfg-clinic-name').value.trim();
    const hotline = document.getElementById('cfg-clinic-hotline').value.trim();
    const address = document.getElementById('cfg-clinic-address').value.trim();
    const branches = document.getElementById('cfg-branches').value.split(',').map(x => x.trim()).filter(Boolean);
    const categories = document.getElementById('cfg-categories').value.split(',').map(x => x.trim()).filter(Boolean);
    const dentists = document.getElementById('cfg-dentists').value.split(',').map(x => x.trim()).filter(Boolean);
    const geminiKey = document.getElementById('cfg-gemini-key')?.value.trim() || '';

    if (!name) {
        showToast('Tên phòng khám không được để trống', 'error');
        return;
    }

    const btn = document.getElementById('btn-save-settings');
    btn.disabled = true;
    btn.innerHTML = '<i class="fa-solid fa-spinner fa-spin"></i> Đang lưu...';

    try {
        await api('/api/admin/settings', {
            method: 'POST',
            body: {
                clinic_name: name,
                clinic_hotline: hotline,
                clinic_address: address,
                branches: branches,
                categories: categories,
                dentists: dentists,
                gemini_api_key: geminiKey
            }
        });
        showToast('Đã lưu toàn bộ cấu hình ứng dụng thành công!', 'success');
        // Refresh meta
        state.meta = await api('/api/meta');
        populateDentistSelects();
        populateBranchSelects();
    } catch (e) {
        showToast(e.message, 'error');
    } finally {
        btn.disabled = false;
        btn.innerHTML = '<i class="fa-solid fa-floppy-disk"></i> <span>Lưu toàn bộ cài đặt ứng dụng</span>';
    }
}

// ───────── Populate Branch Select Options ─────────
function populateBranchSelects() {
    const branches = state.meta?.branches || ['Ninh Vân'];
    const currentFilter = state.branchFilter || '';

    // 1. Filter dropdowns (sidebar, mobile drawer, top header)
    const filterOptions = [
        `<option value="">🏢 ${branches.length > 1 ? `Tất cả ${branches.length} cơ sở` : 'Tất cả cơ sở'}</option>`,
        ...branches.map(b => `<option value="${escapeHtml(b)}">📍 ${escapeHtml(b)}</option>`)
    ].join('');

    const bSide = document.getElementById('branch-select');
    if (bSide) {
        bSide.innerHTML = filterOptions;
        bSide.value = branches.includes(currentFilter) ? currentFilter : '';
    }

    const bMob = document.getElementById('branch-select-mobile');
    if (bMob) {
        bMob.innerHTML = filterOptions;
        bMob.value = branches.includes(currentFilter) ? currentFilter : '';
    }

    const bTop = document.getElementById('branch-select-top');
    if (bTop) {
        bTop.innerHTML = filterOptions;
        bTop.value = branches.includes(currentFilter) ? currentFilter : '';
    }

    // 2. Form dropdowns (patient intake, treatment, appointment)
    const formOptions = branches.map(b => `<option value="${escapeHtml(b)}">${escapeHtml(b)}</option>`).join('');

    const pmBranch = document.getElementById('pm-branch');
    if (pmBranch) {
        const val = pmBranch.value;
        pmBranch.innerHTML = formOptions;
        if (branches.includes(val)) pmBranch.value = val;
        else if (branches.length > 0) pmBranch.value = branches[0];
    }

    const tmBranch = document.getElementById('tm-branch');
    if (tmBranch) {
        const val = tmBranch.value;
        tmBranch.innerHTML = formOptions;
        if (branches.includes(val)) tmBranch.value = val;
        else if (branches.length > 0) tmBranch.value = branches[0];
    }

    const apptmBranch = document.getElementById('apptm-branch');
    if (apptmBranch) {
        const val = apptmBranch.value;
        apptmBranch.innerHTML = formOptions;
        if (branches.includes(val)) apptmBranch.value = val;
        else if (branches.length > 0) apptmBranch.value = branches[0];
    }

    // 3. Update login badge label
    const loginBadge = document.getElementById('login-branch-label');
    if (loginBadge) {
        loginBadge.textContent = branches.length === 1 ? `Cơ sở: ${branches[0]}` : branches.join(' • ');
    }
}

// ───────── Populate Dentist Select Options ─────────
function populateDentistSelects() {
    const dentists = state.meta?.dentists || ['Thắng'];
    const optionsHtml = dentists.map(d => {
        const label = d.startsWith('BS.') ? d : 'BS. ' + d;
        return `<option value="${escapeHtml(d)}">${escapeHtml(label)}</option>`;
    }).join('');

    const pmSelect = document.getElementById('pm-t-dentist');
    if (pmSelect) pmSelect.innerHTML = optionsHtml;

    const tmSelect = document.getElementById('tm-dentist');
    if (tmSelect) tmSelect.innerHTML = optionsHtml;
}

// ───────── Admin Silent Alerts & Notifications ─────────
async function checkAdminAlerts() {
    if (!state.user || state.user.role !== 'admin') return;
    try {
        const res = await api('/api/admin/alerts?limit=1');
        const badge = document.getElementById('admin-alert-badge');
        if (badge) {
            if (res.unread > 0) {
                badge.textContent = res.unread > 99 ? '99+' : res.unread;
                badge.classList.remove('hidden');
            } else {
                badge.classList.add('hidden');
            }
        }
    } catch (_) {}
}

async function loadAdminAlerts() {
    if (!state.user || state.user.role !== 'admin') return;
    const container = document.getElementById('admin-alerts-container');
    const counter = document.getElementById('alerts-unread-counter');
    if (!container) return;

    container.innerHTML = '<div class="p-8 text-center text-slate-400"><i class="fa-solid fa-spinner fa-spin mr-2"></i>Đang tải dữ liệu cảnh báo...</div>';

    try {
        const res = await api('/api/admin/alerts?limit=50');
        if (counter) counter.textContent = `${res.unread || 0} cảnh báo chưa đọc`;

        const badge = document.getElementById('admin-alert-badge');
        if (badge) {
            if (res.unread > 0) {
                badge.textContent = res.unread > 99 ? '99+' : res.unread;
                badge.classList.remove('hidden');
            } else {
                badge.classList.add('hidden');
            }
        }

        if (!res.items || res.items.length === 0) {
            container.innerHTML = '<div class="p-8 text-center text-slate-400 bg-white rounded-2xl border border-slate-200">Chưa có cảnh báo sửa đổi nào từ nhân viên</div>';
            return;
        }

        const actionTitles = {
            treatment_edit: ['Sửa đợt điều trị', 'bg-amber-100 text-amber-800 border-amber-300'],
            treatment_delete: ['Xóa đợt điều trị', 'bg-rose-100 text-rose-800 border-rose-300'],
            patient_edit: ['Sửa hồ sơ bệnh nhân', 'bg-blue-100 text-blue-800 border-blue-300'],
            patient_delete: ['Xóa hồ sơ bệnh nhân', 'bg-rose-100 text-rose-800 border-rose-300'],
            payment_delete: ['Xóa khoản thanh toán', 'bg-rose-100 text-rose-800 border-rose-300']
        };

        container.innerHTML = res.items.map(a => {
            const badgeMeta = actionTitles[a.action] || ['Sửa đổi', 'bg-slate-100 text-slate-700 border-slate-300'];
            const isUnread = !a.is_read;

            let changesHtml = '';
            if (a.changes && a.changes.length > 0) {
                changesHtml = `
                    <div class="mt-2.5 pt-2.5 border-t border-slate-100 space-y-1.5 text-xs bg-slate-50 p-2.5 rounded-xl">
                        <span class="text-[10px] font-bold uppercase text-slate-400 block mb-1">Chi tiết các trường bị sửa đổi:</span>
                        ${a.changes.map(c => `
                            <div class="flex items-center justify-between text-xs py-0.5">
                                <span class="font-semibold text-slate-600">${escapeHtml(c.label || c.field)}:</span>
                                <div class="flex items-center gap-1.5 font-medium">
                                    <span class="line-through text-rose-500">${c.money ? formatVND(c.old) : escapeHtml(c.old || '(Trống)')}</span>
                                    <i class="fa-solid fa-arrow-right text-[10px] text-slate-400"></i>
                                    <span class="font-bold text-emerald-600">${c.money ? formatVND(c.new) : escapeHtml(c.new || '(Trống)')}</span>
                                </div>
                            </div>
                        `).join('')}
                    </div>
                `;
            }

            return `
                <div class="bg-white p-4 rounded-2xl border ${isUnread ? 'border-amber-400 bg-amber-50/20 shadow-md' : 'border-slate-200 shadow-sm'} transition space-y-2 relative">
                    ${isUnread ? '<span class="absolute top-3 right-3 w-2.5 h-2.5 rounded-full bg-rose-500 animate-pulse" title="Chưa đọc"></span>' : ''}
                    <div class="flex items-center gap-2 flex-wrap pr-5">
                        <span class="px-2 py-0.5 text-[10px] font-extrabold rounded-full border ${badgeMeta[1]}">${badgeMeta[0]}</span>
                        <span class="text-xs font-bold text-slate-800">${escapeHtml(a.patient_name || '')} ${a.patient_id ? `(ID ${a.patient_id})` : ''}</span>
                    </div>

                    <p class="text-xs text-slate-700 font-medium">
                        ${escapeHtml(a.summary)}
                    </p>

                    ${changesHtml}

                    <div class="pt-2 flex items-center justify-between text-[11px] text-slate-400 border-t border-slate-100 flex-wrap gap-2">
                        <div class="flex items-center gap-2">
                            <span>👤 Thực hiện: <b class="text-slate-700">${escapeHtml(a.full_name || a.username)}</b></span>
                            <span>•</span>
                            <span>🕒 ${a.at}</span>
                        </div>
                        <div class="flex items-center gap-1.5">
                            ${a.patient_id ? `
                                <button onclick="openPatientDetailFromAlert(${a.patient_id})" class="px-2 py-1 bg-teal-50 hover:bg-teal-100 text-teal-700 rounded-lg font-bold text-[11px] transition">
                                    <i class="fa-solid fa-user-pen mr-1"></i>Xem BN
                                </button>
                            ` : ''}
                            ${isUnread ? `
                                <button onclick="markAdminAlertRead(${a.id})" class="px-2 py-1 bg-slate-100 hover:bg-slate-200 text-slate-600 rounded-lg font-medium text-[11px] transition">
                                    <i class="fa-solid fa-check mr-1"></i>Đã đọc
                                </button>
                            ` : ''}
                        </div>
                    </div>
                </div>
            `;
        }).join('');
    } catch (e) {
        container.innerHTML = `<div class="p-8 text-center text-rose-500 bg-white rounded-2xl border border-rose-200">${e.message}</div>`;
    }
}

function toggleAdminAlertsModal() {
    const modal = document.getElementById('admin-alerts-modal');
    if (!modal) return;
    if (modal.classList.contains('hidden')) {
        modal.classList.remove('hidden');
        loadAdminAlerts();
    } else {
        modal.classList.add('hidden');
    }
}

function closeAdminAlertsModal() {
    const modal = document.getElementById('admin-alerts-modal');
    if (modal) modal.classList.add('hidden');
}

async function markAdminAlertRead(id) {
    try {
        await api('/api/admin/alerts/mark-read', {
            method: 'POST',
            body: { alert_id: id }
        });
        await loadAdminAlerts();
        await checkAdminAlerts();
    } catch (e) {
        showToast(e.message, 'error');
    }
}

async function markAllAdminAlertsRead() {
    try {
        await api('/api/admin/alerts/mark-read', {
            method: 'POST',
            body: {}
        });
        showToast('Đã đánh dấu tất cả cảnh báo là đã đọc', 'success');
        await loadAdminAlerts();
        await checkAdminAlerts();
    } catch (e) {
        showToast(e.message, 'error');
    }
}

async function openPatientDetailFromAlert(pid) {
    closeAdminAlertsModal();
    await openPatientDetail(pid);
}

// ───────── Admin Sub-Tab: Safe Database Backup ─────────
async function downloadNibiExcelFull() {
    try {
        showToast('Đang tạo file Excel NiBi 2026 chuẩn 6 sheet...', 'info');
        const resp = await fetch('/api/admin/export-excel.xlsx', {
            headers: { 'Authorization': `Bearer ${state.token}` }
        });
        if (!resp.ok) {
            const err = await resp.json().catch(() => ({}));
            throw new Error(err.detail || 'Không thể tạo file Excel');
        }
        const blob = await resp.blob();
        const url = window.URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        const today = new Date().toISOString().split('T')[0];
        a.download = `Quan_Ly_PK_NiBi_2026_${today}.xlsx`;
        document.body.appendChild(a);
        a.click();
        a.remove();
        window.URL.revokeObjectURL(url);
        showToast('Đã tải thành công file Excel Quản Lý NiBi 2026!', 'success');
    } catch (e) {
        if (state.token) {
            window.location.href = `/api/admin/export-excel.xlsx?token=${encodeURIComponent(state.token)}`;
        } else {
            showToast(e.message || 'Lỗi khi tải bảng tính Excel', 'error');
        }
    }
}

async function downloadBackupDatabase() {
    try {
        showToast('Đang tạo và tải file sao lưu CSDL (.db)...', 'info');
        const resp = await fetch('/api/admin/backup.db', {
            headers: { 'Authorization': `Bearer ${state.token}` }
        });
        if (!resp.ok) {
            const err = await resp.json().catch(() => ({}));
            throw new Error(err.detail || 'Không thể tải file sao lưu');
        }
        const blob = await resp.blob();
        const url = window.URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        const today = new Date().toISOString().split('T')[0];
        a.download = `NiBi_Dental_Backup_${today}.db`;
        document.body.appendChild(a);
        a.click();
        a.remove();
        window.URL.revokeObjectURL(url);
        showToast('Tải file sao lưu CSDL thành công!', 'success');
    } catch (e) {
        if (state.token) {
            window.location.href = `/api/admin/backup.db?token=${encodeURIComponent(state.token)}`;
        } else {
            showToast(e.message || 'Lỗi khi tải sao lưu', 'error');
        }
    }
}

function toggleMobileSidebar() {
    const drawer = document.getElementById('mobile-sidebar-drawer');
    if (!drawer) return;
    if (drawer.classList.contains('hidden')) {
        drawer.classList.remove('hidden');
    } else {
        drawer.classList.add('hidden');
    }
}

function closeMobileSidebar() {
    const drawer = document.getElementById('mobile-sidebar-drawer');
    if (drawer) drawer.classList.add('hidden');
}

// ─────────────────────────────────────────────────────────────────────────────
// 🤖 NIBI AI ASSISTANT: SMART INTAKE & CLINIC CHATBOT
// ─────────────────────────────────────────────────────────────────────────────

let currentSpeechRecognition = null;

function startVoiceRecognition(targetInputId, micBtnId, autoSubmit = false) {
    const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;
    if (!SpeechRecognition) {
        showToast('Trình duyệt hiện tại chưa hỗ trợ Web Speech API. Hãy dùng Chrome hoặc Safari.', 'warning');
        return;
    }

    const inputEl = document.getElementById(targetInputId);
    const micBtn = document.getElementById(micBtnId);

    if (currentSpeechRecognition) {
        try { currentSpeechRecognition.stop(); } catch (e) {}
        currentSpeechRecognition = null;
        if (micBtn) micBtn.classList.remove('text-rose-500', 'animate-pulse');
        return;
    }

    try {
        const recognition = new SpeechRecognition();
        currentSpeechRecognition = recognition;
        recognition.lang = 'vi-VN';
        recognition.interimResults = false;
        recognition.maxAlternatives = 1;

        if (micBtn) {
            micBtn.classList.add('text-rose-500', 'animate-pulse');
        }
        showToast('🎙️ Đang nghe... Mời Bác sĩ nói tiếng Việt', 'info');

        recognition.onresult = (event) => {
            const transcript = event.results[0][0].transcript;
            if (inputEl) {
                inputEl.value = transcript;
            }
            showToast(`🎙️ Đã nhận diện: "${transcript}"`, 'success');
            if (autoSubmit) {
                if (targetInputId === 'ai-chat-input') {
                    submitAiChatMessage();
                } else if (targetInputId === 'ai-intake-input') {
                    triggerAiIntakeParse();
                }
            }
        };

        recognition.onerror = (event) => {
            console.warn('Speech recognition error:', event.error);
            if (event.error !== 'no-speech') {
                showToast(`Lỗi thu âm: ${event.error}`, 'error');
            }
        };

        recognition.onend = () => {
            currentSpeechRecognition = null;
            if (micBtn) {
                micBtn.classList.remove('text-rose-500', 'animate-pulse');
            }
        };

        recognition.start();
    } catch (err) {
        console.error(err);
        showToast('Không thể bật Micro: ' + err.message, 'error');
        if (micBtn) micBtn.classList.remove('text-rose-500', 'animate-pulse');
    }
}

async function triggerAiIntakeParse() {
    const input = document.getElementById('ai-intake-input');
    const text = input ? input.value.trim() : '';
    if (!text) {
        showToast('Vui lòng nhập hoặc nói câu mô tả bệnh nhân trước', 'warning');
        return;
    }

    const statusEl = document.getElementById('ai-intake-status');
    const btn = document.getElementById('ai-intake-btn');
    if (statusEl) statusEl.classList.remove('hidden');
    if (btn) {
        btn.disabled = true;
        btn.innerHTML = '<i class="fa-solid fa-spinner fa-spin"></i><span>Đang xử lý</span>';
    }

    try {
        const res = await api('/api/ai/parse-intake', {
            method: 'POST',
            body: { text: text }
        });
        if (res && res.data) {
            const d = res.data;
            if (d.name) {
                const el = document.getElementById('pm-name');
                if (el) el.value = d.name;
            }
            if (d.phone) {
                const el = document.getElementById('pm-phone');
                if (el) el.value = d.phone;
            }
            if (d.yob) {
                const el = document.getElementById('pm-yob');
                if (el) {
                    el.value = d.yob;
                    if (typeof syncAgeFromYob === 'function') syncAgeFromYob();
                }
            } else if (d.age) {
                const el = document.getElementById('pm-age');
                if (el) {
                    el.value = d.age;
                    if (typeof syncYobFromAge === 'function') syncYobFromAge();
                }
            }
            if (d.address) {
                const el = document.getElementById('pm-address');
                if (el) el.value = d.address;
            }
            if (d.branch) {
                const branchSelect = document.getElementById('pm-branch');
                if (branchSelect) branchSelect.value = d.branch;
            }
            if (d.notes) {
                const el = document.getElementById('pm-notes');
                if (el) el.value = d.notes;
            }

            // Tự động bật và điền thông tin đợt điều trị đầu tiên nếu có thủ thuật hoặc chi phí
            const hasTreatment = Boolean(d.procedure || (d.cost && d.cost > 0) || d.tooth);
            if (hasTreatment) {
                const chk = document.getElementById('pm-enable-treatment');
                if (chk) {
                    chk.checked = true;
                    if (typeof toggleFirstTreatmentFields === 'function') toggleFirstTreatmentFields();
                }
                if (d.category) {
                    const el = document.getElementById('pm-t-cat');
                    if (el) el.value = d.category;
                }
                if (d.procedure) {
                    const el = document.getElementById('pm-t-proc');
                    if (el) el.value = d.procedure;
                }
                if (d.tooth) {
                    const el = document.getElementById('pm-t-tooth');
                    if (el) el.value = d.tooth;
                }
                if (d.cost !== undefined && d.cost !== null) {
                    const el = document.getElementById('pm-t-cost');
                    if (el) el.value = Math.round(d.cost / 1000);
                }
                if (d.paid !== undefined && d.paid !== null) {
                    const el = document.getElementById('pm-t-paid');
                    if (el) el.value = Math.round(d.paid / 1000);
                }
                if (d.method) {
                    const el = document.getElementById('pm-t-method');
                    if (el) el.value = d.method;
                }
                if (d.next_date) {
                    const el = document.getElementById('pm-t-next');
                    if (el) el.value = d.next_date;
                }
            }

            // Highlight filled fields with brief emerald glow
            ['pm-name', 'pm-phone', 'pm-address', 'pm-t-proc', 'pm-t-cost', 'pm-t-paid'].forEach(id => {
                const el = document.getElementById(id);
                if (el && el.value) {
                    el.classList.add('ring-2', 'ring-emerald-400', 'bg-emerald-50');
                    setTimeout(() => el.classList.remove('ring-2', 'ring-emerald-400', 'bg-emerald-50'), 2500);
                }
            });

            showToast('⚡ AI đã tự động điền đầy đủ thông tin vào hồ sơ!', 'success');
        }
    } catch (e) {
        showToast(e.message || 'Lỗi khi AI phân tích câu nói', 'error');
    } finally {
        if (statusEl) statusEl.classList.add('hidden');
        if (btn) {
            btn.disabled = false;
            btn.innerHTML = '<i class="fa-solid fa-bolt"></i><span>Điền Tự Động</span>';
        }
    }
}

function renderAiWelcomeMessage(isCleared = false) {
    const isAdmin = state.user?.role === 'admin';
    const roleGreeting = isAdmin ? 'Bác sĩ Thắng' : 'Nhân viên';
    const subText = isCleared 
        ? 'Đã làm mới cuộc trò chuyện. Bạn cần tra cứu thông tin gì?' 
        : 'Tôi có thể hỗ trợ tra cứu hồ sơ bệnh nhân, tìm số điện thoại, xem tiền nợ, lịch hẹn' + (isAdmin ? ' và báo cáo doanh thu phòng khám.' : '.');

    let chipsHtml = `
        <button type="button" onclick="sendAiQuickPrompt('Thông tin bệnh nhân ')" class="px-2 py-1 bg-indigo-50 hover:bg-indigo-100 text-indigo-700 rounded-lg text-[11px] font-medium transition border border-indigo-200">
            🔍 Tra cứu bệnh nhân
        </button>
        <button type="button" onclick="sendAiQuickPrompt('Ai đang nợ nhiều nhất?')" class="px-2 py-1 bg-amber-50 hover:bg-amber-100 text-amber-800 rounded-lg text-[11px] font-medium transition border border-amber-200">
            💰 Ai nợ nhiều nhất?
        </button>
        <button type="button" onclick="sendAiQuickPrompt('Lịch hẹn hôm nay có ai?')" class="px-2 py-1 bg-teal-50 hover:bg-teal-100 text-teal-700 rounded-lg text-[11px] font-medium transition border border-teal-200">
            📅 Lịch hẹn hôm nay
        </button>
    `;

    if (isAdmin) {
        chipsHtml = `
            <button type="button" onclick="sendAiQuickPrompt('Doanh thu tháng này thế nào?')" class="px-2 py-1 bg-emerald-50 hover:bg-emerald-100 text-emerald-800 rounded-lg text-[11px] font-medium transition border border-emerald-200">
                📊 Doanh thu tháng này
            </button>
        ` + chipsHtml;
    }

    return `
        <div class="flex gap-2.5 items-start">
            <div class="w-7 h-7 rounded-lg bg-indigo-600 text-white flex items-center justify-center text-xs flex-shrink-0 mt-0.5">
                <i class="fa-solid fa-robot"></i>
            </div>
            <div class="bg-white border border-slate-200 p-3 rounded-2xl rounded-tl-sm text-slate-800 shadow-sm space-y-2 max-w-[85%]">
                <p class="font-semibold text-indigo-900">👋 Chào ${roleGreeting}!</p>
                <p class="text-slate-600 leading-relaxed">${subText}</p>
                <div class="pt-1.5 border-t border-slate-100 flex flex-wrap gap-1.5">
                    ${chipsHtml}
                </div>
            </div>
        </div>
    `;
}

function toggleAiChatDrawer() {
    const drawer = document.getElementById('ai-chat-drawer');
    if (!drawer) return;
    if (drawer.classList.contains('hidden')) {
        drawer.classList.remove('hidden');
        const msgContainer = document.getElementById('ai-chat-messages');
        if (msgContainer && (!msgContainer.children.length || msgContainer.children.length === 1)) {
            msgContainer.innerHTML = renderAiWelcomeMessage(false);
        }
        setTimeout(() => {
            drawer.classList.remove('scale-95');
            drawer.classList.add('scale-100');
            document.getElementById('ai-chat-input')?.focus();
        }, 10);
    } else {
        drawer.classList.remove('scale-100');
        drawer.classList.add('scale-95');
        setTimeout(() => drawer.classList.add('hidden'), 200);
    }
}

function clearAiChat() {
    const msgContainer = document.getElementById('ai-chat-messages');
    if (!msgContainer) return;
    msgContainer.innerHTML = renderAiWelcomeMessage(true);
}

function sendAiQuickPrompt(prompt) {
    const input = document.getElementById('ai-chat-input');
    if (input) {
        input.value = prompt;
        submitAiChatMessage();
    }
}

function formatAiMarkdown(text) {
    if (!text) return '';
    return text
        .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
        .replace(/\*\*(.*?)\*\*/g, '<strong>$1</strong>')
        .replace(/\*(.*?)\*/g, '<em>$1</em>')
        .replace(/`(.*?)`/g, '<code class="px-1 py-0.5 bg-slate-100 text-indigo-700 font-mono rounded">$1</code>')
        .replace(/\n/g, '<br>');
}

async function submitAiChatMessage() {
    const input = document.getElementById('ai-chat-input');
    const msg = input ? input.value.trim() : '';
    if (!msg) return;

    input.value = '';
    const container = document.getElementById('ai-chat-messages');

    // Add user message bubble
    const userDiv = document.createElement('div');
    userDiv.className = 'flex justify-end';
    userDiv.innerHTML = `
        <div class="bg-indigo-600 text-white px-3.5 py-2.5 rounded-2xl rounded-tr-sm shadow-sm max-w-[85%] text-xs leading-relaxed">
            ${msg.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")}
        </div>
    `;
    container.appendChild(userDiv);

    // Add typing indicator
    const typingDiv = document.createElement('div');
    typingDiv.id = 'ai-typing-indicator';
    typingDiv.className = 'flex gap-2.5 items-start';
    typingDiv.innerHTML = `
        <div class="w-7 h-7 rounded-lg bg-indigo-600 text-white flex items-center justify-center text-xs flex-shrink-0 mt-0.5">
            <i class="fa-solid fa-robot"></i>
        </div>
        <div class="bg-white border border-slate-200 px-3 py-2 rounded-2xl rounded-tl-sm text-slate-500 shadow-sm flex items-center gap-1.5 text-xs">
            <i class="fa-solid fa-spinner fa-spin text-indigo-600"></i>
            <span>AI đang tra cứu dữ liệu...</span>
        </div>
    `;
    container.appendChild(typingDiv);
    container.scrollTop = container.scrollHeight;

    try {
        const res = await api('/api/ai/chat', {
            method: 'POST',
            body: { message: msg }
        });
        typingDiv.remove();

        const aiDiv = document.createElement('div');
        aiDiv.className = 'flex gap-2.5 items-start';
        aiDiv.innerHTML = `
            <div class="w-7 h-7 rounded-lg bg-indigo-600 text-white flex items-center justify-center text-xs flex-shrink-0 mt-0.5">
                <i class="fa-solid fa-robot"></i>
            </div>
            <div class="bg-white border border-slate-200 p-3 rounded-2xl rounded-tl-sm text-slate-800 shadow-sm space-y-1.5 max-w-[85%] text-xs leading-relaxed">
                ${formatAiMarkdown(res.reply || '')}
            </div>
        `;
        container.appendChild(aiDiv);
    } catch (e) {
        typingDiv.remove();
        const errDiv = document.createElement('div');
        errDiv.className = 'flex gap-2.5 items-start';
        errDiv.innerHTML = `
            <div class="w-7 h-7 rounded-lg bg-rose-500 text-white flex items-center justify-center text-xs flex-shrink-0 mt-0.5">
                <i class="fa-solid fa-triangle-exclamation"></i>
            </div>
            <div class="bg-rose-50 border border-rose-200 p-2.5 rounded-2xl rounded-tl-sm text-rose-700 shadow-sm text-xs">
                ${e.message || 'Lỗi kết nối tới Trợ lý AI'}
            </div>
        `;
        container.appendChild(errDiv);
    } finally {
        container.scrollTop = container.scrollHeight;
    }
}
