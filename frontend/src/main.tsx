import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { RouterProvider } from "react-router";

import { ThemeProvider } from "./design-system/theme/ThemeProvider";
import { router } from "./app/routes";
import "@fontsource-variable/fraunces/standard.css";
import "./design-system/styles/tokens.css";
import "./design-system/styles/reset.css";
import "./design-system/primitives/typography.css";

const container = document.getElementById("root");
if (!container) {
  throw new Error("Missing #root container.");
}

createRoot(container).render(
  <StrictMode>
    <ThemeProvider>
      <RouterProvider router={router} />
    </ThemeProvider>
  </StrictMode>,
);
