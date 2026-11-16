import { expect, test } from '@playwright/test';

/** Frontend-only soak, replayed HTTP/WebSocket traffic. Financial guarantees are tested in pytest. */
test('Declarator remains bounded under continuous betting updates', async ({page}, testInfo) => {
  const seconds = Number(process.env['DECLARATOR_BROWSER_SECONDS'] || 30);
  test.setTimeout((seconds+60)*1000);
  let frames = 0, connections = 0, active = 0, peak = 0;
  const counts: Record<string, number> = {}, errors: string[] = [];
  const event = {id:'event',name:'Soak arena',status:'live',match_count:1,completed_match_count:0,starts_at:new Date().toISOString()};
  const match = {id:'fight',event_id:'event',match_number:1,status:'in_progress',meron_name:'Meron',wala_name:'Wala',result:null};
  const book = () => ({match_id:'fight',event_id:'event',match_number:1,match_status:'in_progress',state:'open',accepting:true,
    pools:{meron:String(frames*100+100),wala:'100',draw:'0'},odds:{meron_display:'1.83',wala_display:'1.83',meron_probability:'0.5',wala_probability:'0.5'},
    dummy_meron:String(frames*100),dummy_wala:'0',bet_count:1000,version:frames,draw_multiplier:'8',commission_rate:'0.1',obligations:null,settlement:null,
    players:Array.from({length:1000},(_,i)=>({username:`player${i}`,full_name:`Player ${i}`,meron:'100',wala:'100',draw:'0'}))});
  const monitor = () => ({event,matches:[match],current:{match,workflow_status:'pending'},books:[book()],
    betting:{match_id:'fight',match_number:1,match_status:'in_progress',status:'open',book_state:'open',can_open:false,can_close:true}});
  page.on('pageerror',e=>errors.push(e.message));
  await page.addInitScript(() => {
    (window as any).soak = {longTasks:0,maxLongTask:0};
    new PerformanceObserver(list=>{ for (const entry of list.getEntries()) {
      (window as any).soak.longTasks++; (window as any).soak.maxLongTask=Math.max((window as any).soak.maxLongTask,entry.duration);
    }}).observe({entryTypes:['longtask']});
  });
  await page.route('**/api/v1/**', async route => {
    const pathname = new URL(route.request().url()).pathname;
    counts[pathname] = (counts[pathname]||0)+1;
    let body: unknown = {items:[],total:0,page:1,page_size:25};
    if(pathname.endsWith('/public/tenant')) body={id:'tenant',slug:'soak',name:'Soak',status:'active',modules:['events','matches'],feature_flags:{},branding:{app_name:'Soak'}};
    else if(pathname.endsWith('/auth/refresh')) body={access_token:'soak-token',expires_in:86400,token_type:'bearer'};
    else if(pathname.endsWith('/auth/me')) body={id:'judge',username:'judge',full_name:'Judge',account_type:'declarator',permissions:['results.submit'],must_change_password:false};
    else if(pathname.endsWith('/declarator/events')) body=[event];
    else if(pathname.endsWith('/monitor')) body=monitor();
    else if(pathname.endsWith('/book') || pathname.endsWith('/dummy-bets')) body=book();
    else if(pathname.endsWith('/ghost-auto')) body={active:true,runs:frames,added_meron:String(frames*100),added_wala:'0'};
    else if(pathname.endsWith('/online')) body={players:1000,items:[]};
    else if(pathname.includes('quick-bets')) body={meron:['5000.00','10000.00','20000.00','50000.00'],wala:['5000.00','10000.00','20000.00','50000.00']};
    else if(pathname.includes('playback')) body={source:null,status:'offline'};
    active++; peak=Math.max(peak,active);
    await new Promise(resolve=>setTimeout(resolve,25));
    await route.fulfill({json:body}); active--;
  });
  let send: ((message:string)=>void) | undefined;
  await page.routeWebSocket('**/api/ws**', socket => {
    connections++; send=message=>socket.send(message);
    socket.onMessage(message=>{ if(String(message).includes('ping')) socket.send('{"type":"pong"}'); });
    socket.send('{"type":"connected","data":{}}');
  });
  await page.goto('http://127.0.0.1:4301/admin/declarator');
  await expect(page.locator('[data-testid="activity-table"]')).toBeVisible();
  const cdp = await page.context().newCDPSession(page);
  await cdp.send('Performance.enable');
  const samples: unknown[] = [];
  const sample = async () => {
    await cdp.send('HeapProfiler.collectGarbage');
    const {metrics} = await cdp.send('Performance.getMetrics');
    samples.push({seconds:Math.round((Date.now()-started)/1000),metrics:Object.fromEntries(metrics.filter(m=>['JSHeapUsedSize','Nodes','JSEventListeners','TaskDuration','LayoutDuration','RecalcStyleDuration'].includes(m.name)).map(m=>[m.name,m.value]))});
  };
  const started = Date.now(); await sample(); let lastSample=Date.now();
  while(Date.now()-started<seconds*1000) {
    for(let i=0;i<5;i++){frames++;send?.(JSON.stringify({type:'book.updated',data:{event_id:'event',match_id:'fight',version:frames}}));}
    if(frames%100===0) await page.getByRole('button',{name:'Add 5K ghost points to MERON',exact:true}).click();
    await page.waitForTimeout(50);
    if(Date.now()-lastSample>=30000){await sample();lastSample=Date.now();}
  }
  await sample();
  expect(connections).toBe(1);
  const renderedRows = await page.locator('[data-testid="activity-table"] tbody tr').count();
  if (!process.env['DECLARATOR_BASELINE']) expect(renderedRows).toBeLessThanOrEqual(25);
  expect(errors).toEqual([]);
  // Startup/reconciliation only, never one history request per betting frame.
  if (!process.env['DECLARATOR_BASELINE']) expect(counts['/api/v1/declarator/events/event/monitor']).toBeLessThanOrEqual(Math.ceil(seconds/30)+3);
  if (!process.env['DECLARATOR_BASELINE']) {
    const measured = samples as {seconds:number;metrics:Record<string,number>}[];
    const warm = measured[seconds >= 30 ? 1 : 0].metrics, last = measured.at(-1)!.metrics;
    expect(last['JSEventListeners']).toBeLessThanOrEqual(warm['JSEventListeners']+10);
    expect(last['Nodes']).toBeLessThanOrEqual(warm['Nodes']+100);
    expect(last['JSHeapUsedSize']).toBeLessThanOrEqual(warm['JSHeapUsedSize']*1.25+1_000_000);
    expect(counts['/api/v1/declarator/matches/fight/book']).toBeGreaterThan(seconds);
  }
  const report={mode:'mocked-browser',renderedRows,seconds,frames,connections,peakHttpRequests:peak,counts,samples,longTasks:await page.evaluate(()=>(window as any).soak),errors};
  await testInfo.attach('declarator-soak.json',{body:JSON.stringify(report,null,2),contentType:'application/json'});
  console.log(JSON.stringify(report));
});
