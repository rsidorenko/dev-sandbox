import { expect, it } from "vitest";
import { priceForPlan, savingsPctForPlan, perDayRub } from "./savings";
import type { PlansCatalogResponse } from "@/entities/plans/types";
const catalog: PlansCatalogResponse={ok:true,plans:[{plan_id:"1d",duration_days:1,base_rubles:20},{plan_id:"1m",duration_days:30,base_rubles:300}],global_discount:null,custom_day_price_rubles:15,extra_device_price_rubles:80,devices:{base:5,max:20}};
it("считает экономию по текущему каталогу",()=>{expect(priceForPlan("1m",catalog)).toBe(300);expect(perDayRub("1m",catalog)).toBe(10);expect(savingsPctForPlan("1m",catalog)).toBe(50);});
it("не возвращает статическую цену без каталога",()=>expect(priceForPlan("1m")).toBe(0));
