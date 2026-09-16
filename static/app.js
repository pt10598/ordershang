const cart = new Map();
const filters = document.querySelectorAll('.filter');
const cards = document.querySelectorAll('.meal-card');
const storePicker = document.querySelector('#storePicker');
const cartBar = document.querySelector('#cartBar');
const cartDialog = document.querySelector('#cartDialog');
const orderForm = document.querySelector('#orderForm');
const checkoutToken = document.createElement('input');
checkoutToken.type = 'hidden';
checkoutToken.name = 'checkout_token';
checkoutToken.id = 'checkoutToken';
orderForm.append(checkoutToken);
let activeCategory = '全部';
let activeStore = '全部';

function invalidateCheckoutToken() {
  checkoutToken.value = '';
}

function ensureCheckoutToken() {
  if (checkoutToken.value) return;
  if (window.crypto?.randomUUID) checkoutToken.value = window.crypto.randomUUID();
  else {
    const bytes = new Uint8Array(24);
    window.crypto.getRandomValues(bytes);
    checkoutToken.value = [...bytes].map(value => value.toString(16).padStart(2, '0')).join('');
  }
}

function clampQty(value) {
  const parsed = Number.parseInt(value, 10);
  return Math.max(1, Math.min(Number.isFinite(parsed) ? parsed : 1, 99));
}

document.querySelectorAll('.meal-add-qty').forEach(select => {
  const stepper = document.createElement('div');
  stepper.className = 'quantity-stepper';
  stepper.innerHTML = '<button type="button" class="qty-minus" aria-label="減少數量">−</button><input class="meal-add-qty" type="number" value="1" min="1" max="99" inputmode="numeric" aria-label="輸入加入數量"><button type="button" class="qty-plus" aria-label="增加數量">＋</button>';
  select.replaceWith(stepper);
});

function applyFilters() {
  let visible = 0;
  cards.forEach(card => {
    const categoryOk = activeCategory === '全部' || card.dataset.category === activeCategory;
    const storeOk = activeStore === '全部' || card.dataset.storeId === activeStore;
    const scheduleStoreOk = availableStoreIds().includes(card.dataset.storeId);
    card.hidden = !(categoryOk && storeOk && scheduleStoreOk);
    if (!card.hidden) visible++;
  });
  document.querySelector('#emptyState').hidden = visible !== 0;
}

filters.forEach(button => button.addEventListener('click', () => {
  filters.forEach(item => item.classList.remove('active'));
  button.classList.add('active');
  activeCategory = button.dataset.filter;
  applyFilters();
}));
const pickupDates = window.ORDER_PICKUP_DATES || [];
const locations = window.ORDER_LOCATIONS || [];
const stores = window.ORDER_STORES || [];
const dateSelect = document.querySelector('#dateSelect');
const timeSelect = document.querySelector('#timeSelect');
const locationSelect = document.querySelector('#locationSelect');

function selectedDateConfig() {
  return pickupDates.find(item => item.date === dateSelect.value);
}

function selectedLocation() {
  return locations.find(item => item.id === locationSelect.value);
}

function selectedSlotKey() {
  return `${dateSelect.value}|${timeSelect.value}`;
}

function updateAvailability() {
  const location = selectedLocation();
  document.querySelector('#pickupTime').textContent = location ? `取餐：${dateSelect.value}・${timeSelect.value}・${location.name}` : '這個時段目前沒有可選地點';
  const slotSelect = document.querySelector('#pickupTimeSelect');
  slotSelect.replaceChildren(...(timeSelect.value ? [new Option(timeSelect.value, timeSelect.value)] : []));
  updateStores();
  removeUnavailableCartItems();
  applyFilters();
}

function availableStoreIds() {
  if (!selectedLocation()) return [];
  return stores.filter(store => store.active !== false && (!store.locations_configured || (store.location_ids || []).includes(locationSelect.value))).map(store => store.id);
}

