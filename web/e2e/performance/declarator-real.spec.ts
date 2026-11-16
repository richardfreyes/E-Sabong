import { test, expect, request, APIRequestContext, Page } from '@playwright/test';
import { appendFileSync, mkdirSync, writeFileSync } from 'node:fs';
import { execFileSync } from 'node:child_process';
import path from 'node:path';

/** Actual production Angular assets + FastAPI + PostgreSQL + Redis. No route interception. */
test('real Declarator sustained lifecycle and financial integrity', async ({browser}, info) => {
  const seconds=Number(process.env['DECLARATOR_REAL_SECONDS']||120);
  const origin=process.env['DECLARATOR_REAL_ORIGIN']||'http://127.0.0.1:4401';
  if(!['127.0.0.1','localhost'].includes(new URL(origin).hostname) && !process.env['DECLARATOR_ALLOW_UAT']) throw Error('Explicit UAT opt-in required');
  const out=path.resolve(process.env['DECLARATOR_REAL_OUT']||'test-results/declarator-real');mkdirSync(out,{recursive:true});
  test.setTimeout((seconds+600)*1000);
  const errors:string[]=[],latencies=new Map<string,{count:number;sum:number;max:number;values:number[]}>();
  let start=0,cycles=0,bets=0,manual=0,frames=0,wsConnections=0,wsActive=0,wsPeak=0,reconnects=0;
  const checkpoints:unknown[]=[],actions:Record<string,number[]>={},apiActors:Actor[]=[],contexts:any[]=[];
  const record=(name:string,ms:number)=>{let row=latencies.get(name);if(!row){row={count:0,sum:0,max:0,values:[]};latencies.set(name,row);}row.count++;row.sum+=ms;row.max=Math.max(row.max,ms);if(row.values.length===2048)row.values.shift();row.values.push(ms);};
  const percentile=(values:number[],p:number)=>[...values].sort((a,b)=>a-b)[Math.min(values.length-1,Math.floor(values.length*p))]||0;
  class Actor {
    token='';refreshing?:Promise<void>;refreshed=Date.now();ctx!:APIRequestContext;socket?:WebSocket;ping?:ReturnType<typeof setInterval>;closed=false;generation=0;recoveries=0;settled=new Set<string>();
    constructor(readonly name:string,readonly portal:string){}
    async init(){this.ctx=await request.newContext({baseURL:origin,extraHTTPHeaders:{'X-Requested-With':'XMLHttpRequest','X-Portal':this.portal}});const r=await this.ctx.post('/api/v1/auth/login',{data:{username:this.name,password:'DemoPass#2026'}});expect(r.status(),await r.text()).toBe(200);this.token=(await r.json()).access_token;apiActors.push(this);return this;}
    async renew(){if(this.refreshing)return this.refreshing;this.refreshing=(async()=>{const r=await this.ctx.post('/api/v1/auth/refresh',{data:{}});expect(r.status(),await r.text()).toBe(200);this.token=(await r.json()).access_token;this.refreshed=Date.now();})();try{await this.refreshing;}finally{this.refreshing=undefined;}}
    async call(method:string,url:string,data?:unknown,expected=200){if(Date.now()-this.refreshed>540000)await this.renew();const began=performance.now();let r=await this.ctx.fetch('/api/v1'+url,{method,data,headers:{Authorization:`Bearer ${this.token}`}});if(r.status()===401){await this.renew();r=await this.ctx.fetch('/api/v1'+url,{method,data,headers:{Authorization:`Bearer ${this.token}`}});}record(url.replace(/[0-9a-f]{8}-[0-9a-f-]{27}/g,':id'),performance.now()-began);expect(r.status(),`${method} ${url}: ${await r.text()}`).toBe(expected);return r.json();}
    connect(){if(this.closed)return;const ws=new WebSocket(origin.replace('http','ws')+'/api/ws?token='+encodeURIComponent(this.token));this.socket=ws;this.generation++;wsConnections++;
      ws.addEventListener('open',()=>{wsActive++;wsPeak=Math.max(wsPeak,wsActive);});
      ws.addEventListener('message',event=>{const message=JSON.parse(String(event.data));frames++;if(message.type==='fight.settled'){const key=message.data.match_id+':'+message.data.revision;if(this.settled.has(key))errors.push('Duplicate settlement event: '+key);this.settled.add(key);}});
      ws.addEventListener('close',()=>{wsActive=Math.max(0,wsActive-1);if(!this.closed){reconnects++;void this.renew().then(()=>this.connect()).catch(e=>errors.push(String(e)));}});
      ws.addEventListener('error',()=>errors.push('Player WebSocket transport error'));
      if(!this.ping)this.ping=setInterval(()=>{if(this.socket?.readyState===WebSocket.OPEN)this.socket.send('{"type":"ping"}');},25000);
    }
    async stop(){this.closed=true;if(this.ping)clearInterval(this.ping);this.socket?.close(1000);await this.ctx.dispose();}
  }
  const admin=await new Actor('admin','admin').init(),judge=await new Actor('declarator','admin').init();
  await admin.call('PUT','/admin/betting/settings',{enabled:true,starting_balance:'10000000.00',min_bet:'1.00',max_fight_stake:'100000.00'});
  const profile=await judge.call('GET','/auth/me');
  const event=await admin.call('POST','/admin/events',{name:'Real soak '+Date.now(),venue:'Synthetic verification',starts_at:new Date().toISOString()},201);
  for(const operation of ['publish','start'])await admin.call('POST',`/admin/events/${event.id}/${operation}`);
  await admin.call('PUT',`/declarator/events/${event.id}/assignments/${profile.id}`);
  const run=Date.now().toString(36);
  const players:Actor[]=[];
  for(let i=1;i<=12;i++){
    if(i>3)await admin.call('POST','/admin/users',{username:`soak-${run}-${i}`,full_name:`Soak player ${i}`,account_type:'player',password:'DemoPass#2026'},201);
    const actor=await new Actor(i<=3?`player${i}`:`soak-${run}-${i}`,'player').init();players.push(actor);actor.connect();
  }
  const pages:Page[]=[];
  async function login(username:string,adminPage:boolean){const ctx=await browser.newContext();contexts.push(ctx);const page=await ctx.newPage();pages.push(page);page.on('pageerror',e=>errors.push(e.message));page.on('websocket',ws=>{ws.on('framereceived',()=>frames++);});
    await page.addInitScript(()=>{(window as any).realSoak={longTasks:0,maxLongTask:0};new PerformanceObserver(list=>{for(const e of list.getEntries()){(window as any).realSoak.longTasks++;(window as any).realSoak.maxLongTask=Math.max((window as any).realSoak.maxLongTask,e.duration);}}).observe({entryTypes:['longtask']});});
    await page.goto(origin+(adminPage?'/admin/login':'/login'));await page.getByLabel('Username',{exact:true}).fill(username);await page.getByLabel('Password',{exact:true}).fill('DemoPass#2026');await page.getByRole('button',{name:'Sign in',exact:true}).click();await expect(page).not.toHaveURL(/login/);return page;}
  const desk=await login('declarator',true);
  await expect(desk.getByTestId('betting-control')).toBeVisible();
  // Explicitly choose this run's event; demo fixtures also contain a live card.
  const selector=desk.getByRole('combobox',{name:'Event',exact:true}).first();await selector.click();await desk.getByRole('option',{name:event.name,exact:true}).click();
  for(let i=1;i<=3;i++)await login(`player${i}`,false);
  const cdp=await desk.context().newCDPSession(desk);await cdp.send('Performance.enable');
  const browserCdp=await browser.newBrowserCDPSession();
  async function checkpoint(label:string){await cdp.send('HeapProfiler.collectGarbage');const {metrics}=await cdp.send('Performance.getMetrics');const browserProcesses=await browserCdp.send('SystemInfo.getProcessInfo');
    const processRows=execFileSync('ps',['-axo','pid=,rss=,%cpu=,time=,command='],{encoding:'utf8'}).split('\n').filter(line=>line.includes('declarator_runtime.py serve')||browserProcesses.processInfo.some(p=>line.trim().startsWith(p.id+' '))).map(line=>{const match=line.trim().match(/^(\d+)\s+(\d+)\s+([\d.]+)\s+(\S+)/);return match?{pid:Number(match[1]),rssKiB:Number(match[2]),cpuPercent:Number(match[3]),cpuTime:match[4]}:null;}).filter(Boolean);
    const latency=Object.fromEntries([...latencies].map(([key,row])=>[key,{count:row.count,mean:row.sum/row.count,p95:percentile(row.values,.95),max:row.max}]));
    const online=await judge.call('GET',`/declarator/events/${event.id}/online`);const reconciliation=await admin.call('GET','/admin/betting/reconciliation');expect(reconciliation.balanced).toBe(true);expect(reconciliation.checks.pool_mismatches).toBe(0);
    const data={label,at:new Date().toISOString(),elapsedSeconds:(Date.now()-start)/1000,cycles,bets,manual,frames,wsConnections,wsActive,wsPeak,reconnects,onlinePlayers:online.players,metrics:Object.fromEntries(metrics.map(m=>[m.name,m.value])),processes:processRows,browserCpu:browserProcesses.processInfo,actions:Object.fromEntries(Object.entries(actions).map(([name,values])=>[name,{count:values.length,p95:percentile(values,.95),max:Math.max(...values)}])),latency,longTasks:await desk.evaluate(()=>(window as any).realSoak),errors:[...errors],reconciliation};
    checkpoints.push(data);appendFileSync(path.join(out,'checkpoints.jsonl'),JSON.stringify(data)+'\n');writeFileSync(path.join(out,'status.json'),JSON.stringify(data,null,2));console.log(JSON.stringify({checkpoint:label,elapsedSeconds:data.elapsedSeconds,cycles,bets,frames,wsActive,heap: data.metrics['JSHeapUsedSize'],errors:errors.length}));}
  async function action(name:string,click:()=>Promise<void>,ready:()=>Promise<void>){const began=performance.now();await click();await ready();(actions[name]??=[]).push(performance.now()-began);}
  start=Date.now();writeFileSync(path.join(out,'run.json'),JSON.stringify({started:new Date(start).toISOString(),seconds,origin,eventId:event.id,mode:'real-http-websocket-postgresql-redis',checkpoints:[1800,3600,10800]},null,2));await checkpoint('start');
  let nextMinute=60;const milestones=new Set<number>();
  try {
    while(Date.now()-start<seconds*1000){
      await action('open',()=>desk.getByRole('button',{name:'Open bet',exact:true}).click(),()=>expect(desk.getByTestId('betting-status')).toHaveText('OPEN'));
      const snapshot=await judge.call('GET',`/declarator/events/${event.id}/monitor`);const mid=snapshot.betting.match_id;
      await desk.getByTestId('ghost-interval').fill('1');
      await action('auto-start',()=>desk.getByTestId('ghost-start').click(),()=>expect(desk.getByTestId('ghost-status')).toHaveText('ACTIVE'));
      const cycleStart=Date.now();let tick=0, restarted=false;
      while(Date.now()-cycleStart<20000 && Date.now()-start<seconds*1000){
        const actor=players[tick%players.length];await actor.call('POST',`/portal/matches/${mid}/bets`,{side:tick%3===0?'draw':tick%2?'meron':'wala',amount:'1.00',request_id:crypto.randomUUID()},201);bets++;
        if(tick%4===0){await action('manual-ghost',()=>desk.getByRole('button',{name:'Add 5K ghost points to MERON',exact:true}).click(),async()=>{});manual++;}
        if(cycles%2===1&&!restarted&&Date.now()-cycleStart>=10000){
          await action('auto-stop',()=>desk.getByTestId('ghost-stop').click(),()=>expect(desk.getByTestId('ghost-status')).toHaveText('INACTIVE'));
          await action('auto-restart',()=>desk.getByTestId('ghost-start').click(),()=>expect(desk.getByTestId('ghost-status')).toHaveText('ACTIVE'));restarted=true;
        }
        tick++;await desk.waitForTimeout(100);
      }
      await action('close',()=>desk.getByRole('button',{name:'Close bet',exact:true}).click(),()=>expect(desk.getByTestId('betting-status')).toHaveText('CLOSED'));
      const outcome=['Meron','Wala','Draw','Cancel'][cycles%4];
      const deliveryGenerations=players.map(p=>p.generation);
      await action('declare',async()=>{await desk.getByRole('button',{name:`Declare ${outcome}`,exact:true}).click();const dialog=desk.getByRole('dialog');if(outcome==='Cancel')await dialog.getByLabel('Cancellation reason').fill('Synthetic soak cancellation');await dialog.getByRole('button',{name:outcome==='Cancel'?'Cancel fight':'Confirm declaration',exact:true}).click();},async()=>{await expect(desk.getByTestId('settlement-summary')).not.toContainText('Awaiting result');await expect(desk.getByRole('button',{name:'Open bet',exact:true})).toBeEnabled();});
      const book=process.env['DECLARATOR_BASELINE'] ? (await judge.call('GET',`/declarator/events/${event.id}/monitor`)).books.find((b:any)=>b.match_id===mid) : await judge.call('GET',`/declarator/matches/${mid}/book`);expect(book.state).toBe('settled');expect(book.settlement.outcome).toBe(outcome==='Cancel'?'cancelled':outcome.toLowerCase());
      const settlementKey=mid+':'+book.settlement.revision;
      await desk.waitForTimeout(100);
      for(const [index,actor] of players.entries()){
        if(actor.settled.has(settlementKey))continue;
        if(actor.generation!==deliveryGenerations[index]||actor.socket?.readyState!==WebSocket.OPEN){
          const recovered=process.env['DECLARATOR_BASELINE'] ? (await judge.call('GET',`/declarator/events/${event.id}/monitor`)).books.find((b:any)=>b.match_id===mid) : await judge.call('GET',`/declarator/matches/${mid}/book`);
          expect(recovered.state).toBe('settled');actor.recoveries++;
        }else{await expect.poll(()=>actor.settled.has(settlementKey),{timeout:3000}).toBe(true);}
      }
      const auto=await judge.call('GET',`/declarator/events/${event.id}/ghost-auto`);expect(auto.active).toBe(false);expect(auto.runs).toBeGreaterThan(0);cycles++;
      appendFileSync(path.join(out,'cycles.jsonl'),JSON.stringify({cycle:cycles,matchId:mid,outcome,autoRuns:auto.runs,bookVersion:book.version,at:new Date().toISOString()})+'\n');
      const elapsed=(Date.now()-start)/1000;
      if(elapsed>=nextMinute){await checkpoint('minute-'+Math.floor(elapsed/60));nextMinute+=60;}
      for(const gate of [1800,3600,10800])if(elapsed>=gate&&!milestones.has(gate)){await checkpoint('gate-'+gate);milestones.add(gate);}
    }
    await checkpoint('final');expect(errors).toEqual([]);expect(wsActive).toBe(players.length);expect(frames).toBeGreaterThan(seconds*2);
    const final={passed:true,seconds:(Date.now()-start)/1000,cycles,bets,manual,frames,wsConnections,wsPeak,reconnects,settlementDelivery:players.map(p=>({actor:p.name,received:p.settled.size,recoveries:p.recoveries})),checkpoints:checkpoints.length,errors};writeFileSync(path.join(out,'result.json'),JSON.stringify(final,null,2));await info.attach('real-soak-result',{body:JSON.stringify(final),contentType:'application/json'});
  } catch(error){writeFileSync(path.join(out,'result.json'),JSON.stringify({passed:false,elapsed:(Date.now()-start)/1000,error:String(error),errors},null,2));throw error;}
  finally {for(const actor of apiActors)await actor.stop();for(const context of contexts)await context.close();}
});
