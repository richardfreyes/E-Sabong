import { expect, test } from '@playwright/test';
import { DEMO_PASSWORD, PLAYER_URL, apiCall, apiLogin, tenantLogin } from './helpers';

test('Agent creates a Sub-Agent, shares 3% of 5%, and each level receives its settled share', async ({page,browser,request})=>{
  test.setTimeout(120_000);
  const admin=await apiLogin(request,'admin');
  const suffix=Date.now().toString(36);
  const masterName=`master${suffix}`,agentName=`agent${suffix}`,subName=`sub${suffix}`,playerName=`player${suffix}`;
  async function create(token:string,username:string,account_type:string,parent_id?:string) {
    const response=await apiCall(request,token,'post','/admin/users',{username,full_name:username,account_type,parent_id,password:DEMO_PASSWORD});
    expect(response.status(),await response.text()).toBe(201);return (await response.json()).user;
  }
  const master=await create(admin,masterName,'master_agent');
  const boss=await apiLogin(request,masterName);
  const agent=await create(boss,agentName,'agent',master.id);
  expect((await apiCall(request,boss,'put',`/commissions/agents/${agent.id}`,{rate:'0.0500'})).status()).toBe(200);
  await tenantLogin(page,PLAYER_URL,agentName);
  await expect(page).toHaveURL(/\/agent$/);
  await page.getByRole('button',{name:'Open navigation',exact:true}).click();
  await page.getByRole('link',{name:'Sub-Agents',exact:true}).click();
  await expect(page.getByRole('heading',{name:'Sub-Agents',exact:true})).toBeVisible();
  await page.getByRole('button',{name:'Create Sub-Agent',exact:true}).click();
  const dialog=page.getByRole('dialog');
  await dialog.getByLabel('Username',{exact:true}).fill(subName);
  await dialog.getByLabel('Full name',{exact:true}).fill(subName);
  const created=page.waitForResponse(r=>r.url().includes('/admin/users')&&r.request().method()==='POST');
  await dialog.getByRole('button',{name:'Create account',exact:true}).click();
  const sub=(await (await created).json());
  expect(sub.user.account_type).toBe('sub_agent');expect(sub.user.parent_id).toBe(agent.id);
  await dialog.getByRole('button',{name:'Done',exact:true}).click();
  const row=page.locator(`[data-user="${subName}"]`);
  await expect(row).toBeVisible();
  await row.getByRole('button',{name:`Actions for ${subName}`}).click();
  await page.getByRole('menuitem',{name:'Set commission rate',exact:true}).click();
  await page.getByTestId('my-users').scrollIntoViewIfNeeded();
  await page.locator('[data-field="rate-draft"]').fill('6');
  await expect(page.getByRole('form',{name:'Set commission rate'}).getByRole('button',{name:'Save',exact:true})).toBeDisabled();
  await page.locator('[data-field="rate-draft"]').fill('3');
  await expect(page.locator('[data-field="keeps"]')).toContainText('You keep 2.00%');
  await page.getByRole('form',{name:'Set commission rate'}).getByRole('button',{name:'Save',exact:true}).click();
  await expect(row).toContainText('3.00%');await expect(row).toContainText('You retain 2.00%');
  const subToken=await apiLogin(request,subName,sub.temporary_password);
  expect((await apiCall(request,subToken,'post','/auth/me/password',{current_password:sub.temporary_password,new_password:DEMO_PASSWORD})).status()).toBe(200);
  const player=await create(subToken,playerName,'player',sub.user.id);
  const playerToken=await apiLogin(request,playerName);
  const settings=await (await apiCall(request,admin,'get','/admin/betting/settings')).json();
  try {
    expect((await apiCall(request,admin,'put','/admin/betting/settings',{enabled:true,starting_balance:'10000.00',max_bet:'10000.00'})).status()).toBe(200);
    const eventResponse=await apiCall(request,admin,'post','/admin/events',{name:`Sub-Agent ${suffix}`,venue:'Commission verification',starts_at:'2030-08-01T18:00:00+08:00'});
    expect(eventResponse.status(),await eventResponse.text()).toBe(201);
    const event=await eventResponse.json();
    const fight=await (await apiCall(request,admin,'post',`/admin/events/${event.id}/matches`,{meron_name:'Meron',wala_name:'Wala'})).json();
    for(const path of [`/admin/events/${event.id}/publish`,`/admin/events/${event.id}/start`,`/admin/matches/${fight.id}/betting/open`]) {
      const response=await apiCall(request,admin,'post',path);expect(response.status(),await response.text()).toBe(200);
    }
    expect((await apiCall(request,playerToken,'post',`/portal/matches/${fight.id}/bets`,{request_id:crypto.randomUUID(),side:'meron',amount:'10000.00'})).status()).toBe(201);
    await apiCall(request,admin,'post',`/admin/matches/${fight.id}/start`);
    expect((await apiCall(request,admin,'post',`/admin/matches/${fight.id}/result`,{outcome:'meron'})).status()).toBe(201);
    for(const [token,expected] of [[boss,'350.00'],[await apiLogin(request,agentName),'200.00'],[subToken,'300.00']]) {
      expect((await (await apiCall(request,token,'get','/commissions/me')).json()).total_earned).toBe(expected);
    }
    await expect(row).toContainText('300.00'); // WebSocket refresh of the Sub-Agent's earnings
    const subContext=await browser.newContext();const subPage=await subContext.newPage();
    await tenantLogin(subPage,PLAYER_URL,subName);
    await expect(subPage).toHaveURL(/\/agent$/);await expect(subPage.locator('.role')).toContainText('Sub-Agent');
    await subPage.getByRole('button',{name:'Open navigation',exact:true}).click();
    await expect(subPage.getByRole('link',{name:'Sub-Agents',exact:true})).toHaveCount(0);
    await subPage.getByRole('link',{name:'My Users',exact:true}).click();
    await expect(subPage.locator(`[data-user="${playerName}"]`)).toBeVisible();
    const merge=await apiCall(request,subToken,'post','/commissions/earnings-moves',{amount:'100.00',idempotency_key:crypto.randomUUID()});
    expect(merge.status(),await merge.text()).toBe(201);
    await subPage.getByRole('button',{name:'Open navigation',exact:true}).click();
    await subPage.getByRole('link',{name:'Logs',exact:true}).click();
    await expect(subPage.getByRole('region',{name:'Main Wallet transaction history'})).toContainText('Balance 100.00');
    await subPage.setViewportSize({width:390,height:844});
    await expect(subPage.getByRole('heading',{name:'Logs',exact:true})).toBeVisible();
    expect((await apiCall(request,subToken,'put',`/commissions/sub-agents/${sub.user.id}`,{rate:'0.0400'})).status()).toBe(403);
    await subContext.close();
  } finally {
    await apiCall(request,admin,'put','/admin/betting/settings',settings);
  }
});
