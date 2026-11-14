<input id="sku" required maxlength="80" value="580" placeholder="مثلاً 580">
        </div>
        <div class="field">
          <label for="name">نام کالا *</label>
          <input id="name" required maxlength="240" value="رابیتس ۹ ستون" placeholder="مثلاً رابیتس ۱۳ ستون">
        </div>
        <div class="field">
          <label for="unit">واحد / وزن فروش *</label>
          <input id="unit" required maxlength="24" value="۴۰۰ گرم" placeholder="مثلاً ۶۶۰ گرم">
        </div>
        <div class="field">
          <label for="price">قیمت روز (تومان)</label>
          <input id="price" type="number" min="0" step="1" value="195000" placeholder="مثلاً 195000">
        </div>
        <div class="field full">
          <label for="description">توضیحات / مشخصات</label>
          <textarea id="description" placeholder="مشخصات تکمیلی کالا">۹ ستون، وزن ۴۰۰ گرم</textarea>
        </div>
      </div>

      <h2 style="margin-top:24px">۳. انبار و موجودی اولیه <span class="badge">اختیاری</span></h2>
      <div class="field">
        <label for="warehouse">انبار</label>
        <select id="warehouse"><option value="">ابتدا کلید API را وارد و انبارها را دریافت کنید</option></select>
        <div class="hint">برای ثبت موجودی اولیه، انبار و تعداد واقعی را با هم انتخاب کنید. اگر تعداد را خالی بگذارید، موجودی برای این کالا ثبت نمی‌شود.</div>
      </div>
      <div class="field" style="margin-top:13px">
        <label for="quantity">موجودی اولیه (تعداد / واحد کالا)</label>
        <input id="quantity" type="number" min="0" step="0.001" placeholder="مثلاً 250 — در صورت نامشخص بودن خالی بگذارید">
      </div>
      <div class="actions">
        <button id="addWarehouseToggle" class="secondary" type="button">+ افزودن انبار جدید</button>
        <button id="saveBtn" type="submit">ثبت کالا و قیمت</button>
      </div>
      <div id="result" class="result"></div>
      <div id="formStatus" class="status"></div>
    </form>

    <div id="newWarehousePanel" class="subpanel" hidden>
      <h2 style="margin-bottom:12px">افزودن انبار</h2>
      <div class="grid">
        <div class="field">
          <label for="warehouseCode">کد انبار *</label>
          <input id="warehouseCode" maxlength="40" placeholder="مثلاً TEH-AHAN-MEKAN">
        </div>
        <div class="field">
          <label for="warehouseName">نام انبار *</label>
          <input id="warehouseName" maxlength="160" value="تهران - آهن مکان" placeholder="نام انبار">
        </div>
        <div class="field full">
          <label for="branchName">شعبه (اختیاری)</label>
          <input id="branchName" maxlength="160" placeholder="مثلاً تهران">
        </div>
      </div>
      <div class="actions">
        <button id="createWarehouseBtn" type="button">ثبت انبار</button>
        <button id="cancelWarehouseBtn" type="button" class="secondary">انصراف</button>
      </div>
      <div id="warehouseStatus" class="status"></div>
    </div>
  </section>
  <div class="muted">نشانی این صفحه: <b>http://127.0.0.1:8000/admin/products</b></div>
</main>
<script>
const $ = (id) => document.getElementById(id);
const keyInput = $("apiKey");
const warehouseSelect = $("warehouse");

function setStatus(id, message, kind = "") {
  const el = $(id);
  el.textContent = message || "";
  el.className = "status" + (kind ? " " + kind : "");
}
function apiKey() {
  const value = keyInput.value.trim();
  if (!value) throw new Error("ابتدا کلید API را وارد کنید.");
  sessionStorage.setItem("forushyar_product_entry_api_key", value);
  return value;
}
async function api(path, options = {}) {
  const headers = new Headers(options.headers || {});
  headers.set("X-API-Key", apiKey());
  if (options.body) headers.set("Content-Type", "application/json");
  const response = await fetch(path, { ...options, headers });
  let data = {};
  try { data = await response.json(); } catch (_) {}
  if (!response.ok) {
    const detail = data.detail;
    const message = Array.isArray(detail) ? detail.map(x => x.msg || JSON.stringify(x)).join("؛ ") : (detail || `خطای HTTP ${response.status}`);
    throw new Error(message);
  }
  return data;
}
async function loadWarehouses() {
  const items = await api("/api/v1/warehouses");
  warehouseSelect.replaceChildren();
  const placeholder = document.createElement("option");
  placeholder.value = "";
  placeholder.textContent = items.length ? "انتخاب انبار" : "هنوز انباری ثبت نشده است";
  warehouseSelect.append(placeholder);
  for (const item of items) {
    const option = document.createElement("option");
    option.value = item.id;
    option.textContent = `${item.name} (${item.code})`;
    warehouseSelect.append(option);
  }
  return items;
}

$("connectBtn").addEventListener("click", async () => {
  try {
    $("connectBtn").disabled = true;
    const items = await loadWarehouses();
    setStatus("connectionStatus", `اتصال موفق بود؛ ${items.length} انبار دریافت شد.`, "success");
  } catch (error) {
    setStatus("connectionStatus", error.message, "error");
  } finally { $("connectBtn").disabled = false; }
});

$("addWarehouseToggle").addEventListener("click", () => {
  $("newWarehousePanel").hidden = !$("newWarehousePanel").hidden;
});
$("cancelWarehouseBtn").addEventListener("click", () => { $("newWarehousePanel").hidden = true; });

$("createWarehouseBtn").addEventListener("click", async () => {
  try {
    $("createWarehouseBtn").disabled = true;
    setStatus("warehouseStatus", "در حال ثبت انبار...");
    const payload = {
      code: $("warehouseCode").value.trim(),
      name: $("warehouseName").value.trim(),
      branch_name: $("branchName").value.trim() || null
    };
    const created = await api("/api/v1/warehouses", { method: "POST", body: JSON.stringify(payload) });
    await loadWarehouses();
    warehouseSelect.value = created.id;
    setStatus("warehouseStatus", `انبار «${created.name}» ثبت شد و انتخاب گردید.`, "success");
    $("newWarehousePanel").hidden = true;
  } catch (error) {
    setStatus("warehouseStatus", error.message, "error");
  } finally { $("createWarehouseBtn").disabled = false; }
});

$("productForm").addEventListener("submit", async (event) => {
  event.preventDefault();
  const quantityText = $("quantity").value.trim();
  const warehouseId = warehouseSelect.value || null;
  if (quantityText !== "" && !warehouseId) {
    setStatus("formStatus", "برای ثبت موجودی اولیه باید انبار را انتخاب کنید.", "error");
    return;
  }
  const priceText = $("price").value.trim();
  const payload = {
    sku: $("sku").value.trim(),
    name: $("name").value.trim(),
    unit: $("unit").value.trim(),
    description: $("description").value.trim() || null,
    price_toman: priceText === "" ? null : priceText,
    warehouse_id: warehouseId,
    opening_quantity: quantityText === "" ? null : quantityText,
