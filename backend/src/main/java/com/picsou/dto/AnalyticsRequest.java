package com.picsou.dto;

import jakarta.validation.Valid;
import jakarta.validation.constraints.*;
import java.math.BigDecimal;
import java.time.LocalDate;
import java.util.List;
import java.util.Map;

public final class AnalyticsRequest {
    private AnalyticsRequest() {}

    public record FireSettingsRequest(
        @NotNull @DecimalMin("0.01") BigDecimal annualExpenses,
        @NotNull @DecimalMin("0.00") BigDecimal monthlySavings,
        @NotNull @DecimalMin("0.001") @DecimalMax("1.000") BigDecimal safeWithdrawalRate,
        @Min(1) @Max(120) Integer currentAge,
        @Min(1) @Max(120) Integer retirementAge,
        @NotBlank @Pattern(regexp = "OBSERVED_TWR|MANUAL") String returnMode,
        @DecimalMin("-0.99") @DecimalMax("5.00") BigDecimal manualReturnRate
    ) {}
    public record Target(@NotNull Long accountId, @NotBlank String ticker,
                         @NotNull @DecimalMin("0.00") @DecimalMax("100.00") BigDecimal targetPercent) {}
    public record RebalanceTargetsRequest(@NotEmpty @Valid List<Target> targets) {}
    public record BackfillRequest(@NotEmpty List<Long> accountIds, Map<Long, LocalDate> accountStartDates,
                                  Map<String, LocalDate> lineStartDates) {}
}
