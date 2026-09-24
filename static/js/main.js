// =====================================================
//              初始化與事件綁定
// =====================================================

document.addEventListener('DOMContentLoaded', function () {
    // 初始化排序圖標
    initSortIcons();

    // 初始化主題
    initTheme();

    // 初始化寫卷分組
    initManuscriptGrouping();

    // 加載寫卷設置
    loadManuscriptSettings();

    // 初始化全局標記事件監聽器
    initManuscriptGlobalMarkListeners();

    // 設置折疊監控
    setupCollapseMonitoring();

    // 檢查是否需要自動加載更多數據
    checkAndLoadMoreIfNeeded();
});

// =====================================================
//              排序功能
// =====================================================

function initSortIcons() {
    const currentSort = new URLSearchParams(window.location.search).get('sort');
    const currentDirection = new URLSearchParams(window.location.search).get('direction');

    document.querySelectorAll('th.sortable').forEach(th => {
        th.classList.remove('sort-asc', 'sort-desc', 'sort-none');
        if (th.dataset.sort === currentSort) {
            th.classList.add(currentDirection === 'asc' ? 'sort-asc' : 'sort-desc');
        } else {
            th.classList.add('sort-none');
        }

        th.addEventListener('click', () => {
            const sortColumn = th.dataset.sort;
            let newDirection = 'asc';

            if (sortColumn === currentSort && currentDirection === 'asc') {
                newDirection = 'desc';
            }

            const urlParams = new URLSearchParams(window.location.search);
            urlParams.set('sort', sortColumn);
            urlParams.set('direction', newDirection);
            urlParams.set('page', '1'); // 重置到第一頁
            window.location.href = `${window.location.pathname}?${urlParams.toString()}`;
        });
    });
}

// =====================================================
//              主題切換功能
// =====================================================

function initTheme() {
    const savedTheme = localStorage.getItem('theme') || 'light';
    document.documentElement.setAttribute('data-theme', savedTheme);
    updateThemeIcon(savedTheme);
}

function toggleTheme() {
    const currentTheme = document.documentElement.getAttribute('data-theme');
    const newTheme = currentTheme === 'light' ? 'dark' : 'light';

    document.documentElement.setAttribute('data-theme', newTheme);
    localStorage.setItem('theme', newTheme);
    updateThemeIcon(newTheme);
}

function updateThemeIcon(theme) {
    const btn = document.querySelector('.theme-toggle');
    if (btn) {
        btn.textContent = theme === 'light' ? '🌙' : '☀️';
    }
}

// =====================================================
//              寫卷分組功能
// =====================================================

function initManuscriptGrouping() {
    const groupingEnabled = localStorage.getItem('manuscriptGroupingEnabled');
    // 默認開啟分組
    if (groupingEnabled === null) {
        localStorage.setItem('manuscriptGroupingEnabled', 'true');
        document.getElementById('toggle_manuscript_grouping').checked = true;
    } else {
        document.getElementById('toggle_manuscript_grouping').checked = groupingEnabled === 'true';
    }
    toggleManuscriptGrouping();
}

function toggleManuscriptGrouping() {
    const enabled = document.getElementById('toggle_manuscript_grouping').checked;
    localStorage.setItem('manuscriptGroupingEnabled', enabled);

    const groups = document.querySelectorAll('.manuscript-group');
    const flatTable = document.getElementById('flat-table-container');

    if (enabled) {
        // 顯示分組視圖
        groups.forEach(g => g.style.display = 'block');
        if (flatTable) flatTable.style.display = 'none';
    } else {
        // 顯示扁平視圖（這裡需要後端支持或前端重組 DOM，暫時只隱藏分組樣式）
        // 由於結構差異較大，簡單隱藏邊框和標題可能不夠
        // 這裡暫時只做簡單處理
        groups.forEach(g => {
            g.style.border = 'none';
            g.style.boxShadow = 'none';
            g.querySelector('.manuscript-header').style.display = 'none';
        });
    }
}

function toggleManuscript(header) {
    const content = header.nextElementSibling;
    header.classList.toggle('collapsed');
    if (content.style.display === 'none') {
        content.style.display = 'block';
    } else {
        content.style.display = 'none';
    }
}

// =====================================================
//              行狀態管理（殘損、誤入、無標記等）
// =====================================================

function getDefaultRowId(manuscriptId, type) {
    return localStorage.getItem(`defaultRow_${manuscriptId}_${type}`);
}

function setDefaultRowId(manuscriptId, type, charId) {
    localStorage.setItem(`defaultRow_${manuscriptId}_${type}`, charId);
}

function toggleDamagedRows(manuscriptId) {
    const container = document.querySelector(`[data-manuscript-id="${manuscriptId}"]`);
    const damagedRows = container.querySelectorAll('.damaged-row');
    const button = document.getElementById(`damaged-btn-${manuscriptId}`);
    const isHidden = button.textContent.includes('显示');

    damagedRows.forEach(row => {
        if (isHidden) {
            row.classList.remove('collapsed');
        } else {
            row.classList.add('collapsed');
        }
    });

    button.textContent = isHidden ? '隐藏残损' : '显示残损';
    button.classList.toggle('active', isHidden);

    // 保存状态
    const states = JSON.parse(localStorage.getItem('damagedRowStates') || '{}');
    states[manuscriptId] = !isHidden; // true表示隐藏
    localStorage.setItem('damagedRowStates', JSON.stringify(states));
    updateCollapsedCount(manuscriptId, 'damaged');
}

