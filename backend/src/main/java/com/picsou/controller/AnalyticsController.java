package com.picsou.controller;

import com.picsou.dto.AnalyticsRequest;
import com.picsou.dto.AnalyticsResponse;
import com.picsou.service.AnalyticsService;
import com.picsou.service.UserContext;
import jakarta.validation.Valid;
import lombok.RequiredArgsConstructor;
import org.springframework.format.annotation.DateTimeFormat;
import org.springframework.web.bind.annotation.*;

import java.time.LocalDate;
import java.util.List;

@RestController
@RequestMapping("/api/analytics")
@RequiredArgsConstructor
public class AnalyticsController {
    private final AnalyticsService analyticsService;
    private final UserContext userContext;

    @GetMapping("/performance")
    public AnalyticsResponse.Performance performance(
        @RequestParam List<Long> accountIds,
        @RequestParam(required = false) @DateTimeFormat(iso = DateTimeFormat.ISO.DATE) LocalDate from,
        @RequestParam(required = false) @DateTimeFormat(iso = DateTimeFormat.ISO.DATE) LocalDate to,
        @RequestParam(required = false) String benchmark
    ) { return analyticsService.performance(accountIds, userContext.currentMemberId(), from, to, benchmark); }

    @GetMapping("/fire")
    public AnalyticsResponse.FireProjection fire(@RequestParam List<Long> accountIds) {
        return analyticsService.fire(accountIds, userContext.currentMemberId());
    }

    @GetMapping("/fire/settings")
    public AnalyticsResponse.FireSettings fireSettings() { return analyticsService.fireSettings(userContext.currentMemberId()); }

    @PutMapping("/fire/settings")
    public AnalyticsResponse.FireSettings updateFire(@Valid @RequestBody AnalyticsRequest.FireSettingsRequest request) {
        return analyticsService.updateFire(request, userContext.currentMember());
    }

    @GetMapping("/lots")
    public List<AnalyticsResponse.Lot> lots(@RequestParam List<Long> accountIds) {
        return analyticsService.lots(accountIds, userContext.currentMemberId());
    }

    @GetMapping("/rebalance")
    public AnalyticsResponse.Rebalance rebalance(@RequestParam List<Long> accountIds) {
        return analyticsService.rebalance(accountIds, userContext.currentMemberId());
    }

    @PutMapping("/rebalance/targets")
    public AnalyticsResponse.Rebalance targets(@Valid @RequestBody AnalyticsRequest.RebalanceTargetsRequest request) {
        return analyticsService.setTargets(request, userContext.currentMemberId());
    }

    @GetMapping("/backfill/plan")
    public AnalyticsResponse.BackfillPlan backfillPlan(@RequestParam List<Long> accountIds) {
        return analyticsService.backfillPlan(accountIds, userContext.currentMemberId());
    }

    @PostMapping("/backfill")
    public AnalyticsResponse.BackfillResult backfill(@Valid @RequestBody AnalyticsRequest.BackfillRequest request) {
        return analyticsService.backfill(request, userContext.currentMemberId());
    }
}
