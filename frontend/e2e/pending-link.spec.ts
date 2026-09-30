import { test, expect } from "@playwright/test";

test("ссылка на экране, вход перед оплатой и обязательное принятие оферты", async ({page}) => {
  const token="temporary-link-1234567890abcd";
  const link=`https://bravada-connect.ru/sub/${token}`;
  let submitted: Record<string,unknown> | null=null;
  await page.route("**/api/v1/**", async route=> {
    const path=new URL(route.request().url()).pathname;
    let body: unknown={ok:true};
    if(path==="/api/v1/plans") body={ok:true,plans:[
      ["1d",1,20],["7d",7,110],["14d",14,180],["1m",30,439],["3m",90,999],["6m",180,1400],["365d",365,2500]
    ].map(([plan_id,duration_days,base_rubles])=>({plan_id,duration_days,base_rubles})),global_discount:null,custom_day_price_rubles:17,extra_device_price_rubles:85,devices:{base:5,max:20}};
    else if(path==="/api/v1/auth/link/request") {
      expect(route.request().postDataJSON()).toEqual({});
      body={ok:true,subscription_url:link,expires_at:"2026-10-02T12:00:00Z"};
    } else if(path==="/api/v1/user/profile") {
      if(route.request().headers().authorization!==`Bearer ${token}`) return route.fulfill({status:401,json:{ok:false}});
      body={ok:true,user:{telegram_user_id:-1,internal_user_id:"web_test",email:null,pending:true},subscription:null,keys:null,referral:null};
    } else if(path==="/api/v1/payment/create") {
      submitted=route.request().postDataJSON();
      body={ok:true,status:"payment_unavailable",amount_rubles:439,message:"Тестовая оплата"};
    }
    await route.fulfill({json:body});
  });
  await page.goto('/payment/1m');
  await page.getByRole('button',{name:'Получить ссылку подписки'}).click();
  await expect(page.getByLabel('Ваша ссылка')).toHaveValue(link);
  await page.getByLabel('Ссылка подписки',{exact:true}).fill(link);
  await page.getByRole('button',{name:'Продолжить',exact:true}).click();
  const pay=page.getByRole('button',{name:'Перейти к оплате 439 ₽'});
  await expect(pay).toBeDisabled();
  await page.getByRole('checkbox').check();
  await expect(pay).toBeEnabled();
  await pay.click();
  await expect(page.getByText('Тестовая оплата')).toBeVisible();
  expect(submitted).toMatchObject({offer_accepted:true,offer_version:'2026-09-30',expected_amount_kopecks:43900});
});