function toggleMisplacedRows(manuscriptId) {
    const container = document.querySelector(`[data-manuscript-id="${manuscriptId}"]`);
    const misplacedRows = container.querySelectorAll('.misplaced-row');
    const button = document.getElementById(`misplaced-btn-${manuscriptId}`);
    const isHidden = button.textContent.includes('显示');

    misplacedRows.forEach(row => {
        if (isHidden) {
            row.classList.remove('collapsed');
        } else {
            row.classList.add('collapsed');
        }
    });

    button.textContent = isHidden ? '隐藏误入' : '显示误入';
    button.classList.toggle('active', isHidden);

    // 保存状态
    const states = JSON.parse(localStorage.getItem('misplacedRowStates') || '{}');
    states[manuscriptId] = !isHidden;
    localStorage.setItem('misplacedRowStates', JSON.stringify(states));
    updateCollapsedCount(manuscriptId, 'misplaced');
}

function toggleUnmarkedRows(manuscriptId) {
    const container = document.querySelector(`[data-manuscript-id="${manuscriptId}"]`);
    const unmarkedRows = Array.from(container.querySelectorAll('.unmarked-row'));
    const button = document.getElementById(`unmarked-btn-${manuscriptId}`);
    const isHidden = button.textContent.includes('显示');

    if (unmarkedRows.length === 0) return;

    if (isHidden) {
        // 显示所有
        unmarkedRows.forEach(row => row.classList.remove('collapsed'));
    } else {
        // 隐藏（保留第一行）
        // 检查是否有设置默认行
        const preferredId = getDefaultRowId(manuscriptId, 'unmarked');
        let anchor = unmarkedRows[0];

        if (preferredId) {
            const found = unmarkedRows.find(r => r.dataset.charId === preferredId);
            if (found) anchor = found;
        }

        unmarkedRows.forEach(row => row.classList.add('collapsed'));
        anchor.classList.remove('collapsed');
    }

    button.textContent = isHidden ? '隐藏无标记' : '显示无标记';
    button.classList.toggle('active', isHidden);

    // 保存状态
    const states = JSON.parse(localStorage.getItem('unmarkedRowStates') || '{}');
    states[manuscriptId] = !isHidden;
    localStorage.setItem('unmarkedRowStates', JSON.stringify(states));
    updateCollapsedCount(manuscriptId, 'unmarked');
}

function toggleNumberedRows(manuscriptId) {
    const container = document.querySelector(`[data-manuscript-id="${manuscriptId}"]`);
    const numberedRows = Array.from(container.querySelectorAll('.numbered-row'));
    const button = document.getElementById(`numbered-btn-${manuscriptId}`);
    const isHidden = button.textContent.includes('显示');

    if (numberedRows.length === 0) return;

    // 按字形标记值分组（1、2、3、4）
    const rowsByValue = {};
    numberedRows.forEach(row => {
        const value = row.getAttribute('data-operation-value');
        if (value) {
            if (!rowsByValue[value]) {
                rowsByValue[value] = [];
            }
            rowsByValue[value].push(row);
        }
    });

    if (isHidden) {
        // 显示所有
        numberedRows.forEach(row => row.classList.remove('collapsed'));
    } else {
        // 折叠：对每个字形标记值，保留第一行，隐藏其他行
        for (const value in rowsByValue) {
            const rows = rowsByValue[value];
            if (rows.length > 0) {
                const preferredId = getDefaultRowId(manuscriptId, `numbered_${value}`);
                let anchor = rows[0];

                if (preferredId) {
                    const found = rows.find(r => r.dataset.charId === preferredId);
                    if (found) anchor = found;
                }

                rows.forEach(row => row.classList.add('collapsed'));
                anchor.classList.remove('collapsed');
            }
        }
    }

    button.textContent = isHidden ? '隐藏字形标记' : '显示字形标记';
    button.classList.toggle('active', isHidden);

    // 保存状态
    const states = JSON.parse(localStorage.getItem('numberedRowStates') || '{}');
    states[manuscriptId] = !isHidden;
    localStorage.setItem('numberedRowStates', JSON.stringify(states));
    updateCollapsedCount(manuscriptId, 'numbered');
}

function toggleStatusMarkedRows(manuscriptId) {
    const container = document.querySelector(`[data-manuscript-id="${manuscriptId}"]`);
    const statusMarkedRows = container.querySelectorAll('.status-marked-row');
    const button = document.getElementById(`status-marked-btn-${manuscriptId}`);
    const isHidden = button.textContent.includes('显示');

    statusMarkedRows.forEach(row => {
        if (isHidden) {
            row.classList.remove('collapsed');
        } else {
            row.classList.add('collapsed');
        }
    });

    button.textContent = isHidden ? '隐藏状况/正误' : '显示状况/正误';
    button.classList.toggle('active', isHidden);

    // 保存状态
    const states = JSON.parse(localStorage.getItem('statusMarkedRowStates') || '{}');
    states[manuscriptId] = !isHidden;
    localStorage.setItem('statusMarkedRowStates', JSON.stringify(states));
}

