package com.picsou.service;

import com.picsou.dto.AnalyticsRequest;
import com.picsou.dto.AnalyticsResponse;
import com.picsou.exception.ResourceNotFoundException;
import com.picsou.model.*;
import com.picsou.repository.*;
import jakarta.transaction.Transactional;
import lombok.RequiredArgsConstructor;
import org.springframework.stereotype.Service;

import java.math.BigDecimal;
import java.math.RoundingMode;
import java.time.LocalDate;
import java.time.YearMonth;
import java.util.*;
import java.util.stream.Collectors;

@Service
@RequiredArgsConstructor
public class AnalyticsService {
    private static final BigDecimal HUNDRED = BigDecimal.valueOf(100);
    private static final int SCALE = 8;
    private final AccountRepository accountRepository;
    private final AccountHoldingRepository holdingRepository;
    private final BalanceSnapshotRepository snapshotRepository;
    private final TransactionRepository transactionRepository;
    private final PriceSnapshotRepository priceSnapshotRepository;
    private final PriceService priceService;
    private final FireSettingsRepository fireSettingsRepository;
    private final RebalanceTargetRepository targetRepository;

    public AnalyticsResponse.Performance performance(List<Long> accountIds, Long memberId,
                                                      LocalDate from, LocalDate to, String benchmarkTicker) {
        List<Account> accounts = accounts(accountIds, memberId);
        if (from == null) from = LocalDate.now().minusYears(1);
        if (to == null || to.isAfter(LocalDate.now())) to = LocalDate.now();
        if (from.isAfter(to)) throw new IllegalArgumentException("from must be before to");

        Map<Long, List<BalanceSnapshot>> snapshots = accounts.stream().collect(Collectors.toMap(
            Account::getId, account -> snapshotRepository.findByAccountIdOrderByDateAsc(account.getId())));
        String benchmark = benchmarkTicker == null || benchmarkTicker.isBlank() ? "^FCHI" : benchmarkTicker.toUpperCase(Locale.ROOT);
        priceService.backfillHistoricalPrices(Set.of(benchmark), from);
        Map<LocalDate, BigDecimal> benchmarkPrices = priceSnapshotRepository
            .findByTickerAndDateBetweenOrderByDateAsc(benchmark, from, to).stream()
            .collect(Collectors.toMap(PriceSnapshot::getDate, PriceSnapshot::getPriceEur, (a, b) -> b, TreeMap::new));

        List<AnalyticsResponse.PerformancePoint> points = new ArrayList<>();
        Map<String, BigDecimal> monthlyGrowth = new LinkedHashMap<>();
        BigDecimal previousTotal = null, previousInvested = null, twrIndex = HUNDRED, benchmarkBase = null, benchmarkLast = null;
        int returns = 0;
        boolean hasEstimate = false;
        for (LocalDate day = from; !day.isAfter(to); day = day.plusDays(1)) {
            BigDecimal total = BigDecimal.ZERO, invested = BigDecimal.ZERO;
            boolean estimated = false;
            for (Account account : accounts) {
                BalanceSnapshot snapshot = latestAt(snapshots.get(account.getId()), day);
                if (snapshot == null) continue;
                BigDecimal sign = account.getType() == AccountType.LOAN ? BigDecimal.ONE.negate() : BigDecimal.ONE;
                total = total.add(snapshot.getBalance().multiply(sign));
                if (account.getType() != AccountType.LOAN) invested = invested.add(snapshot.getInvestedAmount());
                estimated |= snapshot.getOrigin() != SnapshotOrigin.OBSERVED;
            }
            if (previousTotal != null) {
                BigDecimal flow = invested.subtract(previousInvested);
                BigDecimal denominator = previousTotal.add(flow);
                if (denominator.signum() > 0) {
                    BigDecimal daily = total.divide(denominator, SCALE, RoundingMode.HALF_UP).subtract(BigDecimal.ONE);
                    twrIndex = twrIndex.multiply(BigDecimal.ONE.add(daily));
                    monthlyGrowth.merge(YearMonth.from(day).toString(), BigDecimal.ONE.add(daily), BigDecimal::multiply);
                    returns++;
                }
            }
            BigDecimal price = benchmarkPrices.get(day);
            if (price != null) { if (benchmarkBase == null) benchmarkBase = price; benchmarkLast = price; }
            BigDecimal benchmarkPct = benchmarkBase == null || benchmarkLast == null ? null
                : benchmarkLast.divide(benchmarkBase, SCALE, RoundingMode.HALF_UP).subtract(BigDecimal.ONE).multiply(HUNDRED);
            points.add(new AnalyticsResponse.PerformancePoint(day, total, invested,
                twrIndex.subtract(HUNDRED), benchmarkPct, estimated));
            previousTotal = total; previousInvested = invested; hasEstimate |= estimated;
        }
        Map<String, BigDecimal> monthly = monthlyGrowth.entrySet().stream().collect(Collectors.toMap(
            Map.Entry::getKey, e -> e.getValue().subtract(BigDecimal.ONE).multiply(HUNDRED), (a,b)->b, LinkedHashMap::new));
        BigDecimal annualized = returns < 90 ? null : annualize(twrIndex, returns);
        List<String> warnings = new ArrayList<>();
        if (annualized == null) warnings.add("At least 90 daily observations are required for an observed annual return.");
        if (hasEstimate) warnings.add("The selected period includes reconstructed or estimated snapshots.");
        if (benchmarkBase == null) warnings.add("No historical benchmark price was available.");
        return new AnalyticsResponse.Performance(points, monthly, annualized, benchmark, warnings);
    }

