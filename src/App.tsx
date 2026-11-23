import type { JSX } from "@solidjs/web";
import { For, Show, createSignal, onCleanup } from "solid-js";

import catalogSource from "../runtime/action_catalog.json";
import { ControllerError, platform } from "./platform";
import type {
  ActionCatalog,
  ActionDefinition,
  ActionField,
  InputValues,
  MapperConfig,
  MapperSettings,
  MapperStatus,
  Mapping,
  MappingAction,
} from "./types";

type Direction = "down" | "left" | "right" | "up";
type DiscoveryPurpose = "source" | "target";
type EditorValues = Record<string, unknown>;

interface ModalView {
  closable: boolean;
  content: () => JSX.Element;
  extraClass: string;
}

interface ModalHistory {
  focusIndex: number;
  view: ModalView;
}

const catalog = catalogSource as ActionCatalog;
const actions = new Map(catalog.actions.map((action) => [action.id, action]));
const categories = new Map(catalog.categories.map((category) => [category.id, category]));

const buttonLabels: Record<string, string> = {
  "...": "More actions",
  "...alt": "More actions",
  alexa: "Alexa",
  channels_alt: "Channels",
  ch_down: "Channel down",
  ch_up: "Channel up",
  disney: "Disney+",
  fastforward: "Fast-forward",
  google: "Google Assistant",
  lg_channels: "LG Channels",
  netflix: "Netflix",
  prime: "Prime Video",
  rakuten: "Rakuten TV",
  search_alt: "Search",
  vol_down: "Volume down",
  vol_up: "Volume up",
};

const emptyStatus: MapperStatus = {
  active: false,
  config: {},
  installed: false,
  settings: {},
};

function label(button: string): string {
  return (
    buttonLabels[button] ??
    button.replace(/_/g, " ").replace(/(^|\s)\S/g, (letter) => letter.toUpperCase())
  );
}

function actionDefinition(id: string): ActionDefinition | undefined {
  return actions.get(id);
}

function optionLabel(field: ActionField, value: unknown): string {
  return (
    field.options?.find((option) => String(option.value) === String(value))?.label ?? String(value)
  );
}

function describeAction(mapping: MappingAction): string {
  const definition = actionDefinition(mapping.function);
  const inputs = mapping.inputs ?? {};
  if (mapping.function === "launch_app")
    return `Opens ${String(inputs.app_title ?? inputs.app_id)}`;
  if (mapping.function === "press_button") return `Acts like ${label(String(inputs.button))}`;
  if (mapping.function === "set_oled_backlight")
    return `OLED light set to ${String(inputs.backlight)}`;
  if (mapping.function === "increase_oled_light" || mapping.function === "reduce_oled_light") {
    return `${definition?.title ?? label(mapping.function)} by ${String(inputs.increment ?? 10)}`;
  }
  if (mapping.function === "set_energy_mode") return `Energy saving: ${label(String(inputs.mode))}`;
  if (mapping.function === "set_dynamic_tone_mapping")
    return `Dynamic Tone Mapping: ${String(inputs.value)}`;
  if (mapping.function === "send_ir_command") return `IR: ${String(inputs.keycode)}`;
  if (mapping.function === "curl") return `${String(inputs.method ?? "GET")} ${String(inputs.url)}`;
  if (mapping.function === "send_tcp_command")
    return `TCP: ${String(inputs.ip)}:${String(inputs.port)}`;
  if (mapping.function === "send_cec_button") return `HDMI-CEC code ${String(inputs.code)}`;
  return definition?.title ?? mapping.function.replace(/_/g, " ");
}

function appConditionLabel(appId: string | undefined): string {
  if (!appId) return "Always";
  if (appId === "!") return "Fallback";
  return `In ${appId}`;
}

function isCustomMapping(mapping: Mapping | undefined): boolean {
  if (!mapping || mapping === "disabled") return false;
  if (Array.isArray(mapping)) {
    return mapping.length > 1 || mapping.some((action) => Boolean(action.appId));
  }
  return Boolean(mapping.appId);
}

function describe(mapping: Mapping | undefined): string {
  if (mapping === "disabled") return "Disabled, default action blocked";
  if (Array.isArray(mapping)) {
    if (mapping.length === 1) return describe(mapping[0]);
    return `${mapping.length} custom actions`;
  }
  if (!mapping) return "Unchanged";
  const base = describeAction(mapping);
  if (!mapping.appId) return base;
  return `${base} (${mapping.appId === "!" ? "fallback" : `in ${mapping.appId}`})`;
}

function defaultInputs(action: ActionDefinition): EditorValues {
  return Object.fromEntries(
    action.inputs
      .filter((field) => field.default !== undefined)
      .map((field) => [field.name, field.default]),
  );
}

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

function focusableElements(scope: ParentNode): HTMLElement[] {
  return Array.from(scope.querySelectorAll<HTMLElement>(".focusable")).filter(
    (item) => !(item instanceof HTMLButtonElement && item.disabled) && item.offsetParent !== null,
  );
}

function view(content: () => JSX.Element, extraClass = "", closable = true): ModalView {
  return { closable, content, extraClass };
}