// =====================================================
//              默認行設置功能
// =====================================================

function setDefaultRow(btn, manuscriptId, type, charId) {
    // 移除同组其他按钮的active状态
    const container = btn.closest('tbody');
    container.querySelectorAll(`.default-row-btn[onclick*="'${type}'"]`).forEach(b => {
        b.classList.remove('active');
        b.textContent = '设为默认';
    });

    // 设置当前按钮状态
    btn.classList.add('active');
    btn.textContent = '默认显示';

    // 保存设置
    setDefaultRowId(manuscriptId, type, charId);

    // 触发一次保存到后端的逻辑（如果需要）
    // saveDefaultKeyword(charId, type); // 暂时不需要
}

// =====================================================
//              加載保存的狀態
// =====================================================

function loadDamagedRowStates() {
    const states = JSON.parse(localStorage.getItem('damagedRowStates') || '{}');
    const autoCollapse = localStorage.getItem('autoCollapseDamagedRows') === 'true';

    Object.keys(states).forEach(manuscriptId => {
        // 如果开启了自动折叠，或者状态记录为隐藏
        const shouldHide = autoCollapse || states[manuscriptId];
        if (shouldHide) {
            const container = document.querySelector(`[data-manuscript-id="${manuscriptId}"]`);
            if (container) {
                const damagedRows = container.querySelectorAll('.damaged-row');
                const button = document.getElementById(`damaged-btn-${manuscriptId}`);

                damagedRows.forEach(row => {
                    row.classList.add('collapsed');
                });

                if (button) {
                    button.textContent = '显示残损';
                    button.classList.add('active');
                }
            }
        }
    });
}

function loadMisplacedRowStates() {
    const states = JSON.parse(localStorage.getItem('misplacedRowStates') || '{}');

    Object.keys(states).forEach(manuscriptId => {
        const shouldHide = states[manuscriptId];
        if (shouldHide) {
            const container = document.querySelector(`[data-manuscript-id="${manuscriptId}"]`);
            if (container) {
                const misplacedRows = container.querySelectorAll('.misplaced-row');
                const button = document.getElementById(`misplaced-btn-${manuscriptId}`);

                misplacedRows.forEach(row => {
                    row.classList.add('collapsed');
                });

                if (button) {
                    button.textContent = '显示误入';
                    button.classList.add('active');
                }
            }
        }
    });
}

function loadUnmarkedRowStates() {
    const states = JSON.parse(localStorage.getItem('unmarkedRowStates') || '{}');

    Object.keys(states).forEach(manuscriptId => {
        const shouldHide = states[manuscriptId];
        if (shouldHide) {
            const container = document.querySelector(`[data-manuscript-id="${manuscriptId}"]`);
            if (container) {
                const unmarkedRows = Array.from(container.querySelectorAll('.unmarked-row'));
                const button = document.getElementById(`unmarked-btn-${manuscriptId}`);

                if (unmarkedRows.length > 0) {
                    const preferredId = getDefaultRowId(manuscriptId, 'unmarked');
                    const anchor = unmarkedRows.find(r => r.dataset.charId === preferredId) || unmarkedRows[0];
                    unmarkedRows.forEach(r => r.classList.add('collapsed'));
                    anchor.classList.remove('collapsed');
                }

                if (button) {
                    button.textContent = '显示无标记';
                    button.classList.add('active');
                }
            }
        }
    });
}

function loadNumberedRowStates() {
    const states = JSON.parse(localStorage.getItem('numberedRowStates') || '{}');

    Object.keys(states).forEach(manuscriptId => {
        const shouldHide = states[manuscriptId];
        if (shouldHide) {
            const container = document.querySelector(`[data-manuscript-id="${manuscriptId}"]`);
            if (container) {
                const numberedRows = Array.from(container.querySelectorAll('.numbered-row'));
                const button = document.getElementById(`numbered-btn-${manuscriptId}`);

                if (numberedRows.length > 0) {
                    // 按字形标记值分组（1、2、3、4）
                    const rowsByValue = {};
                    numberedRows.forEach(row => {
                        const value = row.getAttribute('data-operation-value');
                        if (value) {
                            if (!rowsByValue[value]) {
                                rowsByValue[value] = [];
                            }
                            rowsByValue[value].push(row);
                        }
                    });

                    // 对每个字形标记值，保留第一行，隐藏其他行
                    for (const value in rowsByValue) {
                        const rows = rowsByValue[value];
                        if (rows.length > 0) {
                            const preferredId = getDefaultRowId(manuscriptId, `numbered_${value}`);
                            const anchor = rows.find(r => r.dataset.charId === preferredId) || rows[0];
                            rows.forEach(r => r.classList.add('collapsed'));
                            anchor.classList.remove('collapsed');
                        }
                    }
                }

                if (button) {
                    button.textContent = '显示字形标记';
                    button.classList.add('active');
                }
            }
        }
    });
}