    public AnalyticsResponse.FireProjection fire(List<Long> accountIds, Long memberId) {
        FireSettings settings = fireSettingsRepository.findByMemberId(memberId).orElseGet(() -> FireSettings.builder().build());
        AnalyticsResponse.Performance performance = performance(accountIds, memberId, LocalDate.now().minusYears(5), LocalDate.now(), "^FCHI");
        BigDecimal current = performance.points().isEmpty() ? BigDecimal.ZERO : performance.points().getLast().total();
        boolean observed = performance.annualizedReturn() != null;
        BigDecimal rate = "OBSERVED_TWR".equals(settings.getReturnMode()) && observed
            ? performance.annualizedReturn().divide(HUNDRED, SCALE, RoundingMode.HALF_UP) : settings.getManualReturnRate();
        BigDecimal fireNumber = settings.getAnnualExpenses().divide(settings.getSafeWithdrawalRate(), 2, RoundingMode.HALF_UP);
        BigDecimal progress = fireNumber.signum() == 0 ? BigDecimal.ZERO : current.divide(fireNumber, SCALE, RoundingMode.HALF_UP).multiply(HUNDRED);
        LocalDate date = rate == null ? null : projectedDate(current, settings.getMonthlySavings(), rate, fireNumber);
        return new AnalyticsResponse.FireProjection(toDto(settings), fireNumber, current, progress, rate, date,
            current.multiply(settings.getSafeWithdrawalRate()), observed);
    }

    @Transactional
    public AnalyticsResponse.FireSettings updateFire(AnalyticsRequest.FireSettingsRequest request, FamilyMember member) {
        FireSettings settings = fireSettingsRepository.findByMemberId(member.getId()).orElseGet(() -> FireSettings.builder().member(member).build());
        settings.setAnnualExpenses(request.annualExpenses()); settings.setMonthlySavings(request.monthlySavings());
        settings.setSafeWithdrawalRate(request.safeWithdrawalRate()); settings.setCurrentAge(request.currentAge());
        settings.setRetirementAge(request.retirementAge()); settings.setReturnMode(request.returnMode()); settings.setManualReturnRate(request.manualReturnRate());
        return toDto(fireSettingsRepository.save(settings));
    }

    public AnalyticsResponse.FireSettings fireSettings(Long memberId) {
        return toDto(fireSettingsRepository.findByMemberId(memberId).orElseGet(() -> FireSettings.builder().build()));
    }

