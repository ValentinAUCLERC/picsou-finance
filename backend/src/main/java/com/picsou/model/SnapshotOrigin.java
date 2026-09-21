package com.picsou.model;

/** Explains whether a historical value came from a provider or analytics reconstruction. */
public enum SnapshotOrigin {
    OBSERVED,
    RECONSTRUCTED,
    ESTIMATED
}