function loadStatusMarkedRowStates() {
    const states = JSON.parse(localStorage.getItem('statusMarkedRowStates') || '{}');

    Object.keys(states).forEach(manuscriptId => {
        const shouldHide = states[manuscriptId];
        if (shouldHide) {
            const container = document.querySelector(`[data-manuscript-id="${manuscriptId}"]`);
            if (container) {
                const statusMarkedRows = container.querySelectorAll('.status-marked-row');
                const button = document.getElementById(`status-marked-btn-${manuscriptId}`);

                statusMarkedRows.forEach(row => {
                    row.classList.add('collapsed');
                });

                if (button) {
                    button.textContent = '显示状况/正误';
                    button.classList.add('active');
                }
            }
        }
    });
}

function loadManuscriptSettings() {
    // 加载写卷分组设置
    const groupingEnabled = localStorage.getItem('manuscriptGroupingEnabled');
    if (groupingEnabled === 'false') {
        document.getElementById('toggle_manuscript_grouping').checked = false;
        toggleManuscriptGrouping();
    }

    // 加载自动折叠残损行设置
    const autoCollapse = localStorage.getItem('autoCollapseDamagedRows') === 'true';
    const checkbox = document.getElementById('auto_collapse_damaged_rows');
    if (checkbox) {
        checkbox.checked = autoCollapse;
    }

    // 加载残损行状态
    loadDamagedRowStates();

    // 加载误入行状态
    loadMisplacedRowStates();

    // 加载无标记行状态
    loadUnmarkedRowStates();

    // 加载字形标记行状态
    loadNumberedRowStates();

    // 加载状况/正误标记行状态
    loadStatusMarkedRowStates();

    // 加载全局标记显示设置
    loadGlobalMarkSettings();

    // 同步每个写卷的局部显示开关
    syncAllLocalMarkCheckboxes();
}

// =====================================================
//              全局标记显示/隐藏功能
// =====================================================

function toggleGlobalUnmarkedRows() {
    const checkbox = document.getElementById('global_show_unmarked');
    const show = checkbox.checked;

    // 获取所有写卷容器
    const manuscriptContainers = document.querySelectorAll('[data-manuscript-id]');

    manuscriptContainers.forEach(container => {
        const manuscriptId = container.dataset.manuscriptId;

        // 检查是否有个性化设置
        const hasCustomSetting = localStorage.getItem(`manuscript_global_unmarked_${manuscriptId}`) !== null;

        // 如果正在加载写卷设置或有个性化设置，跳过同步
        if (window.isLoadingManuscriptSettings || hasCustomSetting) {
            return;
        }

        const unmarkedRows = Array.from(container.querySelectorAll('.unmarked-row'));
        if (unmarkedRows.length === 0) return;

        if (show) {
            // 显示所有无标记行
            unmarkedRows.forEach(row => {
                row.classList.remove('collapsed');
            });
        } else {
            // 隐藏所有无标记行（保留第一行）
            const firstRow = unmarkedRows[0];
            const otherRows = unmarkedRows.slice(1);
            firstRow.classList.remove('collapsed');
            otherRows.forEach(row => {
                row.classList.add('collapsed');
            });
        }

        // 同步写卷的全局开关
        const globalCheckbox = document.querySelector(`.global-mark-toggle[data-manuscript-id="${manuscriptId}"][data-type="unmarked"]`);
        if (globalCheckbox && globalCheckbox.checked !== show) {
            globalCheckbox.checked = show;
        }
    });

    localStorage.setItem('global_show_unmarked', show.toString());
    syncAllLocalMarkCheckboxes();
    manuscriptContainers.forEach(container => updateCollapsedCount(container.dataset.manuscriptId, 'unmarked'));
}

function toggleGlobalNumberedRows() {
    const checkbox = document.getElementById('global_show_numbered');
    const show = checkbox.checked;

    // 获取所有写卷容器
    const manuscriptContainers = document.querySelectorAll('[data-manuscript-id]');

    manuscriptContainers.forEach(container => {
        const numberedRows = Array.from(container.querySelectorAll('.numbered-row'));
        if (numberedRows.length === 0) return;

        // 按字形标记值分组（1、2、3、4）
        const rowsByValue = {};
        numberedRows.forEach(row => {
            const value = row.getAttribute('data-operation-value');
            if (value) {
                if (!rowsByValue[value]) {
                    rowsByValue[value] = [];
                }
                rowsByValue[value].push(row);
            }
        });

        if (show) {
            // 显示所有字形标记行
            numberedRows.forEach(row => {
                row.classList.remove('collapsed');
            });
        } else {
            // 折叠：对每个字形标记值，保留第一行，隐藏其他行
            for (const value in rowsByValue) {
                const rows = rowsByValue[value];
                if (rows.length > 0) {
                    const firstRow = rows[0];
                    const otherRows = rows.slice(1);

                    firstRow.classList.remove('collapsed'); // 确保第一行显示
                    otherRows.forEach(row => {
                        row.classList.add('collapsed');
                    });
                }
            }
        }

        const manuscriptId = container.dataset.manuscriptId;

        // 检查是否有个性化设置
        const hasCustomSetting = localStorage.getItem(`manuscript_global_numbered_${manuscriptId}`) !== null;

        // 如果正在加载写卷设置或有个性化设置，跳过同步
        if (window.isLoadingManuscriptSettings || hasCustomSetting) {
            return;
        }

        // 同步写卷的全局开关
        const globalCheckbox = document.querySelector(`.global-mark-toggle[data-manuscript-id="${manuscriptId}"][data-type="numbered"]`);
        if (globalCheckbox && globalCheckbox.checked !== show) {
            globalCheckbox.checked = show;
        }
    });

    localStorage.setItem('global_show_numbered', show.toString());
    syncAllLocalMarkCheckboxes();
    manuscriptContainers.forEach(container => updateCollapsedCount(container.dataset.manuscriptId, 'numbered'));
}

