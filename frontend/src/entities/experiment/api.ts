import { apiFetch } from "@/shared/api/client";

export type ExperimentVariant = "control" | "reassurance";

export interface ExperimentResolveResponse {
  ok: true;
  experiment: {
    key: string;
    variant: ExperimentVariant;
    assigned_at: string;
  };
}

export const experimentApi = {
  resolve: (experimentKey: string, planId?: string) =>
    apiFetch<ExperimentResolveResponse>("/api/v1/user/experiments/resolve", {
      method: "POST",
      body: JSON.stringify({ experiment_key: experimentKey, plan_id: planId }),
    }),
};
