import { describe, expect, it } from "vitest";
import { calcExtraDeviceCost, getTariffInfo, roundRubles } from "./calc";
import type { PlansCatalogResponse } from "@/entities/plans/types";
export const catalog: PlansCatalogResponse = {ok:true, plans:[{plan_id:"1d",duration_days:1,base_rubles:12},{plan_id:"1m",duration_days:30,base_rubles:439}],global_discount:null,custom_day_price_rubles:17,extra_device_price_rubles:85,devices:{base:5,max:20}};
describe("серверный каталог",()=>{
 it("не подставляет устаревшую цену при отсутствии каталога",()=>expect(getTariffInfo("1m")).toBeNull());
 it("использует цену и длительность сервера",()=>expect(getTariffInfo("1m",catalog)?.basePrice).toBe(439));
 it("использует серверную цену произвольного срока",()=>expect(getTariffInfo("custom:10",catalog)?.basePrice).toBe(170));
 it("ограничивает произвольный срок",()=>expect(getTariffInfo("custom:366",catalog)).toBeNull());
 it("считает устройства по серверной цене",()=>expect(calcExtraDeviceCost(30,2,85)).toBe(170));
 it("округляет как Python",()=>{expect(roundRubles(2.5)).toBe(2);expect(roundRubles(3.5)).toBe(4);});
});