function toggleGlobalStatusMarkedRows() {
    const checkbox = document.getElementById('global_show_status_marked');
    const show = checkbox.checked;

    document.querySelectorAll('.status-marked-row').forEach(row => {
        if (show) {
            row.classList.remove('collapsed');
        } else {
            row.classList.add('collapsed');
        }
    });

    localStorage.setItem('global_show_status_marked', show.toString());
}

function toggleGlobalDamagedRows() {
    const checkbox = document.getElementById('global_show_damaged');
    const show = checkbox.checked;

    document.querySelectorAll('.damaged-row').forEach(row => {
        if (show) {
            row.classList.remove('collapsed');
        } else {
            row.classList.add('collapsed');
        }
    });

    // 更新所有按钮状态
    document.querySelectorAll('.damaged-toggle-btn').forEach(btn => {
        if (show) {
            btn.textContent = '隐藏残损';
            btn.classList.remove('active');
        } else {
            btn.textContent = '显示残损';
            btn.classList.add('active');
        }
    });

    // 同步写卷的全局开关
    const manuscriptContainers = document.querySelectorAll('[data-manuscript-id]');
    manuscriptContainers.forEach(container => {
        const manuscriptId = container.dataset.manuscriptId;

        // 检查是否有个性化设置
        const hasCustomSetting = localStorage.getItem(`manuscript_global_damaged_${manuscriptId}`) !== null;

        // 如果正在加载写卷设置或有个性化设置，跳过同步
        if (window.isLoadingManuscriptSettings || hasCustomSetting) {
            return;
        }

        const globalCheckbox = document.querySelector(`.global-mark-toggle[data-manuscript-id="${manuscriptId}"][data-type="damaged"]`);
        if (globalCheckbox && globalCheckbox.checked !== show) {
            globalCheckbox.checked = show;
        }
        updateCollapsedCount(manuscriptId, 'damaged');
    });

    localStorage.setItem('global_show_damaged', show.toString());
    syncAllLocalMarkCheckboxes();
}

function toggleGlobalMisplacedRows() {
    const checkbox = document.getElementById('global_show_misplaced');
    const show = checkbox.checked;

    document.querySelectorAll('.misplaced-row').forEach(row => {
        if (show) {
            row.classList.remove('collapsed');
        } else {
            row.classList.add('collapsed');
        }
    });

    // 更新所有按钮状态
    document.querySelectorAll('.misplaced-toggle-btn').forEach(btn => {
        if (show) {
            btn.textContent = '隐藏误入';
            btn.classList.remove('active');
        } else {
            btn.textContent = '显示误入';
            btn.classList.add('active');
        }
    });

    // 同步写卷的全局开关
    const manuscriptContainers = document.querySelectorAll('[data-manuscript-id]');
    manuscriptContainers.forEach(container => {
        const manuscriptId = container.dataset.manuscriptId;

        // 检查是否有个性化设置
        const hasCustomSetting = localStorage.getItem(`manuscript_global_misplaced_${manuscriptId}`) !== null;

        // 如果正在加载写卷设置或有个性化设置，跳过同步
        if (window.isLoadingManuscriptSettings || hasCustomSetting) {
            return;
        }

        const globalCheckbox = document.querySelector(`.global-mark-toggle[data-manuscript-id="${manuscriptId}"][data-type="misplaced"]`);
        if (globalCheckbox && globalCheckbox.checked !== show) {
            globalCheckbox.checked = show;
        }
        updateCollapsedCount(manuscriptId, 'misplaced');
    });

    localStorage.setItem('global_show_misplaced', show.toString());
    syncAllLocalMarkCheckboxes();
}

function loadGlobalMarkSettings() {
    // 加载全局无标记行设置
    const showUnmarked = localStorage.getItem('global_show_unmarked');
    if (showUnmarked === 'false') {
        const checkbox = document.getElementById('global_show_unmarked');
        if (checkbox) {
            checkbox.checked = false;
            toggleGlobalUnmarkedRows();
        }
    }

    // 加载全局字形标记行设置
    const showNumbered = localStorage.getItem('global_show_numbered');
    if (showNumbered === 'false') {
        const checkbox = document.getElementById('global_show_numbered');
        if (checkbox) {
            checkbox.checked = false;
            toggleGlobalNumberedRows();
        }
    }

    // 加载全局状况/正误标记行设置
    const showStatusMarked = localStorage.getItem('global_show_status_marked');
    if (showStatusMarked === 'false') {
        const checkbox = document.getElementById('global_show_status_marked');
        if (checkbox) {
            checkbox.checked = false;
            toggleGlobalStatusMarkedRows();
        }
    }

    // 加载全局残损行设置
    const showDamaged = localStorage.getItem('global_show_damaged');
    if (showDamaged === 'false') {
        const checkbox = document.getElementById('global_show_damaged');
        if (checkbox) {
            checkbox.checked = false;
            toggleGlobalDamagedRows();
        }
    }

    // 加载全局误入行设置
    const showMisplaced = localStorage.getItem('global_show_misplaced');
    if (showMisplaced === 'false') {
        const checkbox = document.getElementById('global_show_misplaced');
        if (checkbox) {
            checkbox.checked = false;
            toggleGlobalMisplacedRows();
        }
    }

    // 初始化折叠计数
    const manuscriptIds = [...new Set(Array.from(document.querySelectorAll('[data-manuscript-id]')).map(el => el.dataset.manuscriptId))];
    manuscriptIds.forEach(id => {
        updateCollapsedCount(id, 'unmarked');
        updateCollapsedCount(id, 'numbered');
        updateCollapsedCount(id, 'damaged');
        updateCollapsedCount(id, 'misplaced');
    });

    // 加载写卷控制区显示设置
    const showControls = localStorage.getItem('show_manuscript_controls');
    if (showControls === 'false') {
        const checkbox = document.getElementById('show_manuscript_controls');
        if (checkbox) {
            checkbox.checked = false;
            toggleManuscriptControls();
        }
    }

    // 加载每个写卷的全局标记设置
    loadManuscriptGlobalMarkSettings();
}

