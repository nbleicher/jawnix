import { render, screen } from "@testing-library/react";
import { createMemoryRouter, RouterProvider } from "react-router";
import { describe, expect, it } from "vitest";

import { AdminDestinationRoute } from "./AdminDestinations";

describe("administrator destination states", () => {
  it("gives an empty destination a safe next step", () => {
    const data = {
      title: "Fulfillment",
      description: "Fulfillment work.",
      sectionTitle: "Fulfillment work",
      sectionDescription: "Review delivery work.",
      areas: [],
      empty: {
        title: "No Fulfillment areas are available",
        description:
          "Return to the administrator Overview and choose another workspace.",
        action: {
          label: "Back to Overview",
          href: "/app/admin/overview",
        },
      },
    };
    const router = createMemoryRouter(
      [
        {
          id: "destination",
          path: "/admin/fulfillment",
          loader: () => data,
          element: <AdminDestinationRoute />,
        },
      ],
      {
        initialEntries: ["/admin/fulfillment"],
        hydrationData: { loaderData: { destination: data } },
      },
    );

    render(<RouterProvider router={router} />);

    expect(
      screen.getByRole("heading", {
        level: 2,
        name: "No Fulfillment areas are available",
      }),
    ).toBeVisible();
    expect(screen.getByRole("link", { name: "Back to Overview" })).toHaveAttribute(
      "href",
      "/app/admin/overview",
    );
  });
});
