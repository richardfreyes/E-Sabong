// Loopback-only gateway serving real production assets and forwarding HTTP/WS to FastAPI.
const http=require('node:http'),fs=require('node:fs'),path=require('node:path'),net=require('node:net');
const root=path.resolve(process.env.DECLARATOR_STATIC_ROOT||path.join(__dirname,'../../dist'));
const apiPort=Number(process.env.DECLARATOR_API_PORT||8104),port=Number(process.env.DECLARATOR_WEB_PORT||4401);
const server=http.createServer((req,res)=>{
  if(req.url.startsWith('/api/')){
    const proxy=http.request({hostname:'127.0.0.1',port:apiPort,path:req.url,method:req.method,headers:{...req.headers,host:'soak.test.local'}},up=>{res.writeHead(up.statusCode,up.headers);up.pipe(res);});
    proxy.on('error',()=>{res.writeHead(502);res.end('API unavailable');});req.pipe(proxy);return;
  }
  const url=new URL(req.url,'http://localhost'),admin=url.pathname.startsWith('/admin/');
  const base=path.join(root,admin?'admin':'player');
  let file=path.resolve(base,decodeURIComponent(url.pathname.replace(admin?/^\/admin\//:/^\//,'')));
  if(!file.startsWith(base+path.sep)||!fs.existsSync(file)||!fs.statSync(file).isFile())file=path.join(base,'index.html');
  res.setHeader('Content-Type',({'.js':'text/javascript','.html':'text/html','.css':'text/css','.svg':'image/svg+xml','.png':'image/png','.woff2':'font/woff2'}[path.extname(file)]||'application/octet-stream'));
  fs.createReadStream(file).on('error',()=>{res.statusCode=404;res.end();}).pipe(res);
});
server.on('upgrade',(req,socket,head)=>{
  if(!req.url.startsWith('/api/ws'))return socket.destroy();
  const upstream=net.connect(apiPort,'127.0.0.1',()=>{
    upstream.write(`${req.method} ${req.url} HTTP/${req.httpVersion}\r\n`+Object.entries({...req.headers,host:'soak.test.local'}).map(([k,v])=>`${k}: ${v}`).join('\r\n')+'\r\n\r\n');
    if(head.length)upstream.write(head);socket.pipe(upstream);upstream.pipe(socket);
  });
  upstream.on('error',()=>socket.destroy());socket.on('error',()=>upstream.destroy());socket.on('close',()=>upstream.destroy());
});
server.listen(port,'127.0.0.1');