// =====================================================
//              每个写卷的全局标记控制功能
// =====================================================

// 处理每个写卷的全局标记开关变化
function handleManuscriptGlobalMarkToggle(checkbox, type, manuscriptId) {
    const show = checkbox.checked;
    const container = document.querySelector(`[data-manuscript-id="${manuscriptId}"]`);
    if (!container) return;

    // 根据类型处理不同的标记行
    switch (type) {
        case 'unmarked':
            toggleManuscriptUnmarkedRows(manuscriptId, show);
            break;
        case 'numbered':
            toggleManuscriptNumberedRows(manuscriptId, show);
            break;
        case 'damaged':
            toggleManuscriptDamagedRows(manuscriptId, show);
            break;
        case 'misplaced':
            toggleManuscriptMisplacedRows(manuscriptId, show);
            break;
    }

    // 保存状态到localStorage
    const key = `manuscript_global_${type}_${manuscriptId}`;
    localStorage.setItem(key, show.toString());

    // 同步对应的局部开关
    syncLocalMarkCheckbox(manuscriptId, type);
}

// 切换写卷的无标记行显示
function toggleManuscriptUnmarkedRows(manuscriptId, show) {
    const container = document.querySelector(`[data-manuscript-id="${manuscriptId}"]`);
    const unmarkedRows = Array.from(container.querySelectorAll('.unmarked-row'));
    if (unmarkedRows.length === 0) return;

    if (show) {
        // 显示所有无标记行
        unmarkedRows.forEach(row => {
            row.classList.remove('collapsed');
        });
    } else {
        // 隐藏所有无标记行（保留第一行）
        const firstRow = unmarkedRows[0];
        const otherRows = unmarkedRows.slice(1);
        firstRow.classList.remove('collapsed');
        otherRows.forEach(row => {
            row.classList.add('collapsed');
        });
    }
    updateCollapsedCount(manuscriptId, 'unmarked');
}

// 切换写卷的字形标记行显示
function toggleManuscriptNumberedRows(manuscriptId, show) {
    const container = document.querySelector(`[data-manuscript-id="${manuscriptId}"]`);
    const numberedRows = Array.from(container.querySelectorAll('.numbered-row'));
    if (numberedRows.length === 0) return;

    if (show) {
        // 显示所有字形标记行
        numberedRows.forEach(row => {
            row.classList.remove('collapsed');
        });
    } else {
        // 按字形标记值分组（1、2、3、4），每组保留第一行
        const rowsByValue = {};
        numberedRows.forEach(row => {
            const value = row.getAttribute('data-operation-value');
            if (value) {
                if (!rowsByValue[value]) {
                    rowsByValue[value] = [];
                }
                rowsByValue[value].push(row);
            }
        });

        for (const value in rowsByValue) {
            const rows = rowsByValue[value];
            if (rows.length > 0) {
                const firstRow = rows[0];
                const otherRows = rows.slice(1);

                firstRow.classList.remove('collapsed'); // 确保第一行显示
                otherRows.forEach(row => {
                    row.classList.add('collapsed');
                });
            }
        }
    }
    updateCollapsedCount(manuscriptId, 'numbered');
}

// 切换写卷的残损行显示
function toggleManuscriptDamagedRows(manuscriptId, show) {
    const container = document.querySelector(`[data-manuscript-id="${manuscriptId}"]`);
    const damagedRows = container.querySelectorAll('.damaged-row');

    damagedRows.forEach(row => {
        if (show) {
            row.classList.remove('collapsed');
        } else {
            row.classList.add('collapsed');
        }
    });

    // 更新按钮状态
    const btn = document.getElementById(`damaged-btn-${manuscriptId}`);
    if (btn) {
        btn.textContent = show ? '隐藏残损' : '显示残损';
        btn.classList.toggle('active', !show);
    }
    updateCollapsedCount(manuscriptId, 'damaged');
}

