"""难受检测单测：30s 阈值 / 降频 / distress 事件 / 瞬时误伤防护。"""

from __future__ import annotations

from elysia.soul.distress import DistressMonitor

T0 = 1_000_000.0


def test_no_distress_below_threshold() -> None:
    monitor = DistressMonitor()
    assert monitor.update(T0, 50.0) is False
    assert monitor.distress is False


def test_distress_after_30s_hold() -> None:
    monitor = DistressMonitor()
    # 高压第 1 秒：记录 onset，不触发
    assert monitor.update(T0, 90.0) is False
    assert monitor.distress is False
    # 高压持续 29.9s：仍不触发（严格阈值）
    assert monitor.update(T0 + 29.9, 90.0) is False
    assert monitor.distress is False
    # 高压持续 30s：触发难受
    assert monitor.update(T0 + 30.0, 90.0) is True
    assert monitor.distress is True


def test_recovers_when_cpu_drops() -> None:
    monitor = DistressMonitor()
    monitor.update(T0, 90.0)
    monitor.update(T0 + 30.0, 90.0)
    assert monitor.distress is True
    # 恢复正常：难受解除，返回切换信号
    assert monitor.update(T0 + 35.0, 10.0) is True
    assert monitor.distress is False
    # 持续正常：无再次切换
    assert monitor.update(T0 + 40.0, 10.0) is False


def test_transient_spike_does_not_trigger() -> None:
    """瞬时高压（< 30s 即回落）不触发难受：防误伤。"""
    monitor = DistressMonitor()
    monitor.update(T0, 95.0)
    monitor.update(T0 + 5.0, 20.0)  # 5s 后回落
    assert monitor.distress is False
    monitor.update(T0 + 100.0, 95.0)  # 重新高压
    assert monitor.distress is False  # onset 重新计时


def test_missing_sample_is_ignored() -> None:
    monitor = DistressMonitor()
    assert monitor.update(T0, None) is False
    assert monitor.distress is False
