#!/usr/bin/env sh
set -eu

: "${PORT:=8080}"
: "${JAWNIX_API_PORT:=8001}"
: "${CADDY_BIN:=caddy}"
: "${APP_DIR:=/app}"
: "${JAWNIX_INVOICE_DIR:=$APP_DIR/invoices}"
: "${JAWNIX_ALLOW_UNPROTECTED:=false}"
: "${JAWNIX_BASIC_AUTH_USER:=}"
: "${JAWNIX_BASIC_AUTH_PASSWORD:=}"

if [ "$JAWNIX_ALLOW_UNPROTECTED" != "true" ]; then
  if [ -z "$JAWNIX_BASIC_AUTH_USER" ] || [ -z "$JAWNIX_BASIC_AUTH_PASSWORD" ]; then
    echo "Error: set JAWNIX_BASIC_AUTH_USER and JAWNIX_BASIC_AUTH_PASSWORD, or set JAWNIX_ALLOW_UNPROTECTED=true for an intentionally unprotected deployment." >&2
    exit 1
  fi
fi

mkdir -p "$JAWNIX_INVOICE_DIR"
python3 "$APP_DIR/app.py" &
api_pid="$!"

cleanup() {
  kill "$api_pid" 2>/dev/null || true
  if [ "${caddy_pid:-}" ]; then
    kill "$caddy_pid" 2>/dev/null || true
  fi
}
trap cleanup INT TERM EXIT

cat > "$APP_DIR/Caddyfile.generated" <<EOF
{
	admin off
	auto_https off
}

:$PORT {
	bind 0.0.0.0
	root * $APP_DIR

	handle /healthz {
		respond "ok" 200
	}

	@api_preflight {
		path /api/*
		method OPTIONS
	}
	handle @api_preflight {
		reverse_proxy 127.0.0.1:$JAWNIX_API_PORT
	}

EOF

if [ "$JAWNIX_ALLOW_UNPROTECTED" != "true" ]; then
  cat >> "$APP_DIR/Caddyfile.generated" <<EOF

	handle /api/auth/* {
		reverse_proxy 127.0.0.1:$JAWNIX_API_PORT
	}

	handle /api/* {
		forward_auth 127.0.0.1:$JAWNIX_API_PORT {
			uri /api/auth/check
		}

		reverse_proxy 127.0.0.1:$JAWNIX_API_PORT
	}

	handle /login.html {
		file_server
	}

	handle /login {
		redir * /login.html 308
	}

	handle /portal {
		redir * /portal.html 308
	}

	@public_portal path /portal.html /portal-accept.html /theme.css
	handle @public_portal {
		file_server
	}

	handle {
		@no_session not header Cookie *jawnix_session=*
		redir @no_session /login.html 303

		forward_auth 127.0.0.1:$JAWNIX_API_PORT {
			uri /api/auth/check
			header_up X-Jawnix-Auth-Redirect /login.html
		}

		try_files {path} /index.html
		file_server
	}
EOF
else
  cat >> "$APP_DIR/Caddyfile.generated" <<EOF

	handle /api/* {
		reverse_proxy 127.0.0.1:$JAWNIX_API_PORT
	}

	handle {
		try_files {path} /index.html
		file_server
	}
EOF
fi

cat >> "$APP_DIR/Caddyfile.generated" <<'EOF'

	header {
		X-Content-Type-Options nosniff
		Referrer-Policy no-referrer-when-downgrade
	}
}
EOF

"$CADDY_BIN" run --config "$APP_DIR/Caddyfile.generated" --adapter caddyfile &
caddy_pid="$!"
wait "$caddy_pid"
