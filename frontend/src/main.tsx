import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { RouterProvider } from "react-router";

import { ThemeProvider } from "./design-system/theme/ThemeProvider";
import { router } from "./app/routes";
import "@fontsource/dm-sans/400.css";
import "@fontsource/dm-sans/500.css";
import "@fontsource/dm-sans/600.css";
import "@fontsource/dm-sans/700.css";
import "@fontsource/dm-mono/400.css";
import "@fontsource/dm-mono/500.css";
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
