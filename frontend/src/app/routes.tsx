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
  adminOverviewLoader,
} from "./routes/AdminDestinations";
import {
  AdminAcquisitionRoute,
  acquisitionLoader,
} from "./routes/AdminAcquisition";
import {
  ScrapeRunDetailsRoute,
  ScraperConfigurationDetailsRoute,
  scrapeRunDetailsLoader,
  scraperConfigurationDetailsLoader,
} from "./routes/AdminAcquisitionDetails";
import {
  AdminCustomersRoute,
  adminCustomerDirectoryLoader,
} from "./routes/AdminCustomers";
import {
  AdminCustomerDetailsRoute,
  adminCustomerDetailsLoader,
} from "./routes/AdminCustomerDetails";
import {
  AdminAgenciesRoute,
  AdminAgencyDetailsRoute,
  adminAgencyDetailsLoader,
  adminAgencyDirectoryLoader,
} from "./routes/AdminAgencies";
import {
  AdminFulfillmentConflictRoute,
  AdminFulfillmentRequestRoute,
  AdminFulfillmentRoute,
  fulfillmentConflictLoader,
  fulfillmentLoader,
  fulfillmentRequestLoader,
} from "./routes/AdminFulfillment";
import {
  AdminLeadReportRoute,
  adminLeadReportLoader,
} from "./routes/AdminLeadReport";
import {
  AdminActivityRoute,
  adminActivityLoader,
} from "./routes/AdminActivity";
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
import {
  ScraperStepUpRoute,
  scraperEntryLoader,
} from "./routes/ScraperWorkspace";
import { ScraperOverviewRoute } from "./routes/ScraperOverview";
import { scraperOverviewLoader } from "./routes/scraperMonitoring";
import { CustomerOverviewRoute } from "./routes/CustomerOverview";
import {
  CustomerFeedbackRoute,
  feedbackLoader,
} from "./routes/CustomerFeedback";
import { CustomerRequestsRoute } from "./routes/CustomerRequests";
import { batchRequestsLoader } from "./routes/batchRequests";

/**
 * Route table for the redesigned application.
 *
 * `basename` matches the `/app/` build base: FastAPI serves the shell there
 * while the current static UI keeps the site root, which is what lets the
 * feature flag switch between them without touching the legacy pages.
 *
 * #47 established the shell, routing, and design system. Customer routes remain
 * placeholders for their later slices; the remaining administrator destinations
 * are task maps whose loaders and screen bodies are replaced by the operational
 * slices without rebuilding the shell. Customers is one that has been: it is a
 * real directory and record screen backed by the administration API.
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
          id: "customer",
          element: <CustomerShell />,
          loader: customerAccessLoader,
          children: [
            {
              path: "overview",
              element: <CustomerOverviewRoute />,
            },
            {
              path: "requests",
              loader: batchRequestsLoader,
              element: <CustomerRequestsRoute />,
            },
            // #53 replaced the placeholder with the guided flow. The loader
            // reads the Lead Disposition catalog, whose consequence copy is
            // derived from the rule that materializes the controls.
            {
              path: "feedback",
              loader: feedbackLoader,
              element: <CustomerFeedbackRoute />,
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
            // #57 replaced the Fulfillment task map with the real workspace.
            // Its loaders read one aggregate contract, and every action the
            // screens render is projected by the backend rather than derived
            // here, so the UI cannot offer what the domain refuses.
            {
              path: "fulfillment",
              loader: fulfillmentLoader,
              element: <AdminFulfillmentRoute />,
            },
            {
              path: "fulfillment/requests/:requestId",
              loader: fulfillmentRequestLoader,
              element: <AdminFulfillmentRequestRoute />,
            },
            {
              path: "fulfillment/conflicts/:conflictId",
              loader: fulfillmentConflictLoader,
              element: <AdminFulfillmentConflictRoute />,
            },
            // #58 added the Lead Report and its eligibility controls. The
            // report is immutable evidence; the controls beside it act on the
            // Lead, so this route reads one report and never edits it.
            {
              path: "fulfillment/reports/:reportId",
              loader: adminLeadReportLoader,
              element: <AdminLeadReportRoute />,
            },
            // #68 replaced the Acquisition task map with the native
            // terminal-themed workspace. Its decisions post back to the
            // endpoints that already own them, so the screen never becomes a
            // second path to change acquisition.
            {
              path: "acquisition",
              loader: acquisitionLoader,
              element: <AdminAcquisitionRoute />,
            },
            {
              path: "acquisition/configurations/:configurationId",
              loader: scraperConfigurationDetailsLoader,
              element: <ScraperConfigurationDetailsRoute />,
            },
            {
              path: "acquisition/runs/:runId",
              loader: scrapeRunDetailsLoader,
              element: <ScrapeRunDetailsRoute />,
            },
            {
              path: "acquisition/scraper",
              loader: scraperEntryLoader,
              element: <ScraperStepUpRoute />,
            },
            {
              path: "acquisition/scraper/workspace",
              // #63 replaced the placeholder workspace with the real GMS/OPS
              // overview: nine monitoring regions on their own refresh
              // cadences, and the audited pipeline controls.
              loader: scraperOverviewLoader,
              element: <ScraperOverviewRoute />,
            },
            {
              path: "customers",
              loader: adminCustomerDirectoryLoader,
              element: <AdminCustomersRoute />,
            },
            {
              path: "activity",
              loader: adminActivityLoader,
              element: <AdminActivityRoute />,
            },
            {
              path: "customers/:customerId",
              loader: adminCustomerDetailsLoader,
              element: <AdminCustomerDetailsRoute />,
            },
            {
              path: "agencies",
              loader: adminAgencyDirectoryLoader,
              element: <AdminAgenciesRoute />,
            },
            {
              path: "agencies/:agencyId",
              loader: adminAgencyDetailsLoader,
              element: <AdminAgencyDetailsRoute />,
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
