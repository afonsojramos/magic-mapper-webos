import { render } from "@solidjs/web";

import App from "./App";

// oxlint-disable-next-line import/no-unassigned-import -- Vite bundles the global stylesheet.
import "../css/app.css";

const root = document.getElementById("root");

if (!root) throw new Error("Magic Mapper could not find its application root");

function renderFatalError(message: string): void {
  if (!root || root.childElementCount > 0) return;
  const container = document.createElement("div");
  container.className = "fatal-fallback";
  const heading = document.createElement("h1");
  heading.textContent = "Magic Mapper could not start";
  const detail = document.createElement("p");
  detail.textContent = message;
  container.append(heading, detail);
  root.append(container);
}

window.addEventListener("error", (event) => {
  renderFatalError(event.message || "Unexpected startup error");
});

try {
  render(() => <App />, root);
} catch (error) {
  renderFatalError(error instanceof Error ? error.message : String(error));
  throw error;
}