    public List<AnalyticsResponse.Lot> lots(List<Long> accountIds, Long memberId) {
        List<AnalyticsResponse.Lot> out = new ArrayList<>();
        for (Account account : accounts(accountIds, memberId)) {
            Map<String, List<LotState>> byTicker = new LinkedHashMap<>();
            for (Transaction tx : transactionRepository.findByAccountIdOrderByDateAscIdAsc(account.getId())) {
                if (tx.getTicker() == null || tx.getTicker().isBlank() || tx.getQuantity() == null || tx.getQuantity().signum() <= 0) continue;
                String ticker = tx.getTicker().toUpperCase(Locale.ROOT);
                List<LotState> lots = byTicker.computeIfAbsent(ticker, ignored -> new ArrayList<>());
                if (tx.getTxType() == TransactionType.BUY) {
                    BigDecimal fees = nullToZero(tx.getFees());
                    BigDecimal cost = tx.getQuantity().multiply(nullToZero(tx.getPricePerUnit())).add(fees);
                    lots.add(new LotState(tx.getDate(), tx.getQuantity(), tx.getQuantity(), cost.divide(tx.getQuantity(), SCALE, RoundingMode.HALF_UP), tx.getName()));
                } else if (tx.getTxType() == TransactionType.SELL) {
                    BigDecimal held = lots.stream().map(l -> l.remaining).reduce(BigDecimal.ZERO, BigDecimal::add);
                    if (held.signum() == 0) continue;
                    BigDecimal sold = tx.getQuantity().min(held);
                    for (LotState lot : lots) {
                        if (lot.remaining.signum() <= 0) continue;
                        BigDecimal soldFromLot = sold.multiply(lot.remaining).divide(held, SCALE, RoundingMode.HALF_UP);
                        lot.remaining = lot.remaining.subtract(soldFromLot).max(BigDecimal.ZERO);
                    }
                }
            }
            Set<String> detailed = new HashSet<>(byTicker.keySet());
            for (Map.Entry<String, List<LotState>> entry : byTicker.entrySet()) {
                BigDecimal price = priceService.getPriceEur(entry.getKey());
                for (LotState lot : entry.getValue()) out.add(toLot(account, entry.getKey(), lot, price, true));
            }
            for (AccountHolding holding : holdingRepository.findByAccount_Id(account.getId())) if (!detailed.contains(holding.getTicker().toUpperCase(Locale.ROOT))) {
                BigDecimal price = priceService.getPriceEur(holding.getTicker());
                LotState aggregate = new LotState(null, holding.getQuantity(), holding.getQuantity(), holding.getAverageBuyIn(), holding.getName());
                out.add(toLot(account, holding.getTicker(), aggregate, price, false));
            }
        }
        return out;
    }

    @Transactional
    public AnalyticsResponse.Rebalance setTargets(AnalyticsRequest.RebalanceTargetsRequest request, Long memberId) {
        BigDecimal sum = request.targets().stream().map(AnalyticsRequest.Target::targetPercent).reduce(BigDecimal.ZERO, BigDecimal::add);
        if (sum.compareTo(HUNDRED) != 0) throw new IllegalArgumentException("Rebalance targets must sum to 100.");
        List<Long> ids = request.targets().stream().map(AnalyticsRequest.Target::accountId).distinct().toList();
        Map<Long, Account> accounts = accounts(ids, memberId).stream().collect(Collectors.toMap(Account::getId, a -> a));
        targetRepository.deleteByMemberIdAndAccountIdIn(memberId, ids);
        targetRepository.saveAll(request.targets().stream().map(target -> RebalanceTarget.builder().member(accounts.get(target.accountId()).getMember())
            .account(accounts.get(target.accountId())).ticker(target.ticker().toUpperCase(Locale.ROOT)).targetPercent(target.targetPercent()).build()).toList());
        return rebalance(ids, memberId);
    }

    public AnalyticsResponse.Rebalance rebalance(List<Long> ids, Long memberId) {
        List<Account> accounts = accounts(ids, memberId);
        Map<String, BigDecimal> targets = targetRepository.findByMemberIdAndAccountIdIn(memberId, ids).stream()
            .collect(Collectors.toMap(t -> t.getAccount().getId() + ":" + t.getTicker(), RebalanceTarget::getTargetPercent));
        List<AnalyticsResponse.RebalanceLine> result = new ArrayList<>(); BigDecimal total = BigDecimal.ZERO;
        List<AccountHolding> holdings = accounts.stream().flatMap(a -> holdingRepository.findByAccount_Id(a.getId()).stream()).toList();
        Map<String, BigDecimal> prices = new HashMap<>();
        for (AccountHolding h : holdings) { BigDecimal p = priceService.getPriceEur(h.getTicker()); if (p != null) { prices.put(h.getAccount().getId()+":"+h.getTicker(), p); total = total.add(h.getQuantity().multiply(p)); } }
        List<String> warnings = new ArrayList<>();
        for (AccountHolding h : holdings) {
            String key = h.getAccount().getId()+":"+h.getTicker(); BigDecimal p = prices.get(key); BigDecimal value = p == null ? null : h.getQuantity().multiply(p);
            BigDecimal target = targets.getOrDefault(key, BigDecimal.ZERO);
            if (p == null) { warnings.add("No EUR price for " + h.getTicker()); result.add(new AnalyticsResponse.RebalanceLine(h.getAccount().getId(), h.getTicker(), h.getName(), null, null, target, null, null, "UNAVAILABLE", "Missing price")); continue; }
            BigDecimal currentPct = total.signum() == 0 ? BigDecimal.ZERO : value.divide(total, SCALE, RoundingMode.HALF_UP).multiply(HUNDRED);
            BigDecimal difference = total.multiply(target.divide(HUNDRED, SCALE, RoundingMode.HALF_UP)).subtract(value);
            result.add(new AnalyticsResponse.RebalanceLine(h.getAccount().getId(), h.getTicker(), h.getName(), value, currentPct, target, difference,
                difference.divide(p, SCALE, RoundingMode.HALF_UP), difference.signum() > 0 ? "BUY" : difference.signum() < 0 ? "SELL" : "HOLD", null));
        }
        return new AnalyticsResponse.Rebalance(result, total, warnings);
    }

