# Serve the React shell from FastAPI behind a flag

> **Update (P8, 2026-08-03):** the `JAWNIX_ENABLE_NEW_UI` flag has been retired.
> The cutover and static-page retirement are complete, so the shell is the only
> UI and the prefix is served unconditionally. The FastAPI-owns-`/app` and
> caching decisions below still hold; only the flag gate is gone.

FastAPI owns the whole `/app` prefix and serves the compiled React shell itself rather than delegating to Caddy's static file server, because the two caching rules the build requires cannot be expressed as one static mount: content-hashed assets are immutable for a year, while `index.html` names the current hashes and must never be cached. FastAPI also returns the shell document for every path under the prefix, so direct navigation to an application route survives a hard refresh. The `JAWNIX_ENABLE_NEW_UI` flag gates the entire prefix and answers 404 rather than 403 while off, leaving the shell undiscoverable and the current static UI untouched as the rollback target until the controlled cutover.
