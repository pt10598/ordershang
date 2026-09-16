const carrierPattern = /\/[0-9A-Z.+-]{7}/;

function updateStatusColor(select) {
  select.dataset.status = select.value;
}

document.querySelectorAll('.status-select').forEach(select => {
  updateStatusColor(select);
  select.addEventListener('change', () => updateStatusColor(select));
});

function alignOrderItemRows() {
  document.querySelectorAll('.order-table tbody tr').forEach(row => {
    const columns = ['.order-item-stores', '.order-item-names', '.order-item-qtys', '.order-item-subtotals']
      .map(selector => [...row.querySelectorAll(`${selector} .order-item-row`)]);
    const rowCount = Math.max(0, ...columns.map(items => items.length));
    columns.flat().forEach(item => { item.style.minHeight = ''; });
    for (let index = 0; index < rowCount; index += 1) {
      const items = columns.map(column => column[index]).filter(Boolean);
      const height = Math.max(...items.map(item => item.getBoundingClientRect().height));
      items.forEach(item => { item.style.minHeight = `${height}px`; });
    }
  });
}

alignOrderItemRows();
window.addEventListener('resize', alignOrderItemRows);

document.querySelectorAll('.order-table').forEach(table => {
  const body = table.tBodies[0];
  const buttons = [...table.querySelectorAll('.table-sort')];
  buttons.forEach((button, columnIndex) => {
    button.addEventListener('click', () => {
      const nextDirection = button.dataset.direction === 'asc' ? 'desc' : 'asc';
      buttons.forEach(other => {
        delete other.dataset.direction;
        other.removeAttribute('aria-sort');
      });
      button.dataset.direction = nextDirection;
      button.setAttribute('aria-sort', nextDirection === 'asc' ? 'ascending' : 'descending');
      const type = button.dataset.type || 'text';
      const rows = [...body.rows].filter(row => !row.querySelector('.table-empty'));
      rows.sort((rowA, rowB) => {
        const valueA = rowA.cells[columnIndex]?.dataset.sortValue ?? rowA.cells[columnIndex]?.textContent.trim() ?? '';
        const valueB = rowB.cells[columnIndex]?.dataset.sortValue ?? rowB.cells[columnIndex]?.textContent.trim() ?? '';
        let result;
        if (type === 'number') result = Number(valueA) - Number(valueB);
        else if (type === 'date') result = new Date(valueA).getTime() - new Date(valueB).getTime();
        else result = valueA.localeCompare(valueB, 'zh-Hant', {numeric: true, sensitivity: 'base'});
        return nextDirection === 'asc' ? result : -result;
      });
      rows.forEach(row => body.appendChild(row));
      alignOrderItemRows();
      sessionStorage.setItem('adminOrderSort', JSON.stringify({columnIndex, direction: nextDirection}));
    });
  });
  try {
    const savedSort = JSON.parse(sessionStorage.getItem('adminOrderSort') || 'null');
    const savedButton = buttons[savedSort?.columnIndex];
    if (savedButton) {
      savedButton.dataset.direction = savedSort.direction === 'desc' ? 'asc' : 'desc';
      savedButton.click();
    }
  } catch (error) {
    sessionStorage.removeItem('adminOrderSort');
  }
});

document.querySelectorAll('form[action*="/admin/orders/"][action$="/status"]').forEach(form => {
  form.addEventListener('submit', () => {
    let returnInput = form.querySelector('input[name="return_to"]');
    if (!returnInput) {
      returnInput = document.createElement('input');
      returnInput.type = 'hidden';
      returnInput.name = 'return_to';
      form.appendChild(returnInput);
    }
    returnInput.value = `${location.pathname}${location.search}`;
    sessionStorage.setItem('adminOrderScrollY', String(window.scrollY));
  });
});

const savedScrollY = Number(sessionStorage.getItem('adminOrderScrollY'));
if (Number.isFinite(savedScrollY) && savedScrollY > 0) {
  requestAnimationFrame(() => window.scrollTo({top: savedScrollY}));
  sessionStorage.removeItem('adminOrderScrollY');
}

function loadBarcodeLibrary() {
  if (window.JsBarcode) return Promise.resolve();
  return new Promise((resolve, reject) => {
    const existing = document.querySelector('script[data-jsbarcode]');
    if (existing) {
      existing.addEventListener('load', resolve, {once: true});
      existing.addEventListener('error', reject, {once: true});
      return;
    }
    const script = document.createElement('script');
    script.src = 'https://cdn.jsdelivr.net/npm/jsbarcode@3.11.6/dist/JsBarcode.all.min.js';
    script.dataset.jsbarcode = 'true';
    script.onload = resolve;
    script.onerror = reject;
    document.head.appendChild(script);
  });
}

function createBarcodeDialog() {
  const dialog = document.createElement('dialog');
  dialog.className = 'barcode-dialog';
  dialog.innerHTML = '<div class="barcode-dialog-head"><h2>手機載具條碼</h2><button type="button" aria-label="關閉">×</button></div><div class="barcode-dialog-body"><svg aria-label="手機載具條碼"></svg><div class="barcode-text"></div><p class="barcode-help">請將條碼對準發票設備掃描</p></div>';
  dialog.querySelector('button').addEventListener('click', () => dialog.close());
  dialog.addEventListener('click', event => {
    if (event.target === dialog) dialog.close();
  });
  document.body.appendChild(dialog);
  return dialog;
}