function updateStores() {
  const previous = activeStore;
  const allowed = new Set(availableStoreIds());
  const available = stores.filter(store => store.active !== false && allowed.has(store.id));
  if (previous !== '全部' && !available.some(store => store.id === previous)) activeStore = '全部';
  const choices = [{id: '全部', name: '全部店家', logo_url: '/static/store-placeholder.svg'}, ...available];
  storePicker.replaceChildren(...choices.map(store => {
    const button = document.createElement('button');
    button.type = 'button';
    button.className = `store-choice${store.id === activeStore ? ' active' : ''}`;
    button.dataset.storeId = store.id;
    const logo = document.createElement('img');
    logo.src = store.logo_url || '/static/store-placeholder.svg';
    logo.alt = '';
    logo.addEventListener('error', () => { logo.src = '/static/store-placeholder.svg'; }, {once: true});
    const name = document.createElement('strong');
    name.textContent = store.name;
    button.append(logo, name);
    button.addEventListener('click', () => {
      activeStore = store.id;
      storePicker.querySelectorAll('.store-choice').forEach(choice => choice.classList.toggle('active', choice.dataset.storeId === activeStore));
      applyFilters();
    });
    return button;
  }));
}

document.querySelectorAll('[data-meal-view]').forEach(button => button.addEventListener('click', () => {
  document.querySelectorAll('[data-meal-view]').forEach(item => item.classList.toggle('active', item === button));
  document.querySelector('#mealGrid').classList.toggle('list-view', button.dataset.mealView === 'list');
}));

function updateLocations() {
  const previous = locationSelect.value;
  const key = selectedSlotKey();
  const available = locations.filter(item => item.active !== false && (!item.availability_configured || (item.slot_keys || []).includes(key)));
  locationSelect.replaceChildren(...available.map(item => new Option(item.name, item.id)));
  if (available.some(item => item.id === previous)) locationSelect.value = previous;
  updateAvailability();
}

function taipeiDateTime() {
  const parts = Object.fromEntries(new Intl.DateTimeFormat('en-CA', {
    timeZone: 'Asia/Taipei', year: 'numeric', month: '2-digit', day: '2-digit',
    hour: '2-digit', minute: '2-digit', hourCycle: 'h23'
  }).formatToParts(new Date()).filter(part => part.type !== 'literal').map(part => [part.type, part.value]));
  return {date: `${parts.year}-${parts.month}-${parts.day}`, time: `${parts.hour}:${parts.minute}`};
}

function availablePickupSlots() {
  const config = selectedDateConfig();
  if (!config) return [];
  const now = taipeiDateTime();
  if (config.date < now.date) return [];
  return (config.pickup_slots || []).filter(slot => config.date > now.date || slot > now.time);
}

function updateTimes() {
  const previous = timeSelect.value;
  const slots = availablePickupSlots();
  timeSelect.replaceChildren(...slots.map(slot => new Option(slot, slot)));
  if (slots.includes(previous)) timeSelect.value = previous;
  updateLocations();
}

dateSelect.addEventListener('change', updateTimes);
timeSelect.addEventListener('change', updateLocations);
locationSelect.addEventListener('change', updateAvailability);
[dateSelect, timeSelect, locationSelect].forEach(field => field.addEventListener('change', invalidateCheckoutToken));
updateTimes();
window.setInterval(updateTimes, 60000);

document.querySelectorAll('.date').forEach(button => button.addEventListener('click', () => {
  document.querySelectorAll('.date').forEach(item => item.classList.remove('active'));
  button.classList.add('active');
}));

document.querySelectorAll('.favorite').forEach(button => button.addEventListener('click', () => {
  button.classList.toggle('on');
  button.textContent = button.classList.contains('on') ? '♥' : '♡';
}));

function showToast(message) {
  const toast = document.querySelector('#toast');
  toast.textContent = message;
  toast.classList.add('show');
  window.setTimeout(() => toast.classList.remove('show'), 1500);
}

