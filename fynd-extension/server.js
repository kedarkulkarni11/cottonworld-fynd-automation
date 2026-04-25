/**
 * Cottonworld → Fynd private extension server.
 *
 * Wraps the Streamlit converter (hosted separately) inside a Fynd-compliant
 * extension with OAuth (/fp/install, /fp/auth) handled by the official
 * @gofynd/fdk-extension-javascript library.
 *
 * After a successful install/auth, the user is redirected to `/` which serves
 * a simple HTML page that iframes the Streamlit app.
 */
const express = require('express');
const cookieParser = require('cookie-parser');
const bodyParser = require('body-parser');
const sqlite3 = require('sqlite3').verbose();
const { setupFdk } = require('@gofynd/fdk-extension-javascript/express');
const { SQLiteStorage } = require('@gofynd/fdk-extension-javascript/express/storage');

const sqliteInstance = new sqlite3.Database(process.env.SQLITE_PATH || '/tmp/session_storage.db');

const STREAMLIT_URL =
  process.env.STREAMLIT_URL || 'https://cottonworld-fynd-automation.onrender.com';

const fdkExtension = setupFdk({
  api_key: process.env.EXTENSION_API_KEY,
  api_secret: process.env.EXTENSION_API_SECRET,
  base_url: process.env.EXTENSION_BASE_URL,
  cluster: process.env.FP_API_DOMAIN || 'https://api.fynd.com',
  callbacks: {
    // Called after OAuth succeeds. Return the path to redirect the user to.
    // We just send them to the landing page that iframes Streamlit; the
    // company_id is appended as a query param for logging/debug purposes.
    auth: async (req) => {
      const companyId = req.query['company_id'] || '';
      return `${req.extension.base_url}/?company_id=${companyId}`;
    },
    uninstall: async (req) => {
      // Nothing persistent to clean up — this is a stateless tool.
      // eslint-disable-next-line no-console
      console.log(`Uninstall for company ${req.body?.company_id || 'unknown'}`);
    },
  },
  storage: new SQLiteStorage(sqliteInstance, 'cottonworld-fynd-ext'),
  access_mode: 'online',
});

const app = express();

app.use(cookieParser('ext.session'));
app.use(bodyParser.json({ limit: '1mb' }));

// Register FDK-provided install / auth / uninstall endpoints under `/`:
//   GET  /fp/install
//   GET  /fp/auth
//   POST /fp/uninstall
app.use('/', fdkExtension.fdkHandler);

// Simple health check (Render uses this)
app.get('/healthz', (_req, res) => res.status(200).send('ok'));

// Landing page — iframe wrapping the Streamlit app.
app.get('/', (_req, res) => {
  const html = `<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<style>
  html, body { margin: 0; padding: 0; height: 100%; overflow: hidden; }
  iframe { display: block; width: 100%; height: 100vh; border: none; }
</style>
</head>
<body>
  <iframe src="${STREAMLIT_URL}" allow="clipboard-write" allowfullscreen></iframe>
</body>
</html>`;
  res
    .status(200)
    .set('Content-Type', 'text/html; charset=utf-8')
    .set('Content-Security-Policy', [
      "frame-ancestors 'self' https://*.fynd.com https://*.fyndx0.com https://*.fyndx1.com https://*.fyndx5.com;",
      `frame-src ${STREAMLIT_URL};`,
    ].join(' '))
    .send(html);
});

module.exports = app;