function DiscoveryView(props: { purpose: DiscoveryPurpose }): JSX.Element {
  return (
    <>
      <p class="eyebrow">BUTTON DISCOVERY</p>
      <h2>{props.purpose === "target" ? "Press the target button" : "Press one remote button"}</h2>
      <p class="modal-copy">
        {props.purpose === "target"
          ? "The first button will behave like this one."
          : "Its normal action will be blocked while we identify it."}
      </p>
      <div class="discovery-signal">···</div>
      <p class="modal-copy">Waiting for a complete press…</p>
    </>
  );
}

function fieldHint(field: ActionField): string {
  if (field.type === "boolean") return "OK to toggle";
  if (field.type === "choice") return "◀  Change  ▶";
  if (
    (field.type === "integer" || field.type === "number") &&
    field.max !== undefined &&
    field.max <= 100
  )
    return "◀  Adjust  ▶";
  return "OK to type";
}

function isValueControl(field: ActionField): boolean {
  return (
    field.type === "boolean" ||
    field.type === "choice" ||
    ((field.type === "integer" || field.type === "number") &&
      field.max !== undefined &&
      field.max <= 100)
  );
}

function displayInputValue(field: ActionField, value: unknown): string {
  if (field.type === "object" && value && typeof value === "object")
    return JSON.stringify(value, null, 2);
  if (field.type === "stringList" && Array.isArray(value)) return value.join("\n");
  return value === undefined ? "" : String(value);
}

function consumeBack(event: KeyboardEvent): void {
  event.preventDefault();
  event.stopPropagation();
  event.stopImmediatePropagation();
}