    public AnalyticsResponse.BackfillPlan backfillPlan(List<Long> ids, Long memberId) {
        return new AnalyticsResponse.BackfillPlan(accounts(ids, memberId).stream().map(a -> {
            List<Transaction> transactions = transactionRepository.findByAccountIdOrderByDateAscIdAsc(a.getId());
            LocalDate first = transactions.stream().map(Transaction::getDate).min(LocalDate::compareTo).orElse(null);
            Set<String> transactionTickers = transactions.stream().map(Transaction::getTicker)
                .filter(Objects::nonNull).map(ticker -> ticker.toUpperCase(Locale.ROOT)).collect(Collectors.toSet());
            List<String> unknownTickers = holdingRepository.findByAccount_Id(a.getId()).stream()
                .map(AccountHolding::getTicker).filter(ticker -> !transactionTickers.contains(ticker.toUpperCase(Locale.ROOT)))
                .sorted().toList();
            return new AnalyticsResponse.BackfillAccount(a.getId(), a.getName(), first,
                first == null ? "USER_INPUT_REQUIRED" : "FIRST_TRANSACTION", first == null, unknownTickers);
        }).toList());
    }

    /**
     * Rebuilds only missing historical days. Investment positions enter on their own BUY date;
     * an undated provider holding may enter only on the line date explicitly supplied by the user.
     */
    @Transactional
    public AnalyticsResponse.BackfillResult backfill(AnalyticsRequest.BackfillRequest request, Long memberId) {
        int created = 0;
        List<String> warnings = new ArrayList<>();
        LocalDate earliestAllowed = LocalDate.now().minusYears(5);
        for (Account account : accounts(request.accountIds(), memberId)) {
            List<Transaction> transactions = transactionRepository.findByAccountIdOrderByDateAscIdAsc(account.getId());
            LocalDate transactionStart = transactions.stream().map(Transaction::getDate).min(LocalDate::compareTo).orElse(null);
            LocalDate start = transactionStart != null ? transactionStart
                : request.accountStartDates() == null ? null : request.accountStartDates().get(account.getId());
            if (start == null) { warnings.add(account.getName() + ": a start date is required."); continue; }
            if (start.isBefore(earliestAllowed)) { start = earliestAllowed; warnings.add(account.getName() + ": limited to five years."); }
            if (isInvestment(account)) created += backfillInvestment(account, transactions, start, request.lineStartDates(), warnings);
            else created += backfillCashAccount(account, transactions, start);
        }
        return new AnalyticsResponse.BackfillResult(created, warnings);
    }

