export type Scalar = string | number | boolean;
export type InputValue = Scalar | string[] | Record<string, unknown>;
export type InputValues = Record<string, InputValue>;

export interface ActionOption {
  label: string;
  value: Scalar;
}

export interface ActionField {
  allowEmpty?: boolean;
  default?: InputValue;
  label?: string;
  max?: number;
  min?: number;
  multiline?: boolean;
  name: string;
  options?: ActionOption[];
  placeholder?: string;
  required?: boolean;
  step?: number;
  type:
    | "boolean"
    | "button"
    | "choice"
    | "integer"
    | "number"
    | "object"
    | "string"
    | "stringList"
    | "url";
}

export interface ActionDefinition {
  category: string;
  editor: "apps" | "button" | "fields" | "instant";
  id: string;
  inputs: ActionField[];
  requires?: string;
  summary: string;
  title: string;
  warning?: string;
}

export interface ActionCategory {
  id: string;
  summary: string;
  title: string;
}

export interface ActionCatalog {
  actions: ActionDefinition[];
  categories: ActionCategory[];
}

export interface MappingAction {
  appId?: string;
  function: string;
  inputs: InputValues;
}

export type Mapping = "disabled" | MappingAction | MappingAction[];
export type MapperConfig = Record<string, Mapping>;

export interface MapperSettings {
  block_mouse?: boolean;
  ir_exclusive_mode?: boolean;
  ir_input_enabled?: boolean;
}

export interface MapperStatus {
  active: boolean;
  config: MapperConfig;
  configDigest?: string;
  installed: boolean;
  irInputDevice?: string | null;
  settings: MapperSettings;
}

export interface StatusResponse {
  migrated?: boolean;
  ok?: boolean;
  status: MapperStatus;
}

export interface DiscoveryResponse {
  button?: string;
  code?: number;
  error?: string;
  ok: boolean;
  pending?: boolean;
}

export interface InstalledApp {
  id: string;
  title: string;
}

export interface Platform {
  apps(): Promise<{ apps: InstalledApp[] }>;
  capabilities(): Promise<{ capabilities: Record<string, boolean> }>;
  configure(config: MapperConfig): Promise<StatusResponse>;
  configureSettings(settings: MapperSettings): Promise<StatusResponse>;
  discover(id: string): Promise<{ ok: boolean }>;
  discoveryResult(id: string): Promise<DiscoveryResponse>;
  install(): Promise<StatusResponse>;
  isMock: boolean;
  logs(): Promise<{ log: string }>;
  start(): Promise<StatusResponse>;
  status(): Promise<StatusResponse>;
  stop(): Promise<StatusResponse>;
  uninstall(): Promise<{ ok: boolean }>;
}
