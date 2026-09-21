package com.picsou.repository;

import com.picsou.model.RebalanceTarget;
import org.springframework.data.jpa.repository.JpaRepository;
import java.util.Collection;
import java.util.List;

public interface RebalanceTargetRepository extends JpaRepository<RebalanceTarget, Long> {
    List<RebalanceTarget> findByMemberIdAndAccountIdIn(Long memberId, Collection<Long> accountIds);
    void deleteByMemberIdAndAccountIdIn(Long memberId, Collection<Long> accountIds);
}
