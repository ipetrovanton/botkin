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
const METRIC_LABELS = {
  heart_rate: "Пульс",
  resting_heart_rate: "Пульс покоя",
  blood_pressure_systolic: "Давление (систолич.)",
  blood_pressure_diastolic: "Давление (диастолич.)",
  bp_pulse: "Пульс при измерении АД",
  steps: "Шаги",
  steps_interval: "Шаги (интервалы)",
  sleep_seconds: "Сон",
  stress_avg: "Стресс (средний)",
  body_battery_max: "Body Battery (макс)",
  hrv_last_night: "HRV за ночь",
  hrv_sdnn: "HRV (SDNN)",
  spo2: "SpO2",
  spo2_avg: "SpO2 (среднее)",
  weight_kg: "Вес",
};
const PROVIDER_LABELS = { garmin: "Garmin Connect", strava: "Strava", apple_health: "Apple Health" };

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

    // Аутентификация
    isAuthed: false,
    authMode: "login",
    authForm: { email: "", password: "", display_name: "" },
    authBusy: false,
    user: null,

    // Данные
    stats: {},
    analytesCount: 0,
    clinics: [],
    doctors: [],
    docs: { items: [], total: 0 },
    current: { doc: null, kind: null, labs: [], reports: [] },
    currentVersions: [],
    // Верификация распознанного: режим правки внутри карточки документа
    verify: {
      editing: false,
      editLab: null,          // копия строки показателя в редакторе
      newLabOpen: false,
      newLab: { analyte_name: "", value_num: "", unit: "", ref_low: "", ref_high: "", taken_at: "" },
      editReport: null,       // копия заключения (списки — текстом, строка = пункт)
      busy: false,
    },
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

    // Режим выбора (массовое удаление документов)
    selMode: false,
    selected: [],

    // Здоровье (Garmin/Strava/Apple Health) и ассистент
    health: {
      accounts: [], stravaConfigured: false, metrics: [], stats: {},
      activities: [], series: { metric: "", unit: "", points: [] },
      picked: "", syncState: { state: "idle" },
      garminEmail: "", garminPassword: "", connecting: false,
    },
    assistant: { question: "", answer: "", chunks: [], busy: false },
    external: { today: null },

    // Формы пациента: профиль тела, жалобы, текущие препараты (учитываются в рекомендациях)
    patient: {
      profile: { sex: "", birth_date: "", height_cm: "", weight_kg: "", blood_type: "", allergies: "", chronic_conditions: "", latitude: "", longitude: "" },
      complaints: [],
      medications: [],
      newComplaint: "",
      newMed: { name: "", dosage: "", schedule: "" },
      busy: false,
      cityQuery: "", cityResults: [], showCities: false,
      drugResults: [], showDrugs: false,
    },

    // Администрирование (видно только роли admin — см. user.role)
    admin: {
      users: [],
      newUser: { telegram_user_id: "", display_name: "" },
      labsUser: null,          // пользователь, чьи анализы открыты
      labs: { items: [], total: 0 },
      labsQuery: "",
      labsOffset: 0,
      editLab: null,           // копия строки в форме редактирования (null = закрыта)
      newLabOpen: false,
      newLab: { analyte_name: "", value_num: "", unit: "", ref_low: "", ref_high: "", taken_at: "" },
      busy: false,
    },
    ragIndex: { chunks: {}, reindex: { state: "idle" }, research: { state: "idle" }, benching: false, benchModels: "", benchResults: null },

    // UI-состояние
    loading: { stats: false, docs: false, doc: false, reports: false, dynamics: false },
    toasts: [],
    // Счётчики запросов по ресурсам: ответ применяется только если он от
    // ПОСЛЕДНЕГО запроса. Иначе при быстрой смене фильтров/показателей поздно
    // пришедший старый ответ перезатирает свежие данные (out-of-order fetch).
    _req: { docs: 0, doc: 0, reports: 0, dynamics: 0 },

    // ===== Инициализация =====
    async init() {
      document.documentElement.setAttribute("data-theme", this.theme);
      // Проверяем сессию: cookie отправляется автоматически.
      try {
        this.user = await this.api("/api/auth/me", { headers: {} });
        this.isAuthed = true;
      } catch (e) {
        this.isAuthed = false;
        return;
      }
      await Promise.all([this.loadStats(), this.loadSelectors()]);
      this.loadExternal();
      // Закрытие подсказок анализов по клику вне.
      document.addEventListener("click", (e) => {
        if (!e.target.closest(".analyte-picker")) this.analyteFocused = false;
      });
    },

    // ===== API-клиент =====
    async api(path, opts = {}) {
      const headers = { ...(opts.headers || {}) };
      const res = await fetch(path, { ...opts, headers, credentials: "same-origin" });
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

    // ===== Навигация =====
    // Списки загружаются сразу при входе на экран (без ожидания фильтров).
    go(s) {
      this.screen = s;
      if (s === "documents") this.loadDocs();
      else if (s === "reports") this.loadReports();
      else if (s === "overview") { this.loadStats(); this.loadExternal(); }
      else if (s === "health") this.loadHealth();
      else if (s === "admin") this.adminLoadUsers();
      else if (s === "profile") this.loadPatient();
      if (s !== "documents" && this.selMode) this.toggleSelMode();
    },

    // ===== Режим выбора и массовое удаление =====
    toggleSelMode() {
      this.selMode = !this.selMode;
      this.selected = [];
    },
    toggleSel(id) {
      this.selected = this.isSel(id)
        ? this.selected.filter((x) => x !== id)
        : [...this.selected, id];
    },
    isSel(id) { return this.selected.includes(id); },
    selectAllDocs() {
      // Все на текущей странице; повторный клик — снять все.
      const ids = this.docs.items.map((d) => d.id);
      this.selected = this.selected.length === ids.length ? [] : ids;
    },
    async deleteSelected() {
      const n = this.selected.length;
      if (!n || !window.confirm(`Удалить документы (${n}) вместе со всеми данными?`)) return;
      try {
        const data = await this.api("/api/documents/delete-batch", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ ids: this.selected }),
        });
        this.toast(`Удалено: ${data?.deleted ?? 0}`, "success");
      } catch (e) { this.toast("Не удалось удалить документы", "error"); console.error(e); }
      this.toggleSelMode();
      this.loadDocs(); this.loadStats(); this.loadSelectors();
    },

    // ===== Действия с открытым документом =====
    async deleteCurrentDoc() {
      const id = this.current.doc?.id;
      if (!id || !window.confirm("Удалить документ со всеми показателями и заключениями?")) return;
      try {
        await this.api(`/api/documents/${id}`, { method: "DELETE" });
        this.toast("Документ удалён", "success");
        this.go("documents"); this.loadStats(); this.loadSelectors();
      } catch (e) { this.toast("Не удалось удалить документ", "error"); console.error(e); }
    },
    async reparseCurrentDoc() {
      const doc = this.current.doc;
      if (!doc) return;
      try {
        await this.api(`/api/documents/${doc.id}/reparse`, { method: "POST" });
      } catch (e) {
        const gone = String(e?.message || "").startsWith("409");
        this.toast(gone ? "Файл-исходник утрачен — обновить невозможно"
                        : "Не удалось запустить обновление", "error");
        return;
      }
      // Прогресс повторного распознавания виден в очереди на экране загрузки.
      this.queue.unshift({
        id: ++this._seq, name: doc.title || `Документ #${doc.id}`,
        state: "processing", status: "received", docId: doc.id,
        progress: 2, etaSeconds: null, stageElapsedSeconds: 0, alive: true,
      });
      this.pollStatus(this.queue[0]);
      this.toast("Документ отправлен на повторное распознавание", "info");
      this.go("upload");
    },
    async replaceSource(event) {
      // Замена файла-исходника: старая версия сохраняется в хранилище,
      // данные перераспознаются заново (прогресс — на экране загрузки).
      const doc = this.current.doc;
      const file = event.target.files?.[0];
      event.target.value = "";
      if (!doc || !file) return;
      const form = new FormData();
      form.append("file", file);
      try {
        const res = await fetch(`/api/documents/${doc.id}/replace`, {
          method: "POST", body: form, credentials: "same-origin",
        });
        if (res.status === 409) {
          this.toast((await res.json())?.detail || "Замена отклонена", "error");
          return;
        }
        if (!res.ok) throw new Error(String(res.status));
      } catch (e) { this.toast("Не удалось заменить файл", "error"); console.error(e); return; }
      this.queue.unshift({
        id: ++this._seq, name: `${doc.title || "Документ #" + doc.id} (новая версия)`,
        state: "processing", status: "received", docId: doc.id,
        progress: 2, etaSeconds: null, stageElapsedSeconds: 0, alive: true,
      });
      this.pollStatus(this.queue[0]);
      this.toast("Файл заменён — распознаём заново", "info");
      this.go("upload");
    },

    async loadVersions() {
      const id = this.current.doc?.id;
      if (!id) return;
      try {
        const data = await this.api(`/api/documents/${id}/versions`);
        this.currentVersions = data?.items || [];
      } catch (e) { console.error("versions", e); this.currentVersions = []; }
    },

    async openSource() {
      // Файл открывается через blob: заголовок идентификации нельзя передать в window.open.
      const id = this.current.doc?.id;
      if (!id) return;
      try {
        const res = await fetch(`/api/documents/${id}/source`, {
          credentials: "same-origin",
        });
        if (!res.ok) throw new Error(String(res.status));
        const url = URL.createObjectURL(await res.blob());
        window.open(url, "_blank");
        setTimeout(() => URL.revokeObjectURL(url), 60000);
      } catch (e) { this.toast("Оригинал недоступен", "error"); console.error(e); }
    },

    // ===== Аутентификация =====
    toggleAuthMode() {
      this.authMode = this.authMode === "login" ? "register" : "login";
      this.authForm.password = "";
    },
    async submitAuth() {
      this.authBusy = true;
      const endpoint = this.authMode === "login" ? "/api/auth/login" : "/api/auth/register";
      const body = this.authMode === "login"
        ? { email: this.authForm.email, password: this.authForm.password }
        : { email: this.authForm.email, password: this.authForm.password, display_name: this.authForm.display_name || null };
      try {
        const res = await fetch(endpoint, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(body),
          credentials: "same-origin",
        });
        if (!res.ok) {
          const detail = (await res.json().catch(() => ({})))?.detail || `Ошибка ${res.status}`;
          this.toast(detail, "error");
          return;
        }
        this.user = await this.api("/api/auth/me", { headers: {} });
        this.isAuthed = true;
        this.authForm = { email: "", password: "", display_name: "" };
        await Promise.all([this.loadStats(), this.loadSelectors()]);
        this.loadExternal();
        this.go("overview");
        this.toast(this.authMode === "login" ? "Вход выполнен" : "Аккаунт создан", "success");
      } catch (e) {
        this.toast("Ошибка сети", "error"); console.error(e);
      } finally {
        this.authBusy = false;
      }
    },
    async logout() {
      try {
        await fetch("/api/auth/logout", { method: "POST", credentials: "same-origin" });
      } catch (e) { /* ignored */ }
      this.isAuthed = false;
      this.user = null;
      this.screen = "overview";
      this.authMode = "login";
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
    async loadExternal() {
      try {
        this.external.today = await this.api("/api/external/today");
      } catch (e) { console.error("external", e); }
    },

    // ===== Формы пациента =====
    async loadPatient() {
      try {
        const [profile, complaints, meds] = await Promise.all([
          this.api("/api/patient/profile"),
          this.api("/api/patient/complaints"),
          this.api("/api/patient/medications"),
        ]);
        const p = profile || {};
        this.patient.profile = {
          sex: p.sex || "", birth_date: p.birth_date || "",
          height_cm: p.height_cm ?? "", weight_kg: p.weight_kg ?? "",
          blood_type: p.blood_type || "", allergies: p.allergies || "",
          chronic_conditions: p.chronic_conditions || "",
          latitude: p.latitude ?? "", longitude: p.longitude ?? "",
        };
        this.patient.complaints = complaints?.items || [];
        this.patient.medications = meds?.items || [];
        // Восстанавливаем город по координатам
        if (p.latitude && p.longitude) {
          this.patient.cityQuery = p.city_name || "";
        }
      } catch (e) { console.error("patient", e); }
    },

    async savePatientProfile() {
      const p = this.patient.profile;
      this.patient.busy = true;
      try {
        await this.api("/api/patient/profile", {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            sex: p.sex || null,
            birth_date: p.birth_date || null,
            height_cm: p.height_cm === "" ? null : Number(p.height_cm),
            weight_kg: p.weight_kg === "" ? null : Number(p.weight_kg),
            blood_type: p.blood_type || null,
            allergies: p.allergies || null,
            chronic_conditions: p.chronic_conditions || null,
            latitude: p.latitude === "" ? null : Number(p.latitude),
            longitude: p.longitude === "" ? null : Number(p.longitude),
          }),
        });
        this.toast("Профиль сохранён — будет учтён в рекомендациях", "success");
      } catch (e) { this.toast("Не удалось сохранить профиль", "error"); console.error(e); }
      finally { this.patient.busy = false; }
    },

    async searchCities() {
      const q = this.patient.cityQuery.trim();
      if (q.length < 2) { this.patient.cityResults = []; return; }
      try {
        this.patient.cityResults = await this.api(`/api/directory/cities?q=${encodeURIComponent(q)}`);
      } catch (e) { console.error("cities", e); }
    },

    selectCity(c) {
      this.patient.cityQuery = c.name;
      this.patient.profile.latitude = c.lat;
      this.patient.profile.longitude = c.lon;
      this.patient.showCities = false;
    },

    async searchDrugs() {
      const q = this.patient.newMed.name.trim();
      if (q.length < 2) { this.patient.drugResults = []; return; }
      try {
        this.patient.drugResults = await this.api(`/api/directory/drugs?q=${encodeURIComponent(q)}`);
      } catch (e) { console.error("drugs", e); }
    },

    selectDrug(d) {
      this.patient.newMed.name = d.name;
      this.patient.showDrugs = false;
    },

    async addComplaint() {
      const text = this.patient.newComplaint.trim();
      if (text.length < 3) { this.toast("Опишите жалобу подробнее", "error"); return; }
      try {
        await this.api("/api/patient/complaints", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ text }),
        });
        this.patient.newComplaint = "";
        this.toast("Жалоба записана", "success");
        this.loadPatient();
      } catch (e) { this.toast("Не удалось сохранить", "error"); console.error(e); }
    },

    async deleteComplaint(c) {
      try {
        await this.api(`/api/patient/complaints/${c.id}`, { method: "DELETE" });
        this.loadPatient();
      } catch (e) { this.toast("Не удалось удалить", "error"); console.error(e); }
    },

    async addMedication() {
      const m = this.patient.newMed;
      if (!m.name.trim()) { this.toast("Укажите название препарата", "error"); return; }
      try {
        await this.api("/api/patient/medications", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ name: m.name.trim(), dosage: m.dosage || null, schedule: m.schedule || null }),
        });
        this.patient.newMed = { name: "", dosage: "", schedule: "" };
        this.toast("Препарат добавлен", "success");
        this.loadPatient();
      } catch (e) { this.toast("Не удалось добавить", "error"); console.error(e); }
    },

    async toggleMedication(m) {
      try {
        await this.api(`/api/patient/medications/${m.id}?is_active=${m.is_active ? "false" : "true"}`,
                       { method: "PATCH" });
        this.loadPatient();
      } catch (e) { this.toast("Не удалось изменить статус", "error"); console.error(e); }
    },

    async deleteMedication(m) {
      try {
        await this.api(`/api/patient/medications/${m.id}`, { method: "DELETE" });
        this.loadPatient();
      } catch (e) { this.toast("Не удалось удалить", "error"); console.error(e); }
    },

    // ===== Верификация распознанного =====
    async reloadDoc() {
      // Тихая перезагрузка карточки без сброса режима редактирования.
      const id = this.current.doc?.id;
      if (!id) return;
      const data = await this.api(`/api/documents/${id}`);
      if (data) this.current = { doc: data.document, kind: data.kind, labs: data.labs, reports: data.reports };
    },

    async verifyDoc() {
      try {
        await this.api(`/api/documents/${this.current.doc.id}/verify`, { method: "POST" });
        this.verify.editing = false;
        this.toast("Данные подтверждены", "success");
        this.reloadDoc();
      } catch (e) { this.toast("Не удалось подтвердить", "error"); console.error(e); }
    },

    vToggleEditing() {
      this.verify.editing = !this.verify.editing;
      this.verify.editLab = null;
      this.verify.newLabOpen = false;
      this.verify.editReport = null;
    },

    vStartEditLab(row) {
      this.verify.editLab = { ...row };
    },

    async vSaveLab() {
      const l = this.verify.editLab;
      if (!l) return;
      this.verify.busy = true;
      try {
        await this.api(`/api/documents/${this.current.doc.id}/labs/${l.id}`, {
          method: "PATCH",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            analyte_name: l.analyte_name,
            value_num: l.value_num === "" || l.value_num === null ? null : Number(l.value_num),
            unit: l.unit || null,
            ref_low: l.ref_low === "" || l.ref_low === null ? null : Number(l.ref_low),
            ref_high: l.ref_high === "" || l.ref_high === null ? null : Number(l.ref_high),
            taken_at: l.taken_at || null,
          }),
        });
        this.verify.editLab = null;
        this.toast("Показатель исправлен", "success");
        this.reloadDoc();
      } catch (e) { this.toast("Не удалось сохранить", "error"); console.error(e); }
      finally { this.verify.busy = false; }
    },

    async vDeleteLab(row) {
      if (!confirm(`Удалить показатель «${row.analyte_name}»?`)) return;
      try {
        await this.api(`/api/documents/${this.current.doc.id}/labs/${row.id}`, { method: "DELETE" });
        this.toast("Показатель удалён", "success");
        this.reloadDoc();
      } catch (e) { this.toast("Не удалось удалить", "error"); console.error(e); }
    },

    async vAddLab() {
      const n = this.verify.newLab;
      if (!n.analyte_name.trim()) { this.toast("Название показателя обязательно", "error"); return; }
      this.verify.busy = true;
      try {
        await this.api(`/api/documents/${this.current.doc.id}/labs`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            analyte_name: n.analyte_name.trim(),
            value_num: n.value_num === "" ? null : Number(n.value_num),
            unit: n.unit || null,
            ref_low: n.ref_low === "" ? null : Number(n.ref_low),
            ref_high: n.ref_high === "" ? null : Number(n.ref_high),
            taken_at: n.taken_at || null,
          }),
        });
        this.verify.newLab = { analyte_name: "", value_num: "", unit: "", ref_low: "", ref_high: "", taken_at: "" };
        this.verify.newLabOpen = false;
        this.toast("Показатель добавлен", "success");
        this.reloadDoc();
      } catch (e) { this.toast("Не удалось добавить", "error"); console.error(e); }
      finally { this.verify.busy = false; }
    },

    vStartEditReport(rep) {
      // Списки редактируются как текст: одна строка = один пункт.
      this.verify.editReport = {
        id: rep.id,
        diagnosis: rep.diagnosis || "",
        doctor_name: rep.doctor_name || "",
        department: rep.department || "",
        recommendations: (rep.recommendations || []).join("\n"),
        medications: (rep.medications || []).join("\n"),
      };
    },

    async vSaveReport() {
      const r = this.verify.editReport;
      if (!r) return;
      const toList = (text) => text.split("\n").map((s) => s.trim()).filter(Boolean);
      this.verify.busy = true;
      try {
        await this.api(`/api/documents/${this.current.doc.id}/reports/${r.id}`, {
          method: "PATCH",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            diagnosis: r.diagnosis || null,
            doctor_name: r.doctor_name || null,
            department: r.department || null,
            recommendations: toList(r.recommendations),
            medications: toList(r.medications),
          }),
        });
        this.verify.editReport = null;
        this.toast("Заключение исправлено", "success");
        this.reloadDoc();
      } catch (e) { this.toast("Не удалось сохранить", "error"); console.error(e); }
      finally { this.verify.busy = false; }
    },

    // ===== Администрирование =====
    isAdmin() { return this.user?.role === "admin"; },

    async adminLoadUsers() {
      try {
        const data = await this.api("/api/admin/users");
        this.admin.users = data?.items || [];
      } catch (e) { this.toast("Нет доступа к администрированию", "error"); console.error(e); }
    },

    async adminCreateUser() {
      const tg = parseInt(String(this.admin.newUser.telegram_user_id).trim(), 10);
      if (!tg || tg <= 0) { this.toast("Введите числовой Telegram ID", "error"); return; }
      try {
        await this.api("/api/admin/users", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ telegram_user_id: tg, display_name: this.admin.newUser.display_name || null }),
        });
        this.admin.newUser = { telegram_user_id: "", display_name: "" };
        this.toast("Пользователь добавлен", "success");
        this.adminLoadUsers();
      } catch (e) {
        this.toast(String(e.message).includes("409") ? "Такой пользователь уже есть" : "Не удалось добавить пользователя", "error");
      }
    },

    async adminSetRole(u, role) {
      try {
        await this.api(`/api/admin/users/${u.id}`, {
          method: "PATCH",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ role }),
        });
        this.toast(`Роль обновлена: ${role}`, "success");
      } catch (e) {
        this.toast(String(e.message).includes("409") ? "Нельзя разжаловать последнего администратора" : "Не удалось сменить роль", "error");
      }
      this.adminLoadUsers();
    },

    async adminDeleteUser(u) {
      if (!confirm(`Удалить пользователя ${u.display_name || u.telegram_user_id} со всеми данными? Это необратимо.`)) return;
      try {
        await this.api(`/api/admin/users/${u.id}`, { method: "DELETE" });
        this.toast("Пользователь удалён", "success");
        if (this.admin.labsUser?.id === u.id) this.admin.labsUser = null;
      } catch (e) {
        this.toast(String(e.message).includes("409") ? "Нельзя удалить самого себя" : "Не удалось удалить", "error");
      }
      this.adminLoadUsers();
    },

    async adminOpenLabs(u) {
      this.admin.labsUser = u;
      this.admin.labsQuery = "";
      this.admin.labsOffset = 0;
      this.admin.editLab = null;
      this.admin.newLabOpen = false;
      await this.adminLoadLabs();
    },

    async adminLoadLabs() {
      if (!this.admin.labsUser) return;
      try {
        const p = new URLSearchParams({ limit: 50, offset: this.admin.labsOffset });
        if (this.admin.labsQuery) p.set("q", this.admin.labsQuery);
        const data = await this.api(`/api/admin/users/${this.admin.labsUser.id}/labs?${p}`);
        this.admin.labs = data || { items: [], total: 0 };
      } catch (e) { this.toast("Не удалось загрузить анализы", "error"); console.error(e); }
    },

    adminStartEditLab(row) {
      // Копия, а не ссылка: отмена редактирования не должна портить список.
      this.admin.editLab = { ...row };
    },

    async adminSaveLab() {
      const l = this.admin.editLab;
      if (!l) return;
      this.admin.busy = true;
      try {
        await this.api(`/api/admin/labs/${l.id}?user_id=${this.admin.labsUser.id}`, {
          method: "PATCH",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            analyte_name: l.analyte_name,
            value_num: l.value_num === "" ? null : Number(l.value_num),
            unit: l.unit || null,
            ref_low: l.ref_low === "" ? null : Number(l.ref_low),
            ref_high: l.ref_high === "" ? null : Number(l.ref_high),
            taken_at: l.taken_at || null,
          }),
        });
        this.admin.editLab = null;
        this.toast("Показатель обновлён", "success");
        this.adminLoadLabs();
      } catch (e) { this.toast("Не удалось сохранить", "error"); console.error(e); }
      finally { this.admin.busy = false; }
    },

    async adminDeleteLab(row) {
      if (!confirm(`Удалить показатель «${row.analyte_name}»?`)) return;
      try {
        await this.api(`/api/admin/labs/${row.id}?user_id=${this.admin.labsUser.id}`, { method: "DELETE" });
        this.toast("Показатель удалён", "success");
        this.adminLoadLabs();
      } catch (e) { this.toast("Не удалось удалить", "error"); console.error(e); }
    },

    async adminAddLab() {
      const n = this.admin.newLab;
      if (!n.analyte_name.trim()) { this.toast("Название показателя обязательно", "error"); return; }
      this.admin.busy = true;
      try {
        await this.api(`/api/admin/users/${this.admin.labsUser.id}/labs`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            analyte_name: n.analyte_name.trim(),
            value_num: n.value_num === "" ? null : Number(n.value_num),
            unit: n.unit || null,
            ref_low: n.ref_low === "" ? null : Number(n.ref_low),
            ref_high: n.ref_high === "" ? null : Number(n.ref_high),
            taken_at: n.taken_at || null,
          }),
        });
        this.admin.newLab = { analyte_name: "", value_num: "", unit: "", ref_low: "", ref_high: "", taken_at: "" };
        this.admin.newLabOpen = false;
        this.toast("Показатель добавлен", "success");
        this.adminLoadLabs();
      } catch (e) { this.toast("Не удалось добавить", "error"); console.error(e); }
      finally { this.admin.busy = false; }
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
      const req = ++this._req.docs;
      this.loading.docs = true;
      try {
        const p = new URLSearchParams();
        for (const [k, v] of Object.entries(this.filters)) {
          if (v !== "" && v !== null && v !== undefined) p.set(k, v);
        }
        p.set("limit", PAGE_SIZE);
        const data = await this.api(`/api/documents?${p}`);
        if (req !== this._req.docs) return; // устаревший ответ — уже запрошено новое
        this.docs = data || { items: [], total: 0 };
      } catch (e) {
        if (req !== this._req.docs) return;
        this.toast("Ошибка загрузки документов", "error"); console.error(e);
      } finally { if (req === this._req.docs) this.loading.docs = false; }
    },

    async openDoc(id) {
      const req = ++this._req.doc;
      this.screen = "document";
      this.loading.doc = true;
      this.current = { doc: null, kind: null, labs: [], reports: [] };
      this.verify = { ...this.verify, editing: false, editLab: null, newLabOpen: false, editReport: null };
      try {
        const data = await this.api(`/api/documents/${id}`);
        if (req !== this._req.doc) return;
        if (!data) { this.toast("Документ не найден", "error"); this.screen = "documents"; return; }
        // API возвращает {document, kind, labs, reports}; HTML читает current.doc — мапим.
        this.current = {
          doc: data.document, kind: data.kind, labs: data.labs, reports: data.reports,
        };
        this.currentVersions = [];
        this.loadVersions();
      } catch (e) {
        if (req !== this._req.doc) return;
        this.toast("Ошибка загрузки документа", "error"); console.error(e);
      } finally { if (req === this._req.doc) this.loading.doc = false; }
    },

    async loadReports() {
      const req = ++this._req.reports;
      this.loading.reports = true;
      try {
        const p = new URLSearchParams();
        for (const [k, v] of Object.entries(this.repFilters)) if (v) p.set(k, v);
        p.set("limit", 100);
        const data = await this.api(`/api/reports?${p}`);
        if (req !== this._req.reports) return;
        this.reports = data || { items: [], total: 0 };
      } catch (e) {
        if (req !== this._req.reports) return;
        this.toast("Ошибка загрузки заключений", "error"); console.error(e);
      } finally { if (req === this._req.reports) this.loading.reports = false; }
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
      const req = ++this._req.dynamics;
      this.analytePicked = name;
      this.analyteQuery = name;
      this.analyteFocused = false;
      this.loading.dynamics = true;
      try {
        const data = await this.api(`/api/dynamics?name=${encodeURIComponent(name)}`);
        if (req !== this._req.dynamics) return; // выбран другой показатель — ответ устарел
        // api() возвращает null на 404 — у показателя нет числовых точек.
        if (!data) {
          this.dynamics = { points: [], analyte: name, unit: "", ref_low: null, ref_high: null };
          this.toast(`Нет данных по «${name}»`, "info");
          return;
        }
        this.dynamics = data;
        this.$nextTick(() => this.renderChart());
      } catch (e) {
        if (req !== this._req.dynamics) return;
        this.dynamics = { points: [], analyte: name, unit: "", ref_low: null, ref_high: null };
        this.toast("Ошибка загрузки динамики", "error"); console.error(e);
      } finally { if (req === this._req.dynamics) this.loading.dynamics = false; }
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
        return `<text x="${x(i)}" y="${H - padB + 20}" text-anchor="middle" font-size="11" fill="${cText}">${escapeHtml(fmtDateShort(p.taken_at))}</text>`;
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
                  <title>${escapeHtml(fmtDateShort(p.taken_at))}: ${fmtNum(p.value_num)} ${escapeHtml(this.dynamics.unit)}</title>
                </circle>`;
      }).join("");

      wrap.innerHTML = `<svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="xMidYMid meet" role="img" aria-label="Динамика показателя ${escapeHtml(this.dynamics.analyte)}">
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
        const raw = {
          id: ++this._seq, name: file.name, state: "uploading", status: "received", docId: null,
          progress: 0, etaSeconds: null, stageElapsedSeconds: 0, alive: true,
        };
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
          body: fd,
          credentials: "same-origin",
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
      const tick = async () => {
        if (item.state === "failed" || item.status === "extracted") return;
        try {
          const data = await this.api(`/api/documents/${item.docId}/status`);
          if (!data) {
            // Документ исчез: повторная загрузка схлопнута дедупликацией,
            // данные объединены с уже существующим документом.
            item.state = "duplicate"; item.status = "extracted";
            this.toast(`${item.name}: уже был загружен — данные объединены`, "info");
            this.loadStats();
            return;
          }
          item.status = data.status;
          item.progress = Number.isFinite(data.percent) ? data.percent : (STAGE_PROGRESS[data.status] ?? 0);
          item.etaSeconds = Number.isFinite(data.eta_seconds) ? data.eta_seconds : null;
          item.stageElapsedSeconds = Number.isFinite(data.stage_elapsed_s) ? data.stage_elapsed_s : 0;
          item.alive = data.alive !== false;
          item.queuePosition = Number.isFinite(data.queue_position) ? data.queue_position : null;
          if (data.status === "extracted") {
            item.progress = 100;
            item.state = "done";
            this.toast(`${item.name} обработан`, "success");
            this.loadStats();
          } else if (data.status === "failed") {
            item.state = "failed";
            this.toast(`${item.name}: ошибка обработки`, "error");
          } else {
            item.state = "processing";
            setTimeout(tick, item.alive ? 2000 : 5000);
          }
        } catch (e) {
          // Сеть может кратко пропасть; не превращаем временную ошибку в ложный failed.
          item.alive = false;
          setTimeout(tick, 5000);
        }
      };
      setTimeout(tick, 1500);
    },
    progressPct(item) { return Number.isFinite(item.progress) ? item.progress : (STAGE_PROGRESS[item.status] ?? 0); },
    etaText(item) {
      if (item.state === "uploading") return "Передаю документ…";
      if (item.state === "done") return "Результат готов";
      if (item.state === "failed") return "Нужно повторить загрузку";
      if (!item.alive) return "Связь с сервером прервалась — перепроверяю…";
      if (item.queuePosition >= 1) return `В очереди на распознавание: ${item.queuePosition}-й`;
      if (item.etaSeconds === null) return "Модель готовит анализ…";
      if (item.etaSeconds < 60) return `Ориентир: меньше минуты · ${item.progress}%`;
      return `Ориентир: около ${Math.ceil(item.etaSeconds / 60)} мин · ${item.progress}%`;
    },
    stageDone(item, stage) {
      // processing — промежуточный статус между received и recognizing;
      // без него indexOf = -1 и чекпоинты стадий «прыгали».
      const order = ["received", "processing", "recognizing", "normalizing", "extracted"];
      const cur = order.indexOf(item.status), target = order.indexOf(stage);
      return cur > target && cur !== -1;
    },
    queueStateText(item) {
      if (item.state === "uploading") return "Загрузка…";
      if (item.state === "processing" && item.queuePosition >= 1) return `В очереди: ${item.queuePosition}-й`;
      if (item.state === "processing") return STATUS_LABELS[item.status] || "Обработка";
      if (item.state === "done") return "Готово";
      if (item.state === "duplicate") return "Уже был загружен";
      if (item.state === "failed") return "Ошибка";
      return "";
    },

    // ===== Здоровье (Garmin/Strava/Apple Health) =====
    async loadHealth() {
      try {
        const [acc, met, act, rag] = await Promise.all([
          this.api("/api/health/accounts"),
          this.api("/api/health/metrics"),
          this.api("/api/health/activities"),
          this.api("/api/rag/status"),
        ]);
        this.health.accounts = acc?.items || [];
        this.health.stravaConfigured = acc?.strava_configured || false;
        this.health.metrics = met?.items || [];
        this.health.stats = met?.stats || {};
        this.health.activities = act?.items || [];
        if (rag) this.ragIndex = rag;
      } catch (e) { console.error("health", e); }
    },
    healthAccount(provider) {
      return this.health.accounts.find((a) => a.provider === provider && a.status !== "disconnected");
    },
    async connectGarmin() {
      const { garminEmail: email, garminPassword: password } = this.health;
      if (!email || !password) { this.toast("Введите e-mail и пароль Garmin", "error"); return; }
      this.health.connecting = true;
      try {
        const data = await this.api("/api/health/connect/garmin", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ email, password }),
        });
        this.toast(`Garmin подключён: ${data?.full_name || email}`, "success");
        this.health.garminPassword = ""; // пароль больше не нужен — живём на токенах
        this.loadHealth();
      } catch (e) { this.toast("Не удалось войти в Garmin", "error"); console.error(e); }
      finally { this.health.connecting = false; }
    },
    async syncGarmin() {
      try {
        await this.api("/api/health/sync/garmin?days=30", { method: "POST" });
        this.health.syncState = { state: "running", done: 0, total: 32 };
        this.pollSync();
      } catch (e) { this.toast("Не удалось запустить синхронизацию", "error"); console.error(e); }
    },
    async setSyncSchedule(event) {
      const val = event.target.value;
      const hours = val ? parseInt(val, 10) : null;
      try {
        await this.api("/api/health/accounts/garmin/schedule", {
          method: "PATCH",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ interval_hours: hours }),
        });
        this.toast(hours ? `Автосинк: каждые ${hours} ч` : "Автосинк выключен", "success");
        this.loadHealth();
      } catch (e) { this.toast("Не удалось сохранить расписание", "error"); console.error(e); }
    },
    async pollSync() {
      try {
        const st = await this.api("/api/health/sync/status");
        this.health.syncState = st || { state: "idle" };
        if (st?.state === "running") { setTimeout(() => this.pollSync(), 2500); return; }
        if (st?.state === "done") {
          this.toast(`Синхронизировано: ${st.metrics} метрик, ${st.activities} активностей`, "success");
          this.loadHealth();
        } else if (st?.state === "error") {
          this.toast(`Синхронизация не удалась: ${st.error}`, "error");
        }
      } catch (e) { console.error(e); }
    },
    syncPct() {
      const s = this.health.syncState;
      return s.total ? Math.round((s.done / s.total) * 100) : 0;
    },
    async pickHealthMetric(metric) {
      this.health.picked = metric;
      try {
        const data = await this.api(`/api/health/series?metric=${encodeURIComponent(metric)}&limit=1000`);
        if (!data) { this.health.series = { metric, unit: "", points: [] }; return; }
        // Внутрисуточные метрики (сотни точек в день) сворачиваем в дневные средние.
        this.health.series = { ...data, points: dailyAverage(data.points) };
        this.$nextTick(() => this.renderHealthChart());
      } catch (e) { this.toast("Ошибка загрузки метрики", "error"); console.error(e); }
    },
    renderHealthChart() {
      const wrap = this.$refs.healthChart;
      const pts = this.health.series.points;
      if (!wrap || !pts.length) return;
      const isSleep = this.health.series.metric === "sleep_seconds";
      const vals = pts.map((p) => isSleep ? p.value_num / 3600 : p.value_num);
      const W = Math.max(320, Math.min(680, wrap.clientWidth));
      const H = 260, padL = 48, padR = 16, padT = 16, padB = 36;
      const innerW = W - padL - padR, innerH = H - padT - padB;
      let lo = Math.min(...vals), hi = Math.max(...vals);
      const span = hi - lo || 1; lo -= span * 0.1; hi += span * 0.1;
      const x = (i) => padL + (pts.length === 1 ? innerW / 2 : (i / (pts.length - 1)) * innerW);
      const y = (v) => padT + innerH - ((v - lo) / (hi - lo)) * innerH;
      const css = (n) => getComputedStyle(document.documentElement).getPropertyValue(n).trim();
      const cVal = css("--brand-1") || "#14B8A6";
      const cText = css("--text-dim") || "#94A3B8";
      const cGrid = css("--border") || "rgba(255,255,255,.08)";
      const line = vals.map((v, i) => `${i === 0 ? "M" : "L"} ${x(i).toFixed(1)} ${y(v).toFixed(1)}`).join(" ");
      const yTicks = [0, 1, 2, 3, 4].map((i) => {
        const v = lo + (hi - lo) * i / 4, yy = y(v);
        return `<line x1="${padL}" y1="${yy}" x2="${W - padR}" y2="${yy}" stroke="${cGrid}" stroke-dasharray="3 4"/>
                <text x="${padL - 8}" y="${yy + 4}" text-anchor="end" font-size="11" fill="${cText}">${fmtNum(v)}</text>`;
      }).join("");
      const xTicks = pts.map((p, i) => {
        if (pts.length > 8 && i % Math.ceil(pts.length / 6) !== 0 && i !== pts.length - 1) return "";
        return `<text x="${x(i)}" y="${H - padB + 18}" text-anchor="middle" font-size="11" fill="${cText}">${escapeHtml(fmtDateShort(p.taken_at))}</text>`;
      }).join("");
      const dots = vals.map((v, i) =>
        `<circle cx="${x(i).toFixed(1)}" cy="${y(v).toFixed(1)}" r="4" fill="${cVal}" stroke="var(--surface)" stroke-width="2">
           <title>${escapeHtml(fmtDateShort(pts[i].taken_at))}: ${fmtNum(v)}${isSleep ? " ч" : " " + escapeHtml(this.health.series.unit || "")}</title>
         </circle>`).join("");
      wrap.innerHTML = `<svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="xMidYMid meet" role="img" aria-label="Динамика метрики">
        ${yTicks}<path d="${line}" fill="none" stroke="${cVal}" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"/>${dots}${xTicks}
      </svg>`;
    },
    async uploadAppleExport(e) {
      const file = e.target.files?.[0];
      if (!file) return;
      const fd = new FormData();
      fd.append("file", file);
      this.toast("Импорт экспорта Apple Health…", "info");
      try {
        const res = await fetch("/api/health/apple/import", {
          method: "POST", body: fd, credentials: "same-origin",
        });
        if (!res.ok) throw new Error(`import ${res.status}`);
        const data = await res.json();
        this.toast(`Импортировано метрик: ${data.metrics}`, "success");
        this.loadHealth();
      } catch (err) { this.toast("Не удалось импортировать экспорт", "error"); console.error(err); }
      finally { e.target.value = ""; }
    },
    metricLabel(m) { return METRIC_LABELS[m] || m; },
    providerLabel(p) { return PROVIDER_LABELS[p] || p; },
    fmtMetricValue(p) {
      if (this.health.series.metric === "sleep_seconds") return `${fmtNum(p.value_num / 3600)} ч`;
      return `${fmtNum(p.value_num)} ${this.health.series.unit || ""}`;
    },
    fmtDuration(s) {
      if (s == null) return "—";
      const h = Math.floor(s / 3600), m = Math.round((s % 3600) / 60);
      return h ? `${h} ч ${m} мин` : `${m} мин`;
    },

    // ===== Ассистент (RAG) =====
    async askAssistant() {
      const q = this.assistant.question.trim();
      if (q.length < 3) { this.toast("Сформулируйте вопрос", "error"); return; }
      this.assistant.busy = true;
      this.assistant.answer = "";
      try {
        const data = await this.api("/api/rag/recommend", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ question: q }),
        });
        this.assistant.answer = data?.answer || "Ответ пуст.";
        this.assistant.chunks = data?.chunks || [];
      } catch (e) { this.toast("Ассистент недоступен (проверьте Ollama)", "error"); console.error(e); }
      finally { this.assistant.busy = false; }
    },
    async ragReindex() {
      try {
        await this.api("/api/rag/reindex", { method: "POST" });
        this.toast("Индексация справочников запущена (займёт несколько минут)", "info");
        this.pollRag();
      } catch (e) { this.toast("Не удалось запустить индексацию", "error"); console.error(e); }
    },
    async pollRag() {
      try {
        const st = await this.api("/api/rag/status");
        if (st) this.ragIndex = st;
        if (st?.reindex?.state === "running") setTimeout(() => this.pollRag(), 5000);
        else if (st?.reindex?.state === "done") this.toast("Справочники проиндексированы", "success");
      } catch (e) { console.error(e); }
    },
    ragTotalChunks() {
      return Object.values(this.ragIndex.chunks || {}).reduce((a, b) => a + b, 0);
    },

    async ragResearchUpdate() {
      try {
        await this.api("/api/rag/research/update", { method: "POST" });
        this.ragIndex.research = { state: "running" };
        this.toast("Обновление PubMed запущено", "info");
        this._pollResearch();
      } catch (e) {
        if (e?.status === 409) this.toast("Обновление уже идёт", "error");
        else this.toast("Не удалось запустить обновление", "error");
        console.error(e);
      }
    },

    async ragResearchStatus() {
      try {
        const st = await this.api("/api/rag/research/status");
        if (st) this.ragIndex.research = st;
      } catch (e) { console.error(e); }
    },

    _pollResearch() {
      setTimeout(async () => {
        try {
          const st = await this.api("/api/rag/research/status");
          if (st) this.ragIndex.research = st;
          if (st?.state === "running") this._pollResearch();
          else if (st?.state === "done") this.toast("PubMed обновлён: " + (st.indexed ?? 0) + " публикаций", "success");
        } catch (e) { console.error(e); }
      }, 5000);
    },

    async ragBenchmark() {
      const raw = this.ragIndex.benchModels.trim();
      if (!raw) { this.toast("Укажите модели через запятую", "error"); return; }
      const models = raw.split(",").map(m => m.trim()).filter(Boolean);
      if (!models.length) { this.toast("Укажите хотя бы одну модель", "error"); return; }
      this.ragIndex.benching = true;
      this.ragIndex.benchResults = null;
      try {
        const data = await this.api("/api/rag/benchmark", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ models }),
        });
        this.ragIndex.benchResults = data.models || [];
        this.toast("Бенчмарк завершён", "success");
      } catch (e) {
        this.toast("Бенчмарк недоступен (проверьте Ollama)", "error");
        console.error(e);
      } finally { this.ragIndex.benching = false; }
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
    // Позиция значения на мини-шкале нормы (0–100%). Коридор занимает середину
    // шкалы (25–75%): норма видна как «внутри», выходы — слева/справа, с клэмпом.
    refPosition(r) {
      if (r.value_num == null || r.ref_low == null || r.ref_high == null) return null;
      const span = r.ref_high - r.ref_low;
      if (span <= 0) return null;
      const pos = 25 + ((r.value_num - r.ref_low) / span) * 50;
      return Math.max(0, Math.min(100, pos));
    },
  };
}

// Чистые хелперы вне компонента (используются и в renderChart).
// Экранирование для innerHTML-шаблона графика: имя/единица показателя и сырые даты
// приходят из OCR/LLM-извлечения загруженного документа — это внешние данные,
// крафтовое имя вида `"><script>` не должно исполняться (stored XSS).
function escapeHtml(v) {
  return String(v ?? "").replace(/[&<>"']/g, (ch) => (
    { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[ch]
  ));
}
// Свёртка внутрисуточных точек в дневные средние: график месяца из 20 000 точек
// пульса нечитаем, а тренд виден по дневным значениям.
function dailyAverage(points) {
  const byDay = new Map();
  for (const p of points) {
    const day = String(p.taken_at).slice(0, 10);
    const acc = byDay.get(day) || { sum: 0, n: 0 };
    acc.sum += p.value_num; acc.n += 1;
    byDay.set(day, acc);
  }
  if (byDay.size === points.length) return points; // уже дневные
  return [...byDay.entries()].map(([day, a]) => ({
    taken_at: day, value_num: Math.round((a.sum / a.n) * 10) / 10,
  }));
}

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
