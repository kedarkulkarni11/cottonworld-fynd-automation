'use strict';

require('dotenv').config();

const app = require('./server');
const port = process.env.PORT || process.env.BACKEND_PORT || 8080;

app.listen(port, () => {
  // eslint-disable-next-line no-console
  console.log(`Cottonworld Fynd extension listening on port ${port}`);
});