const barcodeDialog = createBarcodeDialog();

async function showCarrierBarcode(value) {
  barcodeDialog.querySelector('.barcode-text').textContent = value;
  barcodeDialog.querySelector('.barcode-help').textContent = '請將條碼對準發票設備掃描';
  barcodeDialog.showModal();
  const svg = barcodeDialog.querySelector('svg');
  svg.replaceChildren();
  try {
    await loadBarcodeLibrary();
    window.JsBarcode(svg, value, {format: 'CODE39', width: 3, height: 120, margin: 20, displayValue: false});
  } catch (error) {
    barcodeDialog.querySelector('.barcode-help').textContent = '條碼載入失敗，請確認網路後重新開啟。';
  }
}

document.querySelectorAll('.order-table td, .order-card p').forEach(element => {
  const match = element.textContent.match(carrierPattern);
  if (!match || !element.textContent.includes('手機載具')) return;
  const value = match[0];
  const prefix = element.matches('.order-card p') ? '發票：' : '';
  element.replaceChildren(document.createTextNode(prefix));
  const button = document.createElement('button');
  button.type = 'button';
  button.className = 'barcode-button';
  button.textContent = `📱 ${value}（顯示條碼）`;
  button.addEventListener('click', () => showCarrierBarcode(value));
  element.appendChild(button);
});

const multiDatePicker = document.querySelector('#multiDatePicker');
if (multiDatePicker) {
  const selectedDates = new Set();
  const valuesInput = document.querySelector('#multiDateValues');
  const grid = multiDatePicker.querySelector('[data-calendar-grid]');
  const title = multiDatePicker.querySelector('[data-calendar-title]');
  const summary = multiDatePicker.querySelector('[data-calendar-summary]');
  const today = new Date();
  today.setHours(0, 0, 0, 0);
  let viewDate = new Date(today.getFullYear(), today.getMonth(), 1);
  let rangeStart = null;

  const toDateKey = date => `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, '0')}-${String(date.getDate()).padStart(2, '0')}`;
  const fromDateKey = value => {
    const [year, month, day] = value.split('-').map(Number);
    return new Date(year, month - 1, day);
  };

  function selectedRanges() {
    const dates = [...selectedDates].sort();
    if (!dates.length) return '尚未選擇日期';
    const ranges = [];
    let start = dates[0];
    let end = dates[0];
    for (const value of dates.slice(1)) {
      const expected = fromDateKey(end);
      expected.setDate(expected.getDate() + 1);
      if (toDateKey(expected) === value) end = value;
      else {
        ranges.push(start === end ? start : `${start}～${end}`);
        start = value;
        end = value;
      }
    }
    ranges.push(start === end ? start : `${start}～${end}`);
    return `已選 ${dates.length} 天：${ranges.join('、')}`;
  }

  function syncSelectedDates() {
    valuesInput.value = [...selectedDates].sort().join(',');
    summary.textContent = rangeStart ? `開始日 ${rangeStart}，請再點選結束日` : selectedRanges();
  }

  function chooseDate(date) {
    const key = toDateKey(date);
    if (!rangeStart) {
      if (selectedDates.has(key)) selectedDates.delete(key);
      else {
        selectedDates.add(key);
        rangeStart = key;
      }
    } else {
      let cursor = fromDateKey(rangeStart);
      const end = fromDateKey(key);
      if (cursor > end) [cursor] = [end];
      const last = fromDateKey(rangeStart) > end ? fromDateKey(rangeStart) : end;
      while (cursor <= last) {
        if (cursor >= today) selectedDates.add(toDateKey(cursor));
        cursor.setDate(cursor.getDate() + 1);
      }
      rangeStart = null;
    }
    renderCalendar();
  }

  function renderCalendar() {
    const year = viewDate.getFullYear();
    const month = viewDate.getMonth();
    title.textContent = `${year} 年 ${month + 1} 月`;
    grid.replaceChildren();
    const firstWeekday = new Date(year, month, 1).getDay();
    for (let index = 0; index < firstWeekday; index += 1) grid.append(document.createElement('span'));
    const days = new Date(year, month + 1, 0).getDate();
    for (let day = 1; day <= days; day += 1) {
      const date = new Date(year, month, day);
      const key = toDateKey(date);
      const button = document.createElement('button');
      button.type = 'button';
      button.textContent = day;
      button.disabled = date < today;
      button.classList.toggle('selected', selectedDates.has(key));
      button.classList.toggle('range-start', rangeStart === key);
      button.addEventListener('click', () => chooseDate(date));
      grid.append(button);
    }
    syncSelectedDates();
  }

  multiDatePicker.querySelector('[data-calendar-prev]').addEventListener('click', () => {
    viewDate = new Date(viewDate.getFullYear(), viewDate.getMonth() - 1, 1);
    renderCalendar();
  });
  multiDatePicker.querySelector('[data-calendar-next]').addEventListener('click', () => {
    viewDate = new Date(viewDate.getFullYear(), viewDate.getMonth() + 1, 1);
    renderCalendar();
  });
  multiDatePicker.querySelector('[data-calendar-clear]').addEventListener('click', () => {
    selectedDates.clear();
    rangeStart = null;
    renderCalendar();
  });
  document.querySelector('#multiDateForm').addEventListener('submit', event => {
    if (!selectedDates.size) {
      event.preventDefault();
      window.alert('請先在日曆選擇至少一個取餐日期。');
    }
  });
  renderCalendar();
}
