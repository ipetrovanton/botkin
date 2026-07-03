/* ==========================================================================
   BOTkin — клиент кабинета пациента (Alpine.js)
   Без бандлера, без зависимостей кроме заендоренного Alpine. Один компонент
   `cabinet()` хранит состояние экранов и общается с /api/* того же origin.
   ========================================================================== */

const PAGE_SIZE = 20;

const TYPE_LABELS = {
  analysis: "Анализы",
  doctor_report: "Заключение",
  certificate: "Справка",
  unknown: "Документ",
};
const STATUS_LABELS = {
  received: "Принят",
  processing: "В обработке",
  recognizing: "Распознавание",
  normalizing: "Нормализация",
  extracted: "Готов",
  failed: "Ошибка",
};
// Прогресс по стадиям для прогресс-бара загрузки (0–100%).
const STAGE_PROGRESS = {
  received: 12, processing: 25, recognizing: 45, normalizing: 75, extracted: 100, failed: 100,
};

function cabinet() {
  return {
    PAGE_SIZE,
    screen: "overview",
    theme: localStorage.getItem("botkin.theme") || "dark",

    // Идентификация (demo): сохраняем между сессиями, дефолт — demo-аккаунт.
    tgUserId: localStorage.getItem("botkin.tgUserId") || "113521070",
    demoUserId: localStorage.getItem("botkin.tgUserId") || "113521070",
    user: null,

    // Данные
    stats: {},
    analytesCount: 0,
    clinics: [],
    doctors: [],
    docs: { items: [], total: 0 },
    current: { doc: null, kind: null, labs: [], reports: [] },
    reports: { items: [], total: 0 },
    dynamics: { points: [], analyte: "", unit: "", ref_low: null, ref_high: null },

    // Фильтры документов
    showFilters: false,
    filters: { doc_type: "", clinic: "", doctor: "", status: "", date_from: "", date_to: "", q: "", offset: 0 },
    repFilters: { doctor: "", clinic: "", date_from: "", date_to: "" },

    // Аналитика
    analytePicked: "",
    analyteQuery: "",
    analyteAll: [],
    analyteSuggestions: [],
    analyteFocused: false,

    // Загрузка
    queue: [],
    dragOver: false,
    uploading: false,
    _seq: 0,

    // UI-состояние
    loading: { stats: false, docs: false, doc: false, reports: false, dynamics: false },
    toasts: [],

    // ===== Инициализация =====
    async init() {
      document.documentElement.setAttribute("data-theme", this.theme);
      // Подгрузим всё параллельно — кабинету нужны и сводка, и селекторы фильтров.
      await Promise.all([this.loadStats(), this.loadSelectors()]);
      // Закрытие подсказок анализов по клику вне.
      document.addEventListener("click", (e) => {
        if (!e.target.closest(".analyte-picker")) this.analyteFocused = false;
      });
    },

    // ===== API-клиент =====
    async api(path, opts = {}) {
      const headers = { "X-Telegram-User-Id": this.tgUserId, ...(opts.headers || {}) };
      const res = await fetch(path, { ...opts, headers });
      if (res.status === 404) return null;
      if (!res.ok) throw new Error(`${res.status} ${await res.text().catch(() => "")}`);
      const ct = res.headers.get("content-type") || "";
      return ct.includes("application/json") ? res.json() : res.text();
    },

    toast(msg, type = "info") {
      const id = ++this._seq;
      this.toasts.push({ id, msg, type });
      setTimeout(() => { this.toasts = this.toasts.filter((t) => t.id !== id); }, 3200);
    },

    toggleTheme() {
      this.theme = this.theme === "dark" ? "light" : "dark";
      document.documentElement.setAttribute("data-theme", this.theme);
      localStorage.setItem("botkin.theme", this.theme);
      // Перерисовать график при смене темы — цвета берутся из CSS-переменных.
      if (this.dynamics.points.length) this.$nextTick(() => this.renderChart());
    },

    // ===== Идентификация =====
    setDemoUser() {
      const v = String(this.demoUserId || "").trim();
      if (!v) { this.toast("Введите идентификатор", "error"); return; }
      this.tgUserId = v;
      localStorage.setItem("botkin.tgUserId", v);
      this.toast("Идентификатор применён", "success");
      this.screen = "overview";
      this.loadStats(); this.loadSelectors(); this.loadDocs();
    },
    useDemoUser() {
      this.demoUserId = "113521070";
      this.setDemoUser();
    },

    // ===== Загрузка данных =====
    async loadStats() {
      this.loading.stats = true;
      try {
        const [s, me] = await Promise.all([
          this.api("/api/stats"),
          this.api("/api/me"),
        ]);
        this.stats = s || {};
        this.user = me;
        // Отдельный лёгкий запрос за списком анализов — только ради счётчика на дашборде.
        this.api("/api/analytes").then((a) => { this.analytesCount = (a || []).length; });
      } catch (e) { this.toast("Не удалось загрузить сводку", "error"); console.error(e); }
      finally { this.loading.stats = false; }
    },

    async loadSelectors() {
      try {
        const [cl, dr] = await Promise.all([this.api("/api/clinics"), this.api("/api/doctors")]);
        this.clinics = cl || [];
        this.doctors = dr || [];
        // Кэш анализов для подсказок динамики.
        this.api("/api/analytes").then((a) => { this.analyteAll = a || []; });
      } catch (e) { console.error("selectors", e); }
    },

    async loadDocs() {
      this.loading.docs = true;
      try {
        const p = new URLSearchParams();
        for (const [k, v] of Object.entries(this.filters)) {
          if (v !== "" && v !== null && v !== undefined) p.set(k, v);
        }
        p.set("limit", PAGE_SIZE);
        const data = await this.api(`/api/documents?${p}`);
        this.docs = data || { items: [], total: 0 };
      } catch (e) { this.toast("Ошибка загрузки документов", "error"); console.error(e); }
      finally { this.loading.docs = false; }
    },

    async openDoc(id) {
      this.screen = "document";
      this.loading.doc = true;
      this.current = { doc: null, kind: null, labs: [], reports: [] };
      try {
        const data = await this.api(`/api/documents/${id}`);
        if (!data) { this.toast("Документ не найден", "error"); this.screen = "documents"; return; }
        // API возвращает {document, kind, labs, reports}; HTML читает current.doc — мапим.
        this.current = {
          doc: data.document, kind: data.kind, labs: data.labs, reports: data.reports,
        };
      } catch (e) { this.toast("Ошибка загрузки документа", "error"); console.error(e); }
      finally { this.loading.doc = false; }
    },

    async loadReports() {
      this.loading.reports = true;
      try {
        const p = new URLSearchParams();
        for (const [k, v] of Object.entries(this.repFilters)) if (v) p.set(k, v);
        p.set("limit", 100);
        const data = await this.api(`/api/reports?${p}`);
        this.reports = data || { items: [], total: 0 };
      } catch (e) { this.toast("Ошибка загрузки заключений", "error"); console.error(e); }
      finally { this.loading.reports = false; }
    },

    // ===== Фильтры документов =====
    hasFilters() {
      return Object.entries(this.filters).some(([k, v]) => k !== "offset" && v !== "" && v !== null);
    },
    clearFilters() {
      this.filters = { doc_type: "", clinic: "", doctor: "", status: "", date_from: "", date_to: "", q: "", offset: 0 };
      this.loadDocs();
    },
    resetPage() { this.filters.offset = 0; },
    prevPage() { this.filters.offset = Math.max(0, this.filters.offset - PAGE_SIZE); this.loadDocs(); },
    nextPage() { this.filters.offset += PAGE_SIZE; this.loadDocs(); },
    pageInfo() {
      const from = this.docs.total ? this.filters.offset + 1 : 0;
      const to = Math.min(this.filters.offset + this.docs.items.length, this.docs.total);
      return `${from}–${to} из ${this.docs.total}`;
    },

    // ===== Аналитика / динамика =====
    filterAnalytes() {
      const q = this.analyteQuery.trim().toLowerCase();
      this.analyteSuggestions = q
        ? this.analyteAll.filter((a) => a.toLowerCase().includes(q)).slice(0, 8)
        : this.analyteAll.slice(0, 8);
      this.analyteFocused = true;
    },
    async pickAnalyte(name) {
      this.analytePicked = name;
      this.analyteQuery = name;
      this.analyteFocused = false;
      this.loading.dynamics = true;
      try {
        const data = await this.api(`/api/dynamics?name=${encodeURIComponent(name)}`);
        this.dynamics = data || { points: [], analyte: name, unit: "", ref_low: null, ref_high: null };
        this.$nextTick(() => this.renderChart());
      } catch (e) {
        this.dynamics = { points: [], analyte: name, unit: "", ref_low: null, ref_high: null };
        if (e?.message?.startsWith("404")) this.toast(`Нет данных по «${name}»`, "info");
        else this.toast("Ошибка загрузки динамики", "error");
      } finally { this.loading.dynamics = false; }
    },

    // SVG-график динамики: рисуется в DOM, цвета из CSS-переменных текущей темы.
    renderChart() {
      const wrap = this.$refs.chart;
      if (!wrap || !this.dynamics.points.length) return;
      const pts = this.dynamics.points;
      const W = Math.max(320, Math.min(680, wrap.clientWidth));
      const H = 280, padL = 48, padR = 16, padT = 18, padB = 40;
      const innerW = W - padL - padR, innerH = H - padT - padB;

      const vals = pts.map((p) => p.value_num);
      let lo = Math.min(...vals), hi = Math.max(...vals);
      const refLo = this.dynamics.ref_low, refHi = this.dynamics.ref_high;
      if (refLo != null) lo = Math.min(lo, refLo);
      if (refHi != null) hi = Math.max(hi, refHi);
      const span = hi - lo || 1;
      lo -= span * 0.1; hi += span * 0.1;

      const x = (i) => padL + (pts.length === 1 ? innerW / 2 : (i / (pts.length - 1)) * innerW);
      const y = (v) => padT + innerH - ((v - lo) / (hi - lo)) * innerH;

      const css = (n) => getComputedStyle(document.documentElement).getPropertyValue(n).trim();
      const cVal = css("--brand-1") || "#14B8A6";
      const cRef = css("--ok") || "#22C55E";
      const cText = css("--text-dim") || "#94A3B8";
      const cGrid = css("--border") || "rgba(255,255,255,.08)";

      const linePath = pts.map((p, i) => `${i === 0 ? "M" : "L"} ${x(i).toFixed(1)} ${y(p.value_num).toFixed(1)}`).join(" ");
      const areaPath = `${linePath} L ${x(pts.length - 1).toFixed(1)} ${(padT + innerH).toFixed(1)} L ${x(0).toFixed(1)} ${(padT + innerH).toFixed(1)} Z`;

      // Y-метки (4 деления).
      const yTicks = [0, 1, 2, 3, 4].map((i) => lo + (hi - lo) * i / 4);
      const yTicksSvg = yTicks.map((v) => {
        const yy = y(v);
        return `<line x1="${padL}" y1="${yy}" x2="${W - padR}" y2="${yy}" stroke="${cGrid}" stroke-dasharray="3 4"/>
                <text x="${padL - 8}" y="${yy + 4}" text-anchor="end" font-size="11" fill="${cText}">${fmtNum(v)}</text>`;
      }).join("");

      // X-метки дат.
      const xTicks = pts.map((p, i) => {
        if (pts.length > 8 && i % Math.ceil(pts.length / 6) !== 0 && i !== pts.length - 1) return "";
        return `<text x="${x(i)}" y="${H - padB + 20}" text-anchor="middle" font-size="11" fill="${cText}">${fmtDateShort(p.taken_at)}</text>`;
      }).join("");

      // Коридор нормы.
      const refRect = (refLo != null && refHi != null)
        ? `<rect x="${padL}" y="${y(refHi).toFixed(1)}" width="${innerW}" height="${(y(refLo) - y(refHi)).toFixed(1)}" fill="${cRef}" fill-opacity="0.12" stroke="${cRef}" stroke-dasharray="5 4" stroke-opacity="0.5"/>`
        : "";

      // Точки и hover-подписи.
      const dots = pts.map((p, i) => {
        const yy = y(p.value_num);
        const out = (refLo != null && p.value_num < refLo) || (refHi != null && p.value_num > refHi);
        const fill = out ? (css("--danger") || "#EF4444") : cVal;
        return `<circle cx="${x(i).toFixed(1)}" cy="${yy.toFixed(1)}" r="4.5" fill="${fill}" stroke="var(--surface)" stroke-width="2">
                  <title>${fmtDateShort(p.taken_at)}: ${fmtNum(p.value_num)} ${this.dynamics.unit}</title>
                </circle>`;
      }).join("");

      wrap.innerHTML = `<svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="xMidYMid meet" role="img" aria-label="Динамика показателя ${this.dynamics.analyte}">
        ${refRect}${yTicksSvg}
        <path d="${areaPath}" fill="${cVal}" fill-opacity="0.08"/>
        <path d="${linePath}" fill="none" stroke="${cVal}" stroke-width="2.6" stroke-linecap="round" stroke-linejoin="round"/>
        ${dots}${xTicks}
      </svg>`;
    },

    // ===== Загрузка файлов =====
    handleDrop(e) { this.dragOver = false; this.handleFiles(e.dataTransfer.files); },
    async handleFiles(fileList) {
      const files = Array.from(fileList);
      if (!files.length) return;
      for (const file of files) {
        if (file.size > 20 * 1024 * 1024) { this.toast(`${file.name}: больше 20 МБ`, "error"); continue; }
        const raw = { id: ++this._seq, name: file.name, state: "uploading", status: "received", docId: null, progress: 0 };
        this.queue.unshift(raw);
        // После unshift элемент в массиве обёрнут в reactive-Proxy; внешняя ссылка `raw`
        // остаётся на оригинале — мутации через raw НЕ триггерят перерисовку x-for.
        // Поэтому берём реактивную ссылку из массива и дальше мутируем только её.
        const item = this.queue[0];
        this.uploadFile(file, item);
      }
    },
    async uploadFile(file, item) {
      this.uploading = true;
      try {
        const fd = new FormData();
        fd.append("file", file);
        const res = await fetch("/upload", {
          method: "POST",
          headers: { "X-Telegram-User-Id": this.tgUserId },
          body: fd,
        });
        if (!res.ok) throw new Error(`upload ${res.status}`);
        const data = await res.json();
        item.docId = data.document_id;
        item.state = "processing";
        item.status = "received";
        this.pollStatus(item);
      } catch (e) {
        item.state = "failed"; item.status = "failed";
        this.toast(`Не удалось загрузить ${item.name}`, "error");
        console.error(e);
      } finally { this.uploading = false; }
    },
    // Поллинг статуса: обновляем стадию, останавливаемся на extracted/failed.
    async pollStatus(item) {
      const start = Date.now();
      const timeoutMs = 180000;
      const tick = async () => {
        if (item.state === "failed" || item.status === "extracted") return;
        if (Date.now() - start > timeoutMs) {
          item.state = "failed"; this.toast(`${item.name}: превышено время ожидания`, "error"); return;
        }
        try {
          const data = await this.api(`/api/documents/${item.docId}/status`);
          if (!data) return;
          item.status = data.status;
          if (data.status === "extracted") {
            item.state = "done";
            this.toast(`${item.name} обработан`, "success");
            this.loadStats();
          } else if (data.status === "failed") {
            item.state = "failed";
            this.toast(`${item.name}: ошибка обработки`, "error");
          } else {
            item.state = "processing";
            setTimeout(tick, 2000);
          }
        } catch (e) { setTimeout(tick, 3000); }
      };
      setTimeout(tick, 1500);
    },
    progressPct(item) { return STAGE_PROGRESS[item.status] ?? 0; },
    stageDone(item, stage) {
      const order = ["received", "recognizing", "normalizing", "extracted"];
      const cur = order.indexOf(item.status), target = order.indexOf(stage);
      return cur > target && cur !== -1;
    },
    queueStateText(item) {
      if (item.state === "uploading") return "Загрузка…";
      if (item.state === "processing") return STATUS_LABELS[item.status] || "Обработка";
      if (item.state === "done") return "Готово";
      if (item.state === "failed") return "Ошибка";
      return "";
    },

    // ===== Форматтеры =====
    typeLabel(t) { return TYPE_LABELS[t] || "Документ"; },
    statusLabel(s) { return STATUS_LABELS[s] || s; },
    greeting() {
      const h = new Date().getHours();
      const part = h < 6 ? "Доброй ночи" : h < 12 ? "Доброе утро" : h < 18 ? "Добрый день" : "Добрый вечер";
      return `${part}! Здесь ваши медицинские данные.`;
    },
    fmtDate(s) {
      if (!s) return "—";
      const d = new Date(String(s).replace(" ", "T"));
      if (isNaN(d)) return s;
      return d.toLocaleDateString("ru-RU", { day: "2-digit", month: "short", year: "numeric" });
    },
    fmtValue(r) {
      if (r.value_num != null) return fmtNum(r.value_num);
      return r.value_text || "—";
    },
    fmtRef(r) {
      if (r.ref_low != null && r.ref_high != null) return `норма ${fmtNum(r.ref_low)}–${fmtNum(r.ref_high)}`;
      if (r.ref_operator === "<" && r.ref_high != null) return `норма <${fmtNum(r.ref_high)}`;
      if (r.ref_operator === ">" && r.ref_low != null) return `норма >${fmtNum(r.ref_low)}`;
      if (r.ref_text) return `норма: ${r.ref_text}`;
      return "";
    },
    labValueClass(r) {
      if (r.value_num == null) return "";
      if (r.ref_low != null && r.value_num < r.ref_low) return "low";
      if (r.ref_high != null && r.value_num > r.ref_high) return "high";
      return "";
    },
  };
}

// Чистые хелперы вне компонента (используются и в renderChart).
function fmtNum(v) {
  if (v == null) return "—";
  return Number.isInteger(v) ? String(v) : Number(v).toLocaleString("ru-RU", { maximumFractionDigits: 3 });
}
function fmtDateShort(s) {
  if (!s) return "—";
  const d = new Date(String(s).replace(" ", "T"));
  if (isNaN(d)) return String(s).slice(0, 10);
  return d.toLocaleDateString("ru-RU", { day: "2-digit", month: "2-digit" });
}
