"""Pluggable provider implementations for the write path (Phase 8).

Git and notification backends sit behind protocol interfaces so the proposal
engine depends on the protocol, not the implementation. New backends can be
added without touching the proposal logic.
"""
