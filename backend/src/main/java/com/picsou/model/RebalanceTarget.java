package com.picsou.model;

import jakarta.persistence.*;
import lombok.*;

import java.math.BigDecimal;

@Entity
@Table(name = "rebalance_target", uniqueConstraints = @UniqueConstraint(columnNames = {"member_id", "account_id", "ticker"}))
@Getter @Setter @NoArgsConstructor @AllArgsConstructor @Builder
public class RebalanceTarget {
    @Id @GeneratedValue(strategy = GenerationType.IDENTITY)
    private Long id;
    @ManyToOne(fetch = FetchType.LAZY, optional = false)
    @JoinColumn(name = "member_id", nullable = false)
    private FamilyMember member;
    @ManyToOne(fetch = FetchType.LAZY, optional = false)
    @JoinColumn(name = "account_id", nullable = false)
    private Account account;
    @Column(nullable = false, length = 30) private String ticker;
    @Column(name = "target_percent", nullable = false, precision = 8, scale = 4)
    private BigDecimal targetPercent;
}