// 切换写卷的误入行显示
function toggleManuscriptMisplacedRows(manuscriptId, show) {
    const container = document.querySelector(`[data-manuscript-id="${manuscriptId}"]`);
    const misplacedRows = container.querySelectorAll('.misplaced-row');

    misplacedRows.forEach(row => {
        if (show) {
            row.classList.remove('collapsed');
        } else {
            row.classList.add('collapsed');
        }
    });

    // 更新按钮状态
    const btn = document.getElementById(`misplaced-btn-${manuscriptId}`);
    if (btn) {
        btn.textContent = show ? '隐藏误入' : '显示误入';
        btn.classList.toggle('active', !show);
    }
    updateCollapsedCount(manuscriptId, 'misplaced');
}

// 加载每个写卷的全局标记设置
function loadManuscriptGlobalMarkSettings() {
    // 添加标记，表示正在加载写卷设置，防止全局设置覆盖
    window.isLoadingManuscriptSettings = true;

    const manuscriptIds = [...new Set(Array.from(document.querySelectorAll('[data-manuscript-id]')).map(el => el.dataset.manuscriptId))];

    manuscriptIds.forEach(manuscriptId => {
        ['unmarked', 'numbered', 'damaged', 'misplaced'].forEach(type => {
            const key = `manuscript_global_${type}_${manuscriptId}`;
            const setting = localStorage.getItem(key);
            const checkbox = document.querySelector(`.global-mark-toggle[data-manuscript-id="${manuscriptId}"][data-type="${type}"]`);

            if (checkbox) {
                // 只有明确设置为false时才设置为false，否则保持默认的true
                if (setting === 'false') {
                    checkbox.checked = false;
                } else {
                    checkbox.checked = true; // 默认显示
                }

                // 只在有明确设置时才应用
                if (setting !== null) {
                    const container = document.querySelector(`[data-manuscript-id="${manuscriptId}"]`);
                    const hasRows = container && container.querySelectorAll(`.${type}-row`).length > 0;

                    if (hasRows) {
                        // 延迟执行，避免与全局设置冲突
                        setTimeout(() => {
                            handleManuscriptGlobalMarkToggle(checkbox, type, manuscriptId);
                        }, 100);
                    }
                }
            }
        });
    });

    // 延迟清除标记，确保所有写卷设置都加载完成
    setTimeout(() => {
        window.isLoadingManuscriptSettings = false;
    }, 500);
}

// 初始化每个写卷的全局标记事件监听器
function initManuscriptGlobalMarkListeners() {
    document.querySelectorAll('.global-mark-toggle').forEach(checkbox => {
        checkbox.addEventListener('change', function () {
            const type = this.dataset.type;
            const manuscriptId = this.dataset.manuscriptId;
            handleManuscriptGlobalMarkToggle(this, type, manuscriptId);
        });
    });
}

// 切换写卷头部中控制区域的显示
function toggleManuscriptControls() {
    const checkbox = document.getElementById('show_manuscript_controls');
    const show = checkbox.checked;

    const controlsBlocks = document.querySelectorAll('.manuscript-controls');

    if (show) {
        controlsBlocks.forEach(ctrl => {
            ctrl.classList.remove('hidden');
        });
    } else {
        controlsBlocks.forEach(ctrl => {
            ctrl.classList.add('hidden');
        });
    }

    localStorage.setItem('show_manuscript_controls', show.toString());
}

// 折叠计数：统计当前折叠的行数并显示在局部开关旁
function updateCollapsedCount(manuscriptId, type) {
    const container = document.querySelector(`[data-manuscript-id="${manuscriptId}"]`);
    if (!container) return;

    const rows = Array.from(container.querySelectorAll(`.${type}-row`));
    if (!rows.length) return;

    let collapsed = 0;
    if (type === 'unmarked') {
        // 无标记：第一行保留，其余折叠计数
        const others = rows.slice(1);
        collapsed = others.filter(r => r.classList.contains('collapsed')).length;
    } else if (type === 'numbered') {
        // 字形标记：除每块首行外的折叠数
        const byValue = {};
        rows.forEach(r => {
            const val = r.getAttribute('data-operation-value') || '__none';
            if (!byValue[val]) byValue[val] = [];
            byValue[val].push(r);
        });
        Object.values(byValue).forEach(list => {
            if (list.length > 1) {
                const rest = list.slice(1);
                collapsed += rest.filter(r => r.classList.contains('collapsed')).length;
            }
        });
    } else {
        collapsed = rows.filter(r => r.classList.contains('collapsed')).length;
    }

    const badge = document.querySelector(`.collapsed-count[data-manuscript-id="${manuscriptId}"][data-type="${type}"]`);
    if (badge) {
        if (collapsed > 0) {
            badge.textContent = collapsed;
            badge.style.display = 'inline-flex';
        } else {
            badge.textContent = '';
            badge.style.display = 'none';
        }
    }
}

// =====================================================
//              自動加載更多數據功能
// =====================================================

function countVisibleRows() {
    // 計算所有可見的行數（不包括隱藏的行）
    const allRows = document.querySelectorAll('tbody tr');
    let visibleCount = 0;
    allRows.forEach(row => {
        if (!row.classList.contains('collapsed') && row.offsetParent !== null) {
            visibleCount++;
        }
    });
    return visibleCount;
}

