package com.picsou.service;

import com.picsou.dto.AnalyticsResponse;
import com.picsou.dto.AnalyticsRequest;
import com.picsou.model.Account;
import com.picsou.model.AccountType;
import com.picsou.model.BalanceSnapshot;
import com.picsou.model.Transaction;
import com.picsou.model.TransactionType;
import com.picsou.repository.*;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.InjectMocks;
import org.mockito.Mock;
import org.mockito.ArgumentCaptor;
import org.mockito.junit.jupiter.MockitoExtension;

import java.math.BigDecimal;
import java.time.LocalDate;
import java.util.List;
import java.util.Map;
import java.util.Optional;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.Mockito.*;

@ExtendWith(MockitoExtension.class)
class AnalyticsServiceTest {
    @Mock AccountRepository accountRepository;
    @Mock AccountHoldingRepository holdingRepository;
    @Mock BalanceSnapshotRepository snapshotRepository;
    @Mock TransactionRepository transactionRepository;
    @Mock PriceSnapshotRepository priceSnapshotRepository;
    @Mock PriceService priceService;
    @Mock FireSettingsRepository fireSettingsRepository;
    @Mock RebalanceTargetRepository targetRepository;
    @InjectMocks AnalyticsService service;

    @Test
    void lots_partialSaleIsAllocatedProRataToPreserveAverageCostAccounting() {
        Account account = Account.builder().id(1L).name("PEA").type(AccountType.PEA).build();
        Transaction firstBuy = transaction(TransactionType.BUY, LocalDate.of(2025, 1, 10), "10", "100");
        Transaction secondBuy = transaction(TransactionType.BUY, LocalDate.of(2025, 3, 10), "10", "200");
        Transaction sale = transaction(TransactionType.SELL, LocalDate.of(2025, 6, 10), "10", "250");

        when(accountRepository.findByIdInAndMemberId(List.of(1L), 8L)).thenReturn(List.of(account));
        when(transactionRepository.findByAccountIdOrderByDateAscIdAsc(1L)).thenReturn(List.of(firstBuy, secondBuy, sale));
        when(holdingRepository.findByAccount_Id(1L)).thenReturn(List.of());
        when(priceService.getPriceEur(eq("ETF"))).thenReturn(new BigDecimal("250"));

        List<AnalyticsResponse.Lot> lots = service.lots(List.of(1L), 8L);

        // A sale of half the aggregate position consumes half of each lot, rather than FIFO.
        // This keeps the detailed display consistent with Picsou's existing moving-average P&L.
        assertThat(lots).hasSize(2);
        assertThat(lots.get(0).purchaseDate()).isEqualTo(LocalDate.of(2025, 1, 10));
        assertThat(lots.get(0).remainingQuantity()).isEqualByComparingTo("5");
        assertThat(lots.get(0).costBasis()).isEqualByComparingTo("500");
        assertThat(lots.get(1).purchaseDate()).isEqualTo(LocalDate.of(2025, 3, 10));
        assertThat(lots.get(1).remainingQuantity()).isEqualByComparingTo("5");
        assertThat(lots.get(1).costBasis()).isEqualByComparingTo("1000");
    }

    @Test
    void cashBackfillWritesTheEndOfEachTransactionDay() {
        LocalDate start = LocalDate.now().minusDays(2);
        Account account = Account.builder().id(1L).name("Checking").type(AccountType.CHECKING)
            .currentBalance(new BigDecimal("1200")).build();
        Transaction openingMovement = Transaction.builder().date(start).amount(new BigDecimal("100")).build();
        Transaction nextDayMovement = Transaction.builder().date(start.plusDays(1)).amount(new BigDecimal("100")).build();
        when(accountRepository.findByIdInAndMemberId(List.of(1L), 8L)).thenReturn(List.of(account));
        when(transactionRepository.findByAccountIdOrderByDateAscIdAsc(1L)).thenReturn(List.of(openingMovement, nextDayMovement));
        when(snapshotRepository.findLatestByAccountId(1L)).thenReturn(Optional.empty());
        when(snapshotRepository.findByAccountIdAndDate(eq(1L), any())).thenReturn(Optional.empty());
        when(snapshotRepository.save(any(BalanceSnapshot.class))).thenAnswer(invocation -> invocation.getArgument(0));

        service.backfill(new AnalyticsRequest.BackfillRequest(List.of(1L), Map.of(), Map.of()), 8L);

        ArgumentCaptor<BalanceSnapshot> snapshots = ArgumentCaptor.forClass(BalanceSnapshot.class);
        verify(snapshotRepository, atLeast(2)).save(snapshots.capture());
        assertThat(snapshots.getAllValues().get(0).getBalance()).isEqualByComparingTo("1100");
        assertThat(snapshots.getAllValues().get(1).getBalance()).isEqualByComparingTo("1200");
    }

    private Transaction transaction(TransactionType type, LocalDate date, String quantity, String price) {
        return Transaction.builder().date(date).txType(type).ticker("ETF").name("Example ETF")
            .quantity(new BigDecimal(quantity)).pricePerUnit(new BigDecimal(price)).amount(BigDecimal.ZERO).build();
    }
}
