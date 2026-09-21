import { api } from "@/lib/api-client"
import type {
  AnalyticsPerformance,
  BackfillPlan,
  BackfillResult,
  FireProjection,
  FireSettings,
  PortfolioLot,
  Rebalance,
} from "@/types/api"

export interface FireSettingsRequest extends Omit<
  FireSettings,
  "currentAge" | "retirementAge"
> {
  currentAge: number | null
  retirementAge: number | null
}

export interface RebalanceTargetRequest {
  accountId: number
  ticker: string
  targetPercent: number
}

export interface BackfillRequest {
  accountIds: number[]
  accountStartDates: Record<number, string>
  lineStartDates: Record<string, string>
}

export const analyticsApi = {
  performance: (
    accountIds: number[],
    from: string,
    to: string,
    benchmark: string
  ) =>
    api
      .get<AnalyticsPerformance>("/analytics/performance", {
        params: { accountIds, from, to, benchmark },
        paramsSerializer: { indexes: null },
      })
      .then((response) => response.data),
  fire: (accountIds: number[]) =>
    api
      .get<FireProjection>("/analytics/fire", {
        params: { accountIds },
        paramsSerializer: { indexes: null },
      })
      .then((response) => response.data),
  fireSettings: () =>
    api
      .get<FireSettings>("/analytics/fire/settings")
      .then((response) => response.data),
  updateFireSettings: (data: FireSettingsRequest) =>
    api
      .put<FireSettings>("/analytics/fire/settings", data)
      .then((response) => response.data),
  lots: (accountIds: number[]) =>
    api
      .get<PortfolioLot[]>("/analytics/lots", {
        params: { accountIds },
        paramsSerializer: { indexes: null },
      })
      .then((response) => response.data),
  rebalance: (accountIds: number[]) =>
    api
      .get<Rebalance>("/analytics/rebalance", {
        params: { accountIds },
        paramsSerializer: { indexes: null },
      })
      .then((response) => response.data),
  setRebalanceTargets: (targets: RebalanceTargetRequest[]) =>
    api
      .put<Rebalance>("/analytics/rebalance/targets", { targets })
      .then((response) => response.data),
  backfillPlan: (accountIds: number[]) =>
    api
      .get<BackfillPlan>("/analytics/backfill/plan", {
        params: { accountIds },
        paramsSerializer: { indexes: null },
      })
      .then((response) => response.data),
  backfill: (data: BackfillRequest) =>
    api
      .post<BackfillResult>("/analytics/backfill", data)
      .then((response) => response.data),
}