export default function App(): JSX.Element {
  const [status, setStatus] = createSignal<MapperStatus>(emptyStatus);
  const [ready, setReady] = createSignal(false);
  const [startupError, setStartupError] = createSignal<{ code?: string; message: string }>();
  const [capabilities, setCapabilities] = createSignal<Record<string, boolean>>({});
  const [busy, setBusySignal] = createSignal(false);
  const [toast, setToast] = createSignal<string>();
  const [sourceButton, setSourceButton] = createSignal<string>();
  const [modalView, setModalView] = createSignal<ModalView>();
  const [modalStack, setModalStack] = createSignal<ModalHistory[]>([]);
  const [editorInputs, setEditorInputs] = createSignal<EditorValues>({});
  let modalCard: HTMLElement | undefined;
  let discoverButton: HTMLButtonElement | undefined;
  let returnFocus: HTMLElement | null = null;
  let toastTimer: number | undefined;

  const entries = () => {
    // oxlint-disable-next-line unicorn/no-array-sort -- Older webOS Chromium lacks Array.prototype.toSorted.
    return Object.keys(status().config ?? {}).sort();
  };

  function setBusy(value: boolean): void {
    setBusySignal(value);
    document.body.classList.toggle("is-busy", value);
  }

  function showToast(message: string): void {
    setToast(message);
    window.clearTimeout(toastTimer);
    toastTimer = window.setTimeout(() => setToast(undefined), 3_000);
  }

  async function refresh(): Promise<MapperStatus> {
    try {
      const response = await platform.status();
      setStatus(response.status);
      return response.status;
    } catch (error) {
      showError(error);
      throw error;
    }
  }

  function focusAt(index = 0): void {
    window.setTimeout(() => {
      if (!modalCard) return;
      const candidates = focusableElements(modalCard);
      (candidates[index] ?? candidates[0])?.focus();
    }, 0);
  }

  function currentFocusIndex(): number {
    if (!modalCard) return 0;
    return Math.max(0, focusableElements(modalCard).indexOf(document.activeElement as HTMLElement));
  }

  function renderModal(nextView: ModalView, focusIndex = 0): void {
    setModalView(nextView);
    focusAt(focusIndex);
  }

  function rootModal(content: () => JSX.Element, extraClass = "", closable = true): void {
    if (!modalView()) returnFocus = document.activeElement as HTMLElement | null;
    setModalStack([]);
    renderModal(view(content, extraClass, closable));
  }

  function pushModal(content: () => JSX.Element, extraClass = "", closable = true): void {
    const current = modalView();
    if (!current) {
      rootModal(content, extraClass, closable);
      return;
    }
    setModalStack((stack) => [...stack, { view: current, focusIndex: currentFocusIndex() }]);
    renderModal(view(content, extraClass, closable));
  }

  function replaceModal(content: () => JSX.Element, extraClass = "", closable = true): void {
    renderModal(view(content, extraClass, closable));
  }

  function closeModal(): void {
    const target = returnFocus;
    setModalView(undefined);
    setModalStack([]);
    returnFocus = null;
    window.setTimeout(() => {
      if (target && document.documentElement.contains(target)) target.focus();
      else discoverButton?.focus();
    }, 0);
  }

  function backModal(force = false): void {
    const current = modalView();
    if (!current || (!force && !current.closable)) return;
    const stack = modalStack();
    const previous = stack[stack.length - 1];
    if (!previous) {
      closeModal();
      return;
    }
    setModalStack(stack.slice(0, -1));
    renderModal(previous.view, previous.focusIndex);
  }

  function showError(error: unknown): void {
    setBusy(false);
    rootModal(
      () => (
        <>
          <p class="eyebrow">COULD NOT COMPLETE</p>
          <h2>Something got in the way.</h2>
          <p class="modal-copy">{errorMessage(error)}</p>
          <div class="modal-actions">
            <button class="secondary focusable" type="button" onClick={closeModal}>
              Close
            </button>
          </div>
        </>
      ),
      "narrow",
    );
  }

  async function primaryAction(): Promise<void> {
    if (!status().installed) {
      setBusy(true);
      try {
        const response = await platform.install();
        setStatus(response.status);
        showToast(response.migrated ? "Existing mappings imported" : "Magic Mapper is active");
      } catch (error) {
        showError(error);
      } finally {
        setBusy(false);
      }
    } else if (!status().active) {
      setBusy(true);
      try {
        await platform.start();
        await refresh();
        showToast("Mapper started");
      } catch (error) {
        showError(error);
      } finally {
        setBusy(false);
      }
    } else {
      startDiscovery("source");
    }
  }

  function startDiscovery(purpose: DiscoveryPurpose): void {
    if (!status().active || busy()) return;
    const requestId = `${Date.now()}-${Math.floor(Math.random() * 10_000)}`;
    const content = () => <DiscoveryView purpose={purpose} />;
    if (purpose === "target") pushModal(content, "narrow", false);
    else rootModal(content, "narrow", false);
    platform
      .discover(requestId)
      .then(() => pollDiscovery(requestId, purpose, 0))
      .catch(showError);
  }

  function pollDiscovery(requestId: string, purpose: DiscoveryPurpose, attempts: number): void {
    window.setTimeout(async () => {
      try {
        const result = await platform.discoveryResult(requestId);
        if (result.pending && attempts < 28) {
          pollDiscovery(requestId, purpose, attempts + 1);
          return;
        }
        if (!result.ok && result.error === "cancelled") {
          if (purpose === "target") backModal(true);
          else closeModal();
          return;
        }
        if (!result.ok) throw new Error(result.error ?? "No button was detected");
        if (result.pending || !result.button) throw new Error("No button was detected");
        if (result.button.startsWith("code_")) {
          throw new Error(`That button is not supported yet (code ${String(result.code)})`);
        }
        if (purpose === "target") {
          await applyMapping(sourceButton(), {
            function: "press_button",
            inputs: { button: result.button },
          });
        } else {
          setSourceButton(result.button);
          showActionChoices(result.button, "replace");
        }
      } catch (error) {
        showError(error);
      }
    }, 450);
  }

  function CategoryChoices(): JSX.Element {
    return (
      <div class="choices category-list">
        <For each={catalog.categories}>
          {(category) => {
            const count = catalog.actions.filter(
              (action) => action.category === category.id,
            ).length;
            return (
              <button
                class="choice focusable"
                type="button"
                data-category={category.id}
                onClick={() => showCategory(category.id)}
              >
                <span class="choice-copy">
                  <strong>{category.title}</strong>
                  <span>{category.summary}</span>
                </span>
                <span class="choice-meta">
                  {count}
                  <span class="chevron" aria-hidden="true">
                    ›
                  </span>
                </span>
              </button>
            );
          }}
        </For>
      </div>
    );
  }

  function ActionChoicesView(props: { button: string }): JSX.Element {
    return (
      <>
        <p class="eyebrow">{label(props.button).toUpperCase()} FOUND</p>
        <h2>What should it do?</h2>
        <p class="modal-copy">Choose a group. Back always returns one level.</p>
        <CategoryChoices />
      </>
    );
  }

  function showActionChoices(button: string, mode: "push" | "replace" | "root"): void {
    setSourceButton(button);
    const content = () => <ActionChoicesView button={button} />;
    if (mode === "replace") replaceModal(content);
    else if (mode === "push") pushModal(content);
    else rootModal(content);
  }

  function CategoryView(props: { categoryId: string }): JSX.Element {
    const category = categories.get(props.categoryId);
    if (!category) return <></>;
    const current = status().config[sourceButton() ?? ""];
    const currentId =
      current === "disabled"
        ? "disabled"
        : current && !Array.isArray(current)
          ? current.function
          : undefined;
    const categoryActions = catalog.actions.filter(
      (action) => action.category === props.categoryId,
    );
    return (
      <>
        <p class="eyebrow">CHOOSE ACTION</p>
        <h2>{category.title}</h2>
        <p class="modal-copy">{category.summary}</p>
        <div class="choices action-list">
          <For each={categoryActions}>
            {(action) => {
              const unavailable = Boolean(
                action.requires && capabilities()[action.requires] === false,
              );
              const meta = unavailable
                ? "Not installed"
                : action.id === currentId
                  ? "Current"
                  : action.warning
                    ? "Advanced"
                    : "";
              return (
                <button
                  class="choice focusable"
                  type="button"
                  data-action-id={action.id}
                  disabled={unavailable}
                  onClick={() => chooseAction(action.id)}
                >
                  <span class="choice-copy">
                    <strong>{action.title}</strong>
                    <span>
                      {unavailable
                        ? `Install ${action.requires} to use this action.`
                        : action.summary}
                    </span>
                  </span>
                  <span class="choice-meta">
                    {meta}
                    <span class="chevron" aria-hidden="true">
                      ›
                    </span>
                  </span>
                </button>
              );
            }}
          </For>
        </div>
      </>
    );
  }

  function showCategory(categoryId: string): void {
    pushModal(() => <CategoryView categoryId={categoryId} />);
  }

  function chooseAction(id: string): void {
    const action = actionDefinition(id);
    if (!action || (action.requires && capabilities()[action.requires] === false)) return;
    if (action.editor === "apps") {
      showApps();
      return;
    }
    if (action.editor === "button") {
      startDiscovery("target");
      return;
    }
    if (action.editor === "fields") {
      const current = status().config[sourceButton() ?? ""];
      const existing =
        current && current !== "disabled" && !Array.isArray(current) && current.function === id
          ? current.inputs
          : undefined;
      showActionEditor(action, existing);
      return;
    }
    void applyMapping(
      sourceButton(),
      id === "disabled"
        ? "disabled"
        : { function: id, inputs: defaultInputs(action) as InputValues },
    );
  }

  function AppsView(props: { apps: { id: string; title: string }[] }): JSX.Element {
    return (
      <>
        <p class="eyebrow">CHOOSE DESTINATION</p>
        <h2>Open an app</h2>
        <div class="app-list">
          <For each={props.apps}>
            {(app) => (
              <button
                class="app-option focusable"
                type="button"
                data-app-id={app.id}
                data-app-title={app.title}
                onClick={() => chooseApp(app)}
              >
                <span>{app.title}</span>
                <small>{app.id}</small>
              </button>
            )}
          </For>
        </div>
      </>
    );
  }

  function showApps(): void {
    pushModal(
      () => (
        <>
          <p class="eyebrow">CHOOSE DESTINATION</p>
          <h2>Open an app</h2>
          <p class="modal-copy">Loading the apps installed on this TV…</p>
        </>
      ),
      "",
      false,
    );
    platform
      .apps()
      .then((response) => replaceModal(() => <AppsView apps={response.apps} />))
      .catch(showError);
  }

  function chooseApp(app: { id: string; title: string }): void {
    const current = status().config[sourceButton() ?? ""];
    const preset =
      current &&
      current !== "disabled" &&
      !Array.isArray(current) &&
      current.function === "launch_app" &&
      current.inputs.app_id === app.id
        ? current.inputs
        : {};
    const action = actionDefinition("launch_app");
    if (action)
      showActionEditor(
        action,
        { ...preset, app_id: app.id, app_title: app.title },
        `Open ${app.title}`,
      );
  }

  function fieldValueLabel(field: ActionField, value: unknown): string {
    if (field.type === "boolean") return value ? "On" : "Off";
    if (field.type === "choice") return optionLabel(field, value);
    return value === "" || value === undefined ? "Set" : String(value);
  }

  function updateEditorInput(name: string, value: unknown): void {
    setEditorInputs((inputs) => ({ ...inputs, [name]: value }));
  }

  function ActionFieldEditor(props: { field: ActionField }): JSX.Element {
    const value = () => editorInputs()[props.field.name] ?? props.field.default ?? "";
    if (isValueControl(props.field)) {
      return (
        <button
          class="field-row value-control focusable"
          type="button"
          data-field-name={props.field.name}
          data-field-type={props.field.type}
          data-field-value={String(value())}
          onClick={(event) => adjustField(event.currentTarget, 1)}
        >
          <span>
            <strong>{props.field.label}</strong>
            <small>{fieldHint(props.field)}</small>
          </span>
          <span class="field-value">{fieldValueLabel(props.field, value())}</span>
        </button>
      );
    }
    const multiline =
      props.field.multiline || props.field.type === "object" || props.field.type === "stringList";
    return (
      <label class="text-field">
        <span class="field-label">{props.field.label}</span>
        <Show
          when={multiline}
          fallback={
            <input
              class="text-control focusable"
              type={
                props.field.type === "integer" || props.field.type === "number" ? "number" : "text"
              }
              data-field-name={props.field.name}
              data-field-type={props.field.type}
              placeholder={props.field.placeholder ?? ""}
              min={props.field.min}
              max={props.field.max}
              step={props.field.step}
              value={displayInputValue(props.field, value())}
              onInput={(event) => updateEditorInput(props.field.name, event.currentTarget.value)}
            />
          }
        >
          <textarea
            class="text-control focusable"
            data-field-name={props.field.name}
            data-field-type={props.field.type}
            placeholder={props.field.placeholder ?? ""}
            value={displayInputValue(props.field, value())}
            onInput={(event) => updateEditorInput(props.field.name, event.currentTarget.value)}
          />
        </Show>
      </label>
    );
  }

  function ActionEditorView(props: { action: ActionDefinition; heading?: string }): JSX.Element {
    const fields = props.action.inputs.filter((field) => field.label);
    return (
      <>
        <p class="eyebrow">CONFIGURE ACTION</p>
        <h2>{props.heading ?? props.action.title}</h2>
        <p class="modal-copy">{props.action.summary}</p>
        <Show when={props.action.warning}>
          <div class="warning">
            <strong>Before you continue</strong>
            <p>{props.action.warning}</p>
          </div>
        </Show>
        <div class="field-list" data-action-form={props.action.id}>
          <For each={fields}>{(field) => <ActionFieldEditor field={field} />}</For>
        </div>
        <div class="modal-actions">
          <button
            class="confirm focusable"
            type="button"
            data-save-action={props.action.id}
            onClick={() => saveAction(props.action)}
          >
            Use this action
          </button>
        </div>
      </>
    );
  }

  function showActionEditor(
    action: ActionDefinition,
    preset: EditorValues = {},
    heading?: string,
  ): void {
    setEditorInputs({ ...defaultInputs(action), ...preset });
    pushModal(() => <ActionEditorView action={action} heading={heading} />);
  }

  function adjustField(control: HTMLElement, direction: -1 | 1): void {
    const actionId = control.closest<HTMLElement>("[data-action-form]")?.dataset.actionForm;
    const action = actionId ? actionDefinition(actionId) : undefined;
    const field = action?.inputs.find((candidate) => candidate.name === control.dataset.fieldName);
    if (!field) return;
    let value: unknown = editorInputs()[field.name] ?? field.default ?? "";
    if (field.type === "boolean") {
      value = value !== true && value !== "true";
    } else if (field.type === "choice") {
      const options = field.options ?? [];
      const currentIndex = options.findIndex((option) => String(option.value) === String(value));
      const nextIndex = (Math.max(0, currentIndex) + direction + options.length) % options.length;
      value = options[nextIndex]?.value;
    } else {
      let number = Number(value || field.min || 0) + Number(field.step ?? 1) * direction;
      if (field.min !== undefined) number = Math.max(field.min, number);
      if (field.max !== undefined) number = Math.min(field.max, number);
      value = field.type === "integer" ? Math.round(number) : number;
    }
    updateEditorInput(field.name, value);
  }

  function collectInputs(action: ActionDefinition): InputValues {
    const result: Record<string, unknown> = { ...defaultInputs(action), ...editorInputs() };
    for (const field of action.inputs) {
      const raw = result[field.name];
      if (field.type === "boolean") result[field.name] = raw === true || raw === "true";
      else if (field.type === "integer") {
        if (raw !== "" && raw !== undefined) result[field.name] = Number.parseInt(String(raw), 10);
        else delete result[field.name];
      } else if (field.type === "number") {
        if (raw !== "" && raw !== undefined) result[field.name] = Number(raw);
        else delete result[field.name];
      } else if (field.type === "stringList") {
        if (Array.isArray(raw)) result[field.name] = raw;
        else if (String(raw ?? "").trim())
          result[field.name] = String(raw)
            .split("\n")
            .map((line) => line.trim())
            .filter(Boolean);
        else delete result[field.name];
      } else if (field.type === "object") {
        if (raw && typeof raw === "object") result[field.name] = raw;
        else if (String(raw ?? "").trim()) result[field.name] = JSON.parse(String(raw));
        else delete result[field.name];
      } else if (raw === "" && !field.allowEmpty) {
        delete result[field.name];
      }
    }
    return result as InputValues;
  }

  function saveAction(action: ActionDefinition): void {
    try {
      void applyMapping(sourceButton(), { function: action.id, inputs: collectInputs(action) });
    } catch (error) {
      showToast(errorMessage(error));
    }
  }

  async function applyMapping(button: string | undefined, mapping: Mapping): Promise<void> {
    if (!button) return;
    const config: MapperConfig = { ...status().config, [button]: mapping };
    setBusy(true);
    try {
      const response = await platform.configure(config);
      setStatus(response.status);
      closeModal();
      showToast(`${label(button)} updated and active`);
    } catch (error) {
      showError(error);
    } finally {
      setBusy(false);
    }
  }

  async function removeMapping(button: string): Promise<void> {
    const config = { ...status().config };
    delete config[button];
    setBusy(true);
    try {
      const response = await platform.configure(config);
      setStatus(response.status);
      closeModal();
      showToast(`${label(button)} restored`);
    } catch (error) {
      showError(error);
    } finally {
      setBusy(false);
    }
  }

  function MappingView(props: { button: string }): JSX.Element {
    const current = () => status().config[props.button];
    const customActions = (): MappingAction[] => {
      const mapping = current();
      if (!mapping || mapping === "disabled") return [];
      return Array.isArray(mapping) ? mapping : [mapping];
    };
    return (
      <>
        <p class="eyebrow">REMOTE BUTTON</p>
        <h2>{label(props.button)}</h2>
        <p class="modal-copy">{describe(current())}</p>
        <Show when={isCustomMapping(current())}>
          <ul class="mapping-breakdown">
            <For each={customActions()}>
              {(action) => (
                <li>
                  <strong>{appConditionLabel(action.appId)}</strong>
                  <span>{describeAction(action)}</span>
                </li>
              )}
            </For>
          </ul>
          <div class="warning">
            <strong>Custom configuration</strong>
            <p>
              Choosing Change action replaces this multi-action or per-app rule with a single
              action.
            </p>
          </div>
        </Show>
        <div class="service-list">
          <button
            class="service-row focusable"
            type="button"
            data-change-mapping
            onClick={() => showActionChoices(props.button, "push")}
          >
            <span>Change action</span>
            <span>›</span>
          </button>
          <button
            class="service-row focusable"
            type="button"
            data-restore-mapping
            onClick={() => void removeMapping(props.button)}
          >
            <span>Restore default action</span>
            <span>›</span>
          </button>
        </div>
        <div class="modal-actions">
          <button class="secondary focusable" type="button" onClick={closeModal}>
            Done
          </button>
        </div>
      </>
    );
  }

  function showMapping(button: string): void {
    setSourceButton(button);
    rootModal(() => <MappingView button={button} />);
  }

  function SettingsView(): JSX.Element {
    const active = () => status().active;
    const blockMouse = () => Boolean(status().settings?.block_mouse);
    const irEnabled = () => Boolean(status().settings?.ir_input_enabled);
    return (
      <>
        <p class="eyebrow">MAGIC MAPPER</p>
        <h2>Settings</h2>
        <p class="modal-copy">The mapper runs locally on this TV and starts with webOS.</p>
        <div class="service-list">
          <button class="service-row focusable" type="button" onClick={confirmMouseSetting}>
            <span>
              <strong>Magic Remote pointer</strong>
              <small>Experimental · applies across the TV</small>
            </span>
            <span>{blockMouse() ? "Blocked" : "Allowed"} ›</span>
          </button>
          <button
            class="service-row focusable"
            type="button"
            data-ir-input
            onClick={() => void toggleIrInput()}
          >
            <span>
              <strong>IR remote input</strong>
              <small>Also read infrared presses, including when Bluetooth is off</small>
            </span>
            <span>{irEnabled() ? "On" : "Off"} ›</span>
          </button>
          <button class="service-row focusable" type="button" onClick={() => void toggleService()}>
            <span>{active() ? "Stop mapper" : "Start mapper"}</span>
            <span>→</span>
          </button>
          <button class="service-row focusable" type="button" onClick={() => void showLogs()}>
            <span>View recent log</span>
            <span>→</span>
          </button>
          <button class="service-row focusable" type="button" onClick={confirmUninstall}>
            <span>Uninstall Magic Mapper</span>
            <span>→</span>
          </button>
        </div>
        <div class="modal-actions">
          <button class="secondary focusable" type="button" onClick={closeModal}>
            Done
          </button>
        </div>
      </>
    );
  }

  function showSystem(): void {
    rootModal(() => <SettingsView />);
  }

  async function toggleService(): Promise<void> {
    setBusy(true);
    try {
      if (status().active) await platform.stop();
      else await platform.start();
      await refresh();
      closeModal();
    } catch (error) {
      showError(error);
    } finally {
      setBusy(false);
    }
  }

  function confirmMouseSetting(): void {
    const blocked = Boolean(status().settings?.block_mouse);
    pushModal(
      () => (
        <>
          <p class="eyebrow">EXPERIMENTAL SETTING</p>
          <h2>{blocked ? "Restore the pointer?" : "Disable the pointer?"}</h2>
          <p class="modal-copy">
            This changes the Magic Remote globally, not just for mapped buttons. The mapper restarts
            immediately.
          </p>
          <div class="warning">
            <strong>Keep a fallback handy</strong>
            <p>You can reverse this setting here with the directional pad and OK button.</p>
          </div>
          <div class="modal-actions">
            <button
              class="confirm focusable"
              type="button"
              onClick={() => void applyMouseSetting()}
            >
              {blocked ? "Allow pointer" : "Block pointer"}
            </button>
            <button class="secondary focusable" type="button" onClick={() => backModal()}>
              Cancel
            </button>
          </div>
        </>
      ),
      "narrow",
    );
  }

  async function applyMouseSetting(): Promise<void> {
    const settings: MapperSettings = {
      ...status().settings,
      block_mouse: !status().settings?.block_mouse,
    };
    setBusy(true);
    try {
      const response = await platform.configureSettings(settings);
      setStatus(response.status);
      closeModal();
      showToast(
        settings.block_mouse ? "Magic Remote pointer blocked" : "Magic Remote pointer restored",
      );
    } catch (error) {
      showError(error);
    } finally {
      setBusy(false);
    }
  }

  async function toggleIrInput(): Promise<void> {
    const nextEnabled = !status().settings?.ir_input_enabled;
    const settings: MapperSettings = {
      ...status().settings,
      ir_exclusive_mode: status().settings?.ir_exclusive_mode ?? true,
      ir_input_enabled: nextEnabled,
    };
    setBusy(true);
    try {
      const response = await platform.configureSettings(settings);
      setStatus(response.status);
      showToast(nextEnabled ? "IR remote input enabled" : "IR remote input disabled");
    } catch (error) {
      showError(error);
    } finally {
      setBusy(false);
    }
  }

  async function showLogs(): Promise<void> {
    try {
      const response = await platform.logs();
      pushModal(() => (
        <>
          <p class="eyebrow">RECENT LOG</p>
          <h2>What the mapper sees</h2>
          <p class="modal-copy">Use Up and Down to scroll the log.</p>
          <pre
            ref={(element) => {
              window.setTimeout(() => {
                element.scrollTop = element.scrollHeight;
              }, 0);
            }}
            class="log focusable"
            aria-label="Recent mapper log"
            // oxlint-disable-next-line jsx-a11y/no-noninteractive-tabindex -- Scrollable log needs D-pad focus on TV.
            tabindex={0}
          >
            {response.log || "No log yet."}
          </pre>
          <div class="modal-actions">
            <button class="secondary focusable" type="button" onClick={() => backModal()}>
              Back
            </button>
          </div>
        </>
      ));
    } catch (error) {
      showError(error);
    }
  }

  function confirmUninstall(): void {
    pushModal(
      () => (
        <>
          <p class="eyebrow">UNINSTALL</p>
          <h2>Remove everything?</h2>
          <p class="modal-copy">
            This stops the mapper, restores every remote button, removes your mappings and
            uninstalls this app.
          </p>
          <div class="modal-actions">
            <button class="confirm danger focusable" type="button" onClick={() => void uninstall()}>
              Uninstall
            </button>
            <button class="secondary focusable" type="button" onClick={() => backModal()}>
              Keep it
            </button>
          </div>
        </>
      ),
      "narrow",
    );
  }

  async function uninstall(): Promise<void> {
    setBusy(true);
    try {
      await platform.uninstall();
      closeModal();
      showToast("Magic Mapper removed");
    } catch (error) {
      showError(error);
    }
  }

  function moveFocus(direction: Direction): void {
    const scope: ParentNode = modalView() && modalCard ? modalCard : document;
    const candidates = focusableElements(scope);
    const current = document.activeElement as HTMLElement;
    if (!candidates.includes(current)) {
      candidates[0]?.focus();
      return;
    }
    const from = current.getBoundingClientRect();
    const originX = from.left + from.width / 2;
    const originY = from.top + from.height / 2;
    let best: HTMLElement | undefined;
    let bestScore = Number.POSITIVE_INFINITY;
    for (const candidate of candidates) {
      if (candidate === current) continue;
      const box = candidate.getBoundingClientRect();
      const dx = box.left + box.width / 2 - originX;
      const dy = box.top + box.height / 2 - originY;
      if (
        (direction === "left" && dx >= -4) ||
        (direction === "right" && dx <= 4) ||
        (direction === "up" && dy >= -4) ||
        (direction === "down" && dy <= 4)
      )
        continue;
      const horizontal = direction === "left" || direction === "right";
      const score =
        (horizontal ? Math.abs(dx) : Math.abs(dy)) +
        (horizontal ? Math.abs(dy) : Math.abs(dx)) * 2.2;
      if (score < bestScore) {
        bestScore = score;
        best = candidate;
      }
    }
    best?.focus();
    best?.scrollIntoView({ block: "nearest", inline: "nearest" });
  }

  function handleKeyDown(event: KeyboardEvent): void {
    const directions: Partial<Record<number, Direction>> = {
      37: "left",
      38: "up",
      39: "right",
      40: "down",
    };
    const current = document.activeElement as HTMLElement | null;
    const typing = current instanceof HTMLInputElement || current instanceof HTMLTextAreaElement;
    if (
      (event.keyCode === 38 || event.keyCode === 40) &&
      current?.classList.contains("log") &&
      current.scrollHeight > current.clientHeight
    ) {
      const step = Math.max(48, Math.round(current.clientHeight * 0.45));
      if (event.keyCode === 38 && current.scrollTop > 0) {
        event.preventDefault();
        current.scrollTop = Math.max(0, current.scrollTop - step);
        return;
      }
      if (
        event.keyCode === 40 &&
        current.scrollTop + current.clientHeight < current.scrollHeight - 2
      ) {
        event.preventDefault();
        current.scrollTop = Math.min(current.scrollHeight, current.scrollTop + step);
        return;
      }
    }
    if (
      (event.keyCode === 37 || event.keyCode === 39) &&
      current?.classList.contains("value-control")
    ) {
      event.preventDefault();
      adjustField(current, event.keyCode === 37 ? -1 : 1);
      return;
    }
    const direction = directions[event.keyCode];
    if (direction && !(typing && (event.keyCode === 37 || event.keyCode === 39))) {
      event.preventDefault();
      moveFocus(direction);
    }
    if (event.keyCode === 461 || event.keyCode === 27) {
      consumeBack(event);
      backModal();
    }
  }

  function handleKeyUp(event: KeyboardEvent): void {
    if (event.keyCode === 461) consumeBack(event);
  }

  window.addEventListener("keydown", handleKeyDown, true);
  window.addEventListener("keyup", handleKeyUp, true);
  onCleanup(() => {
    window.removeEventListener("keydown", handleKeyDown, true);
    window.removeEventListener("keyup", handleKeyUp, true);
    window.clearTimeout(toastTimer);
  });

  function loadInitial(): void {
    setStartupError(undefined);
    setReady(false);
    Promise.all([
      platform.status(),
      platform.capabilities().catch(() => ({ capabilities: { piccap: false } })),
    ])
      .then(([statusResponse, capabilityResponse]) => {
        setStatus(statusResponse.status);
        setCapabilities(capabilityResponse.capabilities ?? {});
        setReady(true);
        window.setTimeout(
          () =>
            (statusResponse.status.active
              ? discoverButton
              : document.querySelector<HTMLElement>("#primary-button")
            )?.focus(),
          0,
        );
      })
      .catch((error: unknown) => {
        const code = error instanceof ControllerError ? error.code : undefined;
        setStartupError({ code, message: errorMessage(error) });
        window.setTimeout(() => document.querySelector<HTMLElement>("#retry-button")?.focus(), 0);
      });
  }

  loadInitial();

  return (
    <div class={{ "app-root": true, busy: busy() }}>
      <div class="shell flex h-full flex-col">
        <header class="topbar flex items-center justify-between">
          <div class="wordmark flex items-center" aria-label="Magic Mapper">
            <span class="mark grid place-items-center">M</span>
            <span>Magic Mapper</span>
          </div>
          <div class="topbar-actions flex items-center">
            <div class="status-line flex items-center">
              <span
                class={["status-dot", { active: status().active, error: Boolean(startupError()) }]}
              />
              <span>
                {startupError()
                  ? "Unavailable"
                  : ready()
                    ? status().active
                      ? "Running"
                      : status().installed
                        ? "Stopped"
                        : "Not set up"
                    : "Checking"}
              </span>
            </div>
            <button
              class="icon-button focusable"
              type="button"
              aria-label="Settings"
              disabled={Boolean(startupError())}
              onClick={showSystem}
            >
              <span aria-hidden="true">⚙</span>
            </button>
          </div>
        </header>

        <main class="min-h-0 flex-1" aria-live="polite">
          <section class="screen-heading flex items-center justify-between">
            <div>
              <h1>Remote buttons</h1>
              <p class="summary">
                {startupError()
                  ? "Could not reach the local service"
                  : ready()
                    ? `${entries().length} ${entries().length === 1 ? "button" : "buttons"} changed`
                    : "Reading your mappings…"}
              </p>
            </div>
            <button
              ref={(element) => {
                discoverButton = element;
              }}
              class="primary focusable flex items-center justify-center"
              type="button"
              disabled={!ready() || !status().active}
              onClick={() => startDiscovery("source")}
            >
              <span class="plus" aria-hidden="true">
                ＋
              </span>
              <span>Add button</span>
            </button>
          </section>

          <Show when={startupError()}>
            {(error) => (
              <section class="error-banner" role="alert">
                <div>
                  <strong>
                    {error().code === "missing_python3"
                      ? "Python 3 is required"
                      : "Could not start Magic Mapper"}
                  </strong>
                  <p>{error().message}</p>
                  <Show when={error().code === "missing_python3"}>
                    <p>
                      Install Python 3 through Entware (for example at /opt/bin/python3), then
                      select Try again.
                    </p>
                  </Show>
                </div>
                <button
                  id="retry-button"
                  class="primary focusable"
                  type="button"
                  onClick={loadInitial}
                >
                  Try again
                </button>
              </section>
            )}
          </Show>

          <Show when={ready() && !status().active}>
            <section class="service-banner flex items-center justify-between">
              <div>
                <strong>
                  {status().installed ? "Mapper is stopped" : "Magic Mapper needs setup"}
                </strong>
                <p>
                  {status().installed
                    ? "Start it to apply your saved button changes."
                    : "Set up the local service to change remote buttons."}
                </p>
              </div>
              <button
                id="primary-button"
                class="secondary focusable"
                type="button"
                onClick={() => void primaryAction()}
              >
                {status().installed ? "Start mapper" : "Set up"}
              </button>
            </section>
          </Show>

          <Show when={!startupError()}>
            <section class="mappings-section">
              <Show
                when={entries().length > 0}
                fallback={
                  <div class="empty-state flex items-center">
                    <span class="empty-glyph">＋</span>
                    <div>
                      <strong>No changed buttons</strong>
                      <p>Choose Add button, then press a button on the remote.</p>
                    </div>
                  </div>
                }
              >
                <div class="mappings">
                  <For each={entries()}>
                    {(button) => (
                      <button
                        class="mapping-row focusable"
                        type="button"
                        data-edit={button}
                        onClick={() => showMapping(button)}
                      >
                        <span class="key-name">{label(button)}</span>
                        <span class="mapping-action">{describe(status().config[button])}</span>
                        <span class="chevron" aria-hidden="true">
                          ›
                        </span>
                      </button>
                    )}
                  </For>
                </div>
              </Show>
            </section>
          </Show>
        </main>

        <footer class="flex items-center">
          <span>OK&nbsp; Select</span>
          <span>Back&nbsp; Close</span>
        </footer>
      </div>

      <div id="modal" class="modal" hidden={!modalView()}>
        <div class="modal-scrim" />
        <Show when={modalView()} keyed>
          {(current) => (
            <dialog
              ref={(element) => {
                modalCard = element;
              }}
              open
              class={`modal-card ${current.extraClass}`}
              aria-modal="true"
              data-closable={current.closable}
            >
              {current.content()}
            </dialog>
          )}
        </Show>
      </div>
      <Show when={toast()}>{(message) => <output class="toast">{message()}</output>}</Show>
    </div>
  );
}