function updateCart() {
  const items = [...cart.values()];
  const count = items.reduce((sum, item) => sum + item.qty, 0);
  const total = items.reduce((sum, item) => sum + item.price * item.qty, 0);
  document.querySelector('#cartCount').textContent = count;
  document.querySelector('#cartTotal').textContent = `NT$ ${total}`;
  document.querySelector('#dialogTotal').textContent = `NT$ ${total}`;
  cartBar.hidden = count === 0;
  document.querySelector('#cartItems').innerHTML = [...cart.entries()].map(([key, item]) =>
    `<div class="cart-line"><div class="cart-line-main"><strong>${item.name}${item.optionName ? `（${item.optionName}）` : ''}</strong><div class="cart-actions"><div class="quantity-stepper"><button type="button" class="qty-minus" data-cart-key="${encodeURIComponent(key)}" aria-label="減少數量">−</button><input class="cart-qty" data-cart-key="${encodeURIComponent(key)}" type="number" value="${item.qty}" min="1" max="99" inputmode="numeric" aria-label="輸入數量"><button type="button" class="qty-plus" data-cart-key="${encodeURIComponent(key)}" aria-label="增加數量">＋</button></div><button type="button" class="cart-remove" data-cart-key="${encodeURIComponent(key)}">刪除</button></div></div><strong>NT$ ${item.price * item.qty}</strong></div>`
  ).join('');
}

function setCartQty(key, quantity) {
  const item = cart.get(key);
  if (!item) return;
  item.qty = clampQty(quantity);
  cart.set(key, item);
  invalidateCheckoutToken();
  updateCart();
}

document.querySelector('#cartItems').addEventListener('change', event => {
  const input = event.target.closest('.cart-qty');
  if (input) setCartQty(decodeURIComponent(input.dataset.cartKey), input.value);
});

document.addEventListener('click', event => {
  const button = event.target.closest('.qty-minus, .qty-plus');
  if (!button) return;
  const stepper = button.closest('.quantity-stepper');
  const input = stepper?.querySelector('input');
  if (!input) return;
  const next = clampQty(Number(input.value) + (button.classList.contains('qty-plus') ? 1 : -1));
  if (button.dataset.cartKey) setCartQty(decodeURIComponent(button.dataset.cartKey), next);
  else input.value = next;
});

document.querySelector('#cartItems').addEventListener('click', event => {
  const button = event.target.closest('.cart-remove');
  if (!button) return;
  cart.delete(decodeURIComponent(button.dataset.cartKey));
  invalidateCheckoutToken();
  updateCart();
  showToast('已從購物車刪除餐點');
});

function removeUnavailableCartItems() {
  if (!locationSelect?.value) return;
  let removedCount = 0;
  cart.forEach((item, key) => {
    const storeAllowed = availableStoreIds().includes(item.storeId);
    if (!storeAllowed) {
      cart.delete(key);
      invalidateCheckoutToken();
      removedCount += item.qty;
    }
  });
  if (removedCount) {
    updateCart();
    showToast(`切換地點，已移除 ${removedCount} 份未供應餐點`);
  }
}

document.querySelectorAll('.meal-option').forEach(select => {
  select.addEventListener('change', () => {
    const option = select.selectedOptions[0];
    select.closest('.meal-card').querySelector('.meal-price').textContent = `NT$ ${option.dataset.price}`;
  });
});

document.querySelectorAll('.add-button').forEach(button => button.addEventListener('click', () => {
  const card = button.closest('.meal-card');
  const option = card.querySelector('.meal-option')?.selectedOptions[0];
  const optionName = option?.value || '';
  const key = `${button.dataset.id}::${optionName}`;
  const current = cart.get(key) || {mealId: button.dataset.id, name: button.dataset.name, optionName, price: Number(option?.dataset.price || button.dataset.price), qty: 0, storeId: card.dataset.storeId, locations: card.dataset.locations ? card.dataset.locations.split(',') : [], locationsConfigured: card.dataset.locationsConfigured === 'true'};
  const addQty = clampQty(card.querySelector('.meal-add-qty')?.value || 1);
  if (current.qty >= 99) {
    showToast('每個餐點最多99份');
    return;
  }
  const actualAdded = Math.min(addQty, 99 - current.qty);
  current.qty += actualAdded;
  cart.set(key, current);
  invalidateCheckoutToken();
  updateCart();
  showToast(actualAdded < addQty ? `已加入${actualAdded}份，累計上限99份` : `已加入 ${current.name} × ${actualAdded}`);
}));

