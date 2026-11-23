import { render } from "@solidjs/web";

import App from "./App";

// oxlint-disable-next-line import/no-unassigned-import -- Vite bundles the global stylesheet.
import "../css/app.css";

const root = document.getElementById("root");

if (!root) throw new Error("Magic Mapper could not find its application root");

try {
  render(() => <App />, root);
} catch (error) {
  window.magicMapperFatal?.(error instanceof Error ? error.message : String(error));
  throw error;
}
