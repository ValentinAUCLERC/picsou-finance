package com.picsou.dto;

import com.picsou.model.SnapshotOrigin;
import java.math.BigDecimal;
import java.time.LocalDate;
import java.util.List;
import java.util.Map;

/** Read models used by the member-scoped portfolio analytics endpoints. */
public final class AnalyticsResponse {
    private AnalyticsResponse() {}

    public record PerformancePoint(LocalDate date, BigDecimal total, BigDecimal invested,
                                   BigDecimal twrPercent, BigDecimal benchmarkPercent,
                                   boolean estimated) {}
    public record Performance(List<PerformancePoint> points, Map<String, BigDecimal> monthlyReturns,
                              BigDecimal annualizedReturn, String benchmarkTicker, List<String> warnings) {}
    public record FireSettings(BigDecimal annualExpenses, BigDecimal monthlySavings,
                               BigDecimal safeWithdrawalRate, Integer currentAge, Integer retirementAge,
                               String returnMode, BigDecimal manualReturnRate) {}
    public record FireProjection(FireSettings settings, BigDecimal fireNumber, BigDecimal currentValue,
                                 BigDecimal progressPercent, BigDecimal annualReturnRate,
                                 LocalDate estimatedFireDate, BigDecimal annualPassiveIncome,
                                 boolean observedReturnAvailable) {}
    public record Lot(Long accountId, String accountName, String ticker, String name, LocalDate purchaseDate,
                      BigDecimal originalQuantity, BigDecimal remainingQuantity, BigDecimal costPerUnit,
                      BigDecimal costBasis, BigDecimal currentPrice, BigDecimal currentValue,
                      BigDecimal pnl, BigDecimal pnlPercent, boolean detailed) {}
    public record RebalanceLine(Long accountId, String ticker, String name, BigDecimal currentValue,
                                BigDecimal currentPercent, BigDecimal targetPercent, BigDecimal differenceEur,
                                BigDecimal quantityToTrade, String action, String warning) {}
    public record Rebalance(List<RebalanceLine> lines, BigDecimal totalValue, List<String> warnings) {}
    public record BackfillAccount(Long accountId, String accountName, LocalDate suggestedStartDate,
                                  String source, boolean needsStartDate, List<String> unknownTickers) {}
    public record BackfillPlan(List<BackfillAccount> accounts) {}
    public record BackfillResult(int snapshotsCreated, List<String> warnings) {}
}
