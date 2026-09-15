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
