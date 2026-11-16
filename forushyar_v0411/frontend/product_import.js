  // PRODUCT_IMPORT_V0411_SCRIPT
  (() => {
    const importState = { preview: null, warehouses: [] };
    const numberFa = (value) => new Intl.NumberFormat("fa-IR").format(Number(value));

    function importStatus(message, kind = "") {
      const el = $("excelImportStatus");
      el.textContent = message || "";
      el.className = "status" + (kind ? " " + kind : "");
    }

    async function importFetch(path, options = {}) {
      const headers = new Headers(options.headers || {});
      headers.set("X-API-Key", apiKey());
      if (options.body && !(options.body instanceof FormData)) {
        headers.set("Content-Type", "application/json");
      }
      const response = await fetch(path, { ...options, headers });
      let data = {};
      try { data = await response.json(); } catch (_) {}
      if (!response.ok) {
        const detail = data.detail;
        const message = Array.isArray(detail)
          ? detail.map(item => item.msg || JSON.stringify(item)).join("؛ ")
          : (typeof detail === "string" ? detail : `خطای HTTP ${response.status}`);
        throw new Error(message);
      }
      return data;
    }

    $("downloadExcelTemplateBtn").addEventListener("click", async () => {
      try {
        $("downloadExcelTemplateBtn").disabled = true;
        importStatus("در حال آماده‌سازی قالب Excel...");
        const response = await fetch("/api/v1/products/import/template", {
          headers: { "X-API-Key": apiKey() }
        });
        if (!response.ok) {
          let message = `خطای HTTP ${response.status}`;
          try { const body = await response.json(); if (typeof body.detail === "string") message = body.detail; } catch (_) {}
          throw new Error(message);
        }
        const blob = await response.blob();
        const url = URL.createObjectURL(blob);
        const link = document.createElement("a");
        link.href = url;
        link.download = "forushyar-products-template-v0411.xlsx";
        document.body.append(link);
        link.click();
        link.remove();
        URL.revokeObjectURL(url);
        importStatus("قالب Excel دانلود شد.", "success");
      } catch (error) {
        importStatus(error.message, "error");
      } finally {
        $("downloadExcelTemplateBtn").disabled = false;
      }
    });

    function createCell(row, value, className = "") {
      const cell = document.createElement("td");
      cell.textContent = value == null || value === "" ? "—" : String(value);
      if (className) cell.className = className;
      row.append(cell);
      return cell;
    }

    function refreshImportConfirmState() {
      const preview = importState.preview;
      if (!preview) {
        $("confirmExcelBtn").disabled = true;
        return;
      }
      const hasErrors = preview.error_rows > 0;
      const missingMapping = preview.rows.some(row =>
        row.opening_quantity !== null && row.opening_quantity !== "" && !row.warehouse_id
      );
      $("confirmExcelBtn").disabled = hasErrors || missingMapping || preview.rows.length === 0;
      if (!hasErrors && missingMapping) {
        importStatus("برای ادامه، مقصد تمام کد انبارهای فایل را انتخاب کنید.", "error");
      } else if (hasErrors) {
        importStatus(`${numberFa(preview.error_rows)} ردیف دارای خطاست؛ هیچ کالایی ثبت نمی‌شود. خطاها را در جدول بررسی و فایل را اصلاح کنید.`, "error");
      } else {
        importStatus("اعتبارسنجی موفق است. پس از بررسی نهایی، ثبت گروهی را تأیید کنید.", "success");
      }
    }

    function renderWarehouseMappings(preview) {
      const wrapper = $("excelWarehouseMapping");
      const fields = $("excelWarehouseMappingFields");
      fields.replaceChildren();
      const codes = preview.warehouse_codes || [];
      if (!codes.length) {
        wrapper.hidden = true;
        return;
      }
      wrapper.hidden = false;
      for (const code of codes) {
        const field = document.createElement("div");
        field.className = "field";
        const label = document.createElement("label");
        label.textContent = `کد انبار در فایل: ${code}`;
        const select = document.createElement("select");
        const placeholder = document.createElement("option");
        placeholder.value = "";
        placeholder.textContent = "انتخاب انبار مقصد در فروش‌یار";
        select.append(placeholder);
        for (const warehouse of importState.warehouses) {
          const option = document.createElement("option");
          option.value = warehouse.id;
          option.textContent = `${warehouse.name} (${warehouse.code})`;
          select.append(option);
        }
        select.addEventListener("change", () => {
          for (const row of importState.preview.rows) {
            if (row.warehouse_code === code && row.opening_quantity !== null) {
              row.warehouse_id = select.value || null;
            }
          }
          refreshImportConfirmState();
        });
        field.append(label, select);
        fields.append(field);
      }
    }

    function renderExcelPreview(preview) {
      importState.preview = preview;
      const summary = $("excelImportSummary");
      summary.style.display = "block";
      summary.textContent = [
        `تعداد ردیف‌های خوانده‌شده: ${numberFa(preview.row_count)}`,
        `تعداد کالاهای یکتا: ${numberFa(preview.product_count)}`,
        `ردیف‌های بدون خطا: ${numberFa(preview.valid_rows)}`,
        `ردیف‌های دارای خطا: ${numberFa(preview.error_rows)}`
      ].join("\n");

      const tbody = $("excelPreviewBody");
      tbody.replaceChildren();
      const displayRows = preview.rows.slice(0, 300);
      for (const item of displayRows) {
        const tr = document.createElement("tr");
        createCell(tr, numberFa(item.row_number));
        createCell(tr, item.sku);
        createCell(tr, item.name);
        createCell(tr, item.unit);
        createCell(tr, item.price_toman == null ? "ثبت نمی‌شود" : numberFa(item.price_toman));
        createCell(tr, item.warehouse_code);
        createCell(tr, item.opening_quantity == null ? "ثبت نمی‌شود" : numberFa(item.opening_quantity));
        createCell(tr, item.errors && item.errors.length ? item.errors.join("؛ ") : "معتبر", item.errors && item.errors.length ? "import-error" : "import-ok");
        tbody.append(tr);
      }
      $("excelPreviewCaption").textContent = preview.rows.length > 300
        ? `نمایش ۳۰۰ ردیف اول از ${numberFa(preview.rows.length)} ردیف؛ اعتبارسنجی روی تمام ردیف‌ها انجام شده است.`
        : `تمام ${numberFa(preview.rows.length)} ردیف نمایش داده شده است.`;
      $("excelPreviewWrap").hidden = false;
      renderWarehouseMappings(preview);
      refreshImportConfirmState();
    }

    $("previewExcelBtn").addEventListener("click", async () => {
      const file = $("excelFile").files[0];
      if (!file) {
        importStatus("ابتدا فایل Excel با پسوند XLSX را انتخاب کنید.", "error");
        return;
      }
      if (!file.name.toLowerCase().endsWith(".xlsx")) {
        importStatus("فقط فایل XLSX پشتیبانی می‌شود.", "error");
        return;
      }
      try {
        $("previewExcelBtn").disabled = true;
        $("confirmExcelBtn").disabled = true;
        importStatus("در حال دریافت انبارها و اعتبارسنجی فایل؛ هنوز چیزی در پایگاه داده ثبت نمی‌شود...");
        importState.warehouses = await loadWarehouses();
        const form = new FormData();
        form.append("file", file, file.name);
        const preview = await importFetch("/api/v1/products/import/preview", { method: "POST", body: form });
        renderExcelPreview(preview);
      } catch (error) {
        importState.preview = null;
        $("excelImportSummary").style.display = "none";
        $("excelPreviewWrap").hidden = true;
        $("excelWarehouseMapping").hidden = true;
        importStatus(error.message, "error");
      } finally {
        $("previewExcelBtn").disabled = false;
      }
    });

    $("confirmExcelBtn").addEventListener("click", async () => {
      const preview = importState.preview;
      if (!preview || preview.error_rows > 0) return;
      const missingMapping = preview.rows.some(row =>
        row.opening_quantity !== null && row.opening_quantity !== "" && !row.warehouse_id
      );
      if (missingMapping) {
        importStatus("ابتدا مقصد همه کد انبارها را انتخاب کنید.", "error");
        return;
      }
      const confirmed = window.confirm(
        `تعداد ${preview.product_count} کالا و ${preview.row_count} ردیف بررسی شده است.\n` +
        "ثبت کالاها، قیمت‌ها و موجودی‌های اولیه انجام شود؟ این عملیات فقط برای SKUهای جدید است."
      );
      if (!confirmed) return;

      try {
        $("confirmExcelBtn").disabled = true;
        $("previewExcelBtn").disabled = true;
        importStatus("در حال ثبت تراکنشی کالاها، قیمت‌ها و موجودی‌های اولیه...");
        const rows = preview.rows.map(({ errors, ...row }) => row);
        const result = await importFetch("/api/v1/products/import/confirm", {
          method: "POST",
          body: JSON.stringify({ rows })
        });
        importStatus(
          `ثبت موفق بود؛ ${numberFa(result.imported_products)} کالا، ` +
          `${numberFa(result.price_records)} قیمت و ${numberFa(result.inventory_records)} موجودی ثبت شد.`,
          "success"
        );
        $("excelImportSummary").textContent = result.message;
        importState.preview = null;
        $("excelFile").value = "";
        $("excelPreviewWrap").hidden = true;
        $("excelWarehouseMapping").hidden = true;
        $("confirmExcelBtn").disabled = true;
        await loadWarehouses();
      } catch (error) {
        importStatus(error.message, "error");
        $("confirmExcelBtn").disabled = false;
      } finally {
        $("previewExcelBtn").disabled = false;
      }
    });
  })();
