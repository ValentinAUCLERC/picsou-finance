package com.picsou.repository;

import com.picsou.model.FireSettings;
import org.springframework.data.jpa.repository.JpaRepository;
import java.util.Optional;

public interface FireSettingsRepository extends JpaRepository<FireSettings, Long> {
    Optional<FireSettings> findByMemberId(Long memberId);
}