document.querySelector('#checkoutButton').addEventListener('click', () => {
  if (!dateSelect.value || !timeSelect.value || !locationSelect.value || !selectedLocation()) {
    showToast('請先選擇可下單的日期、時間與地點');
    return;
  }
  document.querySelector('#itemsJson').value = JSON.stringify([...cart.values()].map(item => ({id: item.mealId, option_name: item.optionName, qty: item.qty})));
  document.querySelector('#orderLocation').value = locationSelect.value;
  document.querySelector('#orderDate').value = dateSelect.value;
  document.querySelector('#orderDateDisplay').value = dateSelect.value;
  document.querySelector('#orderLocationDisplay').value = selectedLocation().name;
  cartDialog.showModal();
});
document.querySelector('#closeDialog').addEventListener('click', () => cartDialog.close());

const invoiceType = document.querySelector('#invoiceType');
invoiceType.replaceChildren(new Option('收據', 'receipt'));

const paymentMethod = document.querySelector('#paymentMethod');
const paymentDropdown = document.querySelector('#paymentDropdown');
const paymentTrigger = document.querySelector('#paymentDropdownTrigger');
const paymentMenu = document.querySelector('#paymentDropdownMenu');
const paymentCurrentText = document.querySelector('#paymentCurrentText');
function closePaymentMenu() {
  paymentMenu.hidden = true;
  paymentTrigger.setAttribute('aria-expanded', 'false');
}
paymentTrigger.addEventListener('click', () => {
  const willOpen = paymentMenu.hidden;
  paymentMenu.hidden = !willOpen;
  paymentTrigger.setAttribute('aria-expanded', String(willOpen));
});
paymentMenu.querySelectorAll('.payment-option').forEach(option => option.addEventListener('click', () => {
  const value = option.dataset.payment;
  if (paymentMethod.value !== value) invalidateCheckoutToken();
  paymentMethod.value = value;
  paymentCurrentText.textContent = option.querySelector('strong').textContent;
  paymentTrigger.querySelector('img').hidden = value !== 'line_pay';
  paymentTrigger.querySelector('.onsite-icon').hidden = value === 'line_pay';
  paymentDropdown.classList.toggle('line-pay-selected', value === 'line_pay');
  paymentMenu.querySelectorAll('.payment-option').forEach(item => {
    const selected = item === option;
    item.classList.toggle('selected', selected);
    item.setAttribute('aria-selected', String(selected));
  });
  closePaymentMenu();
}));
document.addEventListener('click', event => {
  if (!paymentDropdown.contains(event.target)) closePaymentMenu();
});
document.addEventListener('keydown', event => {
  if (event.key === 'Escape') closePaymentMenu();
});

orderForm.addEventListener('input', event => {
  if (event.target !== checkoutToken) invalidateCheckoutToken();
});

orderForm.addEventListener('submit', (event) => {
  const location = selectedLocation();
  const pickupTime = document.querySelector('#pickupTimeSelect').value;
  const total = document.querySelector('#dialogTotal').textContent;
  const confirmed = window.confirm(
    `請再次確認訂單資料：\n\n取餐日期：${dateSelect.value}\n取餐地點：${location?.name || ''}\n取餐時間：${pickupTime}\n付款：${paymentCurrentText.textContent}\n憑證：收據\n訂單金額：${total}\n\n確認送出訂單嗎？`
  );
  if (!confirmed) {
    event.preventDefault();
    return;
  }
  ensureCheckoutToken();
  const submitButton = event.currentTarget.querySelector('button[type="submit"]');
  submitButton.disabled = true;
  submitButton.textContent = document.querySelector('#paymentMethod').value === 'line_pay' ? '前往 LINE Pay…' : '訂單送出中…';
});

window.addEventListener('pageshow', () => {
  const submitButton = orderForm.querySelector('button[type="submit"]');
  submitButton.disabled = false;
  submitButton.textContent = '送出訂單';
});
