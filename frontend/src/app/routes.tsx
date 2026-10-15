import { createBrowserRouter, Navigate } from "react-router";
import { AdminShell } from "./shells/AdminShell";
import { MFAShell } from "./shells/MFAShell";
import { CustomerShell } from "./shells/CustomerShell";
import { ManagedAdminShell } from "./shells/ManagedAdminShell";
import { managerAccessLoader } from "./auth/managedAdmin";
import { RouteError } from "./routes/RouteError";
import { adminAccessLoader, adminMFAStatusLoader } from "./auth/adminMFA";
import { customerAccessLoader, invitationLoader, signInLoader } from "./auth/customerAuth";
import { lazyPage } from "./routeModules";
import { scraperOverviewLoader } from "./routes/scraperMonitoring";
import { scraperKeywordsLoader } from "./routes/scraperKeywordApi";
import { scraperCampaignHistoryLoader, scraperRuntimeLoader } from "./routes/scraperRuntimeApi";
import { scraperDatabaseLoader, scraperDatabaseStateLoader } from "./routes/scraperDatabaseData";
import { stateCoverageDetailLoader, stateCoverageLoader } from "./routes/scraperCoverageData";
import { batchRequestsLoader } from "./routes/batchRequests";
import { customerAccountLoader } from "./routes/licensedStates";

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
          lazy: async () => {
            const { SignInRoute } = await import("./routes/CustomerAuth");
            return { Component: SignInRoute };
          },
        },
        {
          path: "sign-up",
          lazy: lazyPage("CustomerSignup", "CustomerSignupRoute"),
        },
        { path: "complete-signup", lazy: lazyPage("CustomerSignup", "CompleteSignupRoute") },
        { path: "appointed-states", lazy: lazyPage("CustomerSignup", "AppointedStatesRoute", "appointedStatesLoader") },
        { path: "registration-status", lazy: lazyPage("CustomerSignup", "RegistrationStatusRoute", "registrationStatusLoader") },
        {
          path: "accept-invitation",
          loader: invitationLoader,
          lazy: async () => {
            const { AcceptInvitationRoute } = await import(
              "./routes/CustomerAuth"
            );
            return { Component: AcceptInvitationRoute };
          },
        },

        // Customer portal — Overview, Requests, Feedback, Account.
        {
          id: "customer",
          element: <CustomerShell />,
          loader: customerAccessLoader,
          children: [
            {
              path: "overview",
              lazy: lazyPage("CustomerOverview", "CustomerOverviewRoute"),
            },
            {
              path: "requests",
              loader: batchRequestsLoader,
              lazy: lazyPage("CustomerRequests", "CustomerRequestsRoute"),
            },
            // #53 replaced the placeholder with the guided flow. The loader
            // reads the Lead Disposition catalog, whose consequence copy is
            // derived from the rule that materializes the controls.
            {
              path: "feedback",
              lazy: lazyPage("CustomerFeedback", "CustomerFeedbackRoute", "feedbackLoader"),
            },
            {
              path: "account",
              loader: customerAccountLoader,
              lazy: lazyPage("CustomerAccount", "CustomerAccountRoute"),
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
              lazy: lazyPage("AdminMFA", "AdminMFAEnrollmentRoute"),
            },
            {
              path: "challenge",
              loader: adminMFAStatusLoader,
              lazy: lazyPage("AdminMFA", "AdminMFAChallengeRoute"),
            },
            {
              path: "recover",
              loader: adminMFAStatusLoader,
              lazy: lazyPage("AdminMFA", "AdminMFARecoveryRoute"),
            },
          ],
        },

        {
          path: "manager",
          element: <ManagedAdminShell />,
          loader: managerAccessLoader,
          children: [
            { index:true, element:<Navigate to="/manager/uploads" replace /> },
            { path:"uploads", lazy:lazyPage("ManagedAdmin", "ManagedUploadsRoute", "managedUploadsLoader") },
            { path:"customers", lazy:lazyPage("ManagedAdmin", "ManagedCustomersRoute", "managedCustomersLoader") },
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
              lazy: lazyPage("AdminDestinations", "AdminDestinationRoute", "adminOverviewLoader"),
            },
            // #57 replaced the Fulfillment task map with the real workspace.
            // Its loaders read one aggregate contract, and every action the
            // screens render is projected by the backend rather than derived
            // here, so the UI cannot offer what the domain refuses.
            {
              path: "fulfillment",
              lazy: lazyPage("AdminFulfillment", "AdminFulfillmentRoute", "fulfillmentLoader"),
            },
            {
              path: "fulfillment/requests/:requestId",
              lazy: lazyPage("AdminFulfillment", "AdminFulfillmentRequestRoute", "fulfillmentRequestLoader"),
            },
            {
              path: "fulfillment/conflicts/:conflictId",
              lazy: lazyPage("AdminFulfillment", "AdminFulfillmentConflictRoute", "fulfillmentConflictLoader"),
            },
            // #58 added the Lead Report and its eligibility controls. The
            // report is immutable evidence; the controls beside it act on the
            // Lead, so this route reads one report and never edits it.
            {
              path: "fulfillment/reports/:reportId",
              lazy: lazyPage("AdminLeadReport", "AdminLeadReportRoute", "adminLeadReportLoader"),
            },
            // #68 replaced the Acquisition task map with the native
            // terminal-themed workspace. Its decisions post back to the
            // endpoints that already own them, so the screen never becomes a
            // second path to change acquisition.
            {
              path: "acquisition",
              lazy: lazyPage("AdminAcquisition", "AdminAcquisitionRoute", "acquisitionLoader"),
            },
            {
              path: "acquisition/configurations/:configurationId",
              lazy: lazyPage("AdminAcquisitionDetails", "ScraperConfigurationDetailsRoute", "scraperConfigurationDetailsLoader"),
            },
            {
              path: "acquisition/runs/:runId",
              lazy: lazyPage("AdminAcquisitionDetails", "ScrapeRunDetailsRoute", "scrapeRunDetailsLoader"),
            },
            // #173 A1 restored Source Performance as its own destination —
            // the descriptive nightly evidence Acquisition's optimizer reads,
            // without re-porting the Niche mappings or Source Recommendation
            // decisions that already live there.
            {
              path: "acquisition/performance",
              lazy: lazyPage("AdminSourcePerformance", "AdminSourcePerformanceRoute", "sourcePerformanceLoader"),
            },
            {
              path: "acquisition/scraper",
              lazy: lazyPage("ScraperWorkspace", "ScraperStepUpRoute", "scraperEntryLoader"),
            },
            {
              path: "acquisition/scraper/workspace",
              // #63 replaced the placeholder workspace with the real GMS/OPS
              // overview: nine monitoring regions on their own refresh
              // cadences, and the audited pipeline controls.
              loader: scraperOverviewLoader,
              lazy: lazyPage("ScraperOverview", "ScraperOverviewRoute"),
            },
            {
              path: "acquisition/scraper/workspace/keywords",
              loader: scraperKeywordsLoader,
              lazy: lazyPage("ScraperKeywords", "ScraperKeywordsRoute"),
            },
            {
              path: "acquisition/scraper/workspace/history",
              loader: scraperCampaignHistoryLoader,
              lazy: lazyPage("ScraperCampaignHistory", "ScraperCampaignHistoryRoute"),
            },
            {
              path: "acquisition/scraper/workspace/runtime",
              loader: scraperRuntimeLoader,
              lazy: lazyPage("ScraperRuntimeConfiguration", "ScraperRuntimeConfigurationRoute"),
            },
            {
              path: "acquisition/scraper/workspace/states",
              loader: stateCoverageLoader,
              lazy: lazyPage("ScraperCoverage", "ScraperStateCoverageRoute"),
            },
            {
              path: "acquisition/scraper/workspace/states/:state",
              loader: stateCoverageDetailLoader,
              lazy: lazyPage("ScraperCoverage", "ScraperStateDetailRoute"),
            },
            {
              path: "acquisition/scraper/workspace/database",
              loader: scraperDatabaseLoader,
              lazy: lazyPage("ScraperDatabase", "ScraperDatabaseRoute"),
            },
            {
              path: "acquisition/scraper/workspace/database/states/:state",
              loader: scraperDatabaseStateLoader,
              lazy: lazyPage("ScraperDatabase", "ScraperDatabaseStateRoute"),
            },
            {
              path: "customers",
              lazy: lazyPage("AdminCustomers", "AdminCustomersRoute", "adminCustomerDirectoryLoader"),
            },
            {
              path: "activity",
              lazy: lazyPage("AdminActivity", "AdminActivityRoute", "adminActivityLoader"),
            },
            {
              path: "customers/:customerId",
              lazy: lazyPage("AdminCustomerDetails", "AdminCustomerDetailsRoute", "adminCustomerDetailsLoader"),
            },
            {
              path: "agencies",
              lazy: lazyPage("AdminAgencies", "AdminAgenciesRoute", "adminAgencyDirectoryLoader"),
            },
            {
              path: "agencies/:agencyId",
              lazy: lazyPage("AdminAgencies", "AdminAgencyDetailsRoute", "adminAgencyDetailsLoader"),
            },
            {
              path: "security",
              loader: adminMFAStatusLoader,
              lazy: lazyPage("AdminMFA", "AdminMFASecurityRoute"),
            },
          ],
        },

        // The design-system gallery renders every primitive in both themes. It
        // is the fixture the accessibility and visual-regression gates in #70
        // run against.
        { path: "design-system", lazy: lazyPage("DesignSystem", "DesignSystemRoute") },
        ...(import.meta.env.DEV
          ? [
              {
                path: "prototype/typeface",
                lazy: async () => {
                  const { TypefacePrototypeRoute } = await import(
                    "./routes/TypefacePrototype"
                  );
                  return { Component: TypefacePrototypeRoute };
                },
              },
            ]
          : []),
      ],
    },
  ],
  { basename: "/app" },
);
