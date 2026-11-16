const http = require('node:http');
const fs = require('node:fs');
const path = require('node:path');
const root = path.resolve(__dirname, '../../dist/admin');
http.createServer((req, res) => {
  const name = decodeURIComponent(new URL(req.url,'http://localhost').pathname).replace(/^\/admin\//,'');
  let file = path.resolve(root,name);
  if (!file.startsWith(root + path.sep) || !fs.existsSync(file) || !fs.statSync(file).isFile()) file = path.join(root,'index.html');
  res.setHeader('Content-Type', ({'.js':'text/javascript','.css':'text/css','.html':'text/html','.svg':'image/svg+xml','.png':'image/png'}[path.extname(file)] || 'application/octet-stream'));
  fs.createReadStream(file).pipe(res);
}).listen(4301,'127.0.0.1');
