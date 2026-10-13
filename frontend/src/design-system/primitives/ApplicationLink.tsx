import type { ComponentProps } from "react";
import { Link, useHref, useInRouterContext } from "react-router";
import { prefetchDestination } from "../../app/routeModules";

type Props = ComponentProps<"a"> & { href: string };

function RoutedLink({ href, ...props }: Props) {
  const root = useHref("/");
  const to = root === "/" ? href : href.replace(/^\/app(?=\/|$)/, "");
  return <Link to={to} {...props} />;
}

/** Keep normal anchors for downloads/external links and router navigation for app pages. */
export function ApplicationLink({ href, onMouseEnter, onFocus, ...props }: Props) {
  const inRouter = useInRouterContext();
  const internal = href.startsWith("/app/") && props.download == null && (!props.target || props.target === "_self");
  const handlers = {
    onMouseEnter: ((event) => { if (internal) prefetchDestination(href); onMouseEnter?.(event); }) as Props["onMouseEnter"],
    onFocus: ((event) => { if (internal) prefetchDestination(href); onFocus?.(event); }) as Props["onFocus"],
  };
  return internal && inRouter
    ? <RoutedLink href={href} {...props} {...handlers} />
    : <a href={href} {...props} {...handlers} />;
}
