(function () {
  "use strict";

  var platform = window.MagicMapperPlatform;
  var state = {
    status: null,
    catalog: null,
    actions: {},
    categories: {},
    capabilities: {},
    sourceButton: null,
    modal: false,
    modalView: null,
    modalStack: [],
    editorInputs: {},
    returnFocus: null,
    busy: false
  };
  var buttonLabels = {
    prime: "Prime Video", netflix: "Netflix", disney: "Disney+", rakuten: "Rakuten TV",
    alexa: "Alexa", google: "Google Assistant", lg_channels: "LG Channels", ch_up: "Channel up",
    ch_down: "Channel down", vol_up: "Volume up", vol_down: "Volume down", fastforward: "Fast-forward",
    channels_alt: "Channels", search_alt: "Search", "...": "More actions", "...alt": "More actions"
  };

  var elements = {
    statusDot: document.getElementById("status-dot"), statusLabel: document.getElementById("status-label"),
    summary: document.getElementById("summary"), serviceBanner: document.getElementById("service-banner"),
    serviceTitle: document.getElementById("service-title"), serviceCopy: document.getElementById("service-copy"),
    primary: document.getElementById("primary-button"), discover: document.getElementById("discover-button"),
    mappings: document.getElementById("mappings"), empty: document.getElementById("empty-state"),
    modal: document.getElementById("modal"), modalCard: document.getElementById("modal-card"),
    system: document.getElementById("system-button"), toast: document.getElementById("toast")
  };

  function label(button) {
    return buttonLabels[button] || button.replace(/_/g, " ").replace(/(^|\s)\S/g, function (letter) { return letter.toUpperCase(); });
  }

  function escapeHtml(value) {
    return String(value).replace(/[&<>'"]/g, function (character) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", "'": "&#39;", '"': "&quot;" }[character];
    });
  }

  function actionDefinition(id) {
    return state.actions[id] || null;
  }

  function optionLabel(field, value) {
    var found = null;
    (field.options || []).some(function (option) {
      if (option.value === value) { found = option.label; return true; }
      return false;
    });
    return found || value;
  }

  function describe(action) {
    if (action === "disabled") return "Disabled — default action blocked";
    if (Array.isArray(action)) return action.length + " actions";
    if (!action) return "Unchanged";
    var definition = actionDefinition(action.function);
    var inputs = action.inputs || {};
    if (action.function === "launch_app") return "Opens " + (inputs.app_title || inputs.app_id);
    if (action.function === "press_button") return "Acts like " + label(inputs.button);
    if (action.function === "set_oled_backlight") return "OLED light set to " + inputs.backlight;
    if (action.function === "increase_oled_light" || action.function === "reduce_oled_light") {
      return (definition ? definition.title : label(action.function)) + " by " + (inputs.increment || 10);
    }
    if (action.function === "set_energy_mode") return "Energy saving: " + label(inputs.mode);
    if (action.function === "set_dynamic_tone_mapping") return "Dynamic Tone Mapping: " + inputs.value;
    if (action.function === "send_ir_command") return "IR: " + inputs.keycode;
    if (action.function === "curl") return (inputs.method || "GET") + " " + inputs.url;
    if (action.function === "send_tcp_command") return "TCP: " + inputs.ip + ":" + inputs.port;
    if (action.function === "send_cec_button") return "HDMI-CEC code " + inputs.code;
    return definition ? definition.title : action.function.replace(/_/g, " ");
  }

  function setBusy(busy) {
    state.busy = busy;
    document.body.classList.toggle("is-busy", busy);
  }

  function showToast(message) {
    elements.toast.textContent = message;
    elements.toast.hidden = false;
    window.clearTimeout(showToast.timeout);
    showToast.timeout = window.setTimeout(function () { elements.toast.hidden = true; }, 3000);
  }

  function indexCatalog(catalog) {
    state.catalog = catalog;
    (catalog.actions || []).forEach(function (action) { state.actions[action.id] = action; });
    (catalog.categories || []).forEach(function (category) { state.categories[category.id] = category; });
  }

  function render() {
    var status = state.status || { active: false, installed: false, config: {}, settings: {} };
    var config = status.config || {};
    var entries = Object.keys(config).sort();
    elements.statusDot.classList.toggle("active", Boolean(status.active));
    elements.statusLabel.textContent = status.active ? "Running" : (status.installed ? "Stopped" : "Not set up");
    elements.summary.textContent = entries.length + (entries.length === 1 ? " button changed" : " buttons changed");
    elements.serviceBanner.hidden = status.active;
    elements.serviceTitle.textContent = status.installed ? "Mapper is stopped" : "Magic Mapper needs setup";
    elements.serviceCopy.textContent = status.installed ? "Start it to apply your saved button changes." : "Set up the local service to change remote buttons.";
    elements.primary.textContent = status.installed ? "Start mapper" : "Set up";
    elements.discover.disabled = !status.active || !state.catalog;
    elements.empty.hidden = entries.length > 0;
    elements.mappings.innerHTML = entries.map(function (button) {
      return '<button class="mapping-row focusable" type="button" data-edit="' + escapeHtml(button) + '"><span class="key-name">' + escapeHtml(label(button)) + '</span>' +
        '<span class="mapping-action">' + escapeHtml(describe(config[button])) + '</span>' +
        '<span class="chevron" aria-hidden="true">›</span></button>';
    }).join("");
    bindDynamicActions();
  }

  function refresh() {
    return platform.status().then(function (response) {
      state.status = response.status;
      render();
      return response.status;
    }).catch(showError);
  }

  function primaryAction() {
    if (!state.status || !state.status.installed) {
      setBusy(true);
      platform.install().then(function (response) {
        state.status = response.status;
        render();
        showToast(response.migrated ? "Existing mappings imported" : "Magic Mapper is active");
      }).catch(showError).finally(function () { setBusy(false); });
    } else if (!state.status.active) {
      setBusy(true);
      platform.start().then(refresh).then(function () { showToast("Mapper started"); }).finally(function () { setBusy(false); });
    } else startDiscovery("source");
  }

  function startDiscovery(purpose) {
    if (!state.status || !state.status.active || state.busy) return;
    var requestId = String(Date.now()) + "-" + Math.floor(Math.random() * 10000);
    var html = '<p class="eyebrow">BUTTON DISCOVERY</p><h2>' + (purpose === "target" ? "Press the target button" : "Press one remote button") + '</h2>' +
      '<p class="modal-copy">' + (purpose === "target" ? "The first button will behave like this one." : "Its normal action will be blocked while we identify it.") + '</p>' +
      '<div class="discovery-signal">···</div><p class="modal-copy">Waiting for a complete press…</p>';
    if (purpose === "target") pushModal(html, "narrow", false);
    else rootModal(html, "narrow", false);
    platform.discover(requestId).then(function () { pollDiscovery(requestId, purpose, 0); }).catch(showError);
  }

  function pollDiscovery(requestId, purpose, attempts) {
    window.setTimeout(function () {
      platform.discoveryResult(requestId).then(function (result) {
        if (result.pending && attempts < 28) return pollDiscovery(requestId, purpose, attempts + 1);
        if (!result.ok && result.error === "cancelled") { if (purpose === "target") backModal(true); else closeModal(); return; }
        if (!result.ok) throw new Error(result.error || "No button was detected");
        if (result.pending) throw new Error("No button was detected");
        if (String(result.button).indexOf("code_") === 0) throw new Error("That button is not supported yet (code " + result.code + ")");
        if (purpose === "target") return applyMapping(state.sourceButton, { function: "press_button", inputs: { button: result.button } });
        state.sourceButton = result.button;
        showActionChoices(result.button, "replace");
      }).catch(showError);
    }, 450);
  }

  function categoryRows() {
    return (state.catalog.categories || []).map(function (category) {
      var count = state.catalog.actions.filter(function (action) { return action.category === category.id; }).length;
      return '<button class="choice focusable" type="button" data-category="' + escapeHtml(category.id) + '">' +
        '<span class="choice-copy"><strong>' + escapeHtml(category.title) + '</strong><span>' + escapeHtml(category.summary) + '</span></span>' +
        '<span class="choice-meta">' + count + '<span class="chevron" aria-hidden="true">›</span></span></button>';
    }).join("");
  }

  function showActionChoices(button, mode) {
    state.sourceButton = button;
    var html = '<p class="eyebrow">' + escapeHtml(label(button).toUpperCase()) + ' FOUND</p><h2>What should it do?</h2>' +
      '<p class="modal-copy">Choose a group. Back always returns one level.</p><div class="choices category-list">' + categoryRows() + '</div>';
    if (mode === "replace") replaceModal(html);
    else if (mode === "push") pushModal(html);
    else rootModal(html);
  }

  function showCategory(categoryId) {
    var category = state.categories[categoryId];
    var current = state.status.config && state.status.config[state.sourceButton];
    var currentId = current === "disabled" ? "disabled" : (current && !Array.isArray(current) ? current.function : null);
    var rows = state.catalog.actions.filter(function (action) { return action.category === categoryId; }).map(function (action) {
      var unavailable = action.requires && state.capabilities[action.requires] === false;
      var meta = action.id === currentId ? "Current" : (action.warning ? "Advanced" : "");
      if (unavailable) meta = "Not installed";
      return '<button class="choice focusable" type="button" data-action-id="' + escapeHtml(action.id) + '"' + (unavailable ? " disabled" : "") + '>' +
        '<span class="choice-copy"><strong>' + escapeHtml(action.title) + '</strong><span>' + escapeHtml(unavailable ? "Install " + action.requires + " to use this action." : action.summary) + '</span></span>' +
        '<span class="choice-meta">' + escapeHtml(meta) + '<span class="chevron" aria-hidden="true">›</span></span></button>';
    }).join("");
    pushModal('<p class="eyebrow">CHOOSE ACTION</p><h2>' + escapeHtml(category.title) + '</h2><p class="modal-copy">' + escapeHtml(category.summary) + '</p><div class="choices action-list">' + rows + '</div>');
  }

  function defaultInputs(action) {
    var inputs = {};
    (action.inputs || []).forEach(function (field) {
      if (field.default !== undefined) inputs[field.name] = field.default;
    });
    return inputs;
  }

  function chooseAction(id) {
    var action = actionDefinition(id);
    if (!action || (action.requires && state.capabilities[action.requires] === false)) return;
    if (action.editor === "apps") { showApps(); return; }
    if (action.editor === "button") { startDiscovery("target"); return; }
    if (action.editor === "fields") {
      var current = state.status.config && state.status.config[state.sourceButton];
      var existing = current && !Array.isArray(current) && current.function === id ? current.inputs : null;
      showActionEditor(action, existing);
      return;
    }
    applyMapping(state.sourceButton, id === "disabled" ? "disabled" : { function: id, inputs: defaultInputs(action) });
  }

  function showApps() {
    pushModal('<p class="eyebrow">CHOOSE DESTINATION</p><h2>Open an app</h2><p class="modal-copy">Loading the apps installed on this TV…</p>', "", false);
    platform.apps().then(function (response) {
      var apps = response.apps || [];
      replaceModal('<p class="eyebrow">CHOOSE DESTINATION</p><h2>Open an app</h2>' +
        '<div class="app-list">' + apps.map(function (app) {
          return '<button class="app-option focusable" type="button" data-app-id="' + escapeHtml(app.id) + '" data-app-title="' + escapeHtml(app.title) + '"><span>' + escapeHtml(app.title) + '</span><small>' + escapeHtml(app.id) + '</small></button>';
        }).join("") + '</div>', "", true);
    }).catch(showError);
  }

  function fieldHint(field) {
    if (field.type === "boolean") return "OK to toggle";
    if (field.type === "choice") return "◀  Change  ▶";
    if ((field.type === "integer" || field.type === "number") && field.max <= 100) return "◀  Adjust  ▶";
    return "OK to type";
  }

  function fieldValueLabel(field, value) {
    if (field.type === "boolean") return value ? "On" : "Off";
    if (field.type === "choice") return optionLabel(field, value);
    return value === "" || value === undefined ? "Set" : value;
  }

  function renderField(field, values) {
    var value = values && values[field.name] !== undefined ? values[field.name] : (field.default !== undefined ? field.default : "");
    if (field.type === "object" && value && typeof value === "object") value = JSON.stringify(value, null, 2);
    if (field.type === "stringList" && Array.isArray(value)) value = value.join("\n");
    var boundedNumber = (field.type === "integer" || field.type === "number") && field.max <= 100;
    if (field.type === "boolean" || field.type === "choice" || boundedNumber) {
      return '<button class="field-row value-control focusable" type="button" data-field-name="' + escapeHtml(field.name) + '" data-field-type="' + escapeHtml(field.type) + '" data-field-value="' + escapeHtml(value) + '">' +
        '<span><strong>' + escapeHtml(field.label) + '</strong><small>' + escapeHtml(fieldHint(field)) + '</small></span>' +
        '<span class="field-value">' + escapeHtml(fieldValueLabel(field, value)) + '</span></button>';
    }
    var multiline = field.multiline || field.type === "object" || field.type === "stringList";
    var tag = multiline ? "textarea" : "input";
    var type = field.type === "integer" || field.type === "number" ? "number" : "text";
    var attrs = ' class="text-control focusable" data-field-name="' + escapeHtml(field.name) + '" data-field-type="' + escapeHtml(field.type) + '" placeholder="' + escapeHtml(field.placeholder || "") + '"';
    if (!multiline) attrs += ' type="' + type + '" value="' + escapeHtml(value) + '"';
    if (field.min !== undefined) attrs += ' min="' + escapeHtml(field.min) + '"';
    if (field.max !== undefined) attrs += ' max="' + escapeHtml(field.max) + '"';
    if (field.step !== undefined) attrs += ' step="' + escapeHtml(field.step) + '"';
    return '<label class="text-field"><span class="field-label">' + escapeHtml(field.label) + '</span><' + tag + attrs + '>' + (multiline ? escapeHtml(value) : "") + '</' + tag + '></label>';
  }

  function showActionEditor(action, preset, heading) {
    state.editorInputs = Object.assign({}, defaultInputs(action), preset || {});
    var visibleFields = (action.inputs || []).filter(function (field) { return field.label; });
    var warning = action.warning ? '<div class="warning"><strong>Before you continue</strong><p>' + escapeHtml(action.warning) + '</p></div>' : "";
    pushModal('<p class="eyebrow">CONFIGURE ACTION</p><h2>' + escapeHtml(heading || action.title) + '</h2><p class="modal-copy">' + escapeHtml(action.summary) + '</p>' + warning +
      '<div class="field-list" data-action-form="' + escapeHtml(action.id) + '">' + visibleFields.map(function (field) { return renderField(field, state.editorInputs); }).join("") + '</div>' +
      '<div class="modal-actions"><button class="confirm focusable" data-save-action="' + escapeHtml(action.id) + '" type="button">Use this action</button></div>');
  }

  function adjustField(control, direction) {
    var action = actionDefinition(control.closest("[data-action-form]").dataset.actionForm);
    var field = null;
    (action.inputs || []).some(function (candidate) {
      if (candidate.name === control.dataset.fieldName) { field = candidate; return true; }
      return false;
    });
    if (!field) return;
    var value = control.dataset.fieldValue;
    if (field.type === "boolean") value = value !== "true";
    else if (field.type === "choice") {
      var options = field.options || [];
      var index = options.map(function (option) { return String(option.value); }).indexOf(String(value));
      index = (index + direction + options.length) % options.length;
      value = options[index].value;
    } else {
      var number = Number(value || field.min || 0);
      var step = Number(field.step || 1);
      number += step * direction;
      if (field.min !== undefined) number = Math.max(field.min, number);
      if (field.max !== undefined) number = Math.min(field.max, number);
      value = field.type === "integer" ? Math.round(number) : number;
    }
    control.dataset.fieldValue = String(value);
    control.querySelector(".field-value").textContent = fieldValueLabel(field, value);
  }

  function collectInputs(action) {
    var inputs = Object.assign({}, defaultInputs(action), state.editorInputs || {});
    Array.prototype.forEach.call(elements.modalCard.querySelectorAll("[data-field-name]"), function (control) {
      var name = control.dataset.fieldName;
      var type = control.dataset.fieldType;
      var raw = control.classList.contains("value-control") ? control.dataset.fieldValue : control.value;
      if (type === "boolean") inputs[name] = raw === "true";
      else if (type === "integer") {
        if (raw !== "") inputs[name] = parseInt(raw, 10);
        else delete inputs[name];
      } else if (type === "number") {
        if (raw !== "") inputs[name] = Number(raw);
        else delete inputs[name];
      } else if (type === "stringList") {
        if (raw.trim()) inputs[name] = raw.split("\n").map(function (line) { return line.trim(); }).filter(Boolean);
        else delete inputs[name];
      } else if (type === "object") {
        if (raw.trim()) inputs[name] = JSON.parse(raw);
        else delete inputs[name];
      } else if (raw !== "" || (action.inputs || []).some(function (field) { return field.name === name && field.allowEmpty; })) inputs[name] = raw;
      else delete inputs[name];
    });
    return inputs;
  }

  function saveAction(id) {
    var action = actionDefinition(id);
    try {
      applyMapping(state.sourceButton, { function: id, inputs: collectInputs(action) });
    } catch (error) {
      showToast(error.message || String(error));
    }
  }

  function applyMapping(button, action) {
    var config = Object.assign({}, state.status.config || {});
    config[button] = action;
    setBusy(true);
    platform.configure(config).then(function (response) {
      state.status = response.status;
      closeModal(); render(); showToast(label(button) + " updated and active");
    }).catch(showError).finally(function () { setBusy(false); });
  }

  function removeMapping(button) {
    var config = Object.assign({}, state.status.config || {});
    delete config[button];
    setBusy(true);
    platform.configure(config).then(function (response) {
      state.status = response.status; closeModal(); render(); showToast(label(button) + " restored");
    }).catch(showError).finally(function () { setBusy(false); });
  }

  function showMapping(button) {
    state.sourceButton = button;
    rootModal('<p class="eyebrow">REMOTE BUTTON</p><h2>' + escapeHtml(label(button)) + '</h2>' +
      '<p class="modal-copy">' + escapeHtml(describe(state.status.config[button])) + '</p>' +
      '<div class="service-list"><button class="service-row focusable" data-change-mapping type="button"><span>Change action</span><span>›</span></button>' +
      '<button class="service-row focusable" data-restore-mapping type="button"><span>Restore default action</span><span>›</span></button></div>' +
      '<div class="modal-actions"><button class="secondary focusable" data-close type="button">Done</button></div>');
  }

  function showSystem() {
    var active = state.status && state.status.active;
    var blockMouse = state.status && state.status.settings && state.status.settings.block_mouse;
    rootModal('<p class="eyebrow">MAGIC MAPPER</p><h2>Settings</h2>' +
      '<p class="modal-copy">The mapper runs locally on this TV and starts with webOS.</p>' +
      '<div class="service-list"><button class="service-row focusable" data-service="mouse" type="button"><span><strong>Magic Remote pointer</strong><small>Experimental · applies across the TV</small></span><span>' + (blockMouse ? "Blocked" : "Allowed") + ' ›</span></button>' +
      '<button class="service-row focusable" data-service="toggle" type="button"><span>' + (active ? "Stop mapper" : "Start mapper") + '</span><span>→</span></button>' +
      '<button class="service-row focusable" data-service="logs" type="button"><span>View recent log</span><span>→</span></button>' +
      '<button class="service-row focusable" data-service="uninstall" type="button"><span>Uninstall Magic Mapper</span><span>→</span></button></div>' +
      '<div class="modal-actions"><button class="secondary focusable" data-close type="button">Done</button></div>');
  }

  function confirmMouseSetting() {
    var blocked = state.status.settings && state.status.settings.block_mouse;
    pushModal('<p class="eyebrow">EXPERIMENTAL SETTING</p><h2>' + (blocked ? "Restore the pointer?" : "Disable the pointer?") + '</h2>' +
      '<p class="modal-copy">This changes the Magic Remote globally, not just for mapped buttons. The mapper restarts immediately.</p>' +
      '<div class="warning"><strong>Keep a fallback handy</strong><p>You can reverse this setting here with the directional pad and OK button.</p></div>' +
      '<div class="modal-actions"><button class="confirm focusable" data-confirm-mouse type="button">' + (blocked ? "Allow pointer" : "Block pointer") + '</button><button class="secondary focusable" data-back type="button">Cancel</button></div>', "narrow");
  }

  function applyMouseSetting() {
    var settings = Object.assign({}, state.status.settings || {});
    settings.block_mouse = !settings.block_mouse;
    setBusy(true);
    platform.configureSettings(settings).then(function (response) {
      state.status = response.status;
      closeModal(); render(); showToast(settings.block_mouse ? "Magic Remote pointer blocked" : "Magic Remote pointer restored");
    }).catch(showError).finally(function () { setBusy(false); });
  }

  function showLogs() {
    platform.logs().then(function (response) {
      pushModal('<p class="eyebrow">RECENT LOG</p><h2>What the mapper sees</h2><pre class="log">' + escapeHtml(response.log || "No log yet.") + '</pre>' +
        '<div class="modal-actions"><button class="secondary focusable" data-back type="button">Back</button></div>');
    }).catch(showError);
  }

  function confirmUninstall() {
    pushModal('<p class="eyebrow">UNINSTALL</p><h2>Remove everything?</h2><p class="modal-copy">This stops the mapper, restores every remote button, removes your mappings and uninstalls this app.</p>' +
      '<div class="modal-actions"><button class="confirm danger focusable" data-confirm-uninstall type="button">Uninstall</button><button class="secondary focusable" data-back type="button">Keep it</button></div>', "narrow");
  }

  function currentFocusIndex() {
    var focusables = Array.prototype.filter.call(elements.modalCard.querySelectorAll(".focusable"), function (item) { return !item.disabled && item.offsetParent !== null; });
    return Math.max(0, focusables.indexOf(document.activeElement));
  }

  function view(html, extraClass, closable) {
    return { html: html, extraClass: extraClass || "", closable: closable === false ? false : true };
  }

  function renderModal(modalView, focusIndex) {
    state.modal = true;
    state.modalView = modalView;
    elements.modalCard.className = "modal-card " + modalView.extraClass;
    elements.modalCard.innerHTML = modalView.html;
    elements.modal.hidden = false;
    elements.modal.dataset.closable = modalView.closable ? "true" : "false";
    bindDynamicActions();
    focusAt(focusIndex || 0);
  }

  function rootModal(html, extraClass, closable) {
    if (!state.modal) state.returnFocus = document.activeElement;
    state.modalStack = [];
    renderModal(view(html, extraClass, closable), 0);
  }

  function pushModal(html, extraClass, closable) {
    if (!state.modal) { rootModal(html, extraClass, closable); return; }
    state.modalStack.push({ view: state.modalView, focusIndex: currentFocusIndex() });
    renderModal(view(html, extraClass, closable), 0);
  }

  function replaceModal(html, extraClass, closable) {
    renderModal(view(html, extraClass, closable), 0);
  }

  function backModal(force) {
    if (!state.modal) return;
    if (!force && elements.modal.dataset.closable === "false") return;
    if (state.modalStack.length) {
      var previous = state.modalStack.pop();
      renderModal(previous.view, previous.focusIndex);
    } else closeModal();
  }

  function closeModal() {
    var returnFocus = state.returnFocus;
    state.modal = false;
    state.modalView = null;
    state.modalStack = [];
    state.returnFocus = null;
    elements.modal.hidden = true;
    elements.modalCard.innerHTML = "";
    if (returnFocus && document.documentElement.contains(returnFocus)) returnFocus.focus();
    else elements.discover.focus();
  }

  function showError(error) {
    setBusy(false);
    var html = '<p class="eyebrow">COULD NOT COMPLETE</p><h2>Something got in the way.</h2><p class="modal-copy">' + escapeHtml(error.message || error) + '</p>' +
      '<div class="modal-actions"><button class="secondary focusable" data-close type="button">Close</button></div>';
    rootModal(html, "narrow");
  }

  function bindDynamicActions() {
    Array.prototype.forEach.call(document.querySelectorAll("[data-edit]"), function (button) { button.onclick = function () { showMapping(button.dataset.edit); }; });
    Array.prototype.forEach.call(elements.modalCard.querySelectorAll("[data-close]"), function (button) { button.onclick = closeModal; });
    Array.prototype.forEach.call(elements.modalCard.querySelectorAll("[data-back]"), function (button) { button.onclick = function () { backModal(); }; });
    Array.prototype.forEach.call(elements.modalCard.querySelectorAll("[data-category]"), function (button) { button.onclick = function () { showCategory(button.dataset.category); }; });
    Array.prototype.forEach.call(elements.modalCard.querySelectorAll("[data-action-id]"), function (button) { button.onclick = function () { chooseAction(button.dataset.actionId); }; });
    Array.prototype.forEach.call(elements.modalCard.querySelectorAll("[data-app-id]"), function (button) {
      button.onclick = function () {
        var current = state.status.config && state.status.config[state.sourceButton];
        var preset = current && !Array.isArray(current) && current.function === "launch_app" && current.inputs.app_id === button.dataset.appId ? current.inputs : {};
        showActionEditor(actionDefinition("launch_app"), Object.assign({}, preset, { app_id: button.dataset.appId, app_title: button.dataset.appTitle }), "Open " + button.dataset.appTitle);
      };
    });
    Array.prototype.forEach.call(elements.modalCard.querySelectorAll(".value-control"), function (button) { button.onclick = function () { adjustField(button, 1); }; });
    Array.prototype.forEach.call(elements.modalCard.querySelectorAll("[data-save-action]"), function (button) { button.onclick = function () { saveAction(button.dataset.saveAction); }; });
    Array.prototype.forEach.call(elements.modalCard.querySelectorAll("[data-service]"), function (button) {
      button.onclick = function () {
        if (button.dataset.service === "toggle") {
          var action = state.status.active ? platform.stop() : platform.start();
          setBusy(true); action.then(refresh).then(closeModal).finally(function () { setBusy(false); });
        }
        if (button.dataset.service === "mouse") confirmMouseSetting();
        if (button.dataset.service === "logs") showLogs();
        if (button.dataset.service === "uninstall") confirmUninstall();
      };
    });
    var confirm = elements.modalCard.querySelector("[data-confirm-uninstall]");
    if (confirm) confirm.onclick = function () { setBusy(true); platform.uninstall().then(function () { closeModal(); showToast("Magic Mapper removed"); }).catch(showError); };
    var confirmMouse = elements.modalCard.querySelector("[data-confirm-mouse]");
    if (confirmMouse) confirmMouse.onclick = applyMouseSetting;
    var change = elements.modalCard.querySelector("[data-change-mapping]");
    if (change) change.onclick = function () { showActionChoices(state.sourceButton, "push"); };
    var restore = elements.modalCard.querySelector("[data-restore-mapping]");
    if (restore) restore.onclick = function () { removeMapping(state.sourceButton); };
  }

  function focusAt(index) {
    window.setTimeout(function () {
      var candidates = Array.prototype.filter.call(elements.modalCard.querySelectorAll(".focusable"), function (item) { return !item.disabled && item.offsetParent !== null; });
      if (candidates[index] || candidates[0]) (candidates[index] || candidates[0]).focus();
    }, 0);
  }

  function moveFocus(direction) {
    var scope = state.modal ? elements.modalCard : document;
    var candidates = Array.prototype.filter.call(scope.querySelectorAll(".focusable"), function (item) { return !item.disabled && item.offsetParent !== null; });
    var current = document.activeElement;
    if (candidates.indexOf(current) < 0) { if (candidates[0]) candidates[0].focus(); return; }
    var from = current.getBoundingClientRect();
    var originX = from.left + from.width / 2, originY = from.top + from.height / 2;
    var best = null, bestScore = Infinity;
    candidates.forEach(function (candidate) {
      if (candidate === current) return;
      var box = candidate.getBoundingClientRect();
      var dx = box.left + box.width / 2 - originX, dy = box.top + box.height / 2 - originY;
      if ((direction === "left" && dx >= -4) || (direction === "right" && dx <= 4) || (direction === "up" && dy >= -4) || (direction === "down" && dy <= 4)) return;
      var primary = direction === "left" || direction === "right" ? Math.abs(dx) : Math.abs(dy);
      var secondary = direction === "left" || direction === "right" ? Math.abs(dy) : Math.abs(dx);
      var score = primary + secondary * 2.2;
      if (score < bestScore) { bestScore = score; best = candidate; }
    });
    if (best) {
      best.focus();
      best.scrollIntoView({ block: "nearest", inline: "nearest" });
    }
  }

  function consumeBack(event) {
    event.preventDefault();
    event.stopPropagation();
    if (event.stopImmediatePropagation) event.stopImmediatePropagation();
  }

  window.addEventListener("keydown", function (event) {
    var directions = { 37: "left", 38: "up", 39: "right", 40: "down" };
    var current = document.activeElement;
    var typing = current && (current.tagName === "INPUT" || current.tagName === "TEXTAREA");
    if ((event.keyCode === 37 || event.keyCode === 39) && current && current.classList.contains("value-control")) {
      event.preventDefault(); adjustField(current, event.keyCode === 37 ? -1 : 1); return;
    }
    if (directions[event.keyCode] && !(typing && (event.keyCode === 37 || event.keyCode === 39))) { event.preventDefault(); moveFocus(directions[event.keyCode]); }
    if (event.keyCode === 461 || event.keyCode === 27) { consumeBack(event); backModal(); }
  }, true);

  window.addEventListener("keyup", function (event) {
    if (event.keyCode === 461) consumeBack(event);
  }, true);

  elements.primary.onclick = primaryAction;
  elements.discover.onclick = function () { startDiscovery("source"); };
  elements.system.onclick = showSystem;

  Promise.all([
    platform.catalog(),
    platform.status(),
    platform.capabilities().catch(function () { return { capabilities: { piccap: false } }; })
  ]).then(function (responses) {
    indexCatalog(responses[0]);
    state.status = responses[1].status;
    state.capabilities = responses[2].capabilities || {};
    render();
    (state.status && state.status.active ? elements.discover : elements.primary).focus();
  }).catch(showError);
}());
