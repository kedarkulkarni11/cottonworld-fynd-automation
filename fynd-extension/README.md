# Cottonworld → Fynd Private Extension (OAuth shim)

A minimal Node.js + Express service that sits in front of the
[Streamlit converter](../README.md) and makes it install-able as a
**Private Extension** inside Fynd Commerce Platform.

```
Fynd Commerce Platform
        │
        │  1. Seller opens extension
        │     → Fynd redirects to /fp/install
        ▼
┌──────────────────────────┐
│  This service (Node)     │
│  @gofynd/fdk-extension   │
│  OAuth flow:             │
│  - /fp/install           │
│  - /fp/auth              │
│  - /fp/uninstall         │
└──────────────────────────┘
        │
        │  2. After OAuth → serve `/`
        │     → HTML page with <iframe>
        ▼
┌──────────────────────────┐
│  Streamlit app (Python)  │
│  Separate Render service │
│  (unchanged)             │
└──────────────────────────┘
```

## What it does
- Implements the required Fynd extension endpoints (`/fp/install`,
  `/fp/auth`, `/fp/uninstall`) via the official
  [`@gofynd/fdk-extension-javascript`](https://github.com/gofynd/fdk-extension-javascript)
  library.
- After OAuth succeeds, serves a single HTML page that embeds the existing
  Streamlit converter in an `<iframe>`.
- Sends a `Content-Security-Policy: frame-ancestors ... *.fynd.com ...`
  header so Fynd Commerce Platform can load this service in *its* iframe.

## Environment variables
Copy `.env.example` to `.env` (local) or set these in the Render dashboard:

| Variable | Where to get it |
| --- | --- |
| `EXTENSION_API_KEY` | Fynd Partner panel → your extension → **Credentials** |
| `EXTENSION_API_SECRET` | Same place (shown once — store securely) |
| `EXTENSION_BASE_URL` | The public URL of **this** Render service, e.g. `https://cottonworld-fynd-extension.onrender.com` |
| `FP_API_DOMAIN` | `https://api.fynd.com` (default) |
| `STREAMLIT_URL` | URL of the Streamlit converter service (defaults to the existing Render URL) |

## Run locally
```bash
cd fynd-extension
cp .env.example .env      # then fill in credentials
npm install
npm run start:dev         # or: npm start
```

## Deploy
This service is declared in the top-level `../render.yaml` as
`cottonworld-fynd-extension`. See the root
[README](../README.md#deploy-as-fynd-private-extension) for end-to-end
setup (Render + Fynd Partner panel wiring).