function checkAndLoadMoreIfNeeded() {
    const minVisibleRows = 500;
    const visibleRows = countVisibleRows();

    if (visibleRows < minVisibleRows && visibleRows > 0) {
        // 獲取當前的per_page值
        const urlParams = new URLSearchParams(window.location.search);
        const currentPerPage = parseInt(urlParams.get('per_page') || '500');

        // 檢測服務器是否已經限制了 per_page
        // 如果我們請求的 per_page 大於實際返回的行數（且不是最後一頁），說明可能被服務器截斷了
        // 但這裡我們簡單點，直接檢查是否已經達到一個較高的閾值，或者檢查是否已經嘗試過加載但無效

        // 獲取服務器實際返回的 per_page (如果有注入變量最好，沒有的話只能猜測或硬編碼)
        // 假設後端限制是 2000 (稍後我們會改為 3000)
        const SERVER_LIMIT = 3000;

        if (currentPerPage >= SERVER_LIMIT) {
            console.warn(`已達到服務器分頁限制 (${SERVER_LIMIT})，停止自動加載。當前可見: ${visibleRows}`);
            return;
        }

        // 計算隱藏率
        const totalRows = document.querySelectorAll('tbody tr').length;
        const hiddenRows = totalRows - visibleRows;
        const hideRate = totalRows > 0 ? hiddenRows / totalRows : 0;

        // 計算需要加載多少數據才能達到最少可見行數
        let newPerPage;
        if (hideRate > 0.9) {
            newPerPage = Math.ceil(minVisibleRows * 10);
        } else if (hideRate > 0.5) {
            newPerPage = Math.ceil(minVisibleRows / (1 - hideRate));
        } else {
            newPerPage = Math.ceil(minVisibleRows * 2);
        }

        // 限制最大per_page
        const MAX_PER_PAGE = 50000;
        newPerPage = Math.min(newPerPage, MAX_PER_PAGE);

        // 若計算結果不大於當前 per_page，但仍然可見數不足，則再放大一次
        if (newPerPage <= currentPerPage && currentPerPage < MAX_PER_PAGE) {
            newPerPage = Math.min(Math.ceil(currentPerPage * 1.5), MAX_PER_PAGE);
        }

        // 如果新的per_page超過當前值，則重新加載
        if (newPerPage > currentPerPage) {
            // 再次檢查是否超過服務器限制
            if (newPerPage > SERVER_LIMIT) {
                newPerPage = SERVER_LIMIT;
            }

            // 如果調整後仍不大於當前值，則不加載
            if (newPerPage <= currentPerPage) {
                console.warn("無法進一步增加每頁顯示數量，停止加載");
                return;
            }

            // 檢查是否已經自動加載過且值相同（避免死循環）
            const autoLoaded = sessionStorage.getItem('auto_loaded_per_page');
            if (autoLoaded && parseInt(autoLoaded) >= newPerPage) {
                console.warn("檢測到重複的加載請求，停止加載");
                return;
            }

            sessionStorage.setItem('auto_loaded_per_page', newPerPage.toString());
            urlParams.set('per_page', newPerPage);
            urlParams.set('page', '1'); // 重置到第一頁
            window.location.href = `${window.location.pathname}?${urlParams.toString()}`;
        }
    } else if (visibleRows >= minVisibleRows) {
        // 如果可見行數足夠，清除自動加載標記
        sessionStorage.removeItem('auto_loaded_per_page');
    }
}

// 監聽折疊操作，在折疊後檢查是否需要加載更多
function setupCollapseMonitoring() {
    // 監聽所有折疊按鈕的點擊事件
    document.addEventListener('click', function (e) {
        if (e.target.classList.contains('damaged-toggle-btn') ||
            e.target.classList.contains('misplaced-toggle-btn') ||
            e.target.classList.contains('unmarked-toggle-btn') ||
            e.target.classList.contains('numbered-toggle-btn') ||
            e.target.classList.contains('status-marked-toggle-btn') ||
            e.target.closest('#global_show_unmarked') ||
            e.target.closest('#global_show_numbered') ||
            e.target.closest('#global_show_status_marked') ||
            e.target.closest('#global_show_damaged') ||
            e.target.closest('#global_show_misplaced')) {
            // 延遲檢查，等待折疊動畫完成
            setTimeout(() => {
                checkAndLoadMoreIfNeeded();
            }, 500);
        }
    });
}

// =====================================================
//              用戶/學生相關
// =====================================================
function logout() {
    fetch('/logout').then(() => {
        window.location.href = '/login';
    });
}

// =====================================================
//              輔助函數：同步局部開關
// =====================================================
function syncLocalMarkCheckbox(manuscriptId, type) {
    // 這裡可以添加代碼來同步局部的開關狀態（如果有的話）
    // 目前代碼中似乎沒有直接對應的局部 checkbox，而是通過按鈕控制
    // 如果有需要，可以在這裡實現
}

function syncAllLocalMarkCheckboxes() {
    // 同步所有寫卷的局部開關
    const manuscriptIds = [...new Set(Array.from(document.querySelectorAll('[data-manuscript-id]')).map(el => el.dataset.manuscriptId))];
    manuscriptIds.forEach(id => {
        ['unmarked', 'numbered', 'damaged', 'misplaced'].forEach(type => {
            syncLocalMarkCheckbox(id, type);
        });
    });
}
