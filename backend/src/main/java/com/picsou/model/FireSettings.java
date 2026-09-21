package com.picsou.model;

import jakarta.persistence.*;
import lombok.*;

import java.math.BigDecimal;

@Entity
@Table(name = "fire_settings", uniqueConstraints = @UniqueConstraint(columnNames = "member_id"))
@Getter @Setter @NoArgsConstructor @AllArgsConstructor @Builder
public class FireSettings {
    @Id @GeneratedValue(strategy = GenerationType.IDENTITY)
    private Long id;

    @OneToOne(fetch = FetchType.LAZY, optional = false)
    @JoinColumn(name = "member_id", nullable = false)
    private FamilyMember member;

    @Column(name = "annual_expenses", nullable = false, precision = 20, scale = 2)
    @Builder.Default private BigDecimal annualExpenses = BigDecimal.valueOf(30000);
    @Column(name = "monthly_savings", nullable = false, precision = 20, scale = 2)
    @Builder.Default private BigDecimal monthlySavings = BigDecimal.ZERO;
    @Column(name = "safe_withdrawal_rate", nullable = false, precision = 8, scale = 6)
    @Builder.Default private BigDecimal safeWithdrawalRate = new BigDecimal("0.040000");
    @Column(name = "current_age") private Integer currentAge;
    @Column(name = "retirement_age") private Integer retirementAge;
    @Column(name = "return_mode", nullable = false, length = 20)
    @Builder.Default private String returnMode = "OBSERVED_TWR";
    @Column(name = "manual_return_rate", precision = 8, scale = 6)
    private BigDecimal manualReturnRate;
}
