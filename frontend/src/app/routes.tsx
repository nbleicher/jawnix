import { createBrowserRouter, Navigate } from "react-router";

import { AdminShell } from "./shells/AdminShell";
import { CustomerShell } from "./shells/CustomerShell";
import { RouteError } from "./routes/RouteError";
import { DesignSystemRoute } from "./routes/DesignSystem";
import { PlaceholderRoute, placeholderLoader } from "./routes/Placeholder";

/**
 * Route table for the redesigned application.
 *
 * `basename` matches the `/app/` build base: FastAPI serves the shell there
 * while the current static UI keeps the site root, which is what lets the
 * feature flag switch between them without touching the legacy pages.
 *
 * Screens are placeholders at this slice — #47 establishes the shell, routing,
 * and design system; later slices replace each element with the real screen.
 * The navigation contract, landmarks, loader seam, and loading/error states are
 * real now, so those slices only have to supply content.
 */
export const router = createBrowserRouter(
  [
    {
      path: "/",
      errorElement: <RouteError />,
      children: [
        { index: true, element: <Navigate to="/overview" replace /> },

        // Customer portal — Overview, Requests, Feedback, Account.
        {
          element: <CustomerShell />,
          children: [
            {
              path: "overview",
              loader: placeholderLoader({ title: "Overview", slice: "#50 — Customer Overview" }),
              element: <PlaceholderRoute />,
            },
            {
              path: "requests",
              loader: placeholderLoader({ title: "Requests", slice: "#51 — Guided Batch Requests" }),
              element: <PlaceholderRoute />,
            },
            {
              path: "feedback",
              loader: placeholderLoader({ title: "Feedback", slice: "#53 — Guided Customer Feedback" }),
              element: <PlaceholderRoute />,
            },
            {
              path: "account",
              loader: placeholderLoader({ title: "Account", slice: "#54 — Licensed State management" }),
              element: <PlaceholderRoute />,
            },
          ],
        },

        // Administration — Overview, Fulfillment, Acquisition, Customers.
        {
          path: "admin",
          element: <AdminShell />,
          children: [
            { index: true, element: <Navigate to="/admin/overview" replace /> },
            {
              path: "overview",
              loader: placeholderLoader({ title: "Operations overview", slice: "#55 — Operations overview" }),
              element: <PlaceholderRoute />,
            },
            {
              path: "fulfillment",
              loader: placeholderLoader({ title: "Fulfillment", slice: "#57 — Core Fulfillment operations" }),
              element: <PlaceholderRoute />,
            },
            {
              path: "acquisition",
              loader: placeholderLoader({ title: "Acquisition", slice: "#62 — Scraper terminal workspace" }),
              element: <PlaceholderRoute />,
            },
            {
              path: "customers",
              loader: placeholderLoader({
                title: "Customers",
                slice: "#59 — Customer and User Account management",
              }),
              element: <PlaceholderRoute />,
            },
          ],
        },

        // The design-system gallery renders every primitive in both themes. It
        // is the fixture the accessibility and visual-regression gates in #70
        // run against.
        { path: "design-system", element: <DesignSystemRoute /> },
      ],
    },
  ],
  { basename: "/app" },
);
