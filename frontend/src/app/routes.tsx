import { createBrowserRouter, Navigate } from "react-router";

import { AdminShell } from "./shells/AdminShell";
import { MFAShell } from "./shells/MFAShell";
import { CustomerShell } from "./shells/CustomerShell";
import { RouteError } from "./routes/RouteError";
import { DesignSystemRoute } from "./routes/DesignSystem";
import { PlaceholderRoute, placeholderLoader } from "./routes/Placeholder";
import {
  AdminMFAChallengeRoute,
  AdminMFAEnrollmentRoute,
  AdminMFARecoveryRoute,
  AdminMFASecurityRoute,
} from "./routes/AdminMFA";
import {
  AdminDestinationRoute,
  adminAcquisitionLoader,
  adminCustomersLoader,
  adminFulfillmentLoader,
  adminOverviewLoader,
} from "./routes/AdminDestinations";
import {
  adminAccessLoader,
  adminMFAStatusLoader,
} from "./auth/adminMFA";
import {
  customerAccessLoader,
  invitationLoader,
  signInLoader,
} from "./auth/customerAuth";
import {
  AcceptInvitationRoute,
  SignInRoute,
} from "./routes/CustomerAuth";

/**
 * Route table for the redesigned application.
 *
 * `basename` matches the `/app/` build base: FastAPI serves the shell there
 * while the current static UI keeps the site root, which is what lets the
 * feature flag switch between them without touching the legacy pages.
 *
 * #47 established the shell, routing, and design system. Customer routes remain
 * placeholders for their later slices; administrator destinations are real
 * task maps whose loaders and screen bodies are replaced by the operational
 * slices without rebuilding the shell.
 */
export const router = createBrowserRouter(
  [
    {
      path: "/",
      errorElement: <RouteError />,
      children: [
        { index: true, element: <Navigate to="/overview" replace /> },
        {
          path: "sign-in",
          loader: signInLoader,
          element: <SignInRoute />,
        },
        {
          path: "accept-invitation",
          loader: invitationLoader,
          element: <AcceptInvitationRoute />,
        },

        // Customer portal — Overview, Requests, Feedback, Account.
        {
          element: <CustomerShell />,
          loader: customerAccessLoader,
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

        // Administrator verification is intentionally outside AdminShell:
        // below AAL2 it must expose enrollment/recovery and no administration.
        {
          path: "admin/mfa",
          element: <MFAShell />,
          children: [
            { index: true, element: <Navigate to="/admin/mfa/challenge" replace /> },
            {
              path: "enroll",
              loader: adminMFAStatusLoader,
              element: <AdminMFAEnrollmentRoute />,
            },
            {
              path: "challenge",
              loader: adminMFAStatusLoader,
              element: <AdminMFAChallengeRoute />,
            },
            {
              path: "recover",
              loader: adminMFAStatusLoader,
              element: <AdminMFARecoveryRoute />,
            },
          ],
        },

        // Administration — every child first crosses the backend's real
        // require_admin boundary through this parent loader.
        {
          path: "admin",
          element: <AdminShell />,
          loader: adminAccessLoader,
          children: [
            { index: true, element: <Navigate to="/admin/overview" replace /> },
            {
              path: "overview",
              loader: adminOverviewLoader,
              element: <AdminDestinationRoute />,
            },
            {
              path: "fulfillment",
              loader: adminFulfillmentLoader,
              element: <AdminDestinationRoute />,
            },
            {
              path: "acquisition",
              loader: adminAcquisitionLoader,
              element: <AdminDestinationRoute />,
            },
            {
              path: "customers",
              loader: adminCustomersLoader,
              element: <AdminDestinationRoute />,
            },
            {
              path: "security",
              loader: adminMFAStatusLoader,
              element: <AdminMFASecurityRoute />,
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