    private List<Account> accounts(List<Long> ids, Long memberId) {
        if (ids == null || ids.isEmpty()) throw new IllegalArgumentException("At least one account is required.");
        List<Account> result = accountRepository.findByIdInAndMemberId(ids.stream().distinct().toList(), memberId);
        if (result.size() != ids.stream().distinct().count()) throw ResourceNotFoundException.account(ids.getFirst());
        return result;
    }
    private int backfillCashAccount(Account account, List<Transaction> transactions, LocalDate start) {
        Map<LocalDate, BigDecimal> movement = new HashMap<>();
        for (Transaction tx : transactions) movement.merge(tx.getDate(), tx.getAmount(), BigDecimal::add);
        // A snapshot is already expressed in EUR; prefer it to the account's native balance
        // when one exists (USD bank accounts are otherwise accidentally treated as EUR).
        BigDecimal value = snapshotRepository.findLatestByAccountId(account.getId())
            .map(BalanceSnapshot::getBalance).orElse(account.getCurrentBalance());
        for (Transaction tx : transactions) if (tx.getDate().isAfter(start)) value = value.subtract(tx.getAmount());
        int created = 0;
        for (LocalDate day = start; day.isBefore(LocalDate.now()); day = day.plusDays(1)) {
            if (snapshotRepository.findByAccountIdAndDate(account.getId(), day).isEmpty()) {
                snapshotRepository.save(BalanceSnapshot.builder().account(account).date(day).balance(value).investedAmount(value)
                    .origin(SnapshotOrigin.RECONSTRUCTED).build());
                created++;
            }
            // `value` starts as the end-of-start-day balance (all later movements were
            // subtracted above), so move to the following day's end-of-day value here.
            value = value.add(movement.getOrDefault(day.plusDays(1), BigDecimal.ZERO));
        }
        return created;
    }
    private int backfillInvestment(Account account, List<Transaction> transactions, LocalDate start,
                                   Map<String, LocalDate> lineStarts, List<String> warnings) {
        Map<LocalDate, List<Transaction>> byDate = transactions.stream().collect(Collectors.groupingBy(Transaction::getDate));
        Map<String, BigDecimal> quantities = new HashMap<>();
        Map<String, BigDecimal> costs = new HashMap<>();
        Map<String, LocalDate> implicitStarts = new HashMap<>();
        for (AccountHolding holding : holdingRepository.findByAccount_Id(account.getId())) {
            String key = account.getId() + ":" + holding.getTicker().toUpperCase(Locale.ROOT);
            LocalDate lineStart = lineStarts == null ? null : lineStarts.get(key);
            if (lineStart != null) implicitStarts.put(holding.getTicker().toUpperCase(Locale.ROOT), lineStart);
        }
        Set<String> allTickers = new TreeSet<>();
        transactions.stream().map(Transaction::getTicker).filter(Objects::nonNull).forEach(t -> allTickers.add(t.toUpperCase(Locale.ROOT)));
        allTickers.addAll(implicitStarts.keySet());
        priceService.backfillHistoricalPrices(allTickers, start);
        Map<String, Map<LocalDate, BigDecimal>> prices = new HashMap<>();
        for (String ticker : allTickers) prices.put(ticker, priceSnapshotRepository.findByTickerAndDateBetweenOrderByDateAsc(ticker, start, LocalDate.now()).stream()
            .collect(Collectors.toMap(PriceSnapshot::getDate, PriceSnapshot::getPriceEur, (a,b)->b, TreeMap::new)));
        int created = 0;
        for (LocalDate day = start; day.isBefore(LocalDate.now()); day = day.plusDays(1)) {
            for (AccountHolding holding : holdingRepository.findByAccount_Id(account.getId())) {
                String ticker = holding.getTicker().toUpperCase(Locale.ROOT);
                if (implicitStarts.get(ticker) != null && implicitStarts.get(ticker).equals(day)) {
                    quantities.put(ticker, holding.getQuantity()); costs.put(ticker, nullToZero(holding.getAverageBuyIn()).multiply(holding.getQuantity()));
                }
            }
            for (Transaction tx : byDate.getOrDefault(day, List.of())) applyTransaction(quantities, costs, tx);
            if (snapshotRepository.findByAccountIdAndDate(account.getId(), day).isPresent()) continue;
            BigDecimal value = BigDecimal.ZERO, invested = BigDecimal.ZERO; boolean complete = true;
            for (Map.Entry<String, BigDecimal> entry : quantities.entrySet()) if (entry.getValue().signum() > 0) {
                BigDecimal price = priceAt(prices.get(entry.getKey()), day);
                if (price == null) { complete = false; warnings.add(account.getName() + ": no historical price for " + entry.getKey() + " on " + day); break; }
                value = value.add(entry.getValue().multiply(price)); invested = invested.add(costs.getOrDefault(entry.getKey(), BigDecimal.ZERO));
            }
            if (!complete) continue;
            snapshotRepository.save(BalanceSnapshot.builder().account(account).date(day).balance(value).investedAmount(invested)
                .origin(transactions.isEmpty() ? SnapshotOrigin.ESTIMATED : SnapshotOrigin.RECONSTRUCTED).build()); created++;
        }
        return created;
    }
    private static void applyTransaction(Map<String, BigDecimal> quantities, Map<String, BigDecimal> costs, Transaction tx) {
        if (tx.getTicker() == null || tx.getQuantity() == null || tx.getQuantity().signum() <= 0) return;
        String ticker = tx.getTicker().toUpperCase(Locale.ROOT); BigDecimal fees = nullToZero(tx.getFees()); BigDecimal amount = tx.getQuantity().multiply(nullToZero(tx.getPricePerUnit()));
        if (tx.getTxType() == TransactionType.BUY) { quantities.merge(ticker, tx.getQuantity(), BigDecimal::add); costs.merge(ticker, amount.add(fees), BigDecimal::add); }
        if (tx.getTxType() == TransactionType.SELL) {
            BigDecimal held = quantities.getOrDefault(ticker, BigDecimal.ZERO); BigDecimal sold = tx.getQuantity().min(held);
            BigDecimal cost = costs.getOrDefault(ticker, BigDecimal.ZERO); BigDecimal costOut = held.signum() == 0 ? BigDecimal.ZERO : cost.multiply(sold).divide(held, SCALE, RoundingMode.HALF_UP);
            quantities.put(ticker, held.subtract(sold)); costs.put(ticker, cost.subtract(costOut));
        }
    }
    private static BigDecimal priceAt(Map<LocalDate, BigDecimal> prices, LocalDate day) {
        if (prices == null) return null; BigDecimal value = null; for (Map.Entry<LocalDate, BigDecimal> entry : prices.entrySet()) { if (entry.getKey().isAfter(day)) break; value=entry.getValue(); } return value;
    }
    private static boolean isInvestment(Account a) { return switch (a.getType()) { case PEA, COMPTE_TITRES, CRYPTO, EMPLOYEE_SAVINGS -> true; default -> false; }; }
    private static BalanceSnapshot latestAt(List<BalanceSnapshot> list, LocalDate day) { BalanceSnapshot latest = null; for (BalanceSnapshot s : list) { if (s.getDate().isAfter(day)) break; latest=s; } return latest; }
    private static BigDecimal annualize(BigDecimal index, int days) { return BigDecimal.valueOf(Math.pow(index.divide(HUNDRED, SCALE, RoundingMode.HALF_UP).doubleValue(), 365d / days) - 1).multiply(HUNDRED); }
    private static BigDecimal nullToZero(BigDecimal n) { return n == null ? BigDecimal.ZERO : n; }
    private static AnalyticsResponse.FireSettings toDto(FireSettings s) { return new AnalyticsResponse.FireSettings(s.getAnnualExpenses(), s.getMonthlySavings(), s.getSafeWithdrawalRate(), s.getCurrentAge(), s.getRetirementAge(), s.getReturnMode(), s.getManualReturnRate()); }
    private static LocalDate projectedDate(BigDecimal current, BigDecimal monthly, BigDecimal annual, BigDecimal target) { if (current.compareTo(target)>=0) return LocalDate.now(); for (int m=1;m<=1200;m++) { current=current.multiply(BigDecimal.ONE.add(annual.divide(BigDecimal.valueOf(12), SCALE, RoundingMode.HALF_UP))).add(monthly); if(current.compareTo(target)>=0)return LocalDate.now().plusMonths(m); } return null; }
    private static AnalyticsResponse.Lot toLot(Account a, String ticker, LotState l, BigDecimal price, boolean detailed) { BigDecimal cost=l.remaining.multiply(nullToZero(l.costPerUnit)); BigDecimal value=price==null?null:l.remaining.multiply(price); BigDecimal pnl=value==null?null:value.subtract(cost); BigDecimal pct=cost.signum()==0||pnl==null?null:pnl.divide(cost,SCALE,RoundingMode.HALF_UP).multiply(HUNDRED); return new AnalyticsResponse.Lot(a.getId(),a.getName(),ticker,l.name,l.date,l.original,l.remaining,l.costPerUnit,cost,price,value,pnl,pct,detailed); }
    private static final class LotState { final LocalDate date; final BigDecimal original; BigDecimal remaining; final BigDecimal costPerUnit; final String name; LotState(LocalDate date, BigDecimal original, BigDecimal remaining, BigDecimal costPerUnit, String name) { this.date=date;this.original=original;this.remaining=remaining;this.costPerUnit=costPerUnit;this.name=name; } }
}
