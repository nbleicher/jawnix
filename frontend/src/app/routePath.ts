/** Backend hrefs carry the /app prefix; router paths drop it. */
export function routePath(href: string): string {
  return href.startsWith("/app/") ? href.slice("/app".length) : href;
}
