import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import {
  analyticsApi,
  type BackfillRequest,
  type FireSettingsRequest,
  type RebalanceTargetRequest,
} from "./api"

export function useAnalyticsPerformance(
  accountIds: number[],
  from: string,
  to: string,
  benchmark: string
) {
  return useQuery({
    queryKey: ["analytics", "performance", accountIds, from, to, benchmark],
    queryFn: () => analyticsApi.performance(accountIds, from, to, benchmark),
    enabled: accountIds.length > 0,
  })
}

export function useFire(accountIds: number[]) {
  return useQuery({
    queryKey: ["analytics", "fire", accountIds],
    queryFn: () => analyticsApi.fire(accountIds),
    enabled: accountIds.length > 0,
  })
}

export function useFireSettings() {
  return useQuery({
    queryKey: ["analytics", "fire-settings"],
    queryFn: analyticsApi.fireSettings,
  })
}

export function usePortfolioLots(accountIds: number[]) {
  return useQuery({
    queryKey: ["analytics", "lots", accountIds],
    queryFn: () => analyticsApi.lots(accountIds),
    enabled: accountIds.length > 0,
  })
}

export function useRebalance(accountIds: number[]) {
  return useQuery({
    queryKey: ["analytics", "rebalance", accountIds],
    queryFn: () => analyticsApi.rebalance(accountIds),
    enabled: accountIds.length > 0,
  })
}

export function useBackfillPlan(accountIds: number[]) {
  return useQuery({
    queryKey: ["analytics", "backfill-plan", accountIds],
    queryFn: () => analyticsApi.backfillPlan(accountIds),
    enabled: accountIds.length > 0,
  })
}

export function useUpdateFireSettings() {
  const client = useQueryClient()
  return useMutation({
    mutationFn: (data: FireSettingsRequest) =>
      analyticsApi.updateFireSettings(data),
    onSuccess: () =>
      client.invalidateQueries({ queryKey: ["analytics", "fire"] }),
  })
}

export function useSetRebalanceTargets() {
  const client = useQueryClient()
  return useMutation({
    mutationFn: (targets: RebalanceTargetRequest[]) =>
      analyticsApi.setRebalanceTargets(targets),
    onSuccess: () =>
      client.invalidateQueries({ queryKey: ["analytics", "rebalance"] }),
  })
}

export function useBackfill() {
  const client = useQueryClient()
  return useMutation({
    mutationFn: (data: BackfillRequest) => analyticsApi.backfill(data),
    onSuccess: () => client.invalidateQueries({ queryKey: ["analytics"] }),
  })
}
